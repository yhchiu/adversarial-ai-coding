# 執行說明

核准後的執行動作只有一件事：把下方「文件內容」（`# adversarial-ai-coding：可續跑的中斷復原 v2` 起）寫入 `C:\Project\adversarial-ai-coding\docs\plans\20260710_resume_run_v2_fable5max.md`（LF 換行），不動其他任何檔案。實作本身依該文件在之後的 session 進行。

---

# adversarial-ai-coding：可續跑的中斷復原 v2（RESUME_RUN）

## Context

v1 計畫（`docs/plans/20260710_resume_run_v1_opus48high.md`）經 GPT-5.6 review（`docs/plans/20260710_resume_run_v1_review_gpt-5.6-sol-ultra.md`）後，逐行核對程式碼確認：C3/C4/C5 是 v1 的真 bug（resume 會刪掉自己需要的狀態、驗收測試保護存在 crash window、dual-spec decision 還原時機晚於使用點會直接 crash），H4/H5 直接牴觸「Ctrl-C 與 reviewer 端 quota 也要能續跑」的需求。v2 吸收這些修正；review 的企業級架構要求（簽章 state、交易狀態機、fsync、receipts）與本工具的信任模型不成比例，明列於「不做」。

目標不變：中斷（quota、網路、Ctrl-C、當機）後用 `RESUME_RUN=<run-id>` 從斷點續跑，已完成 stage 跳過，不重付 AI 費用；所有可捕捉的中止路徑都印出可直接貼上的續跑指令。

### Review 發現的處置對照

| Review 發現 | v2 處置 |
|---|---|
| C1 state 可信任性 | 縮水採納：不 source（data-only 解析）、RESUME_RUN 格式驗證；拒絕簽章/不可寫位置 |
| C2 checkbox 不可靠 + fallback 重做 | 採納：script 持有任務佇列，checkbox 降為 UI；區分「無清單」與「全完成」 |
| C3 init_live_state 刪掉續跑所需狀態 | 全採納：init 拆 fresh/resume 兩種模式 |
| C4 acceptance test_base crash window | 全採納：test_base 持久化 |
| C5 dual-spec decision 還原太晚 | 全採納：finalize-spec 前還原 |
| C6 run identity | 輕量採納：task 快照、不可變欄位驗證、last-head ancestor 檢查；拒絕 repo identity/hash 全套 |
| H1 台帳非交易 | 輕量採納：skip 前 artifact 存在性檢查、temp+mv 原子寫；拒絕狀態機 |
| H2 無鎖 | 輕量採納：mkdir 原子鎖；拒絕 PID/stale takeover |
| H3 last 與 finish 冪等 | 採納：completed 標記、last 略過已完成、gh pr create 前先查 |
| H4 中止路徑覆蓋不全 | 全採納：EXIT/INT/TERM/HUP trap、typed quota abort |
| H5 欄位命名矛盾、清單不全 | 全採納：RESUMED_* 命名空間、補齊欄位、auto-detect 後才寫 state |
| H6 缺 interrupt→resume 整合測試 | 採納：fake-agent 離線整合測試；拒絕全故障矩陣 |

## 保證範圍（寫進 README 的契約）

- 可捕捉中止（engine 失敗、quota、review/gate 輪次用盡、human abort、protected-test 中止、INT/TERM/HUP）：中止時印一次續跑指令並保留原 exit code；resume 從最後完成的 stage 之後繼續。
- SIGKILL、斷電、OS 當機：best effort。狀態為 append-only、失敗方向 fail-safe——最壞情況是多重跑一兩個 stage（多付一點 AI 費用），不會錯誤跳過未完成的工作。
- state 目錄或 worktree 被刪、branch 歷史被 rewrite：fail closed 並給出明確訊息，不做透明恢復。

## 設計

### 狀態目錄 `$WF/state/$RUN_ID/`（`.workflow/` 已整個 gitignore）

