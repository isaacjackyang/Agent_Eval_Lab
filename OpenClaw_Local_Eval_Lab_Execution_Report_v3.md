# Agent Eval Lab Execution Report v3

## 文件定位

這份文件描述的是「目前 repo 已經落地的執行行為」，不是純目標架構草案。

截至 `2026-04-16`，本專案的實作重心已經從早期的 candidate-pool 想像，調整為更接近 `karpathy/autoresearch` 的本機實驗 loop：

- 先量 baseline
- 每輪只改一個參數
- 如果分數提升，就採用這輪 config
- 以新的 config 當下一輪 baseline
- 保留每輪參數與分數紀錄

## 已實作能力

### 1. Runner 與任務執行

- `scripts/run_single.py`
  執行單次任務、建立 workspace、呼叫 runner、寫 artifact、restore workspace。
- `scripts/run_suite.py`
  重複跑多輪 task。
- `configs/experiments/local_llama_cpp_agent.json`
  已可作為本機主線 config。
- `runners/llama_cpp_agent_runner.py`
  直接橋接本機 `llama.cpp` 相容 API。

### 2. Dashboard 控制

- `dashboard.html`
  已有控制按鈕，不只是觀測頁。
- 可從 UI 啟動：
  - single run
  - suite
  - nightly evolution
  - stop current run
- 可選 task type：
  - auto
  - deployment
  - handoff
  - operations
- 進度會顯示為 `current/target`，適用 suite 與 nightly。

### 3. Nightly evolution

- `scripts/run_nightly.py`
  現在是 sequential single-parameter hill-climb。
- `evolution/mutator.py`
  每輪只產出單參數改動候選。
- 改動若提升 `suite_score_c`，且 regression pass rate 未跌破門檻，該輪 config 會成為新的 baseline。

### 4. 評分與 verifier

- `verifiers/km_dynamic_verifier.py`
  現在提供連續 partial credit。
- exact match 仍是 pass gate。
- 但總分已不再接近二元分佈，會吃：
  - same project
  - same doc slug
  - canonical marker
  - filename similarity
  - path similarity
  - search rank

## 目前實驗紀錄

Nightly / suite / single 會寫入：

- `runs/artifacts/*.json`
- `reports/score_history.json`
- `reports/nightly_history.json`
- `reports/config_history.json`
- `reports/parameter_history.json`

其中 `parameter_history.json` 是目前最接近 lab notebook 的紀錄：

- 哪一輪
- 從哪個 reference config 出發
- 改了哪一個參數
- 參數前值 / 後值
- `suite_score_c`
- `fitness`
- 有沒有被採用為下一輪 baseline

## 與舊版本描述的差異

### 1. 不再以 candidate pool 為主要敘事

舊描述常把 nightly 寫成多 candidate 並行 pool search。現在主線不是這個模式。

目前主線是：

1. 量 baseline
2. 產生單參數候選
3. 評估這個候選
4. 如果更好就採用
5. 從新 baseline 繼續下一輪

### 2. verifier 不再近乎 binary

舊描述雖然提到多 subscore，但實際上早期分數很容易只落在兩團。

目前 verifier 已改成：

- `passed` 保持嚴格
- `score` 改為連續
- `details.retrieval_features` 提供可觀測的細項訊號

### 3. dashboard 已可主動控制流程

舊描述偏向 monitor。

目前 dashboard 已實際接：

- `/api/start-run`
- `/api/start-suite`
- `/api/start-nightly`
- `/api/stop`

## 尚未完全完成的部分

### OpenClaw CLI 真實路徑

`openclaw_cli` adapter 已存在，但不同機器上的 OpenClaw runtime、agent add 流程、sandbox 狀態仍可能造成真實路徑不穩。當前最穩定的實驗主線仍是 `llama_cpp_agent`。

### 完整 proposer-executor 研究循環

現在 nightly 已經有 autoresearch 的雛形，但仍屬於：

- single-parameter hill-climb
- failure-aware logging
- score-driven acceptance

還沒有完整做到：

- trace-driven proposal synthesis
- 自動生成 prompt patch
- 自動生成 tool policy patch
- 多 proposal 的研究型 scheduler

### 更深的 dashboard diagnostics

目前 dashboard 仍以控制台與高層歷史資料為主，尚未做完整：

- trace timeline
- failure heatmap
- round-by-round parameter diff 視圖
- candidate lineage 視圖

## 結論

本專案目前已經不是單純的「跑幾輪 benchmark」工具，而是一個可在本機持續做小步實驗、記錄、比較、接受或拒絕改動的 agent evaluation lab。

如果用一句話總結現在的實作狀態：

`它已經開始像一個研究迴圈，但還不是完整的 MetaHarness / AutoResearch 系統。`
