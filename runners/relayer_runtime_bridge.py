from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


RUNNER_CONFIG_SECTION_MAP = {
    "llama_cpp_agent": "llama_cpp",
    "openclaw_cli": "openclaw",
    "session_mock": None,
}


def _runtime_backend_section(config: dict[str, Any]) -> dict[str, Any]:
    relayer = config.get("relayer", {})
    if not isinstance(relayer, dict):
        return {}
    backend = relayer.get("runtime_backend", {})
    return backend if isinstance(backend, dict) else {}


def runtime_backend_command(config: dict[str, Any]) -> list[str]:
    backend = _runtime_backend_section(config)
    command = backend.get("command")
    if isinstance(command, list):
        values = [str(item).strip() for item in command if str(item).strip()]
        return values
    if str(command or "").strip():
        return [str(command).strip()]
    return []


def _runtime_backend_cwd(root: Path, config: dict[str, Any]) -> Path:
    backend = _runtime_backend_section(config)
    working_dir = str(backend.get("working_dir", "")).strip()
    if not working_dir:
        return root
    path = Path(working_dir)
    return path if path.is_absolute() else (root / path).resolve()


def _runtime_backend_env(config: dict[str, Any]) -> dict[str, str]:
    backend = _runtime_backend_section(config)
    extra_env = backend.get("extra_env", {})
    if not isinstance(extra_env, dict):
        return {}
    return {str(key): str(value) for key, value in extra_env.items()}


def _runtime_config_snapshot(config: dict[str, Any], runtime_label: str) -> dict[str, Any]:
    section_name = RUNNER_CONFIG_SECTION_MAP.get(runtime_label)
    if not section_name:
        return {}
    section = config.get(section_name, {})
    return dict(section) if isinstance(section, dict) else {}


def invoke_external_relayer_runtime(
    *,
    root: Path,
    run_id: str,
    runtime_label: str,
    config: dict[str, Any],
    relayer_context: dict[str, Any],
) -> dict[str, Any]:
    command = runtime_backend_command(config)
    if not command:
        raise RuntimeError(
            f"Relayer mode=runtime_patch requires relayer.runtime_backend.command for {runtime_label}."
        )

    output_dir = root / "runs" / "relayer_runtime" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    stdout_path = output_dir / "stdout.txt"
    stderr_path = output_dir / "stderr.txt"
    result_path = output_dir / "result.json"

    backend = _runtime_backend_section(config)
    runtime_root = root.resolve()
    working_dir = _runtime_backend_cwd(runtime_root, config)

    manifest = {
        "run_id": run_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "runtime_label": runtime_label,
        "runner": runtime_label,
        "root_dir": str(runtime_root),
        "working_dir": str(working_dir),
        "output_dir": str(output_dir.resolve()),
        "config_id": config.get("config_id"),
        "mode": relayer_context.get("mode"),
        "relayer": relayer_context,
        "runtime_backend": dict(backend),
        "runtime_config": _runtime_config_snapshot(config, runtime_label),
        "sandbox": dict(config.get("sandbox", {})) if isinstance(config.get("sandbox", {}), dict) else {},
        "paths": dict(config.get("paths", {})) if isinstance(config.get("paths", {}), dict) else {},
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    timeout_sec = int(backend.get("timeout_sec", 60))
    args = command + ["--manifest", str(manifest_path), "--runtime-label", runtime_label]
    environment = os.environ.copy()
    environment["AEL_RELAYER_MANIFEST"] = str(manifest_path)
    environment["AEL_RELAYER_RUN_ID"] = run_id
    environment["AEL_RELAYER_RUNTIME_LABEL"] = runtime_label
    environment.update(_runtime_backend_env(config))

    completed = subprocess.run(
        args,
        cwd=working_dir,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        env=environment,
        check=False,
    )
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    result_payload: dict[str, Any] | None = None
    if stdout:
        try:
            parsed = json.loads(stdout)
            if isinstance(parsed, dict):
                result_payload = parsed
        except Exception:
            result_payload = None
    if result_payload is not None:
        result_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    metadata = {
        "command": args,
        "manifest_path": str(manifest_path.resolve()),
        "output_dir": str(output_dir.resolve()),
        "working_dir": str(working_dir),
        "stdout_path": str(stdout_path.resolve()),
        "stderr_path": str(stderr_path.resolve()),
        "result_path": str(result_path.resolve()) if result_payload is not None else None,
        "returncode": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "result": result_payload,
    }
    if completed.returncode != 0:
        raise RuntimeError(
            f"Relayer runtime backend failed for {runtime_label} with exit={completed.returncode}: {stderr or stdout or 'no output'}"
        )
    return metadata