| 檔案 | 內容與寫入時機 |
|---|---|
| `resume.conf` | data-only 設定快照；GATE_CMD auto-detect（:1441）之後、第一次 AI 呼叫之前寫出；每次 attempt 啟動時以當時生效值重寫（讓 resume 時的顯式覆寫延續到下一次 resume） |
| `task.txt` | resolved task 文字快照，startup 寫一次 |
| `completed-stages` | stage 台帳，一行一個名稱，append-only |
| `last-head` | 每次 end_stage 後的 HEAD sha |
| `acceptance-test-base` | write-acceptance-tests 進場時記下的 base sha |
| `tasks-remaining` | write-code 的任務佇列，script 持有 |
| `completed` | finish 成功後寫入的空標記 |
| `lock/` | mkdir 原子鎖目錄 |

寫入原則：`resume.conf`/`last-head`/`tasks-remaining` 用同目錄 temp 檔 + `mv` 原子替換；台帳純 append（截斷只會丟行→重跑，方向 fail-safe）。

### resume.conf 格式與解析（取代 v1 的 source resume.env）

- 首行 `schema=1`，之後每行 `key=value`（value 取第一個 `=` 之後的整段原文，不引號、不轉義）。
- 解析器逐行 `read`，以 `case` allowlist 比對 key，賦值到 `RESUMED_*` 命名空間變數。**絕不 source、絕不 eval**——這消除了「把 workspace 檔案內容當 shell 執行」這個 v1 引入的質變點。
- 未知 key、缺 schema 行、schema 不是 1 → 拒絕 resume 並報錯（防截斷檔與未來版本）。
- 寫入端遇到值含換行 → 直接報錯（這些值是路徑與 CLI args，正常不含換行；fail fast 發生在任何 AI 呼叫之前）。

欄位分三類：

- **不可變**（resume 時顯式環境變數與快照不同 → 報錯退出）：`spec_dir`、`dual_spec`、`auto_branch`、`use_worktree`、`branch`。這些決定 stage graph 與產物位置，混用會做出混合 run（C6）。
- **可覆寫**（顯式 env > 快照 > 內建預設）：`engine_a/b`、`engine_a_args/b_args`、`model_a/b`、`claude_args`、`codex_args`、`agy_args`、`max_rounds`、`human_gate`、`open_pr`、`tools`、`gate_cmd`、`build_gate_cmd`。覆寫引擎是本功能的核心用例：codex 配額耗盡 → `AGENT_B=agy RESUME_RUN=<id>` 換引擎續跑。
- **資訊**：`task_arg`、`task_source_kind`、`task_source_path`（只印出，不參與控制）。

刻意不持久化：`NOTIFY_CMD`（環境相關且可執行，resume 時重新提供）、`RETRY_*`（重試政策每次 attempt 重新決定）。

優先序實作（H5 的修正）：

- 引擎欄位把快照值當第三參數 default 傳給既有 `alias_env_or_default`（:46-61）：`ENGINE_A="$(alias_env_or_default AGENT_A ENGINE_A "${RESUMED_ENGINE_A:-claude}")"`。顯式 `AGENT_A`/`ENGINE_A` 自然勝出，且不會觸發 alias conflict 檢查。
- 其餘 `VAR="${VAR:-${RESUMED_VAR:-預設}}"`。沿用既有 `:-` 慣例（set-but-empty 視同未設，與現行 `GATE_CMD` 行為一致）。

### 載入順序（設定區頂端，`WF=".workflow"` 定義上移到此之前）

1. `RESUME_RUN` 有值時：
   - `last` → 解析為 `$WF/state/` 下最新且**無 completed 標記**的目錄；全部已完成或不存在 → 報錯並列出可用 run id。
   - 驗證格式 `^[A-Za-z0-9_-]+$`（擋 path traversal 與 symlink 逃逸的入口）；state 目錄不存在 → 列出可用 run id 後 exit 1。
   - 有 completed 標記 → 報錯「run 已完成，無可續跑」。
   - 讀取並解析 resume.conf；`mkdir "$RUN_STATE_DIR/lock"` 取得鎖（已存在 → 報「run busy；若確認前次已死可 rm 該目錄」exit 1）。全部發生在任何副作用之前。
