from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from storage.jsonish import dump_jsonish, load_jsonish_text


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class OpenClawRuntime:
    def __init__(self, root: Path, config: dict, run_id: str) -> None:
        self.root = root
        self.config = config
        self.run_id = run_id
        self.openclaw_config = config.get("openclaw", {})
        self.sandbox_config = config.get("sandbox", {})
        self.runtime_dir = root / "runs" / "openclaw_runtime" / run_id
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.commands_dir = self.runtime_dir / "commands"
        self.commands_dir.mkdir(parents=True, exist_ok=True)
        self.temp_config_path = self.runtime_dir / "openclaw.runtime.json"
        self.agent_dir = self.runtime_dir / "agent"
        self.lifecycle_path = self.runtime_dir / "runtime_lifecycle.json"
        self.command_history_path = self.runtime_dir / "command_history.json"
        self.runtime_report_path = self.runtime_dir / "runtime_report.json"
        self.smoke_report_path = self.runtime_dir / "smoke_report.json"
        self.agent_id = self._make_agent_id()
        self.command_prefix = self._resolve_command_prefix()
        self.env = self._build_env()
        self.command_seq = 0
        self.lifecycle_events: list[dict[str, Any]] = []
        self.command_records: list[dict[str, Any]] = []
        self.smoke_result: dict[str, Any] | None = None
        self.cleanup_result: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.prepared = False
        self.cleaned_up = False
        self.created_at = _now_iso()
        self.agent_metadata: dict[str, Any] = {
            "agent_id": self.agent_id,
            "runtime_dir": str(self.runtime_dir.resolve()),
            "config_path": str(self.temp_config_path.resolve()),
            "agent_dir": str(self.agent_dir.resolve()),
            "sandbox_backend": self.sandbox_config.get("backend", "off"),
            "lifecycle_path": str(self.lifecycle_path.resolve()),
            "command_history_path": str(self.command_history_path.resolve()),
            "runtime_report_path": str(self.runtime_report_path.resolve()),
            "smoke_report_path": str(self.smoke_report_path.resolve()),
        }
        self._record_event("runtime_initialized", command_prefix=self.command_prefix)

    def _make_agent_id(self) -> str:
        prefix = str(self.openclaw_config.get("agent_id_prefix", "eval-lab")).strip() or "eval-lab"
        safe_prefix = "".join(char if char.isalnum() or char in "-_" else "-" for char in prefix).strip("-_") or "eval-lab"
        return f"{safe_prefix}-{self.run_id.lower()}"

    def _resolve_command_prefix(self) -> list[str]:
        command = self.openclaw_config.get("command", ["openclaw"])
        if isinstance(command, str):
            parts = [command]
        else:
            parts = [str(item) for item in command]

        resolved: list[str] = []
        for index, part in enumerate(parts):
            candidate = Path(part)
            if candidate.exists():
                resolved.append(str(candidate.resolve()))
            elif index > 0:
                relative = (self.root / part).resolve()
                resolved.append(str(relative) if relative.exists() else part)
            else:
                resolved.append(part)
        return resolved

    def _resolve_base_config_path(self) -> Path | None:
        explicit = self.openclaw_config.get("base_config_path")
        if explicit:
            path = (self.root / explicit).resolve() if not Path(explicit).is_absolute() else Path(explicit)
            return path if path.exists() else None

        env_path = os.environ.get("OPENCLAW_CONFIG_PATH")
        if env_path:
            path = Path(env_path)
            return path if path.exists() else None

        default_path = Path.home() / ".openclaw" / "openclaw.json"
        return default_path if default_path.exists() else None

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        extra_env = self.openclaw_config.get("extra_env", {})
        env.update({str(key): str(value) for key, value in extra_env.items()})
        return env

    def _should_run_smoke_test(self) -> bool:
        return bool(self.openclaw_config.get("smoke_test_on_prepare", True))

    def _smoke_test_required(self) -> bool:
        return bool(self.openclaw_config.get("smoke_test_required", True))

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _safe_label(self, value: str) -> str:
        cleaned = "".join(char if char.isalnum() or char in "-_" else "_" for char in value.strip())
        return cleaned.strip("_") or "command"

    def _coerce_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    def _persist_runtime_report(self) -> None:
        if not self.runtime_dir.exists():
            return
        self._write_json(self.lifecycle_path, {"events": self.lifecycle_events})
        self._write_json(self.command_history_path, {"commands": self.command_records})
        self._write_json(
            self.runtime_report_path,
            {
                "run_id": self.run_id,
                "created_at": self.created_at,
                "updated_at": _now_iso(),
                "prepared": self.prepared,
                "cleaned_up": self.cleaned_up,
                "last_error": self.last_error,
                "command_prefix": self.command_prefix,
                "agent_metadata": self.agent_metadata,
                "smoke_test": self.smoke_result,
                "cleanup": self.cleanup_result,
                "lifecycle_event_count": len(self.lifecycle_events),
                "command_count": len(self.command_records),
                "artifact_paths": self.describe_runtime(),
            },
        )

    def _record_event(self, stage: str, **fields: Any) -> dict[str, Any]:
        event = {
            "seq": len(self.lifecycle_events) + 1,
            "ts": _now_iso(),
            "stage": stage,
        }
        event.update(fields)
        self.lifecycle_events.append(event)
        self._persist_runtime_report()
        return event

    def _finalize_command_record(self, record: dict[str, Any], stdout_text: str, stderr_text: str) -> dict[str, Any]:
        command_id = str(record["command_id"])
        stdout_path = self.commands_dir / f"{command_id}.stdout.txt"
        stderr_path = self.commands_dir / f"{command_id}.stderr.txt"
        record_path = self.commands_dir / f"{command_id}.json"
        stdout_path.write_text(stdout_text, encoding="utf-8")
        stderr_path.write_text(stderr_text, encoding="utf-8")
        record["stdout_path"] = str(stdout_path.resolve())
        record["stderr_path"] = str(stderr_path.resolve())
        record["record_path"] = str(record_path.resolve())
        self.command_records.append(record)
        self._write_json(record_path, record)
        self._persist_runtime_report()
        return record

    def describe_runtime(self) -> dict[str, Any]:
        return {
            "runtime_dir": str(self.runtime_dir.resolve()),
            "commands_dir": str(self.commands_dir.resolve()),
            "config_path": str(self.temp_config_path.resolve()),
            "agent_dir": str(self.agent_dir.resolve()),
            "lifecycle_path": str(self.lifecycle_path.resolve()),
            "command_history_path": str(self.command_history_path.resolve()),
            "runtime_report_path": str(self.runtime_report_path.resolve()),
            "smoke_report_path": str(self.smoke_report_path.resolve()),
        }

    def _prepare_temp_config(self) -> None:
        base_path = self._resolve_base_config_path()
        if base_path and base_path.exists():
            self.temp_config_path.write_text(base_path.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            dump_jsonish(self.temp_config_path, {"agents": {"list": []}})
        self.env["OPENCLAW_CONFIG_PATH"] = str(self.temp_config_path.resolve())
        self._record_event(
            "runtime_config_prepared",
            config_path=str(self.temp_config_path.resolve()),
            base_config_path=str(base_path.resolve()) if base_path and base_path.exists() else None,
        )

    def _run_cli(self, args: list[str], timeout_sec: int | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        timeout = timeout_sec if timeout_sec is not None else int(self.openclaw_config.get("timeout_sec", 90))
        self.command_seq += 1
        label_parts = args[:2] if args else ["command"]
        command_id = f"cmd_{self.command_seq:03d}_{self._safe_label('_'.join(label_parts))}"
        started = time.perf_counter()
        record = {
            "command_id": command_id,
            "seq": self.command_seq,
            "started_at": _now_iso(),
            "args": args,
            "command": self.command_prefix + args,
            "cwd": str(self.root.resolve()),
            "timeout_sec": timeout,
            "check": check,
            "status": "running",
        }
        self._record_event("command_started", command_id=command_id, args=args, check=check)

        try:
            result = subprocess.run(
                self.command_prefix + args,
                cwd=self.root,
                env=self.env,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            record["finished_at"] = _now_iso()
            record["duration_sec"] = round(time.perf_counter() - started, 3)
            record["returncode"] = result.returncode
            record["status"] = "ok" if result.returncode == 0 else "nonzero_exit"
            self._finalize_command_record(record, result.stdout or "", result.stderr or "")
            if check and result.returncode != 0:
                stderr = result.stderr.strip() or result.stdout.strip() or f"exit={result.returncode}"
                self._record_event(
                    "command_failed",
                    command_id=command_id,
                    returncode=result.returncode,
                    stderr=stderr,
                )
                raise RuntimeError(f"OpenClaw command failed: {' '.join(args)} | {stderr}")

            self._record_event(
                "command_finished",
                command_id=command_id,
                returncode=result.returncode,
            )
            return result
        except subprocess.TimeoutExpired as exc:
            record["finished_at"] = _now_iso()
            record["duration_sec"] = round(time.perf_counter() - started, 3)
            record["status"] = "timeout"
            record["returncode"] = None
            record["error"] = f"timeout after {timeout}s"
            self._finalize_command_record(record, self._coerce_text(exc.stdout), self._coerce_text(exc.stderr))
            self._record_event("command_timeout", command_id=command_id, timeout_sec=timeout)
            raise RuntimeError(f"OpenClaw command timed out after {timeout}s: {' '.join(args)}") from exc
        except FileNotFoundError as exc:
            record["finished_at"] = _now_iso()
            record["duration_sec"] = round(time.perf_counter() - started, 3)
            record["status"] = "missing_command"
            record["returncode"] = None
            record["error"] = str(exc)
            self._finalize_command_record(record, "", "")
            self._record_event("command_missing", command_id=command_id, error=str(exc))
            raise RuntimeError(
                f"OpenClaw CLI not found. Expected command prefix: {' '.join(self.command_prefix)}"
            ) from exc
        except Exception as exc:
            record["finished_at"] = _now_iso()
            record["duration_sec"] = round(time.perf_counter() - started, 3)
            record["status"] = "exception"
            record["returncode"] = None
            record["error"] = str(exc)
            self._finalize_command_record(record, "", "")
            self._record_event("command_exception", command_id=command_id, error=str(exc))
            raise

    def _load_runtime_config(self) -> dict:
        raw = self.temp_config_path.read_text(encoding="utf-8")
        return load_jsonish_text(raw)

    def _save_runtime_config(self, payload: dict) -> None:
        dump_jsonish(self.temp_config_path, payload)

    def _ensure_agent_entry(self, workspace_root: Path) -> dict:
        model = self.openclaw_config.get("model")
        add_args = [
            "agents",
            "add",
            self.agent_id,
            "--workspace",
            str(workspace_root.resolve()),
            "--agent-dir",
            str(self.agent_dir.resolve()),
            "--non-interactive",
            "--json",
        ]
        if model:
            add_args.extend(["--model", str(model)])
        self._run_cli(add_args)

        config_payload = self._load_runtime_config()
        agents = config_payload.setdefault("agents", {}).setdefault("list", [])
        entry = next((item for item in agents if item.get("id") == self.agent_id), None)
        if entry is None:
            entry = {
                "id": self.agent_id,
                "workspace": str(workspace_root.resolve()),
                "agentDir": str(self.agent_dir.resolve()),
            }
            if model:
                entry["model"] = model
            agents.append(entry)

        entry["workspace"] = str(workspace_root.resolve())
        entry["agentDir"] = str(self.agent_dir.resolve())
        if model:
            entry["model"] = model
        if self.sandbox_config:
            entry["sandbox"] = self._build_sandbox_payload()

        self._save_runtime_config(config_payload)
        return entry

    def _build_sandbox_payload(self) -> dict:
        if not self.sandbox_config:
            return {}

        docker_payload = {
            "image": self.sandbox_config.get("docker_image", "openclaw-sandbox:bookworm-slim"),
            "network": self.sandbox_config.get("docker_network", "none"),
            "containerPrefix": self.sandbox_config.get("container_prefix", "openclaw-eval-lab"),
        }
        if self.sandbox_config.get("memory"):
            docker_payload["memory"] = self.sandbox_config["memory"]
        if self.sandbox_config.get("cpus"):
            docker_payload["cpus"] = self.sandbox_config["cpus"]

        payload = {
            "backend": self.sandbox_config.get("backend", "docker"),
            "mode": self.sandbox_config.get("mode", "all"),
            "scope": self.sandbox_config.get("scope", "agent"),
            "workspaceAccess": self.sandbox_config.get("workspace_access", "read-write"),
            "docker": docker_payload,
        }

        if self.sandbox_config.get("prune_after_run") is not None:
            payload["pruneAfterRun"] = bool(self.sandbox_config["prune_after_run"])
        return payload

    def smoke_test(self) -> dict[str, Any]:
        checks = [
            {
                "name": "config_file",
                "args": ["config", "file"],
                "validator": lambda cp: bool(cp.stdout.strip()),
            },
            {
                "name": "agents_list",
                "args": ["agents", "list", "--json"],
                "validator": lambda cp: cp.returncode == 0,
            },
        ]
        result: dict[str, Any] = {
            "checked_at": _now_iso(),
            "required": self._smoke_test_required(),
            "ok": False,
            "selected_check": None,
            "checks": [],
        }
        self._record_event("smoke_test_started", required=result["required"])

        for check in checks:
            completed = self._run_cli(check["args"], check=False)
            stdout = completed.stdout.strip()
            stderr = completed.stderr.strip()
            entry = {
                "name": check["name"],
                "args": check["args"],
                "returncode": completed.returncode,
                "stdout_preview": stdout[:200],
                "stderr_preview": stderr[:200],
                "passed": False,
            }
            if check["name"] == "agents_list" and stdout:
                try:
                    payload = load_jsonish_text(stdout)
                    entry["agent_count"] = len(payload.get("agents", [])) if isinstance(payload, dict) else None
                except Exception:
                    entry["agent_count"] = None
            entry["passed"] = bool(check["validator"](completed))
            result["checks"].append(entry)
            if entry["passed"]:
                result["ok"] = True
                result["selected_check"] = check["name"]
                if check["name"] == "config_file":
                    result["reported_config_path"] = stdout
                break

        self.smoke_result = result
        self._write_json(self.smoke_report_path, result)
        if result["ok"]:
            self._record_event("smoke_test_passed", selected_check=result["selected_check"])
        else:
            self._record_event("smoke_test_failed")
            if result["required"]:
                raise RuntimeError("OpenClaw smoke test failed during runtime prepare.")
        self._persist_runtime_report()
        return result

    def prepare(self, workspace_root: Path) -> dict:
        self._record_event("prepare_started", workspace_root=str(workspace_root.resolve()))
        try:
            self._prepare_temp_config()
            if self._should_run_smoke_test():
                self.smoke_test()

            agent_entry = self._ensure_agent_entry(workspace_root)
            sandbox_info: dict[str, Any] = {}

            if self.sandbox_config.get("backend") == "docker":
                if self.sandbox_config.get("recreate_on_prepare", True):
                    self._run_cli(["sandbox", "recreate", "--agent", self.agent_id, "--force"], check=False)
                explain = self._run_cli(["sandbox", "explain", "--agent", self.agent_id, "--json"], check=False)
                if explain.stdout.strip():
                    try:
                        sandbox_info = load_jsonish_text(explain.stdout.strip())
                    except Exception:
                        sandbox_info = {"raw": explain.stdout.strip()}

            self.prepared = True
            self.agent_metadata.update(
                {
                    "workspace_root": str(workspace_root.resolve()),
                    "agent_entry": agent_entry,
                    "sandbox_info": sandbox_info,
                    "smoke_test": self.smoke_result,
                    "runtime_artifacts": self.describe_runtime(),
                }
            )
            self._record_event("prepare_finished", workspace_root=str(workspace_root.resolve()))
            return dict(self.agent_metadata)
        except Exception as exc:
            self.last_error = str(exc)
            self._record_event("prepare_failed", error=self.last_error)
            raise

    def run_agent(self, prompt: str) -> subprocess.CompletedProcess[str]:
        args = [
            "agent",
            "--agent",
            self.agent_id,
            "-m",
            prompt,
            "--json",
        ]
        if self.openclaw_config.get("local", True):
            args.append("--local")
        if self.openclaw_config.get("thinking"):
            args.extend(["--thinking", str(self.openclaw_config["thinking"])])
        if self.openclaw_config.get("timeout_sec"):
            args.extend(["--timeout", str(int(self.openclaw_config["timeout_sec"]))])

        self._record_event("run_started", agent_id=self.agent_id)
        try:
            result = self._run_cli(args, check=False)
            self._record_event("run_finished", returncode=result.returncode)
            return result
        except Exception as exc:
            self.last_error = str(exc)
            self._record_event("run_failed", error=self.last_error)
            raise

    def cleanup(self) -> dict[str, Any]:
        if self.cleaned_up and self.cleanup_result is not None:
            return dict(self.cleanup_result)

        summary: dict[str, Any] = {
            "started_at": _now_iso(),
            "cleanup_agent": bool(self.openclaw_config.get("cleanup_agent", True)),
            "cleanup_runtime_dir": bool(self.openclaw_config.get("cleanup_runtime_dir", False)),
            "agent_delete_returncode": None,
            "runtime_dir_removed": False,
            "success": True,
            "errors": [],
            "warnings": [],
        }
        self._record_event(
            "cleanup_started",
            cleanup_agent=summary["cleanup_agent"],
            cleanup_runtime_dir=summary["cleanup_runtime_dir"],
        )

        try:
            if summary["cleanup_agent"]:
                try:
                    result = self._run_cli(["agents", "delete", self.agent_id, "--force", "--json"], check=False)
                    summary["agent_delete_returncode"] = result.returncode
                    if result.returncode != 0:
                        summary["warnings"].append(f"agent delete exited with code {result.returncode}")
                except Exception as exc:
                    summary["success"] = False
                    summary["errors"].append(str(exc))
            else:
                summary["warnings"].append("agent cleanup disabled")

            self.cleaned_up = True
            summary["finished_at"] = _now_iso()
            self.cleanup_result = summary
            if summary["success"]:
                self._record_event("cleanup_finished", warnings=summary["warnings"])
            else:
                self.last_error = self.last_error or "; ".join(summary["errors"])
                self._record_event("cleanup_failed", errors=summary["errors"], warnings=summary["warnings"])
            self._persist_runtime_report()

            if summary["cleanup_runtime_dir"] and self.runtime_dir.exists():
                shutil.rmtree(self.runtime_dir, ignore_errors=True)
                summary["runtime_dir_removed"] = True
            return dict(summary)
        except Exception as exc:
            summary["success"] = False
            summary["finished_at"] = _now_iso()
            summary["errors"].append(str(exc))
            self.cleaned_up = True
            self.cleanup_result = summary
            self.last_error = self.last_error or str(exc)
            self._record_event("cleanup_exception", error=str(exc))
            self._persist_runtime_report()
            return dict(summary)
