# Session ID Resume 計畫 Review

- Review 對象：`docs/plans/20260712_session_id_resume_opus48xhigh.md`
- Review 日期：2026-07-12
- 對照版本：`e4cb463`
- 判定：**Request changes；方向正確，但目前不應照原計畫直接實作**

## 結論

用明確的 Codex thread ID／agy conversation ID 取代全域的 `--last`／`--continue`，並把 agent 身分從名稱改成 A/B slot，是正確方向。本機 `codex-cli 0.144.1` 的 help 也確認：`codex exec resume` 接受 UUID 或 thread name、支援 `--json` 與 `-c`，而 resume 子命令沒有 `--sandbox`，所以原計畫的 Codex argv 方向成立。本環境沒有可執行的 agy，因此 agy 的 flag、log 與 stdin 行為仍須由計畫中的真實 smoke test 證實。

目前仍有四個會破壞核心保證的問題：已知 UUID 可能被降級掉、Commit 2 過早放行同名 agy、slot 在 archive 邊界被轉回名稱，以及 agy 固定 log 的生命週期沒有被實作成可靠 invariant。這些問題會造成 context 遺失、接錯 session，或讓 B slot 的 model／args metadata 靜默記成 A slot。

建議保留原本四個 commit 的大方向，但先補上明確的 session state machine、讓 validation relaxation 與各 adapter 同 commit 落地，並把 `AgentRef` 傳到所有需要解析 model／args 的 archive API。

## Blocking findings

### C1. 抓不到「本輪輸出的 ID」時，不能丟掉呼叫前已知的 UUID

原計畫把「事件或 log 沒抓到 ID」直接映射成 `last`／`continue` 或空字串（原計畫 `:22,57,73`），但沒有區分兩種完全不同的情況：

1. fresh call 原本沒有 ID，這次也沒抓到 ID；
2. 本來已用已知 UUID 成功定址 resume，只是這次輸出沒有再重複該 ID。

第二種情況不能降級。若 Codex 某版只在 fresh call 發出 `thread.started`，或 agy resumed run 的 log 沒有 conversation 行，原計畫會把仍然有效的 UUID 清掉：A/B 同名時下一輪另開 session；A/B 不同時則退回全域 `--last`／`--continue`。前者無故失去 context，後者甚至可能接到其他 process 或使用者剛建立的 session。

計畫應加入明確的 transition table：

| 呼叫前狀態 | 本輪使用方式 | 本輪解析結果 | 呼叫後狀態 |
|---|---|---|---|
| `""` | fresh | valid ID | 新 ID |
| `""` | fresh | 無 ID，A==B | `""`，警告；下輪仍 fresh |
| `""` | fresh | 無 ID，A!=B | legacy sentinel，或更安全地維持 fresh |
| UUID | resume by UUID | valid ID | 解析到的 ID |
| UUID | resume by UUID | 無 ID | **保留原 UUID** |
| sentinel | legacy resume | valid ID | 升級成 ID |
| sentinel | legacy resume | 無 ID | 保留原 sentinel |

agy 更應只在 fresh call 解析新 conversation ID；若這次已傳入 `--conversation <uuid>`，沒有新 log match 時應保留傳入的 UUID，而不是重新推測 session。

必須新增下列測試：

- Codex／agy：呼叫前已有 UUID、本輪無 ID，A==B 與 A!=B 都保留 UUID。
- fresh call 無 ID、sentinel 無 ID、解析到新 ID三條獨立路徑。
- 第一次 rate-limit failure 已取得 ID，retry 必須用該 ID；第二次輸出缺 ID 仍不得降級。
- 若仍保留 A!=B 的 legacy sentinel，文件需明列它只維持舊相容性，不能保證抵抗其他 process 的「最近 session」競態。

### C2. Commit 2 在 agy ID resume 尚未存在時就放行同名 agy

Commit 2 要把 `validate_agents` 改成只擋 custom agent，明確同時放行 claude／codex／agy（原計畫 `:59`）；agy 的 conversation ID 實作卻要到 Commit 3（`:68-80`）才加入。

