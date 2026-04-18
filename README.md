此專案是被 David Noel Ng 啟發，RYS 文章網址：https://dnhkng.github.io/posts/rys/?fbclid=IwY2xjawRPfNhleHRuA2FlbQIxMABicmlkETF5Ym1vR1huQ1QyOW5kRG9Ec3J0YwZhcHBfaWQQMjIyMDM5MTc4ODIwMDg5MgABHsaZ8wYBiIpUTwW7DdvBu16rYpquY_6d2kNov9zKY-D_gpJzDOFrIyLUulMl_aem_53YVwB-a2KmbAf85M3sGyA
This project is inspired by David Noel Ng, and the RYS article is here: https://dnhkng.github.io/posts/rys/?fbclid=IwY2xjawRPfNhleHRuA2FlbQIxMABicmlkETF5Ym1vR1huQ1QyOW5kRG9Ec3J0YwZhcHBfaWQQMjIyMDM5MTc4ODIwMDg5MgABHsaZ8wYBiIpUTwW7DdvBu16rYpquY_6d2kNov9zKY-D_gpJzDOFrIyLUulMl_aem_53YVwB-a2KmbAf85M3sGyA

# Agent Eval Lab

## 2026-04-18 Update

- `P1-4` is complete. `openclaw_cli` now has runtime hardening with prepare/run/cleanup lifecycle logs, per-command command journals, smoke-test-on-prepare, and failure-safe cleanup.
- OpenClaw runtime artifacts are written to `runs/openclaw_runtime/<run_id>/`, including `runtime_report.json`, `runtime_lifecycle.json`, `command_history.json`, `smoke_report.json`, and per-command stdout/stderr records under `commands/`.
- `scripts/serve_dashboard.py` remains the HTTP server entrypoint.
- `start_serve_dashboard.cmd` now starts the dashboard in the background, opens the browser automatically, and returns immediately so the CMD window can close.
- `stop_serve_dashboard.cmd` stops that background dashboard service.
- Background dashboard management lives in `scripts/dashboard_service.py`, with PID/log files stored in `runs/control/`.
- Relayer scan now supports RYS-style full-grid probe scoring. Runnable configs default to `relayer.scan.scoring_mode = "heat_map_probes"`; `mock` remains available as the cheap synthetic fallback.

本專案是一個本機 agent evaluation harness，現在的主線用途是：

- 產生 Layer C 檔案檢索任務
- 用本機 runner 執行 agent
- 以 verifier 計分並寫出 artifact / history
- 透過 dashboard 啟動單次、suite、nightly evolution
- 在 nightly 中做逐步 hill-climb，或用 `heat_map` 模式做 RYS 風格的 layer brain-scan

目前已對齊實作的核心精神是比較接近 `karpathy/autoresearch`：

- 先量 baseline
- 每一輪只改一個參數
- 如果分數變好，就把該輪 config 當成新的 baseline
- 下一輪從新的 baseline 繼續改
- 每輪都留下實驗紀錄，而不是只看最後贏家

## 目前行為

### 1. 單次與 suite

- `scripts/run_single.py`
  建立 sandbox workspace、生成任務、執行 runner、跑 verifier、輸出 artifact、做 workspace restore。
- `scripts/run_suite.py`
  重複執行多輪 Layer C task。
- `--task-type`
  支援 `auto`、`deployment`、`handoff`、`operations`。

### Benchmark matrix

- `deployment`
  file-retrieval benchmark that asks the runner to locate the canonical deployment document.
- `handoff`
  file-retrieval benchmark that asks the runner to locate the canonical handoff document.
- `operations`
  file-retrieval benchmark that asks the runner to locate the canonical operations document.
- `math`
  real arithmetic / reasoning benchmark that generates direct-answer tasks such as arithmetic chains, word problems, sequence reasoning, and ordering logic puzzles.
- `math` does not use filesystem search tools. The runner is expected to return a direct answer instead of a file path.

