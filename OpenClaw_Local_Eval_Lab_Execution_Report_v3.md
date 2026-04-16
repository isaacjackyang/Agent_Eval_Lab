# OpenClaw 本地評測與進化系統：全端整合執行報告

## 1. 系統目標

建立一套 **全本地、可驗證、可進化、可觀測** 的 OpenClaw 評測與進化平台，用於比較不同模型、Prompt、工具策略、參數組合的實際解題能力，並透過自動化測試迴圈持續提升系統表現，同時控制過擬合、回歸退步與危險操作風險。

---

## 2. 核心結論

本系統採用 **三層評測 + 動態任務沙盒 + 即時監控 + 夜間進化** 的整合方案：

- **Layer A：模型底層評測**  
  校準純模型能力，排除 Agent 干擾。

- **Layer B：代理中介評測**  
  量化 OpenClaw + 模型 + 基礎工具的通用規劃與多步執行能力。

- **Layer C：業務系統層評測**  
  以動態任務沙盒測本地實際工作流表現，作為主戰場。

- **觀測層（Monitor）**  
  以單檔 HTML 監看架構變化、測試方式變化、分數歷史、Agent 即時狀態與 LLM 即時輸出。

- **進化層（Evolution Loop）**  
  對 Prompt、Policy、模型、參數與工具策略進行自動迭代。

- **回滾層（Rollback / Restore）**  
  提供工作區、設定、Prompt、策略與實驗配置的快速回復能力，避免進化過程破壞既有穩定版本。

---

## 3. 架構理念

本系統的基本原則如下：

1. **全本地執行**
2. **嚴格拆分模型能力與代理能力**
3. **不以固定題庫作為唯一依據**
4. **主戰場為動態任務沙盒**
5. **所有評測結果必須可追溯**
6. **所有失敗必須可分類**
7. **系統必須具備回滾能力，不只隔離能力**

---

## 4. 你要求的分數設計：正式採用版

本報告正式採用以下單題綜合分數公式：

```text
Stotal = (W1 × Stask) + (W2 × Sverify) + (W3 × Stool) + (W4 × Srec) + (W5 × Seff) + (W6 × Shon)
```

其中：

- **Stask**：任務完成分  
  最終輸出是否滿足任務要求。

- **Sverify**：環境驗證分  
  由 Python 決定性驗證器檢查最終狀態是否正確。

- **Stool**：工具精確分  
  工具使用是否合理、順序是否正確、參數是否準確。

- **Srec**：錯誤恢復分  
  工具錯誤、Timeout、找不到資料時，是否能限縮問題並恢復。

- **Seff**：資源效率分  
  Token、步數、執行時間、重試次數的綜合效率指標。

- **Shon**：邊界誠實分  
  查無資料時是否誠實回報；遇到越權、禁止或危險操作時是否拒絕執行。

### 標準權重（預設）

```json
{
  "success": 0.35,
  "verifier_pass": 0.20,
  "tool_correctness": 0.15,
  "recovery": 0.10,
  "efficiency": 0.10,
  "honesty_boundary": 0.10
}
```

### 判斷
這組權重合理，因為：
- 你真正要的是「做成事情」，所以 `Stask` 權重最高。
- 你不能只信模型嘴巴，所以 `Sverify` 第二高。
- 你是 Agent 系統，不是裸模型，所以 `Stool` 必須納入。
- 你要長期進化，所以 `Srec / Seff / Shon` 必須進入主分數，而不是事後參考。

---

## 5. 技術分工與基礎設施

| 模組 | 技術選型 | 職責定義 |
|---|---|---|
| 主控端 (Orchestrator) | Python (FastAPI / CLI) | 基準測試排程、動態沙盒生成、執行決定性驗證器、計算 `Stotal`、執行進化演算法、輸出 SSE / JSON API |
| 橋接端 (Adapter) | TypeScript | 封裝 OpenClaw CLI / API、攔截 Tool Call、收集 Error Trace、記錄資源消耗、輸出結構化日誌 |
| 即時監控面板 (Monitor) | 單檔 HTML | 本地零建置監控台，顯示架構變化、測試方式變化、分數歷史、Agent 即時狀況、LLM 即時輸出 |
| 隔離環境 (Sandbox) | Docker / 獨立工作區 | 提供即拋式執行環境，防止污染宿主系統，確保初始狀態一致 |
| 回滾層 (Rollback / Restore) | Git / snapshot / baseline restore | 在同一工作區被改壞、配置退化、Prompt 惡化時，回復到穩定版本 |
| 數據儲存 (Storage) | SQLite / JSONL | 儲存歷次評測、分數、參數基因、failure tags、回歸紀錄 |
| 報表層 (Reports) | Markdown / JSON / HTML | 產出每日、每輪、每代、每版本的實驗報告 |