2. `RUN_ID="${解析後的 RESUME_RUN:-$(date +%Y%m%d-%H%M%S)}"`；fresh run 在 main 內建 state 目錄時用不帶 `-p` 的 `mkdir` 原子宣告（同秒碰撞 → 明確報錯重試），接著同樣建 lock。
3. task 快照（C6）：resume 時一律讀 `task.txt`，不重讀原始檔或字串；resume 又帶了 task 參數且 resolved 內容與快照不同 → 報錯退出；未帶參數 → 直接沿用快照並印出來源資訊。
4. `establish_run_archive` 不動：resume attempt 的歸檔自然落在 `<id>-2`、`<id>-3`。

### Stage 台帳與跳過驗證

```
STAGE_LEDGER=""                     # init 前為空：stage_done 恆假、end_stage no-op（source-based 單測不變）
stage_done <name>                   # 台帳含該行（grep -Fxq）→ 0
begin_stage <name> [artifact...]    # 已完成且必要 artifact 都存在 → 印 "== skip [name]" 並 return 1
                                    # 已完成但 artifact 缺失 → fail closed，訊息指向 $WF_RUN 歸檔位置
                                    # 未完成 → 照舊 return 0
end_stage                           # append CUR_STAGE 進台帳 + 寫 last-head
```

main() 與 run_dual_spec_spec_stage 的每個 stage 區塊改為 `if begin_stage "name" 必要artifact...; then ...原內容...; end_stage; fi`。涵蓋與必要 artifact：

| Stage | 必要 artifact（skip 前檢查存在） |
|---|---|
| write-spec / finalize-spec | `$SPEC_DIR/spec.md` |
| write-spec-a / write-spec-b | `spec-a.md` / `spec-b.md` |
| review-spec-a / review-spec-b | 對應 `*.review-by-*.md` + `*.verdict-by-*.json` |
| compare-specs-a / compare-specs-b | 對應 `spec-comparison-*.md` |
| select-spec | `$SPEC_DIR/spec-decision.md` |
| commit-spec（包住 :1462 的裸 commit_work） | 無 |
| write-implementation-plan | `$SPEC_DIR/plan.md` |
| write-acceptance-tests | `$WF/protected-tests.txt` + `protected-base.sha` |
| write-code（:1500-1525 全段：任務迴圈+全量 gate+branch review+commit_if_dirty） | 無（由任務佇列治理） |
| final-review-and-fixes | 無 |

`write_spec_comparison_index`（:1323）保持在 guard 外：冪等純檔案寫入，不花 AI。finish 不入台帳，靠冪等化（見下）。

### init_live_state 拆分（C3——v1 最大的 bug）

- `init_live_state`（無參數）：維持現行全刪，既有兩條單測（tests/helpers.test.sh:495-508）不動。
- `init_live_state resume`：只刪自癒暫態 `review.md`、`verdict.json`、`last-engine-output.txt`、`pr-body.md`；**保留** `suggestions.md`、`protected-tests.txt`、`protected-base.sha`、`spec-merge-request.md`（後續 stage 依賴：check_protected :912、apply_dual_spec_decision :688-694、final-self-review :1528）。
- main 依是否 resume 選擇模式。

### 跨 stage 還原

- **restore_dual_spec_decision（C5）**：`DUAL_SPEC_DECISION` 為空且 `DUAL_SPEC=1` 時，從 `$SPEC_DIR/spec-decision.md` 讀 `- decision: ` 行，驗證屬 `{adopt-a,adopt-b,merge-a,merge-b}`（重用 `dual_spec_owner_slot` 驗證），設 `DUAL_SPEC_DECISION` 並 `set_spec_roles_from_slot`；decision 為 `merge-*` 而 `$WF/spec-merge-request.md` 缺 → fail closed，訊息指向歸檔副本。呼叫點**兩處**：
  1. `run_dual_spec_spec_stage` 內、select-spec 區塊之後 finalize-spec 之前——local `decision` 改為從還原後的 `DUAL_SPEC_DECISION` 取值（修正 v1「還原點晚於第一次使用」的 crash）。
  2. main() spec 段落之後、commit-spec 之前——覆蓋 finalize-spec 也被跳過的情形。單規格路徑的 `set_spec_roles_from_slot A`（:1452）保持在 guard 外每次執行。
