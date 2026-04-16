from __future__ import annotations

import json
import shutil
from pathlib import Path


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _remove_empty_dirs(root: Path) -> None:
    for directory in sorted((item for item in root.rglob("*") if item.is_dir()), key=lambda item: len(item.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            continue


def restore_workspace(workspace_root: Path, manifest_path: Path, cleanup_after_restore: bool = False) -> dict:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshot_files = {item["path"]: item for item in payload["files"]}
    current_files = {
        str(file_path.relative_to(workspace_root)).replace("\\", "/"): file_path
        for file_path in workspace_root.rglob("*")
        if file_path.is_file()
    }

    removed_new_files: list[str] = []
    restored_missing_files: list[str] = []
    restored_modified_files: list[str] = []

    for relpath, file_path in current_files.items():
        if relpath not in snapshot_files:
            file_path.unlink(missing_ok=True)
            removed_new_files.append(relpath)

    for relpath, file_info in snapshot_files.items():
        target = workspace_root / Path(relpath)
        backup = Path(file_info["backup_path"])
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, target)
            restored_missing_files.append(relpath)
            continue
        if _sha256(target) != file_info["sha256"]:
            shutil.copy2(backup, target)
            restored_modified_files.append(relpath)

    _remove_empty_dirs(workspace_root)

    if cleanup_after_restore and workspace_root.exists():
        shutil.rmtree(workspace_root, ignore_errors=True)

    success = True
    remaining_files = list(workspace_root.rglob("*")) if workspace_root.exists() else []
    if cleanup_after_restore:
        success = not workspace_root.exists()
    else:
        success = all((workspace_root / Path(relpath)).exists() for relpath in snapshot_files)

    return {
        "success": success,
        "manifest_path": str(manifest_path.resolve()),
        "restored_missing_files": restored_missing_files,
        "restored_modified_files": restored_modified_files,
        "removed_new_files": removed_new_files,
        "cleanup_after_restore": cleanup_after_restore,
        "workspace_exists_after_restore": workspace_root.exists(),
        "remaining_entry_count": len(remaining_files),
    }
