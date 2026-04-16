from __future__ import annotations

from runners.openclaw_cli_runner import OpenClawCliRunner
from runners.session_runner import SessionRunner


def build_runner(config: dict):
    runner_name = config.get("runner", "session_mock")
    if runner_name == "openclaw_cli":
        return OpenClawCliRunner(max_steps=config["max_steps"], runner_config=config)
    if runner_name == "session_mock":
        return SessionRunner(max_steps=config["max_steps"], runner_config=config)
    raise ValueError(f"Unsupported runner: {runner_name}")
