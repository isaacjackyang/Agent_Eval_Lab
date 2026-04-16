# OpenClaw Local Eval Lab

這個專案把 `OpenClaw_Local_Eval_Lab_Execution_Report_v3.md` 落成一個可本地執行、可追蹤、可進化的評測骨架，現在已經包含：

- 真實 `OpenClaw CLI` adapter
- Layer C 動態任務生成
- 決定性 verifier
- `Stotal` 與 nightly weighted fitness
- mutation candidate pool / baseline gate
- Docker sandbox 設定注入
- workspace snapshot + diff restore
- JSON / JSONL / SQLite / HTML dashboard

## 目前完成

- `scripts/run_single.py`
  執行一次完整本地 run，包含 sandbox workspace、OpenClaw runner、verifier、artifact、history 與 diff restore。
- `scripts/run_suite.py`
  批次跑多次 Layer C 任務。
- `scripts/run_nightly.py`
  產生 mutation candidate pool，對每個候選 config 跑 candidate / regression，最後做 weighted fitness selection 與 baseline gate。
- `scripts/rollback_last_good.py`
  將最新穩定 baseline 的 archived config 回寫到指定 experiment config。
- `scripts/summarize.py`
  快速輸出 baseline / nightly / latest score 摘要。
- `dashboard.html`
  讀取 `runs/` 與 `reports/` 的真實輸出，顯示 run、baseline、rollback、nightly、config lifecycle。

## Runner 與 Sandbox

預設 experiment config `configs/experiments/default_mvp.json` 已經切到真實 `openclaw_cli` runner：

- 會透過 `openclaw agents add`
- 對單次 run 建立暫時的 OpenClaw runtime config
- 將 per-agent Docker sandbox 設定寫進 agent config
- 呼叫 `openclaw sandbox recreate` / `openclaw sandbox explain`
- 再用 `openclaw agent --json` 執行 prompt

本機如果沒有安裝 `OpenClaw CLI` 或 `Docker`，真實 config 不能直接跑。

為了讓這個 repo 在沒有 OpenClaw binary 的機器上仍能驗證整條 adapter 流程，我另外提供：

- `configs/experiments/local_stub_openclaw.json`
- `scripts/fixtures/openclaw_cli_stub.py`

stub 不是新的 mock runner，而是模擬 `OpenClaw CLI` 命令列介面，讓 real adapter code path 可以在本地完整測。

## 快速開始

真實 OpenClaw / Docker 環境：

```powershell
python scripts/run_single.py
python scripts/run_suite.py --runs 5
python scripts/run_nightly.py
python scripts/rollback_last_good.py
python scripts/summarize.py
python scripts/serve_dashboard.py --port 8765
```

本地 stub 驗證：

```powershell
python scripts/run_single.py --config configs/experiments/local_stub_openclaw.json --seed 21
python scripts/run_suite.py --config configs/experiments/local_stub_openclaw.json --runs 2 --seed-start 31
python scripts/run_nightly.py --config configs/experiments/local_stub_openclaw.json --seed-start 900
python scripts/rollback_last_good.py --baseline configs/baselines/local_stub_best_stable_config.json --target configs/experiments/local_stub_restored.json
```

然後在瀏覽器打開：

```text
http://127.0.0.1:8765/dashboard.html
```

Windows 如果想快速把目前 repo 同步到 GitHub，也可以用：

```powershell
.\commit_github.cmd "Update project files"
```

這個腳本現在預設會 `git add -A`、commit、再做一般 `git push`。
如果你真的要覆蓋遠端分支，才額外加 `--force`：

```powershell
.\commit_github.cmd --force "Force sync project files"
```

## Snapshot / Restore

workspace restore 現在不是直接刪目錄，而是：

1. 建立 snapshot manifest 與檔案備份
2. run 後比較目前工作區與 snapshot
3. 還原被修改的檔案
4. 補回被刪掉的檔案
5. 移除 run 新增的檔案

stub 驗證路徑會真的修改 canonical 檔案並新增 scratch 檔，所以 `restore_result` 可以實際看到：

- `restored_modified_files`
- `removed_new_files`
- `restored_missing_files`

## 目前仍未完成

- 真實 OpenClaw CLI 在這台機器上的 live end-to-end 驗證
  因為目前環境查不到 `openclaw` 命令
- 真正由 Docker 提供的 live sandbox container 驗證
  因為目前環境查不到 `docker` 命令
- 多代 evolution / Pareto selection
- workspace snapshot 的 binary / large-file 最佳化
- 更細的 OpenClaw trace schema 對齊

## 目錄

```text
Agent_Eval_Lab/
├─ benchmarks/
├─ configs/
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
