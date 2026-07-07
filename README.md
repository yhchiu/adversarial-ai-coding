# auto-workflow — SDD/TDD 雙 AI 互審自動化工作流

用一支 bash script 自動化「一個 AI 做事、另一個 AI 審查」的開發流程,並依 2026 年 agentic 開發的最佳實踐強化:確定性品質關卡、對抗式驗收測試、人工檢查點、分級裁決。對應的原始工作流:

```bash
for work in "訂規格" "規劃實作計畫" "撰寫程式碼" "整體review" "修bug"; do
  用 A 做 $work
  上個步驟的成果交給 B review
  讓 A 確認 review 結果後修改
  要求 B 做最後確認
done
```

其中 A(工作者)與 B(審查者)可以是 **Claude Code CLI**、**Codex CLI** 或 **Antigravity CLI**,透過各家的 headless(非互動)模式驅動。

## 流程

```
訂規格(A 作 / B 審)
  │  spec.md 必含「假設與未決問題」——headless 下 AI 不能問人,禁止默默腦補
  ▼
★ 人工核准(HUMAN_GATE)── 規格是錯誤放大器,人工把關放在最高槓桿處
  ▼
規劃實作計畫(A 作 / B 審)── 任務清單必須是「- [ ] 」checkbox
  ▼
撰寫驗收測試(★B 作 / A 審)── 對抗式 TDD:出題者與答題者分離
  │  記錄受保護測試檔清單;紅燈是預期的(TDD red phase)
  ▼
逐任務實作:for 每個 checkbox 任務 {
    A 實作 → 輕量編譯關卡 → 受保護檔 git diff 硬性檢查 → commit
  }
  → 完整品質關卡(驗收測試須全綠)→ B 審整體 diff
  ▼
整體 review 與修 bug(A 處理累積的 suggestions + 自我 review → 關卡 → B 最終驗收)
  ▼
收尾:印出 git push / gh pr create 指令 + 執行統計(OPEN_PR=1 才自動執行)
```

每個「B 審」都是一個迴圈:B 未通過 → A 依 `.workflow/review.md` 逐條回應並修改 → 過確定性關卡 → B 再審(即「最後確認」),最多 `MAX_ROUNDS` 輪,超過即通知並停下等人工介入。

## 核心設計(為什麼這樣做)

- **確定性關卡不外包給 AI**:AI 會為了「讓測試通過」走捷徑(reward hacking),所以 build/vet/test 由 script 自己跑,失敗輸出直接餵回給工作者修;AI 的「測試通過」回報只當參考。
- **對抗式測試完整性**:驗收測試由審查方(B)依規格撰寫、工作方(A)審查;實作階段 A 禁改這些測試檔,script 在**每次工作者動作後**用 `git diff` 硬性檢查(已提交與未提交的竄改都抓),屢犯即中止。對測試有異議只能記錄在 spec 的「假設與未決問題」。
- **人工檢查點在最高槓桿處**:spec 通過 AI 互審後、開始花大錢實作前,暫停等人核准(可先直接編輯 spec 再繼續);流程終點是「等人 merge 的 PR」,不是靜默結束。
- **分級裁決**:`verdict.json` 為 `{approved, blockers[], suggestions[]}`,只有 blocker 擋關;suggestions 累積到 `.workflow/suggestions.md`,收尾階段逐條評估,避免審查者拿小事擋關或不好意思擋而放水。
- **小批次**:一個 checkbox 任務一個 commit,審查與回退都容易。
- **產物落地成檔案**:A/B 之間靠 `specs/` 檔案與 git diff 溝通,不靠 stdout 傳遞長內容——這是 SDD 的天然優勢。
- **A 同 stage 續 session、B 每輪全新 context**:A 修改時記得自己為什麼那樣寫;B 不被前一輪結論定錨——這也是 A/B 用不同廠牌模型互審的價值(盲點不同)。

## 前置需求

