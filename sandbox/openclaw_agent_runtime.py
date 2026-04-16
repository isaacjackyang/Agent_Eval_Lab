from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from storage.jsonish import dump_jsonish, load_jsonish, load_jsonish_text


class OpenClawRuntime:
    def __init__(self, root: Path, config: dict, run_id: str) -> None:
        self.root = root
        self.config = config
        self.run_id = run_id
        self.openclaw_config = config.get("openclaw", {})
        self.sandbox_config = config.get("sandbox", {})
        self.runtime_dir = root / "runs" / "openclaw_runtime" / run_id
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.temp_config_path = self.runtime_dir / "openclaw.runtime.json"
        self.agent_dir = self.runtime_dir / "agent"
        self.agent_id = self._make_agent_id()
        self.command_prefix = self._resolve_command_prefix()
        self.env = self._build_env()
        self.agent_metadata: dict = {
            "agent_id": self.agent_id,
            "runtime_dir": str(self.runtime_dir.resolve()),
            "config_path": str(self.temp_config_path.resolve()),
            "agent_dir": str(self.agent_dir.resolve()),
            "sandbox_backend": self.sandbox_config.get("backend", "off"),
        }

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

    def _build_env(self) -> dict:
        env = os.environ.copy()
        extra_env = self.openclaw_config.get("extra_env", {})
        env.update({str(key): str(value) for key, value in extra_env.items()})
        return env

    def _prepare_temp_config(self) -> None:
        base_path = self._resolve_base_config_path()
        if base_path and base_path.exists():
            self.temp_config_path.write_text(base_path.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            dump_jsonish(self.temp_config_path, {"agents": {"list": []}})
        self.env["OPENCLAW_CONFIG_PATH"] = str(self.temp_config_path.resolve())

    def _run_cli(self, args: list[str], timeout_sec: int | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        timeout = timeout_sec if timeout_sec is not None else int(self.openclaw_config.get("timeout_sec", 90))
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
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"OpenClaw CLI not found. Expected command prefix: {' '.join(self.command_prefix)}"
            ) from exc

        if check and result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip() or f"exit={result.returncode}"
            raise RuntimeError(f"OpenClaw command failed: {' '.join(args)} | {stderr}")
        return result

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

    def prepare(self, workspace_root: Path) -> dict:
        self._prepare_temp_config()
        agent_entry = self._ensure_agent_entry(workspace_root)
        sandbox_info = {}

        if self.sandbox_config.get("backend") == "docker":
            if self.sandbox_config.get("recreate_on_prepare", True):
                self._run_cli(["sandbox", "recreate", "--agent", self.agent_id, "--force"], check=False)
            explain = self._run_cli(["sandbox", "explain", "--agent", self.agent_id, "--json"], check=False)
            if explain.stdout.strip():
                try:
                    sandbox_info = load_jsonish_text(explain.stdout.strip())
                except Exception:
                    sandbox_info = {"raw": explain.stdout.strip()}

        self.agent_metadata.update(
            {
                "workspace_root": str(workspace_root.resolve()),
                "agent_entry": agent_entry,
                "sandbox_info": sandbox_info,
            }
        )
        return self.agent_metadata

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
        return self._run_cli(args, check=False)

    def cleanup(self) -> None:
        if not self.openclaw_config.get("cleanup_agent", True):
            return
        self._run_cli(["agents", "delete", self.agent_id, "--force", "--json"], check=False)
        if self.openclaw_config.get("cleanup_runtime_dir", False) and self.runtime_dir.exists():
            shutil.rmtree(self.runtime_dir, ignore_errors=True)
