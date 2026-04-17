# Agent Eval Lab

本專案是一個本機 agent evaluation harness，現在的主線用途是：

- 產生 Layer C 檔案檢索任務
- 用本機 runner 執行 agent
- 以 verifier 計分並寫出 artifact / history
- 透過 dashboard 啟動單次、suite、nightly evolution
- 在 nightly 中做逐步 hill-climb，或用 `heat_map` 模式掃描架構格點

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

### 2. Nightly evolution

- `scripts/run_nightly.py`
  不是舊的 multi-parameter pool search；現在支援三種 nightly 模式。
- `model_params`
  逐輪做單參數 hill-climb。
- `architecture_program`
  逐輪切換 agent retrieval policy preset。
- `heat_map`
  固定 baseline，掃描兩條架構軸形成的格點，輸出 matrix / top-k 候選摘要，並自動補建 heat-map 產物與 top-k verify。
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
- heat_map 模式可直接在 dashboard 編輯 `x/y axis`、`x/y values`、`top-k` 與 verify runs，不需要先手改 JSON。
- dashboard 已接上 `reports/parameter_history.json`，可以看 round-by-round 參數變化。
- task type 可選：
  - `Auto (Random)`
  - `Deployment`
  - `Handoff`
  - `Operations`

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

### 執行 relayer synthetic scan

```powershell
python scripts/run_relayer_scan.py --config configs/experiments/local_llama_cpp_agent.json --max-candidates 64
```

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

這會讓 runner 寫出 `runs/relayer_runtime/<run_id>/manifest.json`、`stdout.txt`、`stderr.txt`、`result.json` 與 `stub_runtime_applied.json`，用來驗證 bridge handoff 是否正常；它不是實際模型 forward-path patch。

### Dashboard

```powershell
python scripts/serve_dashboard.py --port 8765
```

開啟：

```text
http://127.0.0.1:8765/dashboard.html
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
- `reports/heat_maps/<suite_id>/verification.json`
- `reports/relayer_scans/<run_id>/aggregated.csv`
- `reports/relayer_scans/<run_id>/heatmap.png`

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
- `scripts/run_relayer_scan.py` 可用 `mock_layer_stack` backend 跑 synthetic scan，輸出 aggregated CSV / heatmap / top-k
- 現有 `llama_cpp_agent / openclaw_cli / session_mock` runner 若偵測到 `relayer.enabled=true`，會把 relayer plan 寫進 metadata，並明確標示是否真的套用
- 目前預設 `relayer.mode=metadata_only`
- `session_mock` 已支援 `mock_layer_stack`，會真的執行 layer trace 驗證 `execution_order`
- `llama_cpp_agent / openclaw_cli` 若設定 `relayer.runtime_backend.command`，現在可進入 `runtime_patch` bridge 路徑，並寫出 manifest / stdout / stderr / result artifact
- repo 內建 `scripts/fixtures/relayer_runtime_stub.py` 可驗證 bridge 協定與 runner handoff，但它不是實際模型 patch backend
- 若把 `relayer.mode` 設成 `runtime_patch`、但沒有設定 `relayer.runtime_backend.command`，runner 仍會直接報錯，避免產出假的 relayer 結果
- `run_relayer_scan.py` 的分數來自 `mock_relayer_probe`，用途是驗證掃描/runner/store/heatmap plumbing，不是模型能力 benchmark
- 也就是說：真正的模型 forward-path relayering backend 仍未完成，但 planner / scanner / mock execution / bridge protocol / guard / synthetic scan pipeline 已補上

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