### 2. Nightly evolution

- `scripts/run_nightly.py`
  不是舊的 multi-parameter pool search；現在支援三種 nightly 模式。
- `model_params`
  逐輪做單參數 hill-climb。
- `architecture_program`
  逐輪切換 agent retrieval policy preset。
- `heat_map`
  固定 baseline，掃描 `Start Layer (i)` / `End Layer (j)` 的 duplicated-block 腦掃描矩陣，並用兩組 task probes 做 `probe_a / probe_b / combined` 評分，再自動補建 heat-map 產物與 top-k verify。
- `model_params` 與 `architecture_program`
  若 `suite_score_c` 提升，且 regression pass rate 沒低於 gate，該輪會成為新的 baseline。
- 每輪結果會寫入：
  - `reports/parameter_history.json`
  - `reports/config_history.json`
  - `reports/nightly_history.json`
  - `reports/heat_map_history.json`（heat_map 模式）
  - `reports/heat_map_verification_history.json`（heat_map verify）

### 3. Verifier

- `verifiers/km_dynamic_verifier.py`
  現在是連續 partial-credit verifier，不再幾乎完全由 `exact_match` 決定總分。
- `passed`
  仍然只有 exact match 才算 pass。
- 但分數會吃以下訊號：
  - same project
  - same doc slug
  - canonical marker
  - filename similarity
  - path similarity
  - search rank
- verifier details 會保留 `retrieval_features` 方便追查。

### 4. Dashboard

- `dashboard.html`
  現在有可操作控制台，不只是監看頁。
- 按鈕：
  - `Start Single Run`
  - `Start Suite`
  - `Start Nightly Evolution`
  - `Stop Current Run`
- 進度顯示用通用 `progress_current / progress_target / progress_text`，所以 suite 和 nightly 都會顯示 `目前/設定數`。
- heat_map 模式可直接在 dashboard 編輯 layer window、block length、repeat count、兩組 probe task/seeds、`top-k` 與 verify runs，不需要先手改 JSON。
- dashboard 已接上 `reports/parameter_history.json`，可以看 round-by-round 參數變化。
- task type 可選：
  - `Auto (Random)`
  - `Deployment`
  - `Handoff`
  - `Operations`
  - `Math Calculation`

## Runner 狀態

### 已驗證主線

- `llama_cpp_agent`
  透過 [configs/experiments/local_llama_cpp_agent.json](/F:/Documents/GitHub/Agent_Eval_Lab/configs/experiments/local_llama_cpp_agent.json) 連本機 `llama.cpp` 相容 API。

### 次要 / 實驗中

- `openclaw_cli`
  adapter 與 code path 已存在；現在 `architecture_program / heat_map` 也會把架構策略轉成 prompt guidance 送進 OpenClaw。
  但是否能在某台機器上完整打通，仍取決於本機 `openclaw` CLI、runtime 狀態與 sandbox 設定。

## 常用指令

### 單次

```powershell
python scripts/run_single.py --config configs/experiments/local_llama_cpp_agent.json --task-type deployment --seed 21
```

### Suite

```powershell
python scripts/run_suite.py --config configs/experiments/local_llama_cpp_agent.json --runs 5 --task-type handoff --seed-start 31
```

### Nightly evolution

```powershell
python scripts/run_nightly.py --config configs/experiments/local_llama_cpp_agent.json --seed-start 500
```

### Nightly heat map

```powershell
python scripts/run_nightly.py --config configs/experiments/local_llama_cpp_agent.json --evolution-mode heat_map --seed-start 500
```

### 重新建 heat-map 產物

```powershell
python scripts/build_heatmap.py --suite-id nightly_20260417_120000
```

### 重跑 top-k verify

```powershell
python scripts/verify.py --suite-id nightly_20260417_120000 --candidate-runs 8
```

### 預覽 relayer 掃描配置

