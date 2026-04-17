# Agent Eval Lab 未完成項目整理（P0 / P1）

更新日期：2026-04-17

這份文件是根據目前 repo 實作重新整理的未完成項目，不是沿用舊規格書的 wish list。判斷基準以目前程式碼、README、dashboard 控制面與 relayer pipeline 為主。

## 目前已完成到哪裡

- `heat_map` 主線已經是完整的 architecture grid scan：nightly 產物鏈、top-k verify、history、dashboard 編輯與控制都已接上。
- `relayer` 已經不是只有規格書：現在已有 `RelayerConfig / RelayerPlan / scanner / mock runner / synthetic scan / dashboard 啟動 / runtime bridge protocol`。
- `session_mock` 已可用 `mock_layer_stack` 真正驗證 `execution_order`。
- `llama_cpp_agent / openclaw_cli` 已能在 `relayer.runtime_backend.command` 存在時進入 `runtime_patch` bridge，並寫出 manifest / stdout / stderr / result artifact。

一句話總結：現在 repo 已具備 relayer 的規劃層、synthetic 層、bridge 層，但還沒完成真模型 runtime relayering 與 relayer 研究主線整合。

## P0

### P0-1. repo 內建的真 `llama.cpp / OpenClaw` forward-path relayer backend 仍未完成

現況：

- `RelayerPlan` 已能生成真正的 `execution_order`。
- `llama_cpp_agent` 與 `openclaw_cli` 在 `mode=runtime_patch` 時，目前走的是 `runners/relayer_runtime_bridge.py` 外部 bridge。
- repo 內建的 `scripts/fixtures/relayer_runtime_stub.py` 只是 bridge 協定驗證器，不會真的改動模型 forward path。
- README 也已明講：真正的模型 forward-path relayering backend 仍未完成。

為什麼是 P0：

- 只要這條沒完成，專案就還不能宣稱「relayering 已經在真模型上運作」。
- 現在的 `runtime_patch` 比較像 integration contract，不是模型能力本身。

完成定義：

- `llama_cpp_agent` 與 `openclaw_cli` 不需要外部自訂 hook，就能在 repo 內建流程中套用 relayer plan。
- 有端到端測試能證明 layer repeat 確實影響真實推理路徑，而不只是 metadata 或 stub artifact。

參考檔案：

- `evolution/relayer_plan.py`
- `runners/llama_cpp_agent_runner.py`
- `runners/openclaw_cli_runner.py`
- `runners/relayer_runtime_bridge.py`
- `scripts/fixtures/relayer_runtime_stub.py`
- `README.md`

### P0-2. relayer 仍停在 synthetic scan，尚未接上真 benchmark / verifier

現況：

- `scripts/run_relayer_scan.py` 明確是 synthetic scan。
- `evolution/relayer_scan.py` 用的是 `mock_layer_stack` 與 `mock_relayer_probe`。
- 目前沒有 relayer 版本的 top-k verifier，把最佳候選送回真實 Layer C task 與現有 verifier。
- 也沒有 relayer 專屬的 acceptance gate，去決定某個 relayer candidate 是否能被當成新的可用基線。

為什麼是 P0：

- 沒有真 benchmark，就無法知道 relayering 對實際任務是否有幫助。
- 沒有 verifier / gate，就不能把 relayer scan 的結果納入可信研究流程。

完成定義：

- relayer candidate 可跑真實 retrieval benchmark，而不是只跑 synthetic probe。
- 有 relayer top-k verify、history 與 gate decision。
- 能回答「這個 relayer cell 是否真的改善任務表現」而不是只回答「mock probe 比較喜歡它」。

參考檔案：

- `scripts/run_relayer_scan.py`
- `evolution/relayer_scan.py`
- `evolution/heat_map_verifier.py`
- `verifiers/km_dynamic_verifier.py`

### P0-3. relayer 尚未進入主線 evolution / baseline adoption loop

現況：

- `run_relayer_scan.py` 是獨立 CLI。
- dashboard 已可直接啟動 relayer scan，但它仍是獨立 run kind，不是 nightly 的正式 evolution mode。
- `scripts/run_nightly.py` 與 `evolution/mutator.py` 目前主線仍是 `model_params / architecture_program / heat_map`。
- relayer 最佳候選不會自動進入 baseline 審核、round history、rollback / adoption 決策。

為什麼是 P0：

- 只要 relayer 還是獨立支線，它就還不是專案的主研究迴圈一部分。
- 目前 `heat_map` 與 `relayer_scan` 語義仍分裂：前者是 architecture grid，後者是 layer relayering synthetic scan。

完成定義：

