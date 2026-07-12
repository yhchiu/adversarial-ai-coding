# Custom Implementation Agent v1 計畫 Review

- Review 對象：`docs/plans/20260712_custom_impl_agent_v1_opus48xhigh.md`
- Review 日期：2026-07-12
- 對照版本：`99a47e0`（功能程式碼基線為 `e05cbe7`）
- 基線驗證：相關測試 `137 passed in 185.79s`
- 判定：**Request changes；第三個 slot 的方向正確，但目前不應照原計畫直接實作**

## 結論

把 per-task 實作迴圈抽成 `AgentRef(slot="I", ...)` 是合適的最小設計。現有 archive、metrics 與 model lookup 已經以 `AgentRef` 為邊界，`base_slot` 也能正確表達「同 CLI 才繼承 owner model」；把 session 換手守衛放在 `run_worker()`，並先於 workflow routing 落地，依賴順序同樣正確。

不過原計畫仍有三個會破壞核心承諾的 blocker：

1. `IMPL_ARGS` 只被接到 metadata helper，沒有接到三個內建 agent 的實際 argv；畫面與 archive 會顯示已套用，CLI 卻完全收不到。
2. 計畫無條件允許 implementation slot 與 A/B 使用同名 custom command，但 custom adapter 不受 `AgentSession.owner` 控制；owner full gate 與 branch reviewer 仍會在迴圈前後呼叫同一 wrapper，正好重現現有 validation 要避免的未知 session 競態。
3. `IMPL_ARGS` 一律套用 Codex ∪ agy 保留字，既會誤擋 custom agent 的合法參數，又漏掉 Claude 的 session／輸出控制旗標；`-c` 在 Codex 是 config，在 Claude 卻是 continue，證明這項驗證不能脫離實際 adapter。

此外，計畫目前沒有真的測到最常見的「同一個內建 CLI 降模型／reasoning effort」路徑；resume 也只能用非空值覆寫，不能清除已 snapshot 的 `IMPL_*`。建議先修正下列 findings，再開始實作。

## Blocking findings

### C1. `resolve_model_args()` 不在內建 agent 的 argv 路徑上

原計畫要求 slot `I` 的 `resolve_model_args()` 回傳內建 agent 原有 args 再加 `IMPL_ARGS`（原計畫 `:119-122`），並以該 helper 與 archive 測試驗證功能（`:191-204`）。但目前 call graph 是：

- `resolve_model_args()` 只被 `archive.py` 的 meta、run metadata、log banner 與 metrics 呼叫（`archive.py:124,224,227,247,250,274,309`）。
- Claude 實際 argv 由 `_claude_common_args()` 直接 split `settings.claude_args`（`agents.py:321-327`）。
- Codex 實際 argv 由 `_codex_model_args()` 直接 split `settings.codex_args`（`agents.py:431-437`）。
- agy 實際 argv 由 `_agy_model_args()` 直接 split `settings.agy_args`（`agents.py:504-510`）。
- 只有 custom path 的 `_run_generic()` 會使用 `generic_agent_args()`（`agents.py:601-612`）。

因此照計畫列出的修改點直譯後，`IMPL_MODEL` 會因 `agent_model()` 已在 adapter helper 中被呼叫而生效，但內建 agent 的 `IMPL_ARGS` 不會進 argv。更糟的是，metrics／`.meta.json` 會記錄 `CODEX_ARGS + IMPL_ARGS`，形成「觀測資料顯示已生效，實際呼叫沒有生效」的假證據。原計畫的 helper test、archive test 與 custom fake-agent E2E 都可能全綠而漏掉這個錯誤。

計畫必須明列：

- 建立單一的「依 ref 解析實際額外 argv」helper，或讓 `_claude_common_args()`、`_codex_model_args()`、`_agy_model_args()` 在 slot `I` 時各自 append `_split_cli_args("IMPL_ARGS", settings.impl_args)`。
- base args 與 `IMPL_ARGS` 要分別 `shlex.split`，保留正確的變數名錯誤訊息，不能只串接兩段 raw string 後再解析。
- metadata 的 rendered args 與實際 argv 必須共用同一份解析來源，避免再次漂移。
- 新增實際 argv 斷言：Claude fresh/resume、Codex fresh/resume、agy fresh/resume，以及 custom agent；不能只測 `resolve_model_args()` 的回傳字串。

### C2. 同名 custom implementation agent 仍有未知 session 競態