```powershell
python scripts/preview_relayer_scan.py --config configs/experiments/local_llama_cpp_agent.json --limit 10
```

### Run relayer scan

```powershell
python scripts/run_relayer_scan.py --config configs/experiments/local_llama_cpp_agent.json --max-candidates 64
```
Default runnable configs use `relayer.scan.scoring_mode = "heat_map_probes"`, which evaluates the full `(Start Layer (i), End Layer (j))` grid with `probe_a / probe_b / combined` deltas.

Set `relayer.scan.scoring_mode = "mock"` when you want the older cheap synthetic smoke-test path via `mock_layer_stack`.

### 驗證 relayer runtime bridge 協定

在任一實驗 config 的 `relayer.runtime_backend.command` 指向 repo 內建 stub：

```json
{
  "relayer": {
    "enabled": true,
    "mode": "runtime_patch",
    "runtime_backend": {
      "command": ["python", "scripts/fixtures/relayer_runtime_stub.py"]
    }
  }
}
```

這會讓 runner 寫出 `runs/relayer_runtime/<run_id>/manifest.json`、`stdout.txt`、`stderr.txt`、`result.json` 與 `stub_runtime_applied.json`，並且可選擇把 backend 回傳的 `runtime_effects` 套進 llama.cpp / OpenClaw request path；它仍不是實際模型 forward-path patch。

### Dashboard

```powershell
python scripts/serve_dashboard.py --port 8765
```

開啟：

```text
http://127.0.0.1:8765/dashboard.html
```

Preferred local service commands on Windows:

```powershell
start_serve_dashboard.cmd
stop_serve_dashboard.cmd
```

## 主要輸出

### Artifact / live 狀態

- `runs/artifacts/*.json`
- `runs/live_status.json`
- `runs/live_stream.jsonl`
- `runs/control/*.log`

### Reports

- `reports/score_history.json`
- `reports/baseline_history.json`
- `reports/rollback_events.json`
- `reports/nightly_history.json`
- `reports/heat_map_history.json`
- `reports/heat_map_verification_history.json`
- `reports/relayer_scan_history.json`
- `reports/config_history.json`
- `reports/parameter_history.json`
- `reports/heat_maps/<suite_id>/matrix.csv`
- `reports/heat_maps/<suite_id>/heatmap.png`
- `reports/heat_maps/<suite_id>/combined/`, `probe_a/`, `probe_b/`（channel-specific matrices / PNGs）
- `reports/heat_maps/<suite_id>/README.md`（先看哪裡、座標解釋、結果解讀）
- `reports/heat_maps/<suite_id>/verification.json`
- `reports/relayer_scans/<run_id>/aggregated.csv`
- `reports/relayer_scans/<run_id>/artifacts/heatmap.png`
- `reports/relayer_scans/<run_id>/README.md`（精華摘要與閱讀順序）

## 目前 nightly 會動到的參數

`evolution/mutator.py` 目前提供的 `model_params` 單參數候選以 sampling 參數為主，例如：

- `llama_cpp.temperature`
- `llama_cpp.top_p`
- `llama_cpp.top_k`
- `llama_cpp.min_p`
- `llama_cpp.repeat_penalty`
- `llama_cpp.repeat_last_n`
- `llama_cpp.seed`
- `llama_cpp.presence_penalty`
- `llama_cpp.frequency_penalty`

## Relayer 狀態

