from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runners.search_helpers import pick_best_match, search_file
from storage.jsonish import dump_jsonish, load_jsonish


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _load_config() -> tuple[Path, dict]:
    config_env = os.environ.get("OPENCLAW_CONFIG_PATH")
    path = Path(config_env) if config_env else ROOT / "configs" / "openclaw" / "base_openclaw.json"
    return path, load_jsonish(path)


def _save_config(path: Path, payload: dict) -> None:
    dump_jsonish(path, payload)


def _extract_flag(name: str, default: str | None = None) -> str | None:
    if name not in sys.argv:
        return default
    index = sys.argv.index(name)
    if index + 1 >= len(sys.argv):
        return default
    return sys.argv[index + 1]


def _extract_agent(config: dict, agent_id: str) -> dict:
    agents = config.setdefault("agents", {}).setdefault("list", [])
    for agent in agents:
        if agent.get("id") == agent_id:
            return agent
    raise SystemExit(f"Agent not found: {agent_id}")


def handle_agents() -> None:
    config_path, config = _load_config()
    agents = config.setdefault("agents", {}).setdefault("list", [])
    subcommand = sys.argv[2]
    if subcommand == "list":
        _print_json({"agents": agents})
        return
    if subcommand == "add":
        agent_id = sys.argv[3]
        workspace = _extract_flag("--workspace")
        agent_dir = _extract_flag("--agent-dir")
        model = _extract_flag("--model")
        agent = {
            "id": agent_id,
            "workspace": workspace,
            "agentDir": agent_dir,
        }
        if model:
            agent["model"] = model
        agents = [item for item in agents if item.get("id") != agent_id]
        agents.append(agent)
        config["agents"]["list"] = agents
        _save_config(config_path, config)
        _print_json({"ok": True, "agent": agent})
        return
    if subcommand == "delete":
        agent_id = sys.argv[3]
        config["agents"]["list"] = [item for item in agents if item.get("id") != agent_id]
        _save_config(config_path, config)
        _print_json({"ok": True, "deleted": agent_id})
        return
    raise SystemExit(f"Unsupported agents subcommand: {subcommand}")


def handle_sandbox() -> None:
    _, config = _load_config()
    subcommand = sys.argv[2]
    agent_id = _extract_flag("--agent")
    agent = _extract_agent(config, agent_id)
    sandbox = agent.get("sandbox", {})
    if subcommand == "explain":
        _print_json({"agent": agent_id, "sandbox": sandbox, "workspace": agent.get("workspace")})
        return
    if subcommand == "recreate":
        _print_json({"ok": True, "agent": agent_id, "sandbox": sandbox})
        return
    raise SystemExit(f"Unsupported sandbox subcommand: {subcommand}")


def _parse_prompt(prompt: str) -> tuple[str, str]:
    project_name = ""
    doc_slug = "deployment"
    if "專案 " in prompt and " 的 " in prompt:
        project_name = prompt.split("專案 ", 1)[1].split(" 的 ", 1)[0]
    if "部署" in prompt:
        doc_slug = "deployment"
    elif "交接" in prompt:
        doc_slug = "handoff"
    elif "維運" in prompt or "運維" in prompt:
        doc_slug = "operations"
    return project_name, doc_slug


def _find_expected_path(workspace_root: Path) -> str:
    for file_path in workspace_root.rglob("*"):
        if not file_path.is_file():
            continue
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        if "Canonical: true" in text:
            return str(file_path.resolve())
    return ""


def handle_agent() -> None:
    _, config = _load_config()
    agent_id = _extract_flag("--agent")
    prompt = _extract_flag("-m", "")
    agent = _extract_agent(config, agent_id)
    workspace_root = Path(agent["workspace"])

    project_name, doc_slug = _parse_prompt(prompt or "")
    broad_query = doc_slug
    focused_query = f"{project_name} {doc_slug}".strip()
    task_meta = {
        "expected_output": _find_expected_path(workspace_root),
        "project_name": project_name,
        "doc_slug": doc_slug,
    }

    broad_matches = search_file(workspace_root, broad_query)
    chosen = pick_best_match(broad_matches, task_meta)
    trace = [
        {"type": "tool_call", "name": "search_file", "args": {"query": broad_query}, "text": f"search_file query={broad_query!r}"},
        {"type": "tool_result", "name": "search_file", "text": f"search_file 命中 {len(broad_matches)} 筆候選"},
    ]

    if not chosen or Path(chosen["path"]).resolve() != Path(task_meta["expected_output"]).resolve():
        focused_matches = search_file(workspace_root, focused_query)
        chosen = pick_best_match(focused_matches, task_meta)
        trace.extend(
            [
                {"type": "assistant", "text": "第一次搜尋結果太廣，縮小範圍後重試。"},
                {"type": "tool_call", "name": "search_file", "args": {"query": focused_query}, "text": f"search_file query={focused_query!r}"},
                {"type": "tool_result", "name": "search_file", "text": f"search_file 命中 {len(focused_matches)} 筆候選"},
            ]
        )

    final_output = str(Path(chosen["path"]).resolve()) if chosen else ""
    if chosen:
        mutated_file = Path(final_output)
        mutated_file.write_text(mutated_file.read_text(encoding="utf-8") + "\nTouched by OpenClaw stub.\n", encoding="utf-8")
        scratch_file = workspace_root / "scratch" / "openclaw_stub_note.txt"
        scratch_file.parent.mkdir(parents=True, exist_ok=True)
        scratch_file.write_text("ephemeral artifact from OpenClaw stub", encoding="utf-8")
        trace.extend(
            [
                {"type": "tool_call", "name": "open_file_location", "args": {"path": final_output}, "text": f"open_file_location path={final_output!r}"},
                {"type": "tool_result", "name": "open_file_location", "text": final_output},
                {"type": "assistant", "text": final_output},
            ]
        )

    _print_json(
        {
            "ok": True,
            "output_text": final_output,
            "trace": trace,
            "usage": {
                "total_tokens": max(32, len(prompt or "") + len(final_output)),
                "step_count": len([item for item in trace if item["type"] == "tool_call"]),
            },
        }
    )


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: openclaw_cli_stub.py <command> ...")

    command = sys.argv[1]
    if command == "agents":
        handle_agents()
        return
    if command == "sandbox":
        handle_sandbox()
        return
    if command == "agent":
        handle_agent()
        return
    if command == "config" and len(sys.argv) > 2 and sys.argv[2] == "file":
        config_path, _ = _load_config()
        print(str(config_path))
        return
    raise SystemExit(f"Unsupported command: {' '.join(sys.argv[1:])}")


if __name__ == "__main__":
    main()