- **acceptance test_base（C4）**：stage 進場改為 restore-or-record——`acceptance-test-base` 存在 → 沿用；否則 `git rev-parse HEAD` 寫入。protected list 計算（:1489-1490）沿用持久化的 base，「commit 後、protected 寫入前中斷 → 重跑時 diff 為空 → 保護靜默關閉」的 crash window 消失。空 protected list 的既有警告行為不變（不動 fresh-run 語義）。
- **write-code 任務佇列（C2）**：進場時 `tasks-remaining` 不存在 → 由 `plan_tasks` 產生寫入（0 筆時沿用現行 whole-plan fallback，fallback 任務同樣寫進佇列）；已存在 → 直接以它驅動迴圈——**空檔案＝全部完成，跳過迴圈直接進全量 gate，不落 fallback**。每個 task 完成順序：work → build gate → commit_work → 從佇列移除該行 → script 用 sed 把 plan.md 對應 `- [ ]` 行標成 `[x]`（worker 已標則 no-op）。checkbox 從此只是 UI，控制流由 script 持有的佇列決定；`implement-plan-task.md` prompt 不變。

### 工作區還原與 identity 驗證（C6 輕量版）

setup_workspace 的 resume 分支：

- 已在 `RESUMED_BRANCH` → 不動；分支存在 → `git switch`；不存在 → 明確報錯。不再 `git switch -c` 或建 worktree。
- `USE_WORKTREE=1`：state 在 worktree 的 `.workflow/` 內，必須在該 worktree 目錄執行 resume；在主 repo 找不到 state 時，錯誤訊息提示可能要 `cd` 進 worktree。README 記載此限制。
- HEAD 檢查：`last-head` 存在時——HEAD 相等 → 乾淨；last-head 是 HEAD 的祖先（`git merge-base --is-ancestor`）→ 警告「檢查點之後有新 commit」後繼續；不可達（branch 被 reset/rebase、錯的 repo）→ **fail closed**，訊息說明刪除 `last-head` 檔可強行續跑。台帳非空但 `last-head` 缺 → fail closed。
- dirty tree：不動它，但印 `git status --short` 摘要與明確警告：「這些變更會被下一次 ensure_committed（:1126 git add -A）吸進 commit」。

### 中止路徑與續跑提示（H4）

- 以單一 **EXIT trap** 取代現行 ERR trap（:1437）：exit code 非零、state 已建立、run 未 completed 時，印 log 路徑 + resume hint（旗標去重只印一次），釋放 lock，保留原 exit code。加 `trap 'exit 130' INT`、`'exit 143' TERM`、`'exit 129' HUP`——**Ctrl-C 也走 EXIT trap 印提示**（v1 完全漏掉；explicit `exit 1` 不觸發 ERR trap 的問題也一併解決：human gate :1150/:1155、protected :918、merge abort :1261/:1264、review/gate 用盡 :1074/:1103 全部自動覆蓋，無需逐點插呼叫）。
- `print_resume_hint`：輸出 `RESUME_RUN=<id> ./adversarial-ai-coding.sh`（task 參數免帶，快照供給）；`USE_WORKTREE=1` 時輸出 `cd <printf %q 的絕對 worktree 路徑> && RESUME_RUN=... <printf %q 的腳本路徑>`，可直接貼上。
- **Typed quota abort**：常數 `QUOTA_ABORT_RC=75`（EX_TEMPFAIL）。`engine_call` 的兩個放棄路徑（RETRY_MAX 用盡 :855-857、reset 過遠 :860-867）以及 `RETRY_ON_LIMIT=0` 且 `is_rate_limited` 為真時，改 `return 75`。`run_review` 不再吞掉 engine 失敗（:1039）：rc==75 → 印中止原因並 `exit 75`（quota 不再偽裝成「reviewer 沒寫 verdict」而觸發整輪無意義的 worker repair）；其他非零維持現行警告行為。worker/gate 路徑的 75 由 errexit → EXIT trap 自然變成帶提示的中止。

### finish 冪等（H3）