- relayer 有清楚的主線入口，可以被當作正式 evolution mode 或正式 benchmark phase。
- 最佳 relayer candidate 能走和其他候選一致的評估、採納、history、rollback 流程。
- `heat_map` 與 `relayer_scan` 的命名與入口語義被收斂，不再需要使用者自己理解兩套不同系統。

參考檔案：

- `scripts/run_nightly.py`
- `evolution/mutator.py`
- `scripts/run_relayer_scan.py`
- `scripts/serve_dashboard.py`

## P1

### P1-1. relayer scan 的研究工程能力還不夠完整

現況：

- `run_relayer_scan.py` 目前只有 `--config`、`--output-dir`、`--max-candidates`。
- 還沒有 `resume`、`max_workers`、skip completed、切批重跑等能力。
- 產物以 aggregated CSV / summary 為主，還沒有 per-question store 或更細的可續跑紀錄。

為什麼放 P1：

- 這些能力不影響「能不能成立為真 relayer backend」，但會直接影響研究效率與可擴展性。

完成定義：

- relayer scan 可續跑、可跳過已完成 cell、可做較大規模批次實驗。
- store 粒度足以支援追查單一候選與單一樣本。

參考檔案：

- `scripts/run_relayer_scan.py`
- `evolution/relayer_scan.py`
- `storage/history_writer.py`

### P1-2. dashboard 還不是完整的研究診斷台

現況：

- dashboard 已能啟動 single / suite / nightly / relayer scan，也已接上 `parameter_history`。
- 但研究視角仍偏高層控制與摘要。
- 還缺更深的 trace timeline、candidate lineage、failure diagnostics、relayer artifact drill-down。
- relayer scan 雖能啟動，但不是完整的 relayer 編輯與分析工作台。

為什麼放 P1：

- 控制面已經能用，但研究者要快速定位「哪個候選為什麼好 / 為什麼壞」仍然不夠直觀。

完成定義：

- dashboard 可以直接追到某輪候選、某次 verifier、某份 relayer artifact 與主要失敗原因。
- 研究者不必頻繁手開 JSON / CSV 才能理解結果。

參考檔案：

- `dashboard.html`
- `scripts/serve_dashboard.py`
- `runs/live_status.json`
- `runs/live_stream.jsonl`

### P1-3. trace / replay / failure analysis 還沒有形成完整研究閉環

現況：

- 目前已有 `live_stream`、artifact、tool trace、partial-credit verifier。
- 但還沒有完整的 raw event schema、replay-friendly trace、failure clustering、counterfactual diagnosis。
- nightly 仍以固定 mutation catalog 為主，還不是完整的 proposer-executor loop。

為什麼放 P1：

- 這些能力會決定專案能否從「可跑的實驗 harness」進化成「會自我分析的研究系統」。
- 但它們建立在 P0 已經把 relayer 主線打通之後才值得大規模投入。

完成定義：

- 可以把失敗案例聚類、標記、回推到 proposal template。
- 可以重放關鍵 trace，回答「這輪為什麼失敗、下一輪該改什麼」。
- nightly 不只改 numeric config，也能逐步擴展到 prompt / policy / routing 類型的提案。

參考檔案：

- `docs/MetaHarness_Architecture_Gap_Review.md`
- `runs/live_stream.jsonl`
- `evolution/mutator.py`
- `scripts/run_nightly.py`

### P1-4. OpenClaw 真實鏈路仍需要持續硬化

現況：

- `openclaw_cli` adapter 已存在，也有本地 stub 與 dashboard 控制。
- 但 README 仍把它標成次要 / 實驗中路徑。
- 真機環境下的 CLI lifecycle、sandbox lifecycle、trace 對齊與穩定 smoke tests 仍不如 `llama_cpp_agent` 主線穩定。

為什麼放 P1：

- 這條線重要，但在 relayer 真 backend 尚未落地前，先把它視為 runtime hardening 會比較務實。

完成定義：

- OpenClaw 在真實環境下有穩定 smoke test、穩定 sandbox lifecycle 與更可預期的 trace/錯誤行為。
- 不再只是「有 adapter」，而是可被當作主線 runner 長期使用。

參考檔案：

- `runners/openclaw_cli_runner.py`
- `sandbox/openclaw_agent_runtime.py`
- `scripts/fixtures/openclaw_cli_stub.py`
- `README.md`

## 最短結論

如果只看最核心的未完成事項，P0 只有三件事：

1. 做出 repo 內建的真 relayer backend。
2. 把 relayer 從 synthetic probe 接到真 benchmark / verifier。
3. 把 relayer 接進主線 evolution / baseline loop。

其餘像 resume、研究視圖、failure analysis、OpenClaw 硬化，屬於 P1。
