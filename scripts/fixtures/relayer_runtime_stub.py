from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _extract_flag(name: str, default: str | None = None) -> str | None:
    if name not in sys.argv:
        return default
    index = sys.argv.index(name)
    if index + 1 >= len(sys.argv):
        return default
    return sys.argv[index + 1]


def main() -> None:
    manifest_arg = _extract_flag("--manifest")
    runtime_label = _extract_flag("--runtime-label", "unknown")
    if not manifest_arg:
        raise SystemExit("Usage: relayer_runtime_stub.py --manifest <path> --runtime-label <label>")

    manifest_path = Path(manifest_arg).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    relayer = manifest.get("relayer", {})
    execution_order = relayer.get("plan", {}).get("execution_order", [])

    payload = {
        "ok": True,
        "runtime_label": runtime_label,
        "config_id": manifest.get("config_id"),
        "runner": manifest.get("runner"),
        "mode": manifest.get("mode"),
        "range_text": relayer.get("range_text"),
        "execution_order_length": len(execution_order),
        "manifest_path": str(manifest_path),
        "env_manifest": os.environ.get("AEL_RELAYER_MANIFEST"),
        "env_runtime_label": os.environ.get("AEL_RELAYER_RUNTIME_LABEL"),
        "env_run_id": os.environ.get("AEL_RELAYER_RUN_ID"),
    }
    sidecar_path = manifest_path.parent / "stub_runtime_applied.json"
    sidecar_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["sidecar_path"] = str(sidecar_path.resolve())
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