- `OPEN_PR=1` 時：`gh pr create` 之前先 `gh pr view --json url` 查當前 branch——已有 PR → 印 URL 並略過 create（避免「PR 已存在 → create 失敗 → run 永遠完成不了」）。`git push` 本身冪等，不動。
- main 尾端（finish 成功後）寫 `completed` 標記。resume 指到 completed run → 報錯「已完成」；`RESUME_RUN=last` 自動略過 completed。

## 修改檔案

- `C:\Project\adversarial-ai-coding\adversarial-ai-coding.sh` — 上述全部；header 環境變數說明加 `RESUME_RUN`
- `C:\Project\adversarial-ai-coding\tests\helpers.test.sh` — 新增單測（沿用既有 tmpdir/new_repo/assert_eq/assert_like harness 與 `source "$SCRIPT"` 子 shell 模式）
- `C:\Project\adversarial-ai-coding\tests\resume.test.sh` — 新增：離線 interrupt→resume 整合測試（fake agents，不呼叫真實 AI、不花配額）
- `C:\Project\adversarial-ai-coding\.github\workflows\ci.yml` — 接上 resume suite；新增 windows-latest job（Git Bash，runner 內建 jq/go，跑 helpers + resume suite——Arthur 的實際環境是 Windows Git Bash，目前 CI 只有 Ubuntu）
- `C:\Project\adversarial-ai-coding\README.md` / `README.zh-TW.md` — Configuration 表加 `RESUME_RUN`；新增「Resuming an interrupted run / 中斷後續跑」小節，含保證範圍表、worktree 限制、斷電 best-effort、state 被刪不支援

`resources/prompts/implement-plan-task.md` 不修改（worker 打勾保留為 UI 行為）。

## Commit 計畫（每個 commit：`bash -n` 四個 shell 檔 + LF 檢查 + 完整單測全綠才提交）

1. `feat: load and validate resume state`
   - state 目錄與 lock、RESUME_RUN 格式驗證與 `last` 解析、resume.conf 讀寫器（schema/allowlist/原子寫/換行拒絕）、RESUMED_* 優先序注入（含 alias default）、task 快照與比對、不可變欄位驗證、completed 檢查、fresh run 的原子 state 建立。
   - 單測：conf 往返（含空白與引號值）；未知 key / 缺 schema / schema=2 拒絕；`RESUME_RUN=../../x` 拒絕；`last` 略過 completed；顯式 env 覆寫快照；不可變欄位衝突報錯；`AGENT_A` 覆寫不觸發 alias conflict；task 參數與快照不符報錯；lock busy。
2. `feat: skip completed stages with a run ledger`
   - stage_done / begin_stage（含 artifact 存在性檢查）/ end_stage、last-head 寫入與 ancestor 驗證、init_live_state 拆 fresh/resume、setup_workspace 續跑分支與 dirty 警告、main 與 run_dual_spec_spec_stage 全部 stage 加 guard。
   - 單測：台帳往返；skip 輸出含 run id；artifact 缺失 fail closed；`STAGE_LEDGER` 為空時行為不變（回歸保護）；init resume 模式保留四個 durable 檔（fresh 模式既有測試不動）；HEAD 不可達 fail closed、祖先關係只警告。
3. `feat: restore cross-stage state on resume`
   - restore_dual_spec_decision + 兩個呼叫點、merge request 存在檢查、acceptance-test-base 持久化（restore-or-record）、tasks-remaining 佇列 + script 打勾 + 空佇列不落 fallback。
   - 單測：spec-decision.md 為 adopt-b/merge-b 時 owner 還原成 ENGINE_B；merge 缺 spec-merge-request.md 報錯；base 沿用既有值；佇列建立/消長/空佇列跳過迴圈；plan.md 無清單仍走 fallback（回歸）。
4. `feat: report resumable aborts and finish idempotently`
   - EXIT/INT/TERM/HUP trap（取代 ERR trap）、print_resume_hint（worktree 引號路徑）、QUOTA_ABORT_RC 與 run_review 傳播、finish 的 pr-view 檢查、completed 標記。
   - 單測：hint 內容含 `RESUME_RUN=<id>`；exit code 保留；engine_call 兩個放棄路徑回傳 75；run_review 收到 75 直接中止而非進 repair；gh stub 下「PR 已存在 → 略過 create」。
