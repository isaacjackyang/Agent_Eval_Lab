# MetaHarness 對照架構缺口審視

## 結論

目前這套 `Agent_Eval_Lab` 已經有一些和 MetaHarness 相近的基礎：

- 有 run artifact、live stream、history report
- 有 verifier / score / nightly candidate pool / baseline gate
- 有工具調用 trace 與 workspace restore
- 有把 OpenClaw adapter、sandbox 設定、baseline lifecycle 接進主流程

但如果用你提供的 MetaHarness 落地原則來看，現在仍然缺少的不是「更多 if-else」，而是三個核心能力：

1. **夠原始、夠完整、可回放的 Raw Trace**
2. **真正 declarative 的 Reward / Tool / Task 契約**
3. **失敗案例驅動的 Proposer-Executor 自我迭代閉環**

---

## 1. Raw Trace 體系仍然不夠「原始」

### 現況

- `runs/live_stream.jsonl` 會記錄 assistant、tool_call、tool_result、verifier、system 事件
- artifact 會保存 `runner_result.metadata.raw_stdout`
- verifier 會保存 `tool_trace`

### 缺口

目前的 trace 還是偏「摘要後的事件」，不是 MetaHarness 意義上的 Raw Trace：

- 沒有 **統一 schema version**
- 沒有 **event_id / parent_id / correlation_id**
- 沒有把 **OpenClaw 原始事件流完整逐條落盤**
- 沒有把 **每一步 latency / token throughput / model latency / sandbox latency** 拆出來
- 沒有保存 **中間變數狀態**
  例如：
  - 檢索候選列表與分數
  - rerank 前後結果
  - 每輪 prompt / response payload
  - sandbox explain / recreate 的原始結果與耗時
- 沒有 **可重播 replay**
  也就是無法用同一份 raw trace 做反事實診斷或離線分析

### 風險

- 失敗時只能看到「結果錯了」，很難知道是檢索、規劃、工具調用還是 sandbox 狀態出問題
- nightly 目前只能做粗粒度 selection，無法做 trace-driven mutation

### 建議

優先新增 `storage/raw_trace_writer.py` 與 `runs/traces/<run_id>.jsonl`：

- 一個 event 一列
- 每列至少包含：
  - `trace_schema_version`
  - `run_id`
  - `event_id`
  - `parent_event_id`
  - `stage`
  - `ts_start`
  - `ts_end`
  - `latency_ms`
  - `token_in`
  - `token_out`
  - `token_per_sec`
  - `tool_name`
  - `tool_args_raw`
  - `tool_result_raw`
  - `model_request_raw`
  - `model_response_raw`
  - `sandbox_state`
  - `intermediate_state`

---

## 2. Reward Function 還不夠 declarative

### 現況

- `Stotal` 與 weighted fitness 已存在
- verifier 有 `task / verify / tool / recovery / efficiency / honesty`
- nightly 已會依 fitness 做 baseline gate

### 缺口

現在的 reward 還是偏「程式裡手工寫死的邏輯」：

- verifier 對 `tool_score` 的規則仍是固定 if-else
- `search_file -> open_file_location` 的成功路徑被寫進了驗證邏輯
- task schema 還不夠 declarative
  - 沒有明確的 `trace expectations`
  - 沒有 `format expectations`
  - 沒有 `retrieval quality expectations`
  - 沒有 `citation / provenance expectations`
- reward 沒有細分成「中間過程 reward」
  - 檢索是否找對候選
  - 是否縮小搜尋範圍成功
  - 是否有 hallucination precursor

### 風險

- 一旦任務類型擴充，就會快速回到大量特例程式碼
- proposer 未來即使存在，也缺少可優化的標準化目標

### 建議

把 task template 升級成 declarative contract，例如：

```json
{
  "expected_trace": {
    "required_tools": ["search_file", "open_file_location"],
    "forbidden_tools": ["write_file", "delete_file"],
    "preferred_order": ["search_file", "open_file_location"]
  },
  "expected_output": {
    "type": "path",
    "must_exist": true,
    "must_match_canonical": true
  },
  "intermediate_rewards": {
    "retrieval_candidate_contains_target": 0.2,
    "query_refinement_success": 0.1
  }
}
```

---

## 3. 工具箱還不是完整的「受控邊界」

### 現況

- 已有 OpenClaw adapter
- sandbox config 已能注入 agent config
- file retrieval 類 task 已有基本工具約束

### 缺口

目前工具層仍然偏 task-specific：

- `search_file` 其實還不是正式的 CLI tool 契約，而是 mock / stub 或程式內搜尋邏輯
- 沒有統一的 tool manifest
- 沒有 capability metadata：
  - side effects
  - required permissions
  - timeout budget
  - sandbox requirement
  - input / output schema
- 沒有獨立的「本地工具層」供 OpenClaw 與 verifier 共用

### 風險

