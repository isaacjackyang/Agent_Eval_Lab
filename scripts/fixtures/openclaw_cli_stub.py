from __future__ import annotations

import itertools
import json
import os
import re
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
    lowered = prompt.lower()
    project_match = re.search(r"project\s+([A-Za-z]+-\d+)", prompt)
    if project_match:
        project_name = project_match.group(1)
    if "handoff" in lowered:
        doc_slug = "handoff"
    elif "operations" in lowered:
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


def _is_math_prompt(prompt: str) -> bool:
    lowered = prompt.lower()
    return "math and reasoning benchmark" in lowered or '{"type":"final","answer"' in lowered


def _solve_math_prompt(prompt: str) -> str:
    arithmetic_match = re.search(r"Compute the integer value of ([0-9+\-*/ ()]+)\.", prompt)
    if arithmetic_match:
        expression = arithmetic_match.group(1)
        return str(eval(expression, {"__builtins__": {}}, {}))

    warehouse_match = re.search(
        r"starts with (\d+) crates and each crate holds (\d+) boxes.*ships (\d+) boxes, receives (\d+) new boxes, and then discards (\d+) damaged boxes",
        prompt,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if warehouse_match:
        crates, per_crate, shipped, received, discarded = (int(item) for item in warehouse_match.groups())
        return str(crates * per_crate - shipped + received - discarded)

    sequence_match = re.search(r"The sequence is ([0-9,\s]+)\.", prompt)
    if sequence_match:
        values = [int(part.strip()) for part in sequence_match.group(1).split(",") if part.strip()]
        gaps = [right - left for left, right in zip(values, values[1:])]
        next_gap = gaps[-1] + (gaps[-1] - gaps[-2])
        return str(values[-1] + next_gap)

    if "finished a race from first to last" in prompt:
        clue_lines = []
        for line in prompt.splitlines():
            line = line.strip()
            if re.match(r"^\d+\.\s", line):
                clue_lines.append(re.sub(r"^\d+\.\s*", "", line))
        names = sorted(set(re.findall(r"\b[A-Z][a-z]+\b", "\n".join(clue_lines))))
        candidates = list(itertools.permutations(names))
        filtered = candidates
        for clue in clue_lines:
            filtered = [order for order in filtered if _clue_holds(clue, order)]
        if len(filtered) == 1:
            return " > ".join(filtered[0])

    return "0"


def _clue_holds(clue: str, order: tuple[str, ...]) -> bool:
    first_match = re.fullmatch(r"([A-Z][a-z]+) finished first\.", clue)
    if first_match:
        return order[0] == first_match.group(1)

    last_match = re.fullmatch(r"([A-Z][a-z]+) finished last\.", clue)
    if last_match:
        return order[-1] == last_match.group(1)

    before_match = re.fullmatch(r"([A-Z][a-z]+) finished before ([A-Z][a-z]+)\.", clue)
    if before_match:
        left, right = before_match.groups()
        return order.index(left) < order.index(right)

    immediate_match = re.fullmatch(r"([A-Z][a-z]+) finished immediately before ([A-Z][a-z]+)\.", clue)
    if immediate_match:
        left, right = immediate_match.groups()
        return order.index(left) + 1 == order.index(right)

    between_match = re.fullmatch(r"([A-Z][a-z]+) finished somewhere between ([A-Z][a-z]+) and ([A-Z][a-z]+)\.", clue)
    if between_match:
        middle, left, right = between_match.groups()
        return order.index(left) < order.index(middle) < order.index(right)

    apart_match = re.fullmatch(r"([A-Z][a-z]+) did not finish next to ([A-Z][a-z]+)\.", clue)
    if apart_match:
        left, right = apart_match.groups()
        return abs(order.index(left) - order.index(right)) > 1

    return True


def handle_agent() -> None:
    _, config = _load_config()
    agent_id = _extract_flag("--agent")
    prompt = _extract_flag("-m", "") or ""
    agent = _extract_agent(config, agent_id)
    workspace_root = Path(agent["workspace"])

    if _is_math_prompt(prompt):
        answer = _solve_math_prompt(prompt)
        _print_json(
            {
                "ok": True,
                "answer": answer,
                "trace": [
                    {"type": "assistant", "text": "Solving math benchmark directly."},
                    {"type": "assistant", "text": answer},
                ],
                "usage": {"total_tokens": max(24, len(prompt) // 2), "step_count": 1},
            }
        )
        return

    project_name, doc_slug = _parse_prompt(prompt)
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
        {"type": "tool_result", "name": "search_file", "text": f"search_file returned {len(broad_matches)} candidates"},
    ]

    if not chosen or Path(chosen["path"]).resolve() != Path(task_meta["expected_output"]).resolve():
        focused_matches = search_file(workspace_root, focused_query)
        chosen = pick_best_match(focused_matches, task_meta)
        trace.extend(
            [
                {"type": "assistant", "text": "Broad search was ambiguous. Retrying with the focused query."},
                {"type": "tool_call", "name": "search_file", "args": {"query": focused_query}, "text": f"search_file query={focused_query!r}"},
                {"type": "tool_result", "name": "search_file", "text": f"search_file returned {len(focused_matches)} focused candidates"},
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
                "total_tokens": max(32, len(prompt) + len(final_output)),
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