因此 Commit 2 單獨 checkout 時，`AGENT_A=agy AGENT_B=agy` 已通過 preflight，但 worker 仍只會使用全域 `--continue`。reviewer 開新 session 後，worker 仍可能接錯 reviewer session。這違反「每個 commit 都可安全執行、測試、bisect」的要求。

應改成：

- Commit 2 只放行同名 Codex；同名 agy 仍拒絕。
- Commit 3 完成 agy ID capture、fallback 與測試後，才放行同名 agy。
- 或合併 Codex／agy adapter 與 validation relaxation；但不建議，因為 commit 會過大。

Claude 已有明確 `--resume <session_id>`，現況本來就允許同名，不需改變。

### C3. `AgentRef` 在 archive 邊界被降回 `ref.name`，B slot metadata 仍會錯

原計畫一方面說 `work()` 的 log／archive 使用 `ref.name`（`:35`），另一方面又要求 archive 中的 model／args 依 slot 解析（`:38`）。兩者互相矛盾。

目前下列 API 都只接收 agent 名稱，再呼叫 `agent_model(agent, settings)`：

- `write_meta`：`archive.py:109-130`
- `archive_snapshot`／`archive_text`：`archive.py:132-162`
- `archive_agent_attempt`：`archive.py:178-197`
- `log_section`：`archive.py:245-268`
- `metric`：`archive.py:270-300`
- `archive_git_state`：`archive.py:302-347`

若 A/B 都是 `codex`，只把 `ref.name == "codex"` 傳進去，任何名稱式 lookup 仍會先命中 A；B slot 的 prompt、output、attempt、git state、log banner 與 metrics 都可能記成 `MODEL_A`。原計畫的 smoke test 又打算靠這些 meta 驗證實際模型（`:103`），因此會產生循環且可能錯誤的證據。

應讓所有 agent 產生的 archive／log／metric API 接收 `AgentRef`，並只在序列化時寫 `ref.name`。workflow 自己產生的 artifact 則使用 `ref=None` 或獨立的 workflow identity；不要把字串 `"workflow"` 偽裝成 `AgentRef`。

同一 refactor 還要修正人類可讀輸出：

- `workflow.py:450-451` 會把 `SpecRoles.owner_agent`／`reviewer_agent` 插入 PR body。
- `dual_spec.py:219` 會把 `agent_for_slot()` 結果寫入 durable decision file。
- `dual_spec.py:331-334` 會把 role 寫入 restore 訊息。

若欄位或 `agent_for_slot()` 改回傳 `AgentRef`，這些位置必須明確使用 `.name`，否則會輸出 `AgentRef(slot='A', name='codex')`。原計畫還提到不存在的 `review_once`，且漏列 `tests/test_stageflow.py` 的字串斷言；應以 `rg` 的完整 call-site inventory 取代手寫的舊行號清單。

至少新增以下同名 agent regression tests：

- run metadata 同時保留不同的 `model_a`／`model_b`。
- B slot 的 artifact meta、attempt meta、log banner、metrics row 都使用 `MODEL_B`。
- B 作 worker、A 作 reviewer 的反向角色也正確；不能只測一般的 A-worker/B-reviewer。
- PR body 與 dual-spec decision file 仍只顯示 agent 名稱，不洩漏 dataclass repr。

### C4. `last-agy.log` 的「每次覆寫」沒有實作保證，第一個 match 可能是舊 session

原計畫指定固定路徑 `wf / "last-agy.log"` 並宣稱每次呼叫覆寫（`:71`），接著又要求 parser 取第一個 regex match（`:72`），但沒有任何步驟在啟動 subprocess 前 truncate／unlink 該檔案，也沒有證明 agy 會以 truncate mode 開檔。

只要 CLI 採 append mode，第二次 fresh call 的 log 就同時含舊、新兩個 conversation ID；「取第一個」會永久拿到舊 ID。retry、crash 留下的 partial log 也有同樣風險。