- bash 環境(Windows 用 **Git Bash** 或 WSL;macOS / Linux 原生即可)
- [`jq`](https://jqlang.github.io/jq/)
- 會用到的 AI CLI 已安裝並登入:`claude`(Claude Code)、`codex`(Codex CLI)、`agy`(Antigravity CLI,選用)
- **在目標專案的 git repo 根目錄執行**(script 會檢查)

## 快速開始

```bash
# script 與 AGENTS.md 範本要一起帶走(bootstrap 從 script 所在目錄讀範本)
cp auto-workflow.sh AGENTS.template.md /path/to/your-project/ && cd /path/to/your-project

# 預設:A = Claude Code,B = Codex
./auto-workflow.sh "為 CLI 加上 --json 輸出選項"

# 任務描述寫成檔案(建議,見下方「任務怎麼寫」)
./auto-workflow.sh task.md

# 交換角色
ENGINE_A=codex ENGINE_B=claude ./auto-workflow.sh task.md

# 輸出 AGENTS.md 規範範本(給已有 AGENTS.md 的專案手動合併)
./auto-workflow.sh print-agents
```

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
| `ENGINE_A` | `claude` | 工作者引擎:`claude` \| `codex` \| `agy` |
| `ENGINE_B` | `codex` | 審查者引擎(驗收測試 stage 兩者角色互換) |
| `MODEL_A` | (CLI 預設) | A 槽引擎的模型,例 `haiku`、`gpt-5.1-codex-mini`;便宜任務/試跑時控制成本用 |
| `MODEL_B` | (CLI 預設) | B 槽引擎的模型;A、B 同為 claude 時以 `MODEL_A` 為準 |
| `CLAUDE_ARGS` / `CODEX_ARGS` / `AGY_ARGS` | (空) | 各 CLI 的額外參數,依空白切割後附加。例:`CODEX_ARGS='-c model_reasoning_effort=low'`(ChatGPT 訂閱帳號無 mini 模型,降 reasoning effort 是主要省錢手段) |
| `MAX_ROUNDS` | `3` | 每個 stage 的審查/關卡最多輪數,超過即通知並中止 |
| `HUMAN_GATE` | `1` | spec 通過 AI 互審後暫停等人核准;無人值守設 `0`(不建議) |
| `GATE_CMD` | 自動偵測 | 完整品質關卡。go:`go build ./... && go vet ./... && go test ./...`;npm(有 test script):`npm test`;cargo:`cargo test`;偵測不到則停用並警告 |
| `BUILD_GATE_CMD` | 自動偵測 | 逐任務的輕量關卡(只驗編譯,容忍驗收測試紅燈) |
| `AUTO_BRANCH` | `1` | 自動建立 `auto/<時間戳>` branch |
| `USE_WORKTREE` | `0` | `1` = 在獨立 git worktree 執行(隔離性比 branch 好) |
| `OPEN_PR` | `0` | `1` = 結尾自動 push 並 `gh pr create`(需 gh 與 origin);預設只印指令 |
| `NOTIFY_CMD` | (空) | 通知指令,訊息以第一個參數傳入,例:`NOTIFY_CMD="ntfy publish mytopic"`。觸發點:待人工核准、各種中止、完成 |
| `AGENTS_TEMPLATE` | script 同目錄的 `AGENTS.template.md` | AGENTS.md 規範範本路徑;範本遺失時 bootstrap 會警告並跳過(流程照常) |
| `SPEC_DIR` | `specs/<時間戳>` | 規格與計畫的存放目錄 |
| `TOOLS` | git/go test/go build/go vet | Claude Code 的 `--allowedTools` 白名單。**注意 `Bash(go *)` 含 `go run`(任意程式碼執行),別圖方便放寬** |

Windows 上想在關卡跑 `-race`:`GATE_CMD='go build ./... && go vet ./... && go test -race -ldflags "-extldflags=-Wl,--default-image-base-low" ./...'`

## 產物與目錄結構

```
your-project/
├── AGENTS.template.md   # 互審規範範本(獨立檔案方便維護;簡單英文撰寫,對各家模型最通用)
├── AGENTS.md            # 互審規範(缺檔時由範本產生;既有檔案絕不覆蓋,只提示)
├── CLAUDE.md            # 缺檔時補一行指向 AGENTS.md
├── specs/<RUN_ID>/
│   ├── spec.md          # 規格(含驗收條件、假設與未決問題)
│   └── plan.md          # 實作計畫(checkbox 任務清單,完成會打勾)
├── .workflow/           # 不進版控(script 自動放 .gitignore)
│   ├── review.md        # B 的審查意見 + A 的逐條回覆
│   ├── verdict.json     # 裁決:{approved, blockers[], suggestions[]}
│   ├── suggestions.md   # 歷輪累積的不擋關建議(收尾階段逐條評估)
│   ├── protected-tests.txt / protected-base.sha   # 受保護測試檔清單與基準 commit
│   ├── pr-body.md       # 收尾產生的 PR 內文
│   ├── metrics.csv      # 每次 AI 呼叫的 stage/角色/引擎/輪次/秒數/費用
│   └── logs/<RUN_ID>.log
└── auto-workflow.sh
```

`metrics.csv` 的「審了幾輪才通過」是提示詞品質的最佳量化訊號;費用目前只有 claude 引擎會回報(`total_cost_usd`)。

## 引擎差異與限制

| | claude | codex | agy |
|---|---|---|---|
| 非互動執行 | `claude -p` | `codex exec` | `agy --print` |
| session 續接 | `--resume <id>`(精準) | `resume --last`(取最近一次) | `--continue`(取最近一次) |
| 權限控制 | `acceptEdits` + `TOOLS` 白名單 | `--sandbox workspace-write` | `--dangerously-skip-permissions`(見安全性) |
| 費用回報 | 有(metrics.csv) | 無 | 無 |

- **A 與 B 不能同時是 `codex` 或同時是 `agy`**:兩者都以「最近一次 session」續接,互審會讓工作者續接到審查者的對話,script 直接擋下。
- `codex exec resume` 沒有 `--sandbox` 旗標,script 改用 `-c 'sandbox_mode="workspace-write"'`。
- agy 的 `--print-timeout` 預設僅 5 分鐘,script 已調高(工作 60 分、審查 30 分)。
- 各 CLI 旗標以本機 `--help` 為準(本 script 依 2026-07 版本撰寫)。

## 受保護測試的逃生口

工作者(A)對驗收測試有異議時,只能把異議寫進 spec 的「假設與未決問題」,不能改測試。若測試**真的**錯了,由人工介入:直接編輯測試檔(人不受 script 限制,下一輪檢查比對的是「工作者動作後」的 diff——但注意人工改動會讓比對基準失真,建議改完後把新內容 commit 並更新 `.workflow/protected-base.sha` 為新的 commit SHA),或從 `.workflow/protected-tests.txt` 移除該檔。

## 安全性注意事項

- **agy 引擎使用 `--dangerously-skip-permissions`**(該 CLI 無細粒度白名單),只建議搭配 `USE_WORKTREE=1` 或容器使用。
- claude / codex 都以最小權限運作;真正的完整隔離是容器(devcontainer),branch/worktree 只隔離 git 狀態,不隔離檔案系統與網路。
- 兩個 AI 互審**很燒 token**:`MAX_ROUNDS`、分級裁決、`commit_if_dirty`(無變更就跳過 commit 呼叫)都是止損機制。中止時已通過的 stage 均已 commit,可從斷點人工接手。

## 自訂 stage

Stage 流程定義在 `main()` 內,由這些積木組成:`begin_stage`(重置 session)、`work <引擎> <指示>`、`review_loop <審查者> <工作者> <範圍> [關卡指令]`、`gate_loop <引擎> <關卡指令>`、`commit_work` / `commit_if_dirty`。照現有 stage 的樣式增刪即可。

## 測試

```bash
bash tests/helpers.test.sh   # 29 個單元測試,不呼叫任何 AI
```

真實 E2E(會呼叫 AI、消耗 token)建議先在小專案用便宜任務試跑一輪,確認提示詞行為符合預期。

## 疑難排解

- **`(B 未產出 verdict.json,視為未通過)`**:審查者引擎失敗或沒照規範寫檔,看 `.workflow/logs/`;連續發生會被 `MAX_ROUNDS` 擋下並通知。
- **卡在權限詢問**:headless 下沒人能按「允許」。Claude Code 需要的指令加進 `TOOLS`;codex 確認 sandbox 模式;agy 確認旗標。
- **`沒有互動終端可供核准`**:`HUMAN_GATE=1` 需要 tty;在 CI 等無人環境設 `HUMAN_GATE=0`,並用 `NOTIFY_CMD` 接手把關。
- **品質關卡在逐任務階段一直紅**:驗收測試在所有任務完成前本來就允許紅燈,逐任務只跑 `BUILD_GATE_CMD`(編譯);若連編譯關卡都過不了才會進修正迴圈。
- **換行問題**:script 必須是 LF;repo 已用 `.gitattributes` 強制 `*.sh eol=lf`。

## 延伸方向

- **Script 邏輯再複雜就換工具**:[Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview)(Python/TS)是 `claude -p` 的程式化介面,原生支援 structured output、session 物件、工具核准 callback。
- **規格模板**:前兩個 stage 可搭配 [GitHub spec-kit](https://github.com/github/spec-kit) 的 SDD 產物格式。
- **CI 無人值守**:`HUMAN_GATE=0 OPEN_PR=1` + `NOTIFY_CMD`,人改在 PR 上把關;Claude Code 與 Codex 都有官方 CI 整合。

## 參考資料

- [Run Claude Code programmatically(官方 headless 文件)](https://code.claude.com/docs/en/headless)
- [Codex CLI Non-interactive mode(官方)](https://developers.openai.com/codex/noninteractive)
- [Codex CLI reference(官方)](https://developers.openai.com/codex/cli/reference)
- [Beyond Autocomplete: Best Agentic Coding Workflow in 2026(Kilo)](https://kilo.ai/articles/beyond-autocomplete)
- [How to evaluate AI agents, avoid reward hacking, and build better specs(Arize)](https://arize.com/blog/how-to-evaluate-ai-agents-and-build-better-specs)
- [Spec-Driven Development: A Spec-First Approach to AI-Native Engineering(Microsoft)](https://developer.microsoft.com/blog/spec-driven-development-ai-native-engineering)
