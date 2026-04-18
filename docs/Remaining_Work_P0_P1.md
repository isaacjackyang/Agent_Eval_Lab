# Agent Eval Lab Remaining Work (P0 / P1)

Updated: 2026-04-18

## Status Summary

- `P0` is complete.
- `P1-1` Relayer Scan engineering is complete.
- `P1-2` Dashboard deepen is complete.
- `P1-3` Trace / Replay / Failure Analysis is complete.
- `P1-4` Runtime hardening is complete.

## Completed P0

- Relayer scan now supports full-grid RYS-style probe scoring when the config is runnable, and falls back to synthetic mock scoring otherwise.
- Top-k verification, baseline gate, rollback, and history are connected to relayer scan results.
- Dashboard can launch relayer scans and display verification/runtime capability summaries.
- Experiment configs now ship with `relayer.scan.scoring_mode = "heat_map_probes"`, plus `relayer.runtime_backend.command` stubs, runnable runtime-effect injection plumbing, and `relayer.scan.verify` defaults for smoke testing.

## Completed P1

### P1-1. Relayer Scan Engineering

- Added `resume`, `skip_completed`, and `max_workers`.
- Added per-cell artifact storage and resumable scan state.
- Added relayer scan drill-down artifacts for later analysis.
- Aligned relayer scan coordinates and summaries to `Start Layer (i)` / `End Layer (j)` so the artifacts match the RYS-style heatmap layout.

### P1-2. Dashboard Deepen

- Added relayer artifact drill-down.
- Added lineage, trace, and failure diagnostics panels.
- Added relayer verification and baseline decision visibility.

### P1-3. Trace / Replay / Failure Analysis

- Live event schema is replay-friendly and includes stable event identifiers.
- Failure clustering and counterfactual diagnosis are recorded.
- Nightly and relayer scan traces now surface proposer/executor behavior in diagnostics.

### P1-4. Runtime Hardening

- `openclaw_cli` runtime now writes a full lifecycle log across prepare, run, and cleanup.
- Every OpenClaw CLI call now produces a command journal entry plus stdout/stderr artifacts.
- Prepare now runs a smoke test by default before agent execution.
- Cleanup is now protected by `try/finally` in the runner so cleanup still runs after failures.
- Failure paths now return artifacts and metadata instead of dropping the whole run on the floor.
- Background dashboard service management is now available through:
  - `start_serve_dashboard.cmd`
  - `stop_serve_dashboard.cmd`
  - `scripts/dashboard_service.py`

## P2 Next

- Replace relayer runtime stubs / request-level effect injection with true forward-path model backends where available.
- Expand dashboard drill-down from summaries into richer replay tooling.
- Add more regression coverage for service management and runtime failure variants.