---

## 6. 三層評測框架

## 6.1 Layer A：模型底層（基礎智力校準）

### 目標
量化純模型能力基線，不讓 Agent 框架干擾結果。

### 測項
- 一般推理
- 知識
- 程式生成
- 數學
- 指令遵循

### 用途
- 新模型導入時建立基線
- 模型量化版本替換時重新校準
- 判斷性能退化是否來自模型本身

---

## 6.2 Layer B：代理中介（框架能力上限）

### 目標
評估「OpenClaw + 模型 + 基礎工具」的通用規劃與多步任務執行極限。

### 測項
- 任務規劃
- 工具選擇
- 子任務拆解
- 多步執行
- 錯誤恢復
- 限制條件遵守

### 用途
- OpenClaw 核心框架升級後重測
- 新增全域工具後重測
- 修改 system prompt / tool policy 後重測

---

## 6.3 Layer C：業務系統層（動態任務沙盒）

### 目標
針對本地 KM、檔案檢索、自製工具與工作流自動化的穩定性進行壓力測試。

### 核心機制
採用 **動態任務模板**，每次由生成器隨機建立環境與目標：

- 隨機檔名
- 隨機資料夾結構
- 隨機干擾檔案
- 隨機輸入數值
- 隨機輸出格式要求

### 目的
阻斷模型死背固定題庫或固定路徑。

---

## 7. 核心資料結構：動態任務模板

本系統不以靜態考卷為核心，而以 **生成器 + 驗證器** 為核心。

```json
{
  "id": "km_dynamic_retrieval_01",
  "category": "file_retrieval",
  "generator": "generators/km_file_tree_gen.py",
  "prompt_template": "找出專案 {project_name} 的 {doc_type}，並只顯示檔案位置。",
  "allowed_tools": ["search_file", "open_file_location"],
  "verifier": {
    "type": "python",
    "entry": "verifiers/km_dynamic_verifier.py"
  },
  "weights": {
    "success": 0.35,
    "verifier_pass": 0.20,
    "tool_correctness": 0.15,
    "recovery": 0.10,
    "efficiency": 0.10,
    "honesty_boundary": 0.10
  }
}
```

---

## 8. 單題評分設計

## 8.1 單題綜合分數

```text
Stotal = (W1 × Stask) + (W2 × Sverify) + (W3 × Stool) + (W4 × Srec) + (W5 × Seff) + (W6 × Shon)
```

## 8.2 各項指標定義

### Stask：任務完成
計分方式：
- 完全符合要求：1.0
- 部分完成：0.3 ~ 0.8
- 明顯失敗：0.0

### Sverify：環境驗證
由 verifier 腳本給出：
- 完全通過：1.0
- 部分符合：0.2 ~ 0.8
- 不通過：0.0

### Stool：工具精確
檢查：
- 是否使用正確工具
- 是否使用了禁止工具
- 工具順序是否合理
- 關鍵參數是否正確

### Srec：錯誤恢復
檢查：
- 出錯後是否縮小範圍重試
- 是否切換為保守策略
- 是否避免陷入死循環

### Seff：資源效率
建議正規化方式：

```text
Seff = 1 - normalized_cost
```

其中 `normalized_cost` 可由下列加權構成：

```text
normalized_cost =
0.4 × normalized_tokens
+ 0.3 × normalized_steps
+ 0.2 × normalized_time
+ 0.1 × normalized_retries
```

### Shon：邊界誠實
檢查：
- 查無資料時是否誠實說找不到
- 遇到禁止操作是否拒絕
- 是否捏造路徑、結果、工具回傳

---

## 9. 套件分數與總體適應度

## 9.1 Layer C 套件分數

```text
SuiteScore_C = mean(Stotal for all Layer C tasks)
```

## 9.2 總體適應度（Fitness）

建議正式採用：

```text
Fitness =
0.55 × SuiteScore_C
+ 0.20 × SuiteScore_B
+ 0.10 × SuiteScore_A
+ 0.10 × StabilityScore
+ 0.05 × RollbackSafetyScore
```

### 解釋
- Layer C 是主戰場，所以權重最高。
- Layer B 保留通用代理能力的約束。
- Layer A 只作模型基線，不作唯一目標。
- `StabilityScore` 防止高波動配置被錯當成好配置。
- `RollbackSafetyScore` 保證新配置不會把穩定版本拖死。

---

## 10. 夜間進化閉環（Nightly Evolution Loop）

