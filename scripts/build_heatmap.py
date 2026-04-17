from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storage.heat_map_artifacts import build_heat_map_artifacts
from storage.history_writer import write_json


def _load_nightly_artifact(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Nightly artifact must be a JSON object: {path}")
    if not isinstance(payload.get("heat_map"), dict):
        raise RuntimeError(f"Artifact does not contain heat-map summary: {path}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Build standalone heat-map artifacts from a nightly artifact.")
    parser.add_argument("--artifact", default=None, help="Path to a nightly artifact JSON under runs/artifacts.")
    parser.add_argument("--suite-id", default=None, help="Suite id; resolves to runs/artifacts/<suite_id>.json.")
    parser.add_argument("--output-dir", default=None, help="Output directory; defaults to reports/heat_maps/<suite_id>.")
    args = parser.parse_args()

    if not args.artifact and not args.suite_id:
        raise RuntimeError("Provide either --artifact or --suite-id.")

    artifact_path = Path(args.artifact) if args.artifact else ROOT / "runs" / "artifacts" / f"{args.suite_id}.json"
    payload = _load_nightly_artifact(artifact_path.resolve())
    suite_id = str(payload.get("suite_id") or args.suite_id or artifact_path.stem)
    output_dir = Path(args.output_dir).resolve() if args.output_dir else ROOT / "reports" / "heat_maps" / suite_id

    manifest = build_heat_map_artifacts(
        output_dir=output_dir,
        suite_id=suite_id,
        heat_map_summary=payload["heat_map"],
        created_at=str(payload.get("created_at") or payload.get("ts") or ""),
        base_config_id=str(payload.get("base_config_id") or payload.get("config_id") or "unknown_config"),
        selected_config_id=str(payload.get("selected_config_id") or payload.get("config_id") or "unknown_config"),
        status=str(payload.get("status") or "unknown"),
    )
    manifest["source_artifact_path"] = str(artifact_path.resolve())
    write_json(output_dir / "rebuild_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
