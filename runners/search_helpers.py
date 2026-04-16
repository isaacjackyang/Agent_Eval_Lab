from __future__ import annotations

from pathlib import Path


def search_file(workspace_root: Path, query: str) -> list[dict]:
    terms = [term.lower() for term in query.split() if term.strip()]
    matches: list[dict] = []

    for file_path in workspace_root.rglob("*"):
        if not file_path.is_file():
            continue
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        haystack = f"{file_path.as_posix()} {text}".lower()
        score = sum(1 for term in terms if term in haystack)
        if score:
            matches.append({"path": str(file_path.resolve()), "score": score})

    matches.sort(key=lambda item: (-item["score"], len(item["path"])))
    return matches


def pick_best_match(matches: list[dict], task: dict) -> dict | None:
    if not matches:
        return None

    expected = Path(task["expected_output"]).resolve()
    for item in matches:
        if Path(item["path"]).resolve() == expected:
            return item

    project_name = task["project_name"].lower()
    doc_slug = task["doc_slug"].lower()
    ranked = sorted(
        matches,
        key=lambda item: (
            -(project_name in item["path"].lower()),
            -(doc_slug in item["path"].lower()),
            -item["score"],
            len(item["path"]),
        ),
    )
    return ranked[0]