建議改成每個 attempt 唯一的 log path，或至少在每次 invocation 前 `unlink(missing_ok=True)`，並在結束後：

- 只解析本次建立的檔案；
- 使用嚴格 UUID parser，而不是 `[0-9a-fA-F-]{36}`；
- fresh call 才從 log 建立新 ID，resume call 缺 match 時保留原 ID；
- 把本次 agy log archive 到 attempt artifact，否則下一次覆寫後無法診斷 parser failure；
- 明確禁止或驗證 `AGY_ARGS` 中會奪走 session ownership 的 `--continue`、`--conversation`、`--log-file`。否則 A==B 的安全 fallback 可被共用 args 靜默繞過。

測試需包含預先存在的 stale ID、同檔兩個 ID、partial UUID、大小寫、檔案不存在、resume log 無 ID，以及 retry 後 log 生命週期。

## High-severity findings

### H1. Codex raw JSONL、quota 偵測與人類可讀輸出不應共用同一個 render stream

原計畫要求未知事件「原樣」保留，細節卻又說重新輸出 compact JSON（`:21,51`），並把同一結果同時 echo 到終端、寫入 `agent_out`、回傳成 `AgentResult.text`（`:52`）。這有兩個問題：

- compact re-serialization 不是原樣；key order、空白、Unicode escape 與字串 escape 都可能改變。
- Codex JSONL 除了 `agent_message` 還會有其他正常事件。全部 echo／回傳會讓終端、run log 與 output artifact 充滿 telemetry JSON，與「自行渲染成可讀輸出」的目標衝突。

建議分成兩條 channel：

1. raw capture：把 subprocess 的原始 line 完整寫入 `last-agent-output.txt`，供 quota regex、debug 與 attempt archive 使用；
2. rendered output：只把 agent message 與需要人類看到的 error／warning echo，並作為 `AgentResult.text`。

如果 rate-limit event 需要額外抽出 message，可在不破壞 raw capture 的前提下將 decoded message 加入 rendered error。測試 fixture 應直接保存一次真實 `codex exec --json` 的完整、去敏輸出，覆蓋 stderr 純文字、malformed JSON、未知事件、含 escaped newline 的 quota 訊息，而不是只手寫一個理想化 `error` event。

### H2. 驗證計畫沒有可重複執行地覆蓋新 adapter path

原計畫承認 `tests/e2e/` 只走 fake custom agent（`:98`），因此完整 E2E 綠燈只能證明既有 custom path 沒回歸，不能證明 Codex JSON parser、agy log、session state transition 或相同名稱的 slot routing。

不必使用付費模型也能加一層 offline integration test：在臨時 PATH 放名為 `codex`／`agy` 的 fake executable，讓它記錄 argv、輸出 JSONL 或建立 log，然後透過真正的 `run_worker`／subprocess runner 執行兩輪。這可驗證：

- fresh -> reviewer fresh -> worker resume by exact ID 的完整序列；
- stdout／stderr merge、raw capture、rendered output；
- stale agy log 不會污染下一輪；
- A/B 同名但 model 不同時，argv 與所有 archive metadata 皆按 slot；
- missing-ID 與 rate-limit retry 的 state transition。

手動 smoke 仍應保留，但 `.meta.json` 只是由 settings 推導出的期望值，不能單獨證明 CLI 實際採用了該模型。真實 smoke 應保存實際 argv／CLI trace，並用「第一輪記住隨機 token、reviewer 插入中間 session、第二輪回答 token」驗證 context 沒串線。

agy smoke 還應確認 subprocess 的 stdin 行為不會讓 `--print` 卡住；若需要 headless mode，runner 應明確使用 `stdin=subprocess.DEVNULL`，而不是依賴呼叫端剛好沒有 TTY。

### H3. Verification 指令不符合本 repo 的 Windows／uv 規則

原計畫用 `powershell` code block 執行 `uv`，只在註解提醒清除環境變數（`:93-97`）。本次提供的 `AGENTS.md` 規則要求 Windows 上用 Git Bash 執行 `uv`，並先清除 `PYTHONHOME`／`PYTHONPATH`。

