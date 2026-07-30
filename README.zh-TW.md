# adversarial-ai-coding

[English](README.md) | 繁體中文

`adversarial-ai-coding` 是一個 AI Agent 開發軟體的流程編排器。

# 多重 AI 對抗式程式開發工作流

自動化「A 實作、B 對抗式審查與驗收測試」的開發流程,以 SDD 規格先行與對抗式驗收測試驅動開發為主軸,並依 2026 年 agentic 開發的最佳實踐強化:確定性品質關卡、人工檢查點、分級裁決。對應的原始工作流:

其中 A(工作者 agent)與 B(審查者 agent)可以是 **Claude Code CLI**、**Codex CLI**、**Antigravity CLI** 或自訂 wrapper,透過各家的 headless(非互動)模式驅動。Stage 5 還可使用獨立的實作 slot I。

## 流程

每次執行由兩個 agent slot 驅動:

  - `A` 是工作者
  - `B` 是對抗式審查者

建議兩個 slot 用不同廠牌的模型(盲點不同)。每個 slot 可以是 `claude`、`codex`、`agy` 或自訂 wrapper。

實作步驟可另外指定第三個 slot `I`(見[強模型規劃、便宜模型實作](#強模型規劃便宜模型實作))。

預設流程:

```text
Spec(A 寫、B review)
Human Gate(未設定 PHASES 且未匯入 plan 時,可能提議啟用 Phased ATDD;詳見下文)
commit
  ↓
Plan 拆成 task 清單(- [ ], A 寫、B review)
(HUMAN_GATE_PLAN=1)Human Gate)
commit
  ↓
B 一次寫完全部 acceptance tests(TDD red;但 workflow 不驗證 Red)
A review
commit acceptance tests
記錄 + 啟動 protected-test 保護(一次記錄整份清單;此後每次 worker 呼叫都檢查)
  ↓
For each task(plan.md 的 - [ ]):
    A 實作(IMPL_AGENT 可換實作 agent;預設 A)
    build gate
    commit
  ↓
Full gate(acceptance tests 必須全綠)
  ↓
B review 整條 branch diff → 有改動才 commit
  ↓
A self-review → Full gate
  ↓
B final acceptance → 有改動才 commit
  ↓
finish:產 pr-body.md、(OPEN_PR=1)push + gh pr create
```

兩個選用模式會改寫部分流程:

- **[分階段 ATDD](#分階段-atdd-模式phased-atdd)**(`PHASES=1`):plan 拆成垂直 phase,單一測試階段改為逐 phase 迴圈——B 寫該 phase 的測試、workflow 驗證測試起始為紅、實作該 phase,phase gate 保證已完成的 phase 持續全綠。
- **[雙 spec](#雙-spec-模式)**(`DUAL_SPEC=1`):取代訂規格階段——A/B 各寫獨立候選 spec、交叉審查,由人選出 base(或合併),選中的 slot 接手後續整個流程。

為什麼這樣設計(確定性關卡、對抗式測試、分級裁決……)見下一節[核心設計](#核心設計為什麼這樣做);逐 stage 的完整說明(完整流程圖、審查迴圈機制、關卡指令與各 stage 細節)見 [`docs/how-it-works.zh-TW.md`](docs/how-it-works.zh-TW.md)。

## 核心設計(為什麼這樣做)

- **確定性關卡不外包給 AI**:AI 會為了「讓測試通過」走捷徑(reward hacking),所以 build/vet/test 由 workflow 自己跑,失敗輸出直接餵回給工作者修;AI 的「測試通過」回報只當參考。
- **對抗式測試完整性**:驗收測試由 reviewer 依規格撰寫、owner 審查;進入受保護階段後,任何 worker 呼叫(包含實作 slot I 與後續 owner 修正)都禁止修改這些測試檔。Workflow 在**每次 worker 動作後**用 `git diff` 硬性檢查(已提交與未提交的竄改都抓),屢犯即中止。對測試有異議只能記錄在 spec 的「假設與未決問題」。
- **人工檢查點在最高槓桿處**:spec 通過 AI 互審後、開始花大錢實作前,暫停等人核准(可先直接編輯 spec 再繼續);流程終點是「等人 merge 的 PR」,不是靜默結束。
- **分級裁決**:`verdict.json` 為 `{approved, blockers[], suggestions[]}`,只有 blocker 擋關;suggestions 累積到 `.workflow/suggestions.md`,收尾階段逐條評估,避免審查者拿小事擋關或不好意思擋而放水。
- **小批次**:一個 checkbox 任務一個 commit,審查與回退都容易。
- **產物落地成檔案**:A/B 之間靠 `specs/` 檔案與 git diff 溝通,不靠 stdout 傳遞長內容——這是 SDD 的天然優勢。
- **同一個 worker ref 在迴圈內續 session、reviewer 每輪全新 context**:同一個 worker ref 修改時保留脈絡;切換 ref 會依下方規則丟棄 session。Reviewer 不被前一輪結論定錨——這也是不同廠牌模型互審的價值(盲點不同)。

## 前置需求

- Python 3.12 以上
- [Astral uv](https://docs.astral.sh/uv/)
- `git`
- 會用到的 AI CLI 已安裝並登入:`claude`(Claude Code)、`codex`(Codex CLI)、`agy`(Antigravity CLI,選用)
- 透過 `AGENT_A`、`AGENT_B` 或 `IMPL_AGENT` 設定的自訂 command / wrapper 可在 `PATH` 中找到
- **在目標專案的 git repo 根目錄執行**(workflow 會檢查)。不需要 Bash 或 `jq`

## 快速開始

```bash
# 先在 workflow checkout 安裝鎖定的環境
cd /path/to/adversarial-ai-coding
uv sync --frozen
```

Repository 提供 macOS/Linux 使用的 `scripts/aac` 與 Windows 使用的
`scripts/aac.cmd`。將 checkout 的 `scripts` 目錄加入目前 shell 的 `PATH`。

macOS、Linux 或 Git Bash:

```bash
export PATH="/path/to/adversarial-ai-coding/scripts:$PATH"
```

Windows PowerShell:

```powershell
$env:Path = "C:\path\to\adversarial-ai-coding\scripts;$env:Path"
```

Windows 命令提示字元:

```bat
set "PATH=C:\path\to\adversarial-ai-coding\scripts;%PATH%"
```

若要讓設定在新的終端 session 仍然生效,請將相同設定加入 shell profile 或使用者
`PATH`。`aac` launcher 會從自身位置找到 workflow checkout,以 `--locked`
執行該環境,並保持目前工作目錄不變。

之後從目標專案根目錄執行 `aac`:

```bash
cd /path/to/your-project

# 預設:A = Claude Code,B = Codex
aac "為 CLI 加上 --json 輸出選項"

# 任務描述寫成檔案(建議,見下方「任務怎麼寫」)
aac task.md

# 交換 agent
AGENT_A=codex AGENT_B=claude aac task.md

# 同一個內建 CLI 放在兩個 slot,各自使用不同模型
AGENT_A=codex AGENT_B=codex MODEL_A=gpt-5.4 MODEL_B=gpt-5.5-codex \
  aac task.md

# 啟用雙 spec 模式(需要互動終端與 HUMAN_GATE=1)
DUAL_SPEC=1 aac task.md

# 輸出 AGENTS.md 規範範本(給已有 AGENTS.md 的專案手動合併)
aac print-agents
```

既有的 `AGENTS.md` 絕不會被覆寫。每次執行都會把 `adversarial-ai-coding:begin`
與 `adversarial-ai-coding:end` 標記之間的區塊拿去跟目前的範本比對,缺少或過時
時會印出提示,新版本加入的規則才不會被漏掉。標記區塊之外你自己寫的內容不會被
動到,也不會被視為過時。

## 強模型規劃、便宜模型實作

寫 spec、規劃、撰寫驗收測試與對抗式審查最需要強模型;重複性高的 stage-5 任務迴圈則可換成較便宜的模型或不同 CLI,而後續完整關卡與審查仍交回原本的 owner/reviewer 配對。

保留 owner 的 command,只降低實作模型:

```bash
AGENT_A=claude MODEL_A=opus IMPL_MODEL=sonnet \
  aac task.md
```

或由 Claude 規劃、Codex 實作 checkbox 任務:

```bash
AGENT_A=claude MODEL_A=opus \
AGENT_B=codex MODEL_B=gpt-5.5 \
IMPL_AGENT=codex IMPL_MODEL=gpt-5-codex \
IMPL_ARGS='-c model_reasoning_effort="low"' \
  aac task.md
```

三個 `IMPL_*` 都為空時,實作 slot 解析成選定的 owner,完全沿用既有行為。`IMPL_MODEL` 為空時,只有實作 command 與 owner command 相同,才繼承 owner slot 的 `MODEL_A` 或 `MODEL_B`;若換了 command 卻沒有設定 `IMPL_MODEL`,就使用該 CLI 自己的預設模型,絕不把一個 CLI 的模型名稱帶到另一個 CLI。

內建 command 可在 A、B、I 間同名,因為 workflow 只會用精準捕捉的 session ID 續接。不同 custom slot 不得共用 command 名稱,workflow 無法判斷 wrapper 是否暗中沿用 session。自訂實作 wrapper 必須同時與 A、B 不同名。若選定的 owner 是 custom agent,只要要設定任何 `IMPL_*` 客製化,就必須明確設定另一個 `IMPL_AGENT` wrapper;三個值全空時仍直接使用 owner,不會建立不同角色。

## 雙 spec 模式

設定 `DUAL_SPEC=1` 後,規格階段會改成:

```text
DUAL_SPEC=1:取代預設/分階段流程的第一行 Spec 階段,其餘流程不變
(前置檢查:必須 HUMAN_GATE=1 且互動終端)

A 寫候選 spec-a.md、B 寫候選 spec-b.md(各自獨立,禁止看對方的)
  ↓
交叉 review:B 審 spec-a、A 審 spec-b(只留報告與 verdict 供人參考,不擋流程、不進修復迴圈)
  ↓
A、B 各寫比較表 spec-comparison-a/b.md;workflow 產索引 spec-comparison.md
  ↓
Human 選擇 a / b / ma / mb:
    a、b:採用該候選為 base
    ma、mb:該候選為 base,人工編輯 spec-merge-request.md 列出要從另一份採納的項目(workflow 驗有實際內容)
    選中的 slot 成為 owner,接手後續流程的「A」角色;另一方成為 reviewer「B」
  ↓
base 複製為 spec.md;(merge 時)owner 依 merge request 合併
reviewer review spec.md(merge 時加驗採納項目沒漏、沒被扭曲)+ Human Gate → commit
  ↓
接預設或分階段流程的 Plan 之後(A=owner、B=reviewer)
```

裁決命令:

- `a`:直接複製 A 的候選成最終 `spec.md`
- `b`:直接複製 B 的候選成最終 `spec.md`
- `ma`:以 A 為 base,編輯 `.workflow/spec-merge-request.md`,要求 A 明確採納 B 的指定項目
- `mb`:以 B 為 base,編輯 `.workflow/spec-merge-request.md`,要求 B 明確採納 A 的指定項目

被選中的 owner 後續負責 plan、完整關卡與審查修正、自我 review;選用的實作 slot 只執行前述逐任務迴圈。另一個 A/B slot 成為 reviewer,並負責撰寫受保護驗收測試。此模式預設關閉,且刻意要求互動終端與 `HUMAN_GATE=1`;無人值守流程請維持 `DUAL_SPEC=0`。

## 匯入外部 Spec 或 Plan

先在你慣用的互動工具裡釐清需求,再把成品交給 workflow:
`IMPORT_SPEC=path` 會把你的檔案當作 `spec.md`,只跳過「worker 撰寫
spec」那一步;`IMPORT_PLAN=path`(需同時設定 `IMPORT_SPEC`)對
`plan.md` 做同樣的事。匯入的產物預設仍會經過 reviewer 的對抗式
review;設 `IMPORT_REVIEW=0` 可跳過該 AI review(human gate、格式檢查
與 commit 一律照跑)。檔案格式要求見
[docs/import-format.md](docs/import-format.md),
[resources/import-authoring-prompt.md](resources/import-authoring-prompt.md)
是可直接貼進你自己工具的 prompt。

若以停用 review 的方式匯入 spec (`IMPORT_SPEC` + `IMPORT_REVIEW=0`)，不會執行 spec reviewer，因此不會產生或提供分階段模式建議。

## 分階段 ATDD 模式(Phased ATDD)

設定 `PHASES=1` 後,單次預先撰寫驗收測試的 stage 會改成逐 phase
迴圈。Plan 必須使用 `## Phase N: <title>` 標題;每個 phase 都需要一行
`Acceptance:`,以穩定邊界上的可觀察行為描述驗收條件,並至少包含一個
`- [ ]` 任務。Phase 必須是垂直功能切片(一個可運作的行為增量),不可是
水平技術分層。Plan review 完成後,workflow 會確定性解析 plan;若結構有
問題,會在任何實作開始前交回 owner 修正。

```text
Spec(A 寫、B review)
Human Gate
commit
  ↓
Plan 拆成 vertical phases(A 寫、B review)
(HUMAN_GATE_PLAN=1)Human Gate
workflow 驗 plan 結構
commit
  ↓
For each Phase:
    B 寫該 Phase acceptance/component/contract tests
    A review
    workflow 驗證測試正確 Red(regression-guard Phase 則必須 Green)
    commit Phase tests
    記錄 + 啟動 protected-test 保護(append;此後每次 worker 呼叫都檢查)
    For each task:
        A 實作(IMPL_AGENT 可換實作 agent;預設 A)
        build gate
        commit
    phase gate:歷史 Phase + 當前 Phase 全綠
    (PHASE_REVIEW=1)B review Phase diff → 有改動才 commit
  ↓
Full gate
  ↓
B review 整條 branch diff → 有改動才 commit
  ↓
A self-review → Full gate
  ↓
B final acceptance → 有改動才 commit
  ↓
finish:產 pr-body.md、(OPEN_PR=1)push + gh pr create
```

每個 phase 依序執行:

1. B 只撰寫此 phase 的驗收測試;A 審查這些測試。
2. Workflow 用 `PHASE_GATE_CMD`(或 `GATE_CMD`)執行 red check:由於此
   phase 尚未實作,新測試必須失敗。標題以 `(regression-guard)` 結尾時
   會反轉預期:這些測試用來鎖定既有行為,所以必須立即通過。
3. 測試會先 commit 並附加到受保護清單;較早 phase 的測試絕不移除。
4. 實作 slot 逐一實作此 phase 的任務(每個任務一個 commit、每個任務
   都跑 build gate),接著執行 phase gate:截至目前寫下的所有測試都必須
   通過。已完成的 phase 在後續整個 run 中都必須維持綠燈。

因為測試是及時撰寫,在 phase 邊界「全部執行」本來就代表「所有已完成
phase 加上目前 phase 都是綠燈」,不需要 test tag 或逐 phase 選取。最後
一個 phase 結束後,既有的完整關卡、branch review 與 final review 仍照常
執行。Resume 時不可變更 `PHASES`:run 啟動時會將其值寫入 snapshot,
若 resume 時的環境設定與之衝突,workflow 會拒絕執行。不過有一個允許的
run 內切換方式。未設定 `PHASES` 且未匯入 plan 時,spec reviewer 也會
判斷工作是否適合分階段模式——至少有兩個可各自獨立驗收的垂直功能——
並將判斷寫入 `.workflow/phased-suggestion.json`。若 reviewer 建議採用,
spec human gate 會顯示理由並詢問
`Enable Phased ATDD for this run? [y/N]:`。回答 `y` 會啟用分階段模式,
並以不可分割的方式重寫 run snapshot,因此後續每次 resume 仍會讀到一致
的設定。使用 `HUMAN_GATE=0` 時只會記錄建議,絕不自動啟用。若環境中明確
設定 `PHASES=0`,則完全停用這項建議。

### 任務怎麼寫

結果好壞幾乎取決於任務範圍是否明確、「完成」是否可驗證。建議用檔案 + 這個格式:

```markdown
## 目標
為 CLI 加上 --json 輸出選項。

## 驗收條件
- `mytool list --json` 輸出合法 JSON 陣列
- 沒有 --json 時行為完全不變

## 不做什麼
- 不改既有的文字輸出格式
- 不處理 --yaml
```

## 環境變數

| 變數 | 預設值 | 說明 |
|---|---|---|
| `AGENT_A` | `claude` | 工作者 agent command:`claude` \| `codex` \| `agy` 或自訂命令 |
| `AGENT_B` | `codex` | 審查者 agent command(驗收測試 stage 兩者角色互換) |
| `IMPL_AGENT` | 選定 owner 的 command | Stage-5 逐任務實作迴圈使用的 command。內建 command 可與 A/B 同名;自訂實作 wrapper 必須與兩者都不同名 |
| `MODEL_A` | (CLI 預設) | A 槽內建 agent 的模型;即使 A/B 使用相同 command 也按 slot 解析。自訂 agent 請把模型參數放在 `AGENT_A_ARGS` |
| `MODEL_B` | (CLI 預設) | B 槽內建 agent 的模型;即使 A/B 使用相同 command 也按 slot 解析。自訂 agent 請把模型參數放在 `AGENT_B_ARGS` |
| `IMPL_MODEL` | 繼承或 CLI 預設 | 內建實作 slot 的模型。未設定時,只有 command 與 owner 相同才繼承 owner 模型;自訂實作 agent 會忽略此值,請改用 `IMPL_ARGS` |
| `CLAUDE_ARGS` / `CODEX_ARGS` / `AGY_ARGS` | (空) | 各內建 agent command 共用的額外參數,依 POSIX shell quoting 解析。session 控制旗標由 workflow 保留,見下節 |
| `AGENT_A_ARGS` / `AGENT_B_ARGS` | (空) | 自訂 agent command 的額外參數,依 POSIX shell quoting 解析後加在 prompt-file instruction 前 |
| `IMPL_ARGS` | (空) | 實作 slot 的額外參數,依 POSIX shell quoting 解析。內建實作 agent 會接在該 command 的共用 args 後;自訂實作 wrapper 的模型旗標也放這裡 |
| `MAX_ROUNDS` | `3` | 每個 stage 的審查/關卡最多輪數,超過即通知並中止 |
| `HUMAN_GATE` | `1` | spec 通過 AI 互審後暫停等人核准;無人值守設 `0`(不建議) |
| `HUMAN_GATE_PLAN` | `0` | `1` = plan 通過 AI 互審後、commit 之前也暫停等人核准。plan 是實作階段的任務佇列(一個 checkbox 一個 commit),plan 錯了後面每個 commit 都跟著錯,這是燒錢前最後一個便宜的介入點。與 `HUMAN_GATE` 互相獨立(`HUMAN_GATE=0 HUMAN_GATE_PLAN=1` 合法),需要互動終端 |
| `DUAL_SPEC` | `0` | `1` = 啟用雙 spec: A/B 各寫獨立候選、互審一次、各寫比較表、等人選 owner。需要互動終端與 `HUMAN_GATE=1` |
| `IMPORT_SPEC` | 空 | 使用此檔案作為 `spec.md`;跳過「worker 撰寫 spec」步驟。 |
| `IMPORT_PLAN` | 空 | 使用此檔案作為 `plan.md`;跳過「worker 撰寫 plan」步驟。需要 `IMPORT_SPEC`。 |
| `IMPORT_REVIEW` | `1` | 匯入的產物仍會經過 reviewer 的 review loop。`0` 只會跳過匯入產物的 AI review。需要 `IMPORT_SPEC`。 |
| `PHASES` | `0` | `1` 啟用分階段 ATDD 流程:plan 拆成垂直 phase,每個 phase 先寫自己的受保護驗收測試再實作。此設定決定 stage 圖,resume 時不可變更。未設定 `PHASES` 且未設定 `IMPORT_PLAN` 時,spec reviewer 也會判斷是否適合分階段模式,spec human gate 可能提議啟用(詳見[分階段 ATDD 模式](#分階段-atdd-模式phased-atdd))。 |
| `PHASE_GATE_CMD` | 空 | 每個 phase 的 red check 與 phase gate 命令。空值時改用 `GATE_CMD`。 |
| `PHASE_REVIEW` | `0` | `1` 時每個 phase 結尾由 reviewer 審該 phase 的 diff(含 blocker 迴圈)。預設關閉,因為 phase gate 本身就是 reviewer 寫的受保護測試在把關。 |
| `GATE_CMD` | 自動偵測 | 完整品質關卡。go:`go build ./... && go vet ./... && go test ./...`;npm(有 test script):`npm test`;cargo:`cargo test`;偵測不到則停用並警告 |
| `BUILD_GATE_CMD` | 自動偵測 | 逐任務的輕量關卡(只驗編譯,容忍驗收測試紅燈) |
| `AUTO_BRANCH` | `1` | 自動建立 `auto/<時間戳>` branch |
| `USE_WORKTREE` | `0` | `1` = 在獨立 git worktree 執行(隔離性比 branch 好) |
| `OPEN_PR` | `0` | `1` = 結尾自動 push 並 `gh pr create`(需 gh 與 origin);預設只印指令 |
| `NOTIFY_CMD` | (空) | 通知指令,訊息以第一個參數傳入,例:`NOTIFY_CMD="ntfy publish mytopic"`。觸發點:待人工核准、各種中止、限額等待、完成 |
| `COLOR` | `auto` | 為 workflow 自身的狀態訊息上色。`auto` 通常讓重導向或非終端機輸出保持無色碼;`NO_COLOR` 停用上色,`FORCE_COLOR` 可在 `auto` 模式強制 ANSI 色碼,包括重導向輸出,而 `TERM=dumb` 會停用未強制的上色。`always` 可讓重導向輸出包含 ANSI 色碼,`never` 停用。封存的 run log 即使強制上色也永遠不含色碼。 |
| `COLOR_THEME` | `dark` | 狀態訊息主題:`dark` 或 `light`。 |
| `COLOR_<CATEGORY>` | 主題預設 | 逐類別覆寫顏色,類別為 `STAGE`、`PROGRESS`、`ERROR`、`WARNING`、`CHECKPOINT`、`SUCCESS`、`AGENT`。接受顏色名(`red`、`bright-cyan`、`bold-bright-red`)或原始 SGR 參數(`1;91`),例如 `COLOR_ERROR=bold-bright-red`。 |
| `RETRY_ON_LIMIT` | `1` | 撞用量限額/429 時自動等待重試,三個內建 agent 通用。claude 回報的精確重置時刻優先(+2 分緩衝);其次才解析訊息(claude 的 `resets HH:MMam` +2 分緩衝;codex 的 `try again in 90s`、`try again at Jul 14th, 2026 7:23 PM` 或只有時刻的 `try again at 12:50 AM` +30 秒緩衝),都沒有才指數退避;`0` = 直接失敗 |
| `RETRY_MAX` | `6` | 每次 agent 呼叫的限額重試上限 |
| `RETRY_BASE_WAIT` | `300` | 指數退避的初始等待秒數(每次 ×2) |
| `RETRY_MAX_WAIT` | `3600` | 指數退避的單次等待上限(秒) |
| `RETRY_MAX_RESET_WAIT` | `21600` | 訊息中的重置時刻若比這個秒數還遠(如週配額要等好幾天),立即放棄而不空等 |
| `RESUME_RUN` | (空) | 續跑中斷的 run:填 `.workflow/state/` 下的 run id,或 `last` 取最新未完成的 run。已完成 stage 直接跳過。詳見「中斷後續跑」 |
| `AGENTS_TEMPLATE` | workflow checkout 內的 `resources/AGENTS.template.md` | AGENTS.md 規範範本路徑;範本遺失時 bootstrap 會警告並跳過(流程照常) |
| `PROMPTS_DIR` | workflow checkout 內的 `resources/prompts` | workflow prompt template 目錄;除非要覆寫內建 prompt,通常不用設定 |
| `SPEC_DIR` | `specs/<時間戳>` | 規格與計畫的存放目錄 |
| `RUNS_DIR` | `.workflow/runs` | 每次 run 的 archive 根目錄;相對路徑會在 branch/worktree 準備完成後解析 |
| `TOOLS` | git/go test/go build/go vet | Claude Code 的 `--allowedTools` 白名單。**注意 `Bash(go *)` 含 `go run`(任意程式碼執行),別圖方便放寬**。審查者同受白名單限制,被擋的指令會空轉燒 token(E2E 實測):依專案補上常用唯讀指令(如 `Bash(gofmt *)`),並靠 AGENTS.md 的規則引導 agent 改用內建檔案工具 |

Windows 上想在關卡跑 `-race`:`GATE_CMD='go build ./... && go vet ./... && go test -race -ldflags "-extldflags=-Wl,--default-image-base-low" ./...'`

## 中斷後續跑

每次 run 會把進度記錄在 `.workflow/state/<run-id>/`:resolved task 快照、生效設定、stage 完成台帳、write-code 剩餘任務佇列。run 中止時會印出可直接貼上的續跑指令:

```bash
RESUME_RUN=20260710-153012 aac
```

續跑會跳過所有已完成的 stage(不重付 AI 費用),還原跨 stage 狀態(dual-spec 裁決、驗收測試基準、剩餘實作任務),從中斷點繼續。`RESUME_RUN=last` 自動選最新未完成的 run。續跑不必再帶 task 參數:以 run 的 task 快照為準,帶了且內容不同會直接拒絕。

引擎、模型等多數設定每次 attempt 都可覆寫。最主要的用例是換掉配額耗盡的 agent:

```bash
AGENT_B=agy RESUME_RUN=last aac
```

持久化的 `IMPL_AGENT`、`IMPL_MODEL`、`IMPL_ARGS` 也採非空值覆寫規則:續跑指令提供非空值,即可在該 attempt 取代 snapshot;空字串不能清除已儲存的值。要清除時,請直接編輯 `.workflow/state/<run-id>/settings.json` 內對應的小寫 key,維持合法的 schema-1 JSON,再執行續跑。例如把 `"impl_model"` 設成 `""`,即可回到模型繼承規則。

`SPEC_DIR`、`DUAL_SPEC`、`AUTO_BRANCH`、`USE_WORKTREE` 跨續跑不可變:它們決定 stage 圖與產物位置,衝突的覆寫會被拒絕。`NOTIFY_CMD` 刻意不持久化,每次 attempt 重新提供。

保證範圍:

| 中斷類型 | 行為 |
|---|---|
| 可捕捉中止:agent 失敗、配額耗盡(exit code 75)、審查/關卡輪次用盡、人工中止、受保護測試中止、Ctrl-C / SIGTERM / SIGHUP | 印一次續跑指令並保留原 exit code;續跑從最後完成的 stage 之後繼續 |
| SIGKILL、斷電、OS 當機 | Best effort。狀態為 append-only、失敗方向 fail-safe:最壞情況是多重跑一兩個 stage(多付一點 AI 費用),不會錯誤跳過未完成的工作 |
| state 目錄或 worktree 被刪、branch 歷史被 rewrite | Fail closed 並給出明確訊息,不做透明恢復 |

注意事項:

- `USE_WORKTREE=1` 的 run,state 在 worktree 的 `.workflow/` 內;必須 cd 進該 worktree 執行續跑(印出的提示已含 `cd` 指令)。
- attempt 異常死亡可能留下 stale lock;錯誤訊息會給出確認前次已死後手動清除 `.workflow/state/<run-id>/lock` 的指令。
- 已完成的 run 拒絕續跑;`RESUME_RUN=last` 會自動略過已完成的 run。

## 產物與目錄結構

```
adversarial-ai-coding/
├── pyproject.toml
├── src/adversarial_ai_coding/
└── resources/
    ├── AGENTS.template.md   # 互審規範範本(簡單英文撰寫,對各家模型最通用)
    └── prompts/
        └── *.md             # workflow prompt templates

your-project/
├── AGENTS.md            # 互審規範(缺檔時由範本產生;既有檔案絕不覆蓋,只提示)
├── CLAUDE.md            # 缺檔時補一行指向 AGENTS.md
├── specs/<RUN_ID>/
│   ├── spec-a.md                    # DUAL_SPEC=1 時 A 的候選 spec
│   ├── spec-b.md                    # DUAL_SPEC=1 時 B 的候選 spec
│   ├── spec-a.review-by-b.md        # DUAL_SPEC=1 時 B 對 A 的 one-shot review
│   ├── spec-b.review-by-a.md        # DUAL_SPEC=1 時 A 對 B 的 one-shot review
│   ├── spec-comparison-a.md         # DUAL_SPEC=1 時 A 寫的比較表
│   ├── spec-comparison-b.md         # DUAL_SPEC=1 時 B 寫的比較表
│   ├── spec-comparison.md           # DUAL_SPEC=1 時給人看的裁決索引
│   ├── spec-decision.md             # DUAL_SPEC=1 時記錄選定 owner/reviewer
│   ├── spec.md          # 規格(含驗收條件、假設與未決問題)
│   └── plan.md          # 實作計畫(checkbox 任務清單,完成會打勾)
├── .workflow/           # 不進版控(workflow 自動放 .gitignore)
│   ├── review.md        # B 的審查意見 + A 的逐條回覆
│   ├── verdict.json     # 裁決:{approved, blockers[], suggestions[]}
│   ├── suggestions.md   # 歷輪累積的不擋關建議(收尾階段逐條評估)
│   ├── spec-merge-request.md        # DUAL_SPEC=1 merge 時的人類採納指示
│   ├── protected-tests.txt / protected-base.sha   # 受保護測試檔清單與基準 commit
│   ├── pr-body.md       # 收尾產生的 PR 內文
│   ├── latest-run.txt   # 指向最近一次 run archive 目錄
│   ├── state/<RUN_ID>/  # 續跑狀態:設定快照、stage 台帳、任務佇列(RESUME_RUN 用)
│   └── runs/<RUN_ID>/   # 每次 run 的完整中間資料 archive
│       ├── 001-run-metadata.json
│       ├── 002-task-source.md / 003-task.txt
│       ├── NNN-*-prompt.md / NNN-*-output.txt / NNN-*-attempt-*-rc*.raw
│       ├── NNN-review-*.md / NNN-verdict-*.json
│       ├── NNN-*-git-status.txt / NNN-*-git-diff.patch
│       ├── metrics.csv  # stage/角色/engine欄位/輪次/秒數/費用/model/args/time
│       └── logs/001-run.log (+ 001-run.log.meta.json)
```

Archive 產物檔名前綴 `NNN-` 是單一 run 內的生成順序;每個 artifact 會有對應 `.meta.json`,記錄生成時間、角色、`engine`、模型與模型參數。`engine` 是為了相容既有 archive schema 而保留的穩定欄位,記錄該次呼叫實際解析出的 agent command/runtime。`metrics.csv` 的前 7 欄維持 `run_id,stage,role,engine,round,duration_s,cost_usd`,尾端追加 model/model_args/generated_at;費用目前只有 claude agent 會回報(`total_cost_usd`)。

## Agent CLI 差異與限制

| | claude | codex | agy |
|---|---|---|---|
| 非互動執行 | `claude -p --output-format stream-json` | `codex exec` | `agy --print` |
| session 續接 | `--resume <id>`(精準) | `resume ... <thread-id>`(精準) | `--conversation <conversation-id>`(精準) |
| id 來源 | 結構化回應 | `thread.started` JSONL event | 每 attempt 的 `--log-file` |
| 權限控制 | `acceptEdits` + `TOOLS` 白名單 | `--sandbox workspace-write` | `--dangerously-skip-permissions`(見安全性) |
| 費用回報 | 有(metrics.csv) | 無 | 無 |
| 即時輸出 | 訊息,加上每個工具呼叫一行摘要 | 訊息,加上每個工具呼叫一行摘要 | 原始合併輸出 |

- Claude、Codex、Agy 都可放在 A、B、I slot;`MODEL_A`、`MODEL_B`、`IMPL_MODEL` 仍各自生效。worker 只按已捕捉的 id 精準續接,reviewer 每輪都開新 session。
- 全程只有一個 active worker session,不是每個 slot 各自保存一個。相同的完整 agent ref 可在同一迴圈續接;只要換到不同 agent ref(例如 slot 或 command 改變),就立即丟棄已捕捉的 ID 並 fresh start。**只更換模型本身不會丟棄 active session**,因為模型值不是 `AgentRef` 的一部分;只有 slot/command 的 ref identity 改變(或 stage 邊界重設)才會丟棄。因此 I 進入逐任務迴圈時從新 session 開始,迴圈內可累積 context;切回 owner 跑完整關卡時,I 的 ID 會被丟棄,舊 owner session 也不會恢復。Workflow prompt 會指向內容完整的 archive prompt 檔,不依賴保留 chat context。
- workflow 絕不退回 Codex `--last` 或 Agy `--continue`:fresh call 抓不到 id 時會警告且下輪仍 fresh;已知 id 後某輪抓不到則保留原 id。Claude 與 Codex 的原始 JSONL、Agy log 都會存成每 attempt 的 `.cli.raw` artifact。
- 三家 agent 執行時都會即時串流輸出,長步驟不會變成無聲等待。每一行串流都會加上 slot 與指令的前綴(例如 `[A claude] `),並套用 `AGENT` 顏色類別。前綴的作用是讓 agent 自己寫的 `### 標題` 不會被誤判成 workflow 的 human checkpoint;它只在列印時加上,封存產物與 run log 永遠不含前綴。Claude 與 Codex 另外會把每個工具呼叫印成一行,標示工具名稱與它操作的檔案、指令或搜尋樣式,其餘輸入一律捨棄,因此一次大量寫檔也只佔一行。Codex 把 shell 呼叫回報成完整的解譯器命令列,因此 `powershell -Command` 或 `bash -c` 這層包裝會被剝掉,只顯示你真正在意的那段指令。
- 內建 agent 的 session、輸出、sandbox 與 log 旗標由 workflow 管理。`CLAUDE_ARGS`(以及 I 解析成 Claude 時的 `IMPL_ARGS`)不得包含 `-c` / `--continue`、`-r` / `--resume`、`--session-id`、`--fork-session`、`--no-session-persistence`、`--from-pr`,也不得用 `--output-format`、`--verbose` 或 `--json-schema` 覆寫結構化輸出契約。`CODEX_ARGS` 與 Codex 的 `IMPL_ARGS` 不得包含 `--json`、`resume`、`--sandbox` / `-s`、`--dangerously-bypass-approvals-and-sandbox`、`--yolo`、`--ephemeral`,也不得透過 `-c` / `--config` 覆寫 `sandbox_mode`;`AGY_ARGS` 與 Agy 的 `IMPL_ARGS` 不得包含 `--log-file`、`--continue`、`--conversation`。
- 內建 args 也不得用 `--model`、`-m` 或 Codex 的 `-c model=` / `--config model=` 指定模型;必須使用 `MODEL_A`、`MODEL_B` 或 `IMPL_MODEL`,確保實際呼叫與 archive metadata 一致。`-mMODEL`、`-sVALUE`、`-cVALUE` 等 attached short forms 也依相同的保留參數規則解析。Custom args 則原樣傳入,自訂 agent 的模型或 session 旗標可以放在對應的 `AGENT_A_ARGS`、`AGENT_B_ARGS` 或 `IMPL_ARGS`。
- 所有內建與自訂 agent 的額外參數在各平台都採 POSIX shell quoting;含空白的值必須引用。Windows 反斜線路徑必須引用或改用 `/`,未引用的反斜線會套用 POSIX escape 語意。
- Agy conversation id 依目前 log 文字解析;若升版改格式,會安全退化成警告 + fresh session,不會猜測或接到其他 conversation。
- 自訂 agent 沒有自動 session resume;A/B 使用完全相同的自訂 command 仍會拒絕。若底層 CLI 相同,請用兩個 wrapper command 名稱隔離 session/profile。
- `codex exec resume` 沒有 `--sandbox` 旗標,workflow 改用 `-c 'sandbox_mode="workspace-write"'`。
- agy 的 `--print-timeout` 預設僅 5 分鐘,workflow 已調高(工作 60 分、審查 30 分)。
- 各 CLI 旗標以本機 `--help` 為準(本 workflow 依 2026-07 版本撰寫)。

## 受保護測試的逃生口

驗收測試由 reviewer 撰寫後受保護。實作期間,任何 worker(包含實作 slot I 與後續 owner 修正)都不得修改、刪除或略過 `.workflow/protected-tests.txt` 列出的檔案。acceptance stage 結束後,目前 workflow process 會在記憶體保存 `.workflow/protected-tests.txt` 與 `.workflow/protected-base.sha` 的 exact bytes、解析後 paths 與 base commit,並在每個 active worker boundary 前後驗證 exact bytes。即使 path list 為空,兩個 control files 仍受保護。這個 snapshot 只在目前 process 有效;resume 啟動的新 process 會把當時磁碟上的 controls 視為新的起始信任。這不是 OS-level lock,也不保證能防禦兩個 filesystem calls 之間的 concurrent pathname replacement。

若 worker 對測試有異議,只能把異議寫進 spec 的「假設與未決問題」,不能自行改測試。若受保護測試**真的**錯了,請停止 workflow 並由人工依序處理:編輯修正後的測試、commit 新內容,再把該新 commit SHA 寫入 `.workflow/protected-base.sha`;若該測試不應再受保護,則可改由人工從 `.workflow/protected-tests.txt` 移除 path。確認 controls 已描述預期的 trusted state 後再 resume。

## 安全性注意事項

- **agy agent 使用 `--dangerously-skip-permissions`**(該 CLI 無細粒度白名單),只建議搭配 `USE_WORKTREE=1` 或容器使用。
- claude / codex 都以最小權限運作;真正的完整隔離是容器(devcontainer),branch/worktree 只隔離 git 狀態,不隔離檔案系統與網路。
- 兩個 AI 互審**很燒 token**:`MAX_ROUNDS`、分級裁決、`commit_if_dirty`(無變更就跳過 commit 呼叫)都是止損機制。中止時已通過的 stage 均已 commit,可從斷點人工接手。
- 雙 spec 模式會多花第二份候選、互審與比較表的 AI 呼叫;只有在規格決策值得額外成本時再開。
- `DUAL_SPEC=1` 會拒絕 `HUMAN_GATE=0` 與無互動終端,因為此流程必須由人選定最終 spec owner。

## 自訂 stage

Stage 流程定義在 `workflow.run_workflow()` 內,由 `begin_stage`、`work`、`review_loop`、`gate_loop`、`commit_work` / `commit_if_dirty` 等 Python 積木組成。照現有 stage 的樣式增刪即可。

## 測試

```bash
uv run pytest -q   # 單元與整合測試,不呼叫任何 AI
```

### 手動 E2E(會呼叫真實 AI、消耗訂閱配額)

```bash
uv run pytest -m e2e -s   # 完整六 stage(預設 sonnet worker/low effort + codex gpt-5.5/low effort;約 20~40 分、$2~5 等值配額)
```

執行器在臨時目錄現生 fixture git repo(Go 小專案 + ASCII 任務書,沉澱自五次真實試跑的教訓),直接引用本 repo 的 Python 套件與 `resources/`(無複本漂移),跑完後自動驗收:六 stage 完成、spec 含 Assumptions 節、plan checkbox 全打勾、受保護測試未被改動、逐任務小 commit、最終關卡由執行器親測、metrics 摘要。成敗都會保留現場路徑供檢視,`E2E_DIR` 可指定位置,agent 與模型可用一般環境變數覆寫。

**定位:改動 workflow 核心邏輯後、發版前的手動回歸;絕不掛進 CI 或單元測試入口。**

## 疑難排解

- **`(B 未產出 verdict.json,視為未通過)`**:審查者 agent 失敗或沒照規範寫檔,看 `.workflow/logs/`;連續發生會被 `MAX_ROUNDS` 擋下並通知。
- **卡在權限詢問**:headless 下沒人能按「允許」。Claude Code 需要的指令加進 `TOOLS`;codex 確認 sandbox 模式;agy 確認旗標。
- **`沒有互動終端可供核准`**:`HUMAN_GATE=1` 需要 tty,`HUMAN_GATE_PLAN=1` 也是(這個在啟動時就會擋下,不會白燒 AI 額度);在 CI 等無人環境設 `HUMAN_GATE=0`(並讓 `HUMAN_GATE_PLAN` 維持 `0`),並用 `NOTIFY_CMD` 接手把關。
- **品質關卡在逐任務階段一直紅**:驗收測試在所有任務完成前本來就允許紅燈,逐任務只跑 `BUILD_GATE_CMD`(編譯);若連編譯關卡都過不了才會進修正迴圈。
- **審查者報告檔案「損壞」但檔案其實正常**:Windows(特別是中文語系)上 codex 讀檔可能把 UTF-8 內容用系統碼頁(CP950)解碼成亂碼,產生假性 corruption blocker。對策:規格、計畫與測試資料盡量用 ASCII,非 ASCII 字元寫成 Unicode escape(Go 中即反斜線接 `u4e0a`,代表 U+4E0A「上」)——AGENTS.md 範本已內建此規則。
- **撞到訂閱用量限額**(`You've hit your session limit`、`You've hit your usage limit`、429):預設會自動等待重試,三個 agent 通用。**判斷只讀 agent 自己的錯誤通道,絕不讀 agent 執行過的指令輸出** —— claude 看結構化回應裡回報的狀態碼(它自己就足以定案),codex 看 `error` 與 `turn.failed` 事件加上 CLI 寫在 JSON 之外的文字;agy 沒有結構化通道,仍然掃整包輸出。因此測試套件剛好印出「rate limit」字樣不會害整個 run 去睡覺。等待時間方面,claude 的串流會回報精確的重置時刻,有就直接用;否則靠訊息解析(支援 `resets 10:50am`、`try again in 90s`、`try again at Jul 14th, 2026 7:23 PM`、只有時刻的 `try again at 12:50 AM` 四種格式,即使被換行折斷也能解析),都沒有才指數退避;等待會發 `NOTIFY_CMD` 通知並記錄在 log。
  **若重置時刻比 `RETRY_MAX_RESET_WAIT`(預設 6 小時)還遠**——例如 codex 週配額要等好幾天——則立即放棄並印出重置時刻,不做徒勞的空等;配額回來後重跑即可。`RETRY_ON_LIMIT=0` 可完全關閉重試。非限額的 agent 失敗不會重試,原始輸出攤印在 log 結尾供診斷。

## 延伸方向

- **進一步的 agent 整合**:[Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview)(Python/TS)是 `claude -p` 的程式化介面,原生支援 structured output、session 物件、工具核准 callback。
- **規格模板**:前兩個 stage 可搭配 [GitHub spec-kit](https://github.com/github/spec-kit) 的 SDD 產物格式。
- **CI 無人值守**:`HUMAN_GATE=0 OPEN_PR=1` + `NOTIFY_CMD`,人改在 PR 上把關;Claude Code 與 Codex 都有官方 CI 整合。

## 參考資料

- [Run Claude Code programmatically(官方 headless 文件)](https://code.claude.com/docs/en/headless)
- [Codex CLI Non-interactive mode(官方)](https://developers.openai.com/codex/noninteractive)
- [Codex CLI reference(官方)](https://developers.openai.com/codex/cli/reference)
- [Beyond Autocomplete: Best Agentic Coding Workflow in 2026(Kilo)](https://kilo.ai/articles/beyond-autocomplete)
- [How to evaluate AI agents, avoid reward hacking, and build better specs(Arize)](https://arize.com/blog/how-to-evaluate-ai-agents-and-build-better-specs)
- [Spec-Driven Development: A Spec-First Approach to AI-Native Engineering(Microsoft)](https://developer.microsoft.com/blog/spec-driven-development-ai-native-engineering)