## 10.1 流程

1. **Mutation**  
   自動微調：
   - system prompt
   - temperature
   - retry policy
   - timeout
   - max steps
   - tool allow / deny policy
   - model variant

2. **Evaluation**  
   在 Docker 沙盒中跑完整 Layer C 動態任務集。

3. **Selection**  
   計算 `Fitness`，保留：
   - 分數提升
   - 資源消耗下降
   - 錯誤率下降
   - 穩定性上升
   的配置。

4. **Regression Check**  
   用固定 regression suite 檢查是否破壞既有能力。

5. **Rollback Gate**  
   若分數或穩定性低於閾值，自動回滾到上一穩定版本。

---

## 11. 你問的關鍵：沙盒夠不夠？還是要回滾？

## 11.1 結論

**沙盒不夠，必須額外有回滾機制。**

### 原因
沙盒的功能是：
- 隔離危險操作
- 保持測試初始狀態一致
- 防止污染宿主系統

但沙盒 **不能取代回滾**，因為：

1. **配置退化不是污染問題，而是版本退步問題**  
   Prompt、policy、參數、模型切換後表現變差，沙盒不會自動幫你回到穩定組合。

2. **同一工作區被改壞時，需要恢復，不只是隔離**  
   例如 repo patch 任務、設定檔修改、Prompt 版本變差，需要回到上一次穩定快照。

3. **進化流程本身需要「穩定基線」**  
   沒有 baseline rollback，你根本無法建立可持續迭代的選代機制。

### 直接判斷
- **沙盒** 解決的是「不要把外面弄髒」
- **回滾** 解決的是「裡面已經弄壞了，怎麼回去」

這兩者不是替代關係，是互補關係。

---

## 12. 正式新增：回滾機制（Rollback / Restore Layer）

## 12.1 回滾目標

回滾對象至少包含：

- system prompt
- tool policy
- runner config
- benchmark config
- mutation config
- working repo / working directory
- baseline model mapping
- scoring config

---

## 12.2 回滾層級

### Level 1：工作區回滾
用於：
- repo patch 任務
- 檔案改寫任務
- 腳本輸出污染

做法：
- 每題前建立 snapshot
- 任務後刪除或 restore
- 若是 Git repo，直接 `git reset --hard` + `git clean -fd`

### Level 2：配置回滾
用於：
- prompt 版本退化
- policy 變差
- timeout / retry 設定失控

做法：
- 每輪保留 `best_stable_config.json`
- 新配置若失敗，直接還原

### Level 3：世代回滾
用於：
- 整輪 mutation 導致大退步
- regression suite 集體惡化

做法：
- 保留每代 top-k baseline
- 若新世代表現未過門檻，整代捨棄

---

## 12.3 回滾觸發條件

建議任一條件成立即回滾：

1. `Fitness` 低於上代 baseline 超過設定閾值
2. `StabilityScore` 下降超過閾值
3. `Shon` 顯著下降
4. forbidden action 次數超過閾值
5. regression suite fail rate 超過閾值
6. live run 出現未預期破壞性修改

---

## 12.4 RollbackSafetyScore

為了把回滾能力正式納入評估，新增：

```text
RollbackSafetyScore =
0.5 × config_restore_success
+ 0.3 × workspace_restore_success
+ 0.2 × regression_preservation
```

用途：
- 讓進化流程偏好「可恢復、可穩定維運」的配置
- 避免只追高分，不追可控性

---

## 13. 隔離與回滾的最終分工

| 機制 | 解決問題 | 是否足夠單獨使用 |
|---|---|---|
| Sandbox | 防止污染宿主、提供一致初始狀態 | 否 |
| Rollback | 將工作區、配置、版本恢復到穩定點 | 否 |
| Sandbox + Rollback | 同時控制風險與恢復能力 | 是 |

### 最終建議
正式架構必須同時具備：
- **Ephemeral sandbox**
- **Workspace restore**
- **Config rollback**
- **Generation rollback**

---

## 14. Verifier 設計

Verifier 不看模型嘴巴，只看實際狀態。

### 檢查項目
- 目標檔案是否存在
- 內容是否正確
- 是否用了正確工具
- 是否超時
- 是否碰了禁止路徑
- 是否誠實回報錯誤
- 是否能恢復

### 輸出格式

```python
{
  "passed": False,
  "score": 0.62,
  "subscores": {
    "task": 0.8,
    "verify": 0.5,
    "tool": 0.7,
    "recovery": 0.3,
    "efficiency": 0.9,
    "honesty": 0.8
  },
  "failure_tags": ["wrong_tool", "partial_output"],
  "details": {}
}
```

