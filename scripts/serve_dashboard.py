from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
CONFIGS_DIR = ROOT / "configs" / "experiments"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from generators.km_file_tree_gen import TASK_TYPE_OPTIONS

PROGRESS_KEYS = (
    "task_type",
    "suite_progress_current",
    "suite_progress_target",
    "suite_progress_text",
    "progress_current",
    "progress_target",
    "progress_text",
)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class RunController:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.lock = threading.Lock()
        self.logs_dir = root / "runs" / "control"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.live_status_path = root / "runs" / "live_status.json"
        self.live_stream_path = root / "runs" / "live_stream.jsonl"
        self.process: subprocess.Popen[str] | None = None
        self.log_handle = None
        self.current: dict[str, Any] | None = None
        self.last_result: dict[str, Any] | None = None

    def _config_dir(self) -> Path:
        return CONFIGS_DIR

    def _config_summary(self, path: Path) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "name": path.name,
            "relative_path": self._relative_path(path),
            "config_id": path.stem,
            "runner": None,
        }
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return summary
        if isinstance(payload, dict):
            summary["config_id"] = payload.get("config_id") or summary["config_id"]
            summary["runner"] = payload.get("runner")
        return summary

    def _default_config_path(self) -> Path | None:
        preferred = [
            self._config_dir() / "local_llama_cpp_agent.json",
            self._config_dir() / "default_mvp.json",
        ]
        for candidate in preferred:
            if candidate.exists():
                return candidate
        matches = sorted(self._config_dir().glob("*.json"))
        return matches[0] if matches else None

    def _relative_path(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return str(path.resolve())

    def list_configs(self) -> dict[str, Any]:
        default_path = self._default_config_path()
        items = [self._config_summary(path) for path in sorted(self._config_dir().glob("*.json"))]
        for item in items:
            item["selected"] = default_path is not None and item["relative_path"] == self._relative_path(default_path)
        items.sort(key=lambda item: (0 if item["selected"] else 1, item["name"]))
        return {
            "configs": items,
            "default_config": self._relative_path(default_path) if default_path else None,
        }

    def list_task_types(self) -> dict[str, Any]:
        return {
            "task_types": [dict(item) for item in TASK_TYPE_OPTIONS],
            "default_task_type": "auto",
        }

    def _normalize_task_type(self, raw_value: Any) -> str:
        value = str(raw_value or "auto").strip().lower() or "auto"
        valid_values = {item["value"] for item in TASK_TYPE_OPTIONS}
        if value not in valid_values:
            raise RuntimeError(f"Unsupported task type: {raw_value}")
        return value

    def _read_live_status(self) -> dict[str, Any]:
        if not self.live_status_path.exists():
            return {}
        try:
            payload = json.loads(self.live_status_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _estimate_nightly_total_evals(self, config_path: Path) -> int | None:
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        regression_path_value = config.get("regression_suite", {}).get("path")
        if not regression_path_value:
            return None
        regression_path = (self.root / str(regression_path_value)).resolve()
        try:
            regression_suite = json.loads(regression_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        pool_size = int(config.get("nightly", {}).get("candidate_pool_size", 1))
        candidate_runs = int(
            config.get("nightly", {}).get(
                "candidate_runs_per_config",
                config.get("nightly", {}).get("candidate_runs", 4),
            )
        )
        include_base = bool(config.get("nightly", {}).get("include_base_config", True))
        variant_count = max(pool_size, 1 if include_base else 1)
        case_count = len(regression_suite.get("cases", []))
        return variant_count * (candidate_runs + case_count)

    def _resolve_config_path(self, raw_value: str | None) -> Path:
        if raw_value:
            candidate = Path(raw_value)
            candidates = []
            if candidate.is_absolute():
                candidates.append(candidate)
            else:
                candidates.append((self.root / raw_value).resolve())
                candidates.append((self._config_dir() / raw_value).resolve())
            for path in candidates:
                if path.exists():
                    return path
            raise FileNotFoundError(f"Config not found: {raw_value}")

        default_path = self._default_config_path()
        if default_path is None:
            raise FileNotFoundError("No experiment configs were found under configs/experiments.")
        return default_path

    def _close_log_handle_locked(self) -> None:
        if self.log_handle is not None:
            try:
                self.log_handle.close()
            except Exception:
                pass
            self.log_handle = None

    def _write_live_status(self, payload: dict[str, Any]) -> None:
        self.live_status_path.parent.mkdir(parents=True, exist_ok=True)
        data = dict(payload)
        data.setdefault("updated_at", now_iso())
        self.live_status_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append_live_event(self, text: str, **extra: Any) -> None:
        self.live_stream_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"ts": now_iso(), "type": "system", "name": "dashboard_control", "text": text, **extra}
        with self.live_stream_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _refresh_process_locked(self) -> None:
        if self.process is None:
            return

        returncode = self.process.poll()
        if returncode is None:
            if self.current is not None:
                self.current["pid"] = self.process.pid
            return

        finished_at = now_iso()
        metadata = dict(self.current or {})
        current_status = self._read_live_status()
        for key in PROGRESS_KEYS:
            if current_status.get(key) is not None:
                metadata[key] = current_status.get(key)
        metadata.update(
            {
                "active": False,
                "finished_at": finished_at,
                "returncode": returncode,
            }
        )
        self.last_result = metadata
        self.process = None
        self.current = None
        self._close_log_handle_locked()
        self._append_live_event(
            f"Dashboard process finished with exit code {returncode}.",
            control_kind=metadata.get("kind"),
            control_config=metadata.get("config_name"),
        )

    def _snapshot_locked(self, message: str | None = None) -> dict[str, Any]:
        self._refresh_process_locked()
        current = dict(self.current) if self.current else None
        last_result = dict(self.last_result) if self.last_result else None
        data = {
            **self.list_configs(),
            **self.list_task_types(),
            "active": bool(current),
            "current": current,
            "last_result": last_result,
        }
        if message:
            data["message"] = message
        return data

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return self._snapshot_locked()

    def _launch_locked(
        self,
        *,
        kind: str,
        config_path: Path,
        extra_args: list[str],
        extras: dict[str, Any],
    ) -> dict[str, Any]:
        self._refresh_process_locked()
        if self.process is not None:
            raise RuntimeError("Another experiment is already running. Stop it before starting a new one.")

        script_name_by_kind = {
            "single": "run_single.py",
            "suite": "run_suite.py",
            "nightly": "run_nightly.py",
        }
        script_name = script_name_by_kind.get(kind)
        if script_name is None:
            raise RuntimeError(f"Unsupported experiment kind: {kind}")
        script_path = self.root / "scripts" / script_name
        summary = self._config_summary(config_path)
        command = [sys.executable, str(script_path), "--config", str(config_path), *extra_args]
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = self.logs_dir / f"{kind}_{stamp}.log"

        self._close_log_handle_locked()
        self.log_handle = log_path.open("w", encoding="utf-8")
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        self.process = subprocess.Popen(
            command,
            cwd=self.root,
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creationflags,
        )

        self.current = {
            "kind": kind,
            "pid": self.process.pid,
            "command": command,
            "config_name": summary["name"],
            "config_id": summary["config_id"],
            "config_path": str(config_path.resolve()),
            "config_relative": summary["relative_path"],
            "runner": summary["runner"],
            "started_at": now_iso(),
            "log_path": str(log_path.resolve()),
            **extras,
        }
        self.last_result = None

        run_kind = kind
        suite_progress_target = extras.get("runs") if kind == "suite" else None
        suite_progress_current = 0 if kind == "suite" else None
        progress_target = extras.get("estimated_total_evals")
        if kind == "suite":
            progress_target = extras.get("runs")
        elif kind == "single":
            progress_target = 1
        progress_current = 0 if kind in {"suite", "nightly"} else 1
        status_payload = {
            "run_id": None,
            "status": "launching",
            "task_id": None,
            "config_id": summary["config_id"],
            "runner": "dashboard_control",
            "current_tool": "process_launch",
            "last_error": None,
            "step_count": 0,
            "max_steps": progress_target or 1,
            "elapsed_sec": 0,
            "updated_at": now_iso(),
            "run_kind": run_kind,
            "suite_id": None,
            "case_id": None,
            "task_type": extras.get("task_type", "auto"),
            "control_active": True,
            "control_pid": self.process.pid,
            "control_command": " ".join(command),
        }
        if suite_progress_current is not None:
            status_payload["suite_progress_current"] = suite_progress_current
        if suite_progress_target is not None:
            status_payload["suite_progress_target"] = suite_progress_target
        if suite_progress_current is not None and suite_progress_target is not None:
            status_payload["suite_progress_text"] = f"{suite_progress_current}/{suite_progress_target}"
        if progress_target is not None:
            status_payload["progress_target"] = progress_target
            status_payload["progress_current"] = progress_current
            status_payload["progress_text"] = f"{progress_current}/{progress_target}"
        self._write_live_status(
            status_payload
        )
        self._append_live_event(
            f"Started {kind} experiment from dashboard using {summary['name']}.",
            control_kind=kind,
            control_config=summary["name"],
        )
        return self._snapshot_locked(message=f"Started {kind} experiment.")

    def start_single(self, payload: dict[str, Any]) -> dict[str, Any]:
        config_path = self._resolve_config_path(payload.get("config"))
        extra_args: list[str] = []
        task_type = self._normalize_task_type(payload.get("task_type"))
        extra_args.extend(["--task-type", task_type])
        extras: dict[str, Any] = {"task_type": task_type}
        seed = payload.get("seed")
        if seed is not None and str(seed).strip() != "":
            seed_value = int(seed)
            extra_args.extend(["--seed", str(seed_value)])
            extras["seed"] = seed_value

        with self.lock:
            return self._launch_locked(kind="single", config_path=config_path, extra_args=extra_args, extras=extras)

    def start_suite(self, payload: dict[str, Any]) -> dict[str, Any]:
        config_path = self._resolve_config_path(payload.get("config"))
        runs = int(payload.get("runs", 3))
        if runs < 1:
            raise RuntimeError("Suite runs must be at least 1.")

        task_type = self._normalize_task_type(payload.get("task_type"))
        extra_args = ["--runs", str(runs), "--task-type", task_type]
        extras: dict[str, Any] = {"runs": runs, "task_type": task_type}
        seed_start = payload.get("seed_start")
        if seed_start is not None and str(seed_start).strip() != "":
            seed_start_value = int(seed_start)
            extra_args.extend(["--seed-start", str(seed_start_value)])
            extras["seed_start"] = seed_start_value

        with self.lock:
            return self._launch_locked(kind="suite", config_path=config_path, extra_args=extra_args, extras=extras)

    def start_nightly(self, payload: dict[str, Any]) -> dict[str, Any]:
        config_path = self._resolve_config_path(payload.get("config"))
        extra_args: list[str] = []
        extras: dict[str, Any] = {
            "estimated_total_evals": self._estimate_nightly_total_evals(config_path),
        }
        seed_start = payload.get("seed_start")
        if seed_start is not None and str(seed_start).strip() != "":
            seed_start_value = int(seed_start)
            extra_args.extend(["--seed-start", str(seed_start_value)])
            extras["seed_start"] = seed_start_value

        with self.lock:
            return self._launch_locked(kind="nightly", config_path=config_path, extra_args=extra_args, extras=extras)

    def stop(self) -> dict[str, Any]:
        with self.lock:
            self._refresh_process_locked()
            if self.process is None or self.current is None:
                return self._snapshot_locked(message="No active experiment to stop.")

            metadata = dict(self.current)
            pid = self.process.pid

            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
            else:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()

            try:
                self.process.wait(timeout=5)
            except Exception:
                pass

            returncode = self.process.poll()
            stopped_at = now_iso()
            current_status = self._read_live_status()
            for key in PROGRESS_KEYS:
                if current_status.get(key) is not None:
                    metadata[key] = current_status.get(key)
            metadata.update({"active": False, "stopped_by_user": True, "stopped_at": stopped_at, "returncode": returncode})
            self.last_result = metadata
            self.process = None
            self.current = None
            self._close_log_handle_locked()

            status_payload = {
                "run_id": None,
                "status": "stopped_by_user",
                "task_id": None,
                "config_id": metadata.get("config_id"),
                "runner": "dashboard_control",
                "current_tool": None,
                "last_error": "Stopped from dashboard",
                "step_count": 0,
                "max_steps": metadata.get("progress_target") or metadata.get("suite_progress_target") or 1,
                "elapsed_sec": 0,
                "updated_at": stopped_at,
                "run_kind": metadata.get("kind"),
                "suite_id": None,
                "case_id": None,
                "task_type": metadata.get("task_type"),
                "control_active": False,
                "control_pid": None,
                "control_command": " ".join(metadata.get("command", [])),
            }
            for key in PROGRESS_KEYS:
                if metadata.get(key) is not None:
                    status_payload[key] = metadata.get(key)
            self._write_live_status(status_payload)
            self._append_live_event(
                "Stopped active experiment from dashboard.",
                control_kind=metadata.get("kind"),
                control_config=metadata.get("config_name"),
            )
            return self._snapshot_locked(message="Stopped active experiment.")


class DashboardServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], handler_class, controller: RunController) -> None:
        super().__init__(server_address, handler_class)
        self.controller = controller


class DashboardHandler(SimpleHTTPRequestHandler):
    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Request body must be valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/control":
            self._send_json(self.server.controller.snapshot())
            return
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return

        try:
            if parsed.path == "/api/start-run":
                response = self.server.controller.start_single(payload)
            elif parsed.path == "/api/start-suite":
                response = self.server.controller.start_suite(payload)
            elif parsed.path == "/api/start-nightly":
                response = self.server.controller.start_nightly(payload)
            elif parsed.path == "/api/stop":
                response = self.server.controller.stop()
            else:
                self._send_json({"error": "Not found."}, status=404)
                return
        except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, status=404)
            return
        except RuntimeError as exc:
            data = self.server.controller.snapshot()
            data["error"] = str(exc)
            self._send_json(data, status=409)
            return
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=500)
            return

        self._send_json(response)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the local dashboard over HTTP.")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    os.chdir(ROOT)
    controller = RunController(ROOT)
    server = DashboardServer(("127.0.0.1", args.port), DashboardHandler, controller)
    print(f"Serving dashboard at http://127.0.0.1:{args.port}/dashboard.html")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