5. `test: add offline interrupt-resume integration suite`
   - `tests/resume.test.sh`：臨時 git repo + 兩個放在 PATH 的 fake agent bash script（依 prompt 檔關鍵字寫 spec/plan/verdict/執行 git commit，所有呼叫記帳到 calls.log；可注入 quota 訊息或 exit 1 模擬中斷）。場景：
     - quota 中止 → resume 跑完，**已完成 stage 的 fake-agent 呼叫數為 0**
     - 刪台帳末行模擬「commit 後、記帳前」中斷 → 該 stage 重跑（at-least-once，不跳過）
     - acceptance crash window：刪台帳行 + 刪 `$WF/protected-*` → 重跑後 protected list 非空（base 沿用）
     - write-code 全部完成後中斷 → resume 不落 whole-plan fallback、不重呼叫 worker
     - `kill -INT` → hint + exit 130 → resume 成功
     - `RESUME_RUN=last` 略過 completed；nonexistent → exit 1 列出可用 id；壞 state（未知 key、截斷）拒絕
     - dual-spec「select-spec 完成、finalize-spec 未完成」還原：用 `script(1)` 提供 pty 跑完整流程，無 `script` 的環境（Windows 本機）跳過，該邏輯已由 commit 3 的單測覆蓋
   - ci.yml 接上 resume suite + 新增 windows job。
6. `docs: document resuming an interrupted run`
   - 兩份 README：RESUME_RUN 環境變數、續跑小節、保證範圍契約（可捕捉中止／best-effort／不支援三級）。

## 驗證

1. `bash -n adversarial-ai-coding.sh tests/helpers.test.sh tests/resume.test.sh tests/e2e/run.sh` + LF 檢查
2. `bash tests/helpers.test.sh` 全綠（現有 115 + 新增約 30）
3. `bash tests/resume.test.sh` 全綠（離線、無 AI、無配額消耗，CI 可重複執行）
4. Windows Git Bash 本機跑 2 與 3（Arthur 的實際使用環境）；CI 的 windows job 作持續防護
5. `E2E_SETUP_ONLY=1 bash tests/e2e/run.sh` 維持綠（回歸）
6. 真實 AI E2E 保留為手動 rollout gate：codex 週配額 2026-07-14 19:23 重置後，用真實中斷（Ctrl-C 與 quota）各驗一次續跑；不阻塞本次落地

## 不做（有意識拒絕的 review 要求）

- **state 簽章／放到 agent 不可寫的位置**：worker 本就有任意程式碼執行能力（gate 執行 worker 剛寫的 `go test`（:1064 `bash -c`）、agy 帶 `--dangerously-skip-permissions`（:793）），今天就能刪 `.workflow/protected-tests.txt` 靜默解除保護（check_protected :912 對缺檔 return 0）。防 agent 竄改 state 不改變實際安全邊界；v2 以 data-only 解析消除「執行 workspace 檔案內容」的質變點即止。
- **完整交易狀態機（pending→validated→committed→complete + needs_reconciliation）與 fsync/斷電語義**：單機工具，torn write 的方向 fail-safe（台帳丟行→重跑），代價是重付一個 stage 的 AI 費用；README 明列 best effort。
- **push/PR receipts 與通知去重**：pr-view 檢查 + completed 標記已覆蓋實際風險；通知本就 at-least-once。
- **跨 attempt 的 RETRY_MAX/MAX_ROUNDS 持久預算**：resume 由使用者手動觸發，重置預算正是使用者的意思表示。
- **repo identity／git common dir／逐 stage artifact hash**：last-head ancestor 檢查 + skip 前 artifact 存在性檢查已擋住現實場景（branch reset、state 半殘）。
- **schema migration**：v2 之前不存在有效 state；schema≠1 一律拒絕，無升降版。
- **PID／stale-lock takeover**：stale lock 由使用者依錯誤訊息手動 `rm`。
- **RESUME_FROM=<stage>、worktree 刪除後的復原**：沿 v1 不做。
