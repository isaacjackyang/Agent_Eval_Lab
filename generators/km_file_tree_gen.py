from __future__ import annotations

import random
from pathlib import Path


DOC_PROFILES = [
    {
        "doc_type": "部署說明",
        "slug": "deployment",
        "target_names": ["deployment_note.md", "deploy_runbook.md"],
        "noise_names": ["deployment_draft.md", "deployment_archive.md"],
    },
    {
        "doc_type": "交接清單",
        "slug": "handoff",
        "target_names": ["handoff_checklist.md", "handoff_packet.md"],
        "noise_names": ["handoff_notes.md", "handoff_archive.md"],
    },
    {
        "doc_type": "維運手冊",
        "slug": "operations",
        "target_names": ["operations_runbook.md", "ops_manual.md"],
        "noise_names": ["operations_old.md", "ops_draft.md"],
    },
]

PROJECT_PREFIXES = ["Atlas", "Nova", "Cinder", "Harbor", "Quartz", "Falcon"]
FOLDER_L1 = ["knowledge", "ops", "docs", "delivery"]
FOLDER_L2 = ["release", "handover", "field-notes", "archives"]
FOLDER_L3 = ["v1", "v2", "current", "verified"]


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_noise_files(rng: random.Random, workspace_root: Path, project_name: str, profile: dict) -> list[str]:
    noise_paths: list[str] = []
    alt_project = f"{rng.choice(PROJECT_PREFIXES)}-{rng.randint(10, 99)}"
    same_project_base = workspace_root / "projects" / project_name
    other_project_base = workspace_root / "projects" / alt_project

    candidates = [
        same_project_base / "docs" / "archives" / "v1" / profile["noise_names"][0],
        same_project_base / "ops" / "handover" / "current" / profile["noise_names"][1],
        other_project_base / "knowledge" / "release" / "verified" / profile["target_names"][0],
        other_project_base / "docs" / "field-notes" / "v2" / "summary.md",
        workspace_root / "shared" / "templates" / f"{profile['slug']}_template.md",
        workspace_root / "scratch" / project_name / "notes.txt",
    ]

    for index, path in enumerate(candidates, start=1):
        if path.suffix == ".md":
            content = (
                f"# Noise File {index}\n"
                f"Project: {project_name if index < 3 else alt_project}\n"
                f"Topic: {profile['doc_type']}\n"
                "Canonical: false\n"
            )
        else:
            content = f"scratch note {index} for {project_name}"
        _write_text(path, content)
        noise_paths.append(str(path.resolve()))

    return noise_paths


def generate_task(run_id: str, workspace_root: Path, seed: int | None = None) -> dict:
    rng = random.Random(seed if seed is not None else run_id)
    workspace_root.mkdir(parents=True, exist_ok=True)

    profile = rng.choice(DOC_PROFILES)
    project_name = f"{rng.choice(PROJECT_PREFIXES)}-{rng.randint(10, 99)}"

    target_dir = (
        workspace_root
        / "projects"
        / project_name
        / rng.choice(FOLDER_L1)
        / rng.choice(FOLDER_L2)
        / rng.choice(FOLDER_L3)
    )
    target_file = target_dir / rng.choice(profile["target_names"])

    target_content = (
        f"# {profile['doc_type']}\n"
        f"Project: {project_name}\n"
        f"Doc-Slug: {profile['slug']}\n"
        "Canonical: true\n"
        "Owner: local-eval-lab\n"
    )
    _write_text(target_file, target_content)

    noise_files = _build_noise_files(rng, workspace_root, project_name, profile)

    return {
        "id": "km_dynamic_retrieval_01",
        "category": "file_retrieval",
        "prompt": f"找出專案 {project_name} 的 {profile['doc_type']}，並只顯示檔案位置。",
        "project_name": project_name,
        "doc_type": profile["doc_type"],
        "doc_slug": profile["slug"],
        "workspace_root": str(workspace_root.resolve()),
        "expected_output": str(target_file.resolve()),
        "target_relpath": str(target_file.relative_to(workspace_root)).replace("\\", "/"),
        "allowed_tools": ["search_file", "open_file_location"],
        "search_hints": {
            "broad": profile["slug"],
            "focused": f"{project_name} {profile['slug']}",
        },
        "metadata": {
            "run_id": run_id,
            "seed": seed if seed is not None else run_id,
            "noise_file_count": len(noise_files),
            "noise_files": noise_files,
        },
    }
