from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_snapshot(workspace_root: Path, snapshot_dir: Path) -> dict:
    workspace_root.mkdir(parents=True, exist_ok=True)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    backups_dir = snapshot_dir / "files"
    backups_dir.mkdir(parents=True, exist_ok=True)

    files = []
    file_count = 0
    for file_path in sorted(item for item in workspace_root.rglob("*") if item.is_file()):
        file_count += 1
        relpath = str(file_path.relative_to(workspace_root)).replace("\\", "/")
        backup_path = backups_dir / relpath
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, backup_path)
        files.append(
            {
                "path": relpath,
                "sha256": _sha256(file_path),
                "backup_path": str(backup_path.resolve()),
            }
        )

    manifest = {
        "workspace_root": str(workspace_root.resolve()),
        "snapshot_dir": str(snapshot_dir.resolve()),
        "file_count": file_count,
        "files": files,
    }
    manifest_path = snapshot_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "manifest_path": str(manifest_path.resolve()),
        "snapshot_dir": str(snapshot_dir.resolve()),
        "file_count": file_count,
    }
