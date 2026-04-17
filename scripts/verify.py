from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evolution.heat_map_verifier import run_heat_map_verification
from evolution.mutator import apply_heat_map_overrides
from storage.history_writer import ensure_report_files


def _load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-run heat-map top-k verification from a nightly artifact.")
    parser.add_argument("--artifact", default=None, help="Nightly artifact JSON under runs/artifacts.")
    parser.add_argument("--suite-id", default=None, help="Suite id; resolves to runs/artifacts/<suite_id>.json.")
    parser.add_argument("--config", default=None, help="Override base config path. Falls back to base_config_path in the artifact.")
    parser.add_argument("--candidate-runs", type=int, default=None, help="Override verify candidate runs per config.")
    parser.add_argument("--top-k", type=int, default=None, help="Override verify top-k candidate count.")
    parser.add_argument("--seed-start", type=int, default=500, help="Seed base used when the artifact does not specify one.")
    args = parser.parse_args()

    if not args.artifact and not args.suite_id:
        raise RuntimeError("Provide either --artifact or --suite-id.")

    artifact_path = Path(args.artifact) if args.artifact else ROOT / "runs" / "artifacts" / f"{args.suite_id}.json"
    artifact = _load_json(artifact_path.resolve())
    if not isinstance(artifact.get("heat_map"), dict):
        raise RuntimeError(f"Artifact does not contain heat-map summary: {artifact_path}")

    base_config_path = Path(
        args.config
        or artifact.get("base_config_path")
        or (ROOT / "configs" / "experiments" / "default_mvp.json")
    ).resolve()
    base_config = _load_json(base_config_path)
    verify_overrides = {
        "candidate_runs_per_config": args.candidate_runs,
        "top_k": args.top_k,
    }
    if any(value is not None for value in verify_overrides.values()):
        base_config = apply_heat_map_overrides(base_config, verify_overrides=verify_overrides)

    reports_dir = ROOT / base_config["paths"]["reports_dir"]
    ensure_report_files(reports_dir)
    suite_id = str(artifact.get("suite_id") or args.suite_id or artifact_path.stem)
    artifact_dir = ROOT / "reports" / "heat_maps" / suite_id
    report = run_heat_map_verification(
        base_config_path=base_config_path,
        base_config=base_config,
        heat_map_summary=artifact["heat_map"],
        evaluated_candidates=artifact.get("evaluated_candidates", []),
        suite_id=suite_id,
        reports_dir=reports_dir,
        artifact_dir=artifact_dir,
        seed_start=int(args.seed_start),
    )
    if report is None:
        raise RuntimeError("Heat-map verification did not run; ensure the artifact has top candidates and verification is enabled.")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