- 真實 OpenClaw 上線後，tool 行為與 verifier 假設可能逐漸漂移
- 很難做到跨任務共用與安全治理

### 建議

新增 `tools/manifest.json` 與統一 wrapper：

- 每個工具都有 schema、timeout、risk class、side-effect class
- OpenClaw runner 與 verifier 都讀同一份 manifest

---

## 4. 現在還沒有真正的 Proposer-Executor

### 現況

- nightly 會產生 candidate pool
- mutation 已存在 `fast_lane / deep_search / strict_honesty`

### 缺口

這仍然是「模板變體生成器」，不是 MetaHarness 所說的 proposer：

- 沒有讀取失敗案例 trace 後提出變更建議
- 沒有針對特定 failure tag 提案
- 沒有把 proposal 與 executor 分離
- 沒有自動產生 prompt patch / config patch / tool policy patch
- 沒有 proposal quality scoring

### 風險

- nightly 目前只是在有限模板裡挑最好的一個
- 一旦遇到新的 failure mode，不會學到新的策略

### 建議

最小可行版 proposer-executor：

- `proposer`
  讀取最近 N 個 failed runs 的 raw trace + failure tags
- 產出：
  - config patch proposal
  - prompt patch proposal
  - tool policy proposal
- `executor`
  把 proposal 套到候選 config 上，跑小規模 candidate + regression
- 最後再經 baseline gate

建議新增：

- `proposer/trace_analyzer.py`
- `proposer/proposal_schema.json`
- `scripts/run_proposer_cycle.py`

---

## 5. 缺少「失敗分類 -> 對策」映射層

### 現況

- 目前有 failure tags
- 有 nightly selection

### 缺口

現在 failure tags 還沒有變成可操作的自動化知識：

- `hallucinated_path` 沒有對應的 proposal strategy
- `wrong_tool` 沒有對應的 policy tightening
- `failed_recovery` 沒有對應的 retry / timeout mutation
- 沒有 failure cluster 與 recurring pattern 分析

### 建議

新增一層：

- `reports/failure_clusters.json`
- `reports/failure_to_action_map.json`

這會讓 proposer 不只看單次 run，而是看 recurring failure pattern。

---

## 6. Docker sandbox 目前是「設定已接上」，不是「實際驗證完畢」

### 現況

- sandbox config 已注入 OpenClaw runtime
- runner 會呼叫 `sandbox recreate/explain`
- artifact 會保存 sandbox metadata

### 缺口

這台機器目前沒有 `docker` 指令可驗證，所以還缺：

- 真實 container 啟動驗證
- volume mount 驗證
- workspace permissions 驗證
- network isolation 驗證
- container cleanup 驗證
- sandbox failure path 驗證

### 建議

在有 Docker 的機器上補一套 `sandbox smoke tests`：

- `scripts/test_docker_sandbox.py`
- 驗證：
  - container exists
  - workspace mount works
  - write/read permission matches config
  - network mode is correct
  - prune after run works

---

## 7. Dashboard 還沒有「trace diagnostics」視角

### 現況

- dashboard 已有 baseline / rollback / nightly / config history

### 缺口

如果要往 MetaHarness 靠近，dashboard 還少：

- raw trace timeline
- token/s latency chart
- stage latency breakdown
- failure tag heatmap
- proposal vs baseline comparison
- candidate pool leaderboard detail
- sandbox health / cleanup status

### 建議

下一版 dashboard 可以增加：

- `Trace Diagnostics`
- `Failure Heatmap`
- `Candidate Leaderboard`
- `Sandbox Status`

---

## 8. 缺少「資料血緣」與 schema 治理

### 缺口

目前 artifact 與 reports 雖然多，但還缺：

- `schema_version`
- `config_hash`
- `prompt_hash`
- `tool_manifest_hash`
- `baseline_lineage`
- `proposal_id`
- `parent_run_id`

### 風險

- 之後資料越多，難以知道某個 run 到底屬於哪一代配置
- 回溯 nightly 決策會越來越痛苦

---

## 優先補強順序

### P0

1. 建立真正的 raw trace schema 與落盤
2. 補 proposer-executor 最小閉環
3. 把 failure tag 對應到 proposal strategy

### P1

1. 把 task / tool / reward 改成更 declarative 的契約
2. 補 Docker sandbox smoke tests
3. dashboard 加入 diagnostics 視角

### P2

1. 做 trace replay / counterfactual diagnosis
2. 做多代 proposal lineage
3. 做 candidate Pareto frontier 與多目標 selection

---

## 一句話結論

我們現在已經有「評測平台」與「基礎選代機制」，但距離 MetaHarness 式的系統還差在：

- **trace 還不夠 raw**
- **reward 還不夠 declarative**
- **nightly 還不是 failure-driven proposer-executor**

如果只補更多 mutation 模板，提升會很快碰頂；真正的下一個躍遷點會是 **Raw Trace + Failure-driven Proposer**。