- repo 現在已有 `RelayerConfig / RelayerPlan / scanner / mock relayer runner` 骨架，可驗證 `execution_order`
- `scripts/preview_relayer_scan.py` 可預覽 `(start_layer, end_layer)` 掃描配置與對應 `execution_order`
- `scripts/run_relayer_scan.py` 現在支援兩條 scoring path：`heat_map_probes` 會跑完整 `(Start Layer (i), End Layer (j))` RYS-style probe heatmap；`mock` 則保留 `mock_layer_stack` 的便宜 synthetic smoke-test 路徑
- 現有 `llama_cpp_agent / openclaw_cli / session_mock` runner 若偵測到 `relayer.enabled=true`，會把 relayer plan 寫進 metadata，並明確標示是否真的套用
- 目前預設 `relayer.mode=metadata_only`
- `session_mock` 已支援 `mock_layer_stack`，會真的執行 layer trace 驗證 `execution_order`
- `llama_cpp_agent / openclaw_cli` 若設定 `relayer.runtime_backend.command`，現在可進入 `runtime_patch` bridge 路徑，寫出 manifest / stdout / stderr / result artifact，並把 backend 回傳的 prompt / request / env overrides 套進 runner
- repo 內建 `scripts/fixtures/relayer_runtime_stub.py` 可驗證 bridge 協定、runner handoff 與 runtime effect 注入，但它不是實際模型 patch backend
- 若把 `relayer.mode` 設成 `runtime_patch`、但沒有設定 `relayer.runtime_backend.command`，runner 仍會直接報錯，避免產出假的 relayer 結果
- `run_relayer_scan.py` 在 `mock` 模式下仍使用 `mock_relayer_probe`，用途是驗證掃描/runner/store/heatmap plumbing，不是模型能力 benchmark
- 也就是說：目前真正缺的是模型 forward-path relayering backend，不是 heatmap 掃描、artifact pipeline 或 top-k verification 流程

## 限制與現況

- `passed` 還是 exact-match gate；現在改善的是連續分數，不是把 pass 標準放寬。
- nightly 已改成單參數 hill-climb，但還不是完整 proposer-executor research loop。
- dashboard 目前能控制 run / suite / nightly，但還沒有更深的 trace diagnostics 視圖。
- OpenClaw CLI 真實端到端路徑仍受本機環境影響，`llama_cpp_agent` 是目前最穩定的可跑配置。
- 真正的 runtime relayering 仍需要專用模型 backend；目前只完成可驗證的規劃層、mock execution 層與 external bridge handoff 層。

## 目錄

```text
Agent_Eval_Lab/
├─ benchmarks/
├─ configs/
├─ docs/
├─ evolution/
├─ generators/
├─ rollback/
├─ runners/
├─ sandbox/
├─ scoring/
├─ scripts/
├─ storage/
├─ verifiers/
├─ reports/
├─ runs/
└─ dashboard.html
```

## Relayer Update (2026-04-18)

- `scripts/run_relayer_scan.py` 現在支援兩種模式：
  1. `heat_map_probes`：對完整 relayer cell grid 跑 `probe_a / probe_b / combined` delta，對齊 README 第一行引用的 RYS-style heatmap 方法
  2. `mock`：保留 `mock_layer_stack` synthetic ranking，專門用來做 smoke test 與 plumbing 驗證
  3. top-k candidate 仍會走真實 Layer C evaluation / regression verification，再套用 baseline gate，寫入 `baseline_history / rollback_events / config_history`
- relayer scan candidate 仍會依 `relayer.scan.runtime_mode` 解析成真正的 runtime mode；目前 runnable experiment config 預設 `relayer.scan.scoring_mode = "heat_map_probes"`，而 `runtime_patch` 已可透過 `scripts/fixtures/relayer_runtime_stub.py` 驗證 bridge handoff 與 runtime effect 注入。
- 這條線已完成 heatmap generation、artifact plumbing、verifier / gate / adoption loop，以及 runtime backend effect plumbing；剩下的 relayer gap 是真正的模型 forward-path backend，而不只是 stub。

### New Relayer Outputs

- `reports/relayer_scan_history.json`
- `reports/relayer_scan_verification_history.json`
- `reports/relayer_scans/<run_id>/verification.json`
- `reports/relayer_scans/<run_id>/artifacts/heatmap.png`
- `runs/relayer_runtime/<run_id>/manifest.json`
- `runs/relayer_runtime/<run_id>/result.json`