原計畫明確允許 `IMPL_AGENT` 與 A 或 B 同名，理由是 per-task 迴圈內沒有 reviewer 交錯（`:69-70`），並要求 `validate_agents()` 不做同名檢查（`:131-132`）。這個理由忽略了迴圈前後的實際 call sequence：

1. implementation custom wrapper 執行 implement、build-gate repair、commit；
2. 迴圈結束後，owner 會在同一個 `write-code` stage 執行 full-gate repair（`workflow.py:729-742`）；
3. branch review 隨後可能以 B custom wrapper review，並以 owner custom wrapper repair（`workflow.py:743-758`）。

如果 I 與 A 或 B 是同一個 custom command，該 wrapper 仍會跨 slot 被連續呼叫。現有程式刻意禁止 A/B 同名 custom command，正是因為 workflow 不知道 wrapper 是否會隱式使用 last／continue session（`agents.py:95-108`）。新守衛只能清除 Python 端的 `session.worker_session`；`_run_generic()` 的 signature 根本沒有 `AgentSession`，無法清除 wrapper 自己的全域 session 或 profile state。

這會讓 cheap-slot 的 context／model profile 流入 owner full gate，或讓 branch reviewer 接到 implementation session，違反計畫要求的 session 隔離。應改成：

- 只允許 workflow 已能以 exact ID 控制的內建 agent 在 I/A/B 間同名。
- distinct slot 的 custom command 若與 A 或 B 同名，維持拒絕並要求不同 wrapper command name；三個 `IMPL_*` 全空、`impl_ref()` 直接回傳 owner ref 的既有 custom 路徑則不應誤擋。
- 若未來要允許同名 custom command，需先新增明確的 stateless/session capability 契約或 opt-in，不能從 command name 猜測。
- 測試需分別覆蓋：同名 built-in 允許、同名 custom distinct slot 拒絕、不同名 custom 允許、全空設定回傳原 owner ref。

### C3. `IMPL_ARGS` 的保留參數驗證必須依 effective adapter，而非固定聯集

原計畫因 dual-spec owner 尚未決定，要求所有 `IMPL_ARGS` 一律用 Codex ∪ agy 的保留字集合驗證（`:125-130`）。這同時造成安全缺口與相容性破壞：

- 明確設定 `IMPL_AGENT=<custom>` 時，agent 其實已知，卻仍會把 custom command 自己合法使用的 `resume`、`--conversation` 或 `--json` 當成 workflow 基礎設施旗標而拒絕，與「沿用現有 custom agent 契約」矛盾。
- `IMPL_AGENT=claude` 或 owner 最後是 Claude 時，固定聯集沒有封鎖 Claude 的 `-c/--continue`、`-r/--resume`、`--session-id`、`--fork-session`、`--no-session-persistence`。本機 Claude Code 2.1.207 的 help 明確列出這些旗標；任何一個都可能繞過 `AgentSession.owner` 的隔離。
- Claude adapter 固定要求 `--output-format json` 並以 JSON parser 讀結果（`agents.py:337-375`）；`IMPL_ARGS='--output-format text'` 也能讓呼叫與 parser 契約失配。
- `-c` 對 Codex 是合法的 config 入口，對 Claude 卻是「繼續最近 session」，因此固定 token 聯集無法正確判斷同一個字串。

建議改成兩階段驗證：

1. startup 一律對 `IMPL_ARGS` 做獨立 `shlex.split`，先抓 quoting 錯誤。
2. 若 `IMPL_AGENT` 明確指定，依該 adapter 驗證；若未指定且 `DUAL_SPEC=0`，依 A 驗證；若 `DUAL_SPEC=1`，對 A/B 中所有可能成為 owner 的內建 adapter 套用相應限制。明確 custom agent 不套用內建保留字。
3. dual-spec 做出選擇並建立 `impl_ref()` 後，再以 resolved ref 做一次便宜的 invariant check，防止 startup candidate logic 與 runtime resolution 漂移。
4. 把每個內建 adapter 自己擁有的 session、輸出格式、sandbox/log 旗標列成獨立規則；同一 helper 最好也套用到既有的 `CLAUDE_ARGS`／`CODEX_ARGS`／`AGY_ARGS`，或至少明確記錄為何政策不同。

測試必須證明 custom agent 可以使用碰巧同名的旗標、Claude 的 session/output flags 會被擋、Codex 的 reasoning `-c` 仍可用，以及 agy 的 log/conversation flags 仍被擋。

