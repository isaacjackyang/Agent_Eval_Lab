# MetaHarness Architecture Gap Review

## 文件定位

這份文件是「目標態 vs 現況」的 gap review。

它不再假設 repo 已經完成完整 MetaHarness；相反地，它描述：

- 現在真的有什麼
- 跟理想中的 `karpathy/autoresearch` / MetaHarness 還差什麼
- 下一步最值得補哪些能力

## 現況摘要

截至 `2026-04-16`，`Agent_Eval_Lab` 已具備：

- 本機 Layer C file retrieval 任務生成
- `llama.cpp` runner 主線
- dashboard 控制 single / suite / nightly
- sequential single-parameter nightly tuning
- 連續 partial-credit verifier
- artifact / history / rollback / baseline 紀錄

這表示它已經不是純 benchmark script，但也還不是完整 MetaHarness。

## 已對齊 MetaHarness 精神的部分

### 1. 實驗不是只看最後分數

目前已有：

- `reports/parameter_history.json`
- `reports/config_history.json`
- `reports/nightly_history.json`

因此每輪的參數、分數、是否採用，都可追溯。

### 2. 調參開始有「研究循環」味道

`scripts/run_nightly.py` 已改成：

1. 量 baseline
2. 每輪只改一個參數
3. 提升就採用
4. 以新 baseline 繼續下一輪

這比舊式 candidate pool 更接近 autoresearch 的 iterative loop。

### 3. 評分不再接近 pass/fail 二元

`verifiers/km_dynamic_verifier.py` 已加入連續檢索品質訊號：

- same project
- same doc slug
- canonical marker
- filename similarity
- path similarity
- search rank

因此現在可以區分：

- 完全錯
- 接近正解
- 正解

## 與完整 MetaHarness 的差距

### Gap A: 還沒有真正的 proposer

目前 nightly 只會從 `evolution/mutator.py` 裡的固定單參數候選挑下一輪。

這代表：

- 目前沒有讀 trace 後自動提出 patch 的 proposer
- 目前沒有 failure cluster -> parameter proposal 的映射器
- 目前沒有 prompt patch / policy patch generator

### Gap B: trace 還不夠原始、也不夠結構化

雖然已有：

- `runs/live_stream.jsonl`
- artifact 裡的 `tool_trace`

但離完整 MetaHarness trace 還差：

- event schema version
- correlation id / parent-child trace structure
- model latency / token throughput / tool latency 細分
- replay-friendly raw request / raw response
- 更完整的 sandbox telemetry

### Gap C: nightly 仍偏 config mutation，不是全面研究循環

目前 nightly 主要改的是：

- `max_steps`
- `time_budget_sec`
- `efficiency_caps.*`
- `llama_cpp.*`
- `openclaw.thinking`

還沒有涵蓋：

- system prompt variant
- search policy variant
- rerank policy
- recovery policy
- tool routing strategy

### Gap D: dashboard 還沒有研究視圖

目前 dashboard 已能控制流程，也能看高層結果，但還沒有：

- round-by-round parameter diff
- lineage / ancestry view
- failure heatmap
- trace timeline
- “為什麼這輪被採用 / 被拒絕”的可視化

### Gap E: OpenClaw CLI 真實路徑仍不穩

`openclaw_cli` adapter 已存在，但 repo 目前最穩定的實驗路徑仍是本機 `llama_cpp_agent`。

所以如果目標是完整 MetaHarness + OpenClaw runtime 真實鏈路，還需要：

- CLI lifecycle 穩定化
- sandbox lifecycle 檢查
- trace schema 對齊 OpenClaw event
- 端到端 smoke tests

### Gap F: Relayering still lacks a true forward-path backend

Current repo status:

- `RelayerConfig / RelayerPlan` and the relayer scan candidate generator are implemented.
- `run_relayer_scan.py` can now score the full `(i, j)` grid with RYS-style `probe_a / probe_b / combined` deltas when the config is runnable.
- `mock_layer_stack` remains available as the cheap fallback for smoke tests and `execution_order` validation.
- `runtime_patch` bridge artifacts and top-k verification are already wired into baseline / rollback / history.
- External relayer backends can now return limited runtime effects that are applied to llama.cpp / OpenClaw request paths (prompt / request / env overrides), so the bridge is no longer metadata-only.

Remaining gap:

- A true forward-path relayer backend for llama.cpp / OpenClaw is still not complete.
- Current `runtime_patch` support can now inject request-level effects, but it is still bridge / stub based rather than a native model-layer patch implementation.

## 建議的優先順序

### P0

1. 為 nightly 加上更明確的 `accepted / rejected because ...` 結構化理由。
2. 把 `parameter_history.json` 接到 dashboard。已完成。
3. 把 search trace 補成可穩定取 rank / candidates / chosen path。

### P1

1. 加入 failure-cluster 分析。
2. 建立 `failure tag -> proposal template` 規則。
3. 讓 nightly 不只改 numeric config，也能改 prompt / policy。

### P2

1. 建立真正的 proposer-executor loop。
2. 建立 trace replay / counterfactual diagnosis。
3. 加入多策略比較與 lineage 視圖。

## 一句話結論

`Agent_Eval_Lab` 現在已經有 MetaHarness 的骨架與 autoresearch 的迭代精神，但仍屬於「可研究的實驗室」階段，尚未成為完整的自動研究系統。`
