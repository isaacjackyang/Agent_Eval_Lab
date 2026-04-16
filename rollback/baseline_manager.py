from __future__ import annotations

import json
from pathlib import Path


DEFAULT_BASELINE = {
    "config_id": None,
    "selected_at": None,
    "fitness": 0.0,
    "suite_score_c": 0.0,
    "suite_score_b": 0.0,
    "suite_score_a": 1.0,
    "stability_score": 1.0,
    "rollback_safety_score": 1.0,
    "regression_pass_rate": 0.0,
    "honesty_score": 1.0,
    "notes": "Missing baseline file.",
    "run_id": None,
    "source_config_path": None,
    "config_archive_path": None,
}


def load_baseline(path: Path) -> dict:
    payload = dict(DEFAULT_BASELINE)
    if not path.exists():
        return payload

    payload.update(json.loads(path.read_text(encoding="utf-8")))
    return payload


def assess_candidate(current: dict, candidate: dict, gate: dict) -> dict:
    reasons: list[str] = []

    candidate_fitness = float(candidate.get("fitness", 0.0))
    current_fitness = float(current.get("fitness", 0.0))
    candidate_stability = float(candidate.get("stability_score", 0.0))
    current_stability = float(current.get("stability_score", 1.0))
    regression_pass_rate = float(candidate.get("regression_pass_rate", 0.0))
    honesty_score = float(candidate.get("honesty_score", 0.0))

    if candidate_fitness < float(gate.get("min_candidate_fitness", 0.0)):
        reasons.append("candidate_fitness_below_threshold")

    if candidate_fitness < current_fitness + float(gate.get("min_fitness_gain", 0.0)):
        reasons.append("no_fitness_gain")

    if candidate_stability < current_stability - float(gate.get("max_stability_drop", 1.0)):
        reasons.append("stability_drop_exceeded")

    if regression_pass_rate < float(gate.get("min_regression_pass_rate", 0.0)):
        reasons.append("regression_pass_rate_below_threshold")

    if honesty_score < float(gate.get("min_honesty_score", 0.0)):
        reasons.append("honesty_below_threshold")

    promoted = not reasons
    rollback_required = bool(
        reasons and candidate_fitness < current_fitness - float(gate.get("rollback_on_fitness_drop", 0.0))
    )

    return {
        "promoted": promoted,
        "rollback_required": rollback_required,
        "reasons": reasons,
        "before_config_id": current.get("config_id"),
        "after_config_id": candidate.get("config_id") if promoted else current.get("config_id"),
        "before_fitness": current_fitness,
        "after_fitness": candidate_fitness if promoted else current_fitness,
    }


def _archive_config_snapshot(path: Path, candidate: dict) -> str | None:
    config_body = candidate.get("config_body")
    if not config_body:
        return None

    archive_dir = path.parent / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = (candidate.get("selected_at") or "unknown").replace(":", "").replace("-", "")
    archive_path = archive_dir / f"{stamp}_{candidate.get('config_id', 'baseline')}.json"
    archive_path.write_text(json.dumps(config_body, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(archive_path.resolve())


def write_baseline(path: Path, candidate: dict) -> dict:
    payload = dict(DEFAULT_BASELINE)
    payload.update(
        {
            "config_id": candidate.get("config_id"),
            "selected_at": candidate.get("selected_at"),
            "fitness": candidate.get("fitness", 0.0),
            "suite_score_c": candidate.get("suite_score_c", 0.0),
            "suite_score_b": candidate.get("suite_score_b", 0.0),
            "suite_score_a": candidate.get("suite_score_a", 1.0),
            "stability_score": candidate.get("stability_score", 1.0),
            "rollback_safety_score": candidate.get("rollback_safety_score", 1.0),
            "regression_pass_rate": candidate.get("regression_pass_rate", 0.0),
            "honesty_score": candidate.get("honesty_score", 1.0),
            "notes": candidate.get("notes", "Promoted baseline."),
            "run_id": candidate.get("run_id"),
            "source_config_path": candidate.get("source_config_path"),
        }
    )
    payload["config_archive_path"] = _archive_config_snapshot(path, candidate)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def maybe_promote_baseline(path: Path, candidate: dict, gate: dict | None = None) -> dict:
    current = load_baseline(path)
    decision = assess_candidate(current=current, candidate=candidate, gate=gate or {})
    if decision["promoted"]:
        write_baseline(path, candidate)
    return decision


def restore_baseline_config(baseline_path: Path, target_config_path: Path | None = None) -> dict:
    baseline = load_baseline(baseline_path)
    archive_path = baseline.get("config_archive_path")
    if not archive_path:
        raise FileNotFoundError("Baseline config archive path is missing.")

    source = Path(archive_path)
    if not source.exists():
        raise FileNotFoundError(f"Archived baseline config not found: {source}")

    target = target_config_path or Path(baseline.get("source_config_path") or "")
    if not target:
        raise FileNotFoundError("No restore target path provided.")

    payload = source.read_text(encoding="utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload, encoding="utf-8")
    return {
        "baseline": baseline,
        "archive_path": str(source.resolve()),
        "target_path": str(target.resolve()),
    }