## High-severity findings

### H1. 測試組合可以全綠，卻沒有證明主要的 built-in 降級路徑

原計畫的端到端 scenario 使用 `fake-impl` custom wrapper（`:205-210`）。custom agent 按設計忽略 `IMPL_MODEL`，也不走 Claude/Codex/agy 的 model、args 或 session argv builder；所以它只能證明 workflow routing，不能證明標題所承諾的「同 CLI 降模型」或「Claude 規劃、Codex 實作」。原計畫的 `test_archive_io` 又只證明 settings 可以被序列化，不能證明 CLI 收到相同值。

此外，scenario 的 `BUILD_GATE_CMD` 與 `GATE_CMD` 若都直接成功，branch review 也直接 approve，便不會實際呼叫三個關鍵 repair closure。僅計數兩次 implement／commit，無法證明 build-gate repair 留在 I、full-gate repair 回到 owner、branch-review repair 仍由 owner 執行。

至少補上：

- adapter-level argv tests：三個內建 agent 都驗證 effective model、base args、`IMPL_ARGS` 的順序，以及 fresh/resume 的 session flags。
- stageflow test 主動呼叫 patched gate/review closure，分別斷言 build repair 使用 I、full gate 與 branch repair 使用 owner。
- 保留 custom fake-agent E2E 作 routing 證明，但另加一個 offline fake built-in（優先 Codex）integration，記錄實際 argv，證明 `IMPL_MODEL`／reasoning args 與 ref 換手。
- rate-limit retry 仍在相同 I ref 上 resume exact ID，而 I → owner 及 owner → I 都不得攜帶前一個 ref 的 ID。

### H2. 「resume 時可以改」沒有定義如何清除 snapshot 值

原計畫說三個 key 不列入 `IMMUTABLE_KEYS`，因此 resume 時可以更換實作模型（`:109-112`）。非空值確實能覆寫，但 `Settings.from_env()` 的 `persisted()` 是 `env.get(key) or snap.get(key) or default`（`config.py:83-84`）；空環境變數會回退 snapshot，且現有 `test_empty_env_values_fall_back_like_bash` 已固定這項行為。

結果是：run 一旦 snapshot 了 `IMPL_AGENT`／`IMPL_MODEL`／`IMPL_ARGS`，使用者無法用 `IMPL_ARGS=` 清除壞掉或已不需要的參數，也無法把 `IMPL_AGENT` 恢復成「跟隨 owner」。這在 quota abort、CLI 升級後某參數失效，或 cheap model 不可用時，會直接降低 resume 的可恢復性。

計畫應選擇並測試一個明確政策：讓 `IMPL_*` 以「key 是否存在」而非 truthiness 決定 env override、提供明確的 clear sentinel，或清楚承認不能清除並提供安全操作。另需新增 mid-queue integration：第一個 task 完成後中止，resume 時更換／清除 implementation 設定，只讓剩餘 task 使用新 ref，已完成 task 不重跑。

### H3. requested config、effective ref 與可觀測資料目前混在一起

`cli.py` 的 startup settings 在 workflow 開始前就印出（`cli.py:158-162`），此時 dual-spec 尚未選出 owner；原計畫卻要求顯示 `IMPL=<agent>/<model>`（`:167-169`）。當只設 `IMPL_MODEL`，或 A/B 使用同一 CLI 但不同 model 時，effective agent、繼承 model 與 `base_slot` 都要等 human dual-spec decision 才能確定，因此這個字串目前沒有唯一正確值。

同樣地，計畫要在 `run-metadata.json` 寫 raw `impl_*` 即可（`:171-174`），但 per-call `.meta.json`、log banner 與 metrics 只序列化 `agent.name`、model、args，沒有 slot。若使用者明確設 `IMPL_AGENT` 等於 owner，且 effective model/args 也相同，archive 無法分辨該列是 owner slot 還是 I slot；「第三個 slot」只存在記憶體內，無法被稽核。

應把兩種資料分開：