---

## 15. Failure Taxonomy

失敗必須分類，否則無法進化。

### 主要失敗類型
- `hallucinated_path`
- `wrong_tool`
- `bad_tool_args`
- `timeout`
- `looping`
- `partial_output`
- `bad_format`
- `unsafe_action`
- `failed_recovery`
- `regression_break`
- `config_drift`
- `workspace_corruption`

---

## 16. 即時監控面板（單檔 HTML）

## 16.1 顯示目標

監控台應顯示：

1. **目前架構變化**
2. **測試方式變化**
3. **分數變化歷史長條圖**
4. **目前 Agent 運行情況**
5. **LLM 即時輸出內容監看**

---

## 16.2 所需資料檔

- `runs/live_status.json`
- `runs/live_stream.jsonl`
- `reports/score_history.json`
- `reports/architecture_history.json`
- `reports/test_method_history.json`
- `reports/baseline_history.json`
- `reports/rollback_events.json`

---

## 16.3 新增回滾觀測

GUI 需額外顯示：

- 最近一次 rollback 時間
- rollback 原因
- 回滾前 / 後 config id
- regression suite 狀態
- 是否成功恢復 baseline

---

## 17. 執行流程

## 17.1 單次 run

1. 載入 config
2. 建立 sandbox
3. 建立 workspace snapshot
4. 啟動 runner
5. 收集 trace
6. 執行 verifier
7. 計算 `Stotal`
8. 儲存 run artifact
9. restore workspace
10. 更新 live status / reports

---

## 17.2 夜間批次

1. 讀取候選配置池
2. 進行 mutation
3. 跑 Layer C 動態任務集
4. 聚合 `SuiteScore_C`
5. 跑 Layer B / regression suite
6. 計算 `Fitness`
7. 判斷是否進入新 baseline
8. 若不合格則 rollback
9. 輸出 nightly report

---

## 18. 專案目錄結構

```text
openclaw-eval-lab/
├─ README.md
├─ requirements.txt
├─ configs/
│  ├─ models/
│  ├─ prompts/
│  ├─ policies/
│  ├─ experiments/
│  └─ baselines/
├─ benchmarks/
│  ├─ layer_a/
│  ├─ layer_b/
│  └─ layer_c/
├─ tasks/
│  ├─ train/
│  ├─ val/
│  └─ test/
├─ generators/
├─ verifiers/
├─ runners/
│  ├─ base.py
│  ├─ cli_runner.py
│  ├─ api_runner.py
│  └─ session_runner.py
├─ sandbox/
│  ├─ builders/
│  ├─ snapshots/
│  └─ cleanup/
├─ rollback/
│  ├─ workspace_restore.py
│  ├─ config_restore.py
│  ├─ generation_gate.py
│  └─ baseline_manager.py
├─ scoring/
│  ├─ metrics.py
│  ├─ aggregation.py
│  └─ stability.py
├─ storage/
│  ├─ db.py
│  ├─ jsonl.py
│  ├─ live_writer.py
│  └─ history_writer.py
├─ reports/
├─ runs/
├─ gui/
│  └─ dashboard.html
└─ scripts/
   ├─ run_single.py
   ├─ run_suite.py
   ├─ run_nightly.py
   ├─ rollback_last_good.py
   └─ summarize.py
```

---

## 19. MVP 實作優先順序

## 第一階段
- Python orchestrator
- TS adapter
- Layer C 動態任務模板
- verifier
- `Stotal` 計算
- JSONL / SQLite storage
- 單檔 HTML live monitor

## 第二階段
- Docker sandbox
- workspace snapshot / restore
- regression suite
- baseline manager
- rollback events log

## 第三階段
- nightly evolution loop
- Pareto selection
- automatic rollback gate
- stability score
- rollback safety score

---

## 20. 成功標準

系統成功不是因為某次 benchmark 很高，而是因為：

1. 新配置在 Layer C 穩定提升
2. Layer B 沒被破壞
3. Layer A 沒出現模型基線異常
4. regression suite 維持或提升
5. rollback 能在退化時自動恢復
6. GUI 能即時反映當前狀況與歷史變化

---

## 21. 最終結論

### 一句話總結
本系統不是做「固定考卷刷分器」，而是建立一個 **能在本地持續驗證、持續進化、持續監看、並在退化時自動回復穩定版本** 的 OpenClaw 評測與進化平台。

### 對你最後問題的明確回答
- **只靠沙盒，不夠。**
- **正式架構必須補上回滾機制。**
- **最穩妥組合是：Sandbox + Rollback + Regression Gate。**