應改成可直接貼上的命令：

```bash
unset PYTHONHOME PYTHONPATH
uv run --locked pytest tests/test_agents.py tests/test_archive_io.py tests/test_archive_git.py tests/test_work.py tests/test_review.py tests/test_dual_spec.py tests/test_stageflow.py -q -p no:cacheprovider
uv run --locked pytest -q -p no:cacheprovider
```

每個 commit 都應先跑該 commit 的 targeted tests，再跑完整 suite，通過後才 commit。Commit 2 與 Commit 3 另外各跑自己的 offline fake-CLI integration test。

## 建議的修訂後 commit 切分

1. `refactor(agents): resolve model and args by slot`
   - 引入經驗證的 `AgentRef`／slot helper。
   - 完整傳遞到 worker、reviewer、retry、archive、metrics、run metadata 與 dual-spec。
   - 加同名 Claude／Codex 的雙向 slot metadata regression tests。
   - 不改 Codex／agy validation。
2. `feat(codex): resume workers by captured thread id`
   - 實作 raw capture 與 rendered output 分流。
   - 實作明確的 session transition table，已知 UUID 不降級。
   - 只在本 commit 放行同名 Codex。
   - 加 unit fixture、offline fake-Codex integration 與 quota tests。
3. `feat(agy): resume workers by captured conversation id`
   - 加 per-attempt log lifecycle、嚴格 UUID parse、log archive 與 known-ID retention。
   - 處理 reserved args 與 headless stdin。
   - 測試完成後才放行同名 agy。
4. `docs: document same-agent slots and session guarantees`
   - 更新中英文 README 與 parity 文件。
   - 明列 ID capture 失敗時的精確 transition、legacy sentinel 的剩餘風險、agy log schema 相容性與支援的 args。

## 建議 acceptance criteria

1. A/B 同名時，任何 worker resume 都只能使用該 worker 已保存的 exact ID；reviewer 或外部最近 session 不影響它。
2. 一旦取得 exact ID，後續缺少 `thread.started`／log match 不得讓 state 降級成 sentinel 或空值。
3. fresh call 抓不到 ID 時，A/B 同名永遠不使用全域 `--last`／`--continue`。
4. A/B 同名、model 不同時，實際 argv、artifact meta、attempt meta、log、metrics、run metadata 都按 slot 正確；A/B 角色交換也成立。
5. agy 每個 attempt 只解析本次 log；stale／partial／多 ID log 不會選錯 conversation，且原始 log 可供 archive 診斷。
6. Commit 2 單獨 checkout 時同名 agy 仍被拒；Commit 3 完成後才放行。
7. Codex raw JSONL 完整保留供 quota／debug 使用，終端與 `AgentResult.text` 維持可讀；真實 quota fixture 的 `is_rate_limited()` 與 `parse_reset_wait()` 都通過。
8. targeted tests、offline adapter integration、完整 pytest 依序通過後才建立各 commit；真實 Codex／agy smoke 作為 rollout gate。

## 計畫中值得保留的部分

- 先做 slot-aware refactor，再解除同名 agent 限制，依賴順序正確。
- reviewer 永遠開新 session、只有 worker 保存 resume state，模型簡單且符合現有 workflow。
- Codex resume 使用 `-c sandbox_mode="workspace-write"`，與本機 CLI help 顯示 resume 無 `--sandbox` 相符。
- custom agent 維持同名拒絕，避免對未知 wrapper 的 session semantics 作不安全假設。
- 把真實 CLI smoke test 列為必要 rollout gate 是對的；補上 offline integration 後，CI 與真實行為驗證可各自負責不同風險。

總結而言，原計畫不需要推翻；先把「已知 ID 不降級」、「validation 與 adapter 原子落地」、「slot identity 不在 archive 邊界遺失」及「agy log 每次呼叫隔離」寫成不可違反的 invariants，再進入實作即可。