- startup／run metadata 明確標為 requested config，例如 `impl_agent_requested`、`impl_model_requested`、`impl_args_requested`；未知繼承值顯示 `<owner>`／`<inherit>`，不能假裝已 resolve。
- dual-spec 決定後、進入 `write-code` 前，記錄 resolved `slot/name/model/model_args/base_slot`，並輸出一次 resolved settings line。
- agent artifact meta、log banner 與 metrics 加入 `agent_slot`（metrics 可在尾端新增欄位並更新 schema tests），才能在同名同 model 時仍直接證明 routing。
- custom `IMPL_MODEL` 被忽略的警告應寫入 durable run log；resolved metadata 的 model 必須是空字串，不能只留下 raw request 造成誤解。

### H4. Commit 6 混入程式行為，verification 也漏了本 repo 的 Windows/uv 規則

原計畫把 `cli.py` startup output 的修改放在 `docs: document the implementation agent slot`（`:229-230`）。這不是純文件變更，且測試清單沒有 `tests/test_cli.py` 的「全空時輸出不變／有設定時顯示 requested 或 resolved 語意」斷言。應把它拆成 `feat(cli): ...` 或併入 workflow feature commit，並在同一 commit 加 CLI test。

Verification 使用 Git Bash 語法並有清除 `PYTHONHOME`／`PYTHONPATH`，方向正確；但本 repo 的 Windows 規則還要求把 `UV_CACHE_DIR` 設到 workspace 內的 ignored writable directory。建議可直接執行：

```bash
unset PYTHONHOME PYTHONPATH
export UV_CACHE_DIR="$PWD/.venv/.codex-uv-cache"

uv run --locked pytest <該 commit 的 targeted tests> -q -p no:cacheprovider
uv run --locked pytest -q -p no:cacheprovider
```

原計畫列出的 targeted command 只含 agents/session/stageflow，沒有 config、runstate、archive、CLI 與 resume integration。應在 Commits 區塊為每個 commit 寫出自己的 targeted test 集合；每組 targeted 與完整 suite 都通過後再 commit。

## 其他重要修訂

### M1. 文件應描述「換手即丟棄」，不是兩個 slot 各自保存 session

計畫的實作只有一個 `AgentSession.worker_session`；`owner` 改變時會清空它，並沒有 `dict[AgentRef, session_id]`。因此「實作 slot 與 owner slot 各自持有獨立 session」（`:148-150`）容易讓讀者誤以為兩個 ID 都會被保留。實際語意是：I 進場時開新 session；切回 owner 時丟棄 I 的 ID 並再開新 session；未來若同一 stage 又切回 I，也會再次 fresh start。

這個 one-active-session 設計符合目前單向 I → owner 的 stage flow，不必改成 session map，但文件、docstring 與測試名稱應精確描述。`begin_stage()` 最好同時把 `worker_session` 清空並把 `owner` 重設為 `None`，讓 stage-boundary invariant 可直接理解；既有以預填 `worker_session` 建立 `AgentSession` 的 adapter tests也要補上 matching owner，否則新守衛會按設計將其清除。

### M2. `base_slot` 的繼承 invariant 不應只依賴呼叫端自律

`impl_ref()` 會在 `name == owner.name` 時才填 `base_slot`，這條規則正確；但 `AgentRef` 是公開 dataclass，測試與其他模組可以直接建立 `AgentRef("I", "codex", base_slot="A")`。若 A 實際是 Claude，單看 `base_slot` 會錯誤繼承 Claude model 名稱。

`agent_model()` 應再次驗證 `ref.name == agent_ref(ref.base_slot, settings).name` 才繼承，或讓 I ref 只能經過一個驗證 factory 建立。至少加一個 malformed ref 不繼承 model 的單元測試，將「不同 CLI 永不繼承 owner model」落成 agents 層 invariant，而不是 workflow 慣例。

## 建議的修訂後 commit 切分

1. `feat(config): add implementation agent settings`
   - `Settings`、snapshot keys/value、明確的 resume override／clear 語意。
   - targeted：`test_config.py`、`test_runstate_snapshot.py`。
2. `feat(agents): resolve and validate the implementation agent profile`
   - `AgentRef.base_slot`、`impl_ref()`、effective model、實際 parsed argv、metadata rendering 共用來源。
   - adapter-specific reserved args；同名 built-in/custom 政策；PATH validation。
   - targeted：`test_agents.py`，包含三個 built-in 的 fresh/resume argv 與 custom path。
3. `fix(agents): isolate worker sessions by agent ref`
   - `AgentSession.owner`、`run_worker()` 換手守衛、stage reset invariant。
   - targeted：`test_session_resume.py`、`test_stageflow.py`。
4. `feat(workflow): route plan tasks through the implementation slot`
   - implement、build repair、protected repair、commit 使用 I；full gate、branch fixes、final stage使用 owner。
   - durable custom-model warning；resolved implementation metadata；archive/metrics 記錄 slot。
   - targeted：`test_stageflow.py`、`test_archive_io.py`、相關 archive schema tests。
5. `test(workflow): cover implementation agents and resume end to end`
   - custom fake-agent routing scenario。
   - offline fake built-in argv/session scenario。
   - build/full/review failure routing與 mid-queue resume 換設定 scenario。
   - targeted：`test_resume_integration.py` 及新增的 offline adapter integration tests。
6. `feat(cli): report implementation agent settings`
   - requested 與 resolved 顯示語意；全空時保持既有輸出。
   - targeted：`test_cli.py`。
7. `docs: document the implementation agent slot`
   - 中英文 README、parity 文件、custom command 限制、one-active-session 語意、resume clear/override 方法。
   - targeted：文件連結／範例檢查；再跑完整 suite 後 commit。

## 建議 acceptance criteria

1. 三個 `IMPL_*` 都未設定時，`impl_ref()` 回傳原 owner ref，實際 argv、session、輸出與 metadata 都維持既有行為。
2. 對 Claude、Codex、agy，slot I 的實際 argv依序包含 effective model、該 CLI 的 base args、`IMPL_ARGS`；metadata 必須與實際解析結果一致。
3. 換 CLI 時不繼承 owner model；同 CLI 時才依 owner slot 繼承，malformed `base_slot` 也不能把其他 CLI 的 model 餵進來。
4. per-task implement、build-gate repair、protected-test repair、task commit 全部使用 I；full-gate repair、branch-review repair、final review 全部使用 owner。
5. 同一 ref 的 retry 可以 exact-ID resume；任何 ref 換手都先 fresh start，舊 ID 永不出現在另一個 slot／CLI 的 argv。
6. 同名 built-in slot 可用 exact ID 安全隔離；同名 custom distinct slot 在沒有明確 stateless capability 前會被拒絕並提供可操作訊息。
7. `IMPL_ARGS` 依 effective adapter 驗證：Claude、Codex、agy 的基礎設施旗標不能被奪走，custom agent 不因碰巧同名的 token 被誤擋。
8. dual-spec 選 A 或 B 時都能得到正確的 owner name、base slot、繼承 model 與 resolved args；startup 不輸出尚未確定的假 effective 值。
9. quota／中途中止後可在 resume 更換或明確清除 implementation 設定；只處理剩餘 task，snapshot 與 run metadata 反映所定義的 requested/effective 語意。
10. artifact meta、log 與 metrics 能直接辨認 `agent_slot=I`，即使 I 與 owner 的 command、model、args 完全相同。
11. custom routing E2E、offline built-in integration、targeted tests 與完整 pytest 依序通過；Windows 指令使用 Git Bash、清除 Python 2.7 環境變數並設定 workspace-local `UV_CACHE_DIR`。
12. 每個 commit 都是 Conventional Commit、含詳細 body，且該 commit 的 targeted 與完整 suite 通過後才建立下一個 commit。

## 計畫中值得保留的部分

- 利用既有 `AgentRef` slot-aware archive 設計，而不是重新引入 `AgentProfile`，大幅縮小了必要 refactor。
- `base_slot` 能表達 dual-spec 選 B 後的 model 繼承，比用 agent name 反查 A/B 安全。
- 把 session ownership 守衛放在 `run_worker()` 而不是只放在 stage 5，是正確的分層；先於 workflow routing 的 commit 順序也可安全 bisect。
- implement、build gate、commit 與 full gate／branch review 的邊界切得清楚，`check_protected()` 沿傳入 ref 遞迴的判讀也正確。
- 測試已涵蓋 config、snapshot、agents、session、stageflow、archive 與 E2E 等層次；只要把 assertions 移到「實際 argv、失敗 closure、resume mutation」上，就能形成可信的防回歸網。
- 中英文 README 與 parity 文件都列入同一計畫，文件範圍完整。

總結而言，不需要推翻第三個 slot 的架構；先把「effective args 必須真的進 argv」、「未知 custom session 不得因 I slot 繞過同名限制」與「reserved args 必須依 adapter 驗證」寫成不可違反的 invariants，再補上 built-in offline integration、resume clear 語意與可稽核的 slot metadata，就可以進入實作。
