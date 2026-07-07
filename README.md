# auto-workflow — SDD/TDD 雙 AI 互審自動化工作流

用一支 bash script 自動化「一個 AI 做事、另一個 AI 審查」的開發流程。對應的原始工作流:

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
每個 stage:
  A 執行工作(spec / plan / code / review-fix)
      │
      ▼
  B 全新 context 審查 ──── 通過 ────► A 以 conventional commit 提交 ──► 下一個 stage
      │
    未通過(意見寫入 .workflow/review.md)
      │
      ▼
  A 逐條回應意見並修改(同一個 session,記得前因後果)
      │
      ▼
  B 再審(= 「最後確認」)……最多 MAX_ROUNDS 輪,超過即停下等人工介入
```

內建四個 stage:**訂規格 → 規劃實作計畫 → 撰寫程式碼(TDD)→ 整體 review 與修 bug**。
原始偽碼中的「修 bug」由每個 stage 內建的「審查 → 修改 → 再審」迴圈涵蓋,最後一個 stage 再做一次全局收尾。

## 前置需求

- bash 環境(Windows 用 **Git Bash** 或 WSL;macOS / Linux 原生即可)
- [`jq`](https://jqlang.github.io/jq/)
- 會用到的 AI CLI 已安裝並登入:
  - `claude`(Claude Code)
  - `codex`(Codex CLI)
  - `agy`(Antigravity CLI,選用)
- **在目標專案的 git repo 根目錄執行**(script 會檢查,不是 repo 會直接拒絕)

## 快速開始

```bash
# 把 script 複製到你的專案根目錄(或加入 PATH)
cp auto-workflow.sh /path/to/your-project/
cd /path/to/your-project

# 預設:A = Claude Code,B = Codex
./auto-workflow.sh "為 CLI 加上 --json 輸出選項"

# 交換角色:A = Codex,B = Claude Code
ENGINE_A=codex ENGINE_B=claude ./auto-workflow.sh "重構設定檔載入邏輯"
```

## 環境變數

| 變數 | 預設值 | 說明 |
|---|---|---|
| `ENGINE_A` | `claude` | 工作者引擎:`claude` \| `codex` \| `agy` |
| `ENGINE_B` | `codex` | 審查者引擎:`claude` \| `codex` \| `agy` |
| `MAX_ROUNDS` | `3` | 每個 stage 最多審查輪數,超過即中止等人工介入 |
| `AUTO_BRANCH` | `1` | `1` = 自動建立 `auto/<時間戳>` branch;`0` = 用目前 branch |
| `SPEC_DIR` | `specs/<時間戳>` | 規格與計畫檔的存放目錄 |
| `TOOLS` | `Bash(git *),Bash(go *)` | Claude Code 的 `--allowedTools` 白名單,依專案調整(例如 `Bash(git *),Bash(npm *)`) |

## 產物與目錄結構

```
your-project/
├── specs/<RUN_ID>/
│   ├── spec.md          # stage 1 產物:規格(含驗收條件)
│   └── plan.md          # stage 2 產物:實作計畫(含測試策略、commit 切分)
├── .workflow/           # 不進版控(script 自動放了 .gitignore)
│   ├── review.md        # B 的審查意見;A 的逐條回覆也寫在這裡
│   ├── verdict.json     # B 的裁決:{"approved": true|false}
│   └── logs/<RUN_ID>.log# 完整執行紀錄
└── auto-workflow.sh
```

每個 stage 通過審查後,由 A 以 conventional commit 格式提交(英文訊息 + 詳細 body)。

## 設計說明

- **產物落地成檔案**:A 與 B 之間不靠 stdout 傳遞長內容;B 直接讀 `specs/` 的檔案與 `git diff`,這是 SDD 的天然優勢。
- **A 同一 stage 內延續 session**:修改時記得自己當初為什麼那樣寫。Claude Code 用 `--resume <session_id>`;Codex 用 `codex exec resume --last`;Antigravity 用 `--continue`。跨 stage 則重置 context,靠檔案接續,避免 context 汙染。
- **B 每輪全新 context**:避免審查被前一輪結論定錨,這也是 A/B 用不同廠牌模型互審的價值所在(盲點不同)。
- **裁決結構化**:B 產出 `verdict.json`,script 用 `jq` 判斷是否放行。B 用 Claude 時走 `--json-schema`,由結構化輸出保證格式合法;其他引擎用提示詞要求寫檔。
- **有限迴圈**:`MAX_ROUNDS` 防止兩個 AI 互相不服氣而無限燒 token;A 對不同意的意見會在 `review.md` 回覆理由,B 下一輪會先驗證這些回覆。

## 引擎差異與限制

| | claude | codex | agy |
|---|---|---|---|
| 非互動執行 | `claude -p` | `codex exec` | `agy --print` |
| session 續接 | `--resume <id>`(精準) | `resume --last`(取最近一次) | `--continue`(取最近一次) |
| 權限控制 | `--permission-mode acceptEdits` + `--allowedTools` | `--sandbox workspace-write` | `--dangerously-skip-permissions`(見下方警告) |

- **A 與 B 不能同時是 `codex` 或同時是 `agy`**:兩者都是以「最近一次 session」續接,B 的審查 session 會蓋掉「最近一次」,讓 A 續接到錯的對話。script 會直接擋下這種組合。
- **Claude Code 的 session 是以目錄為範圍**:整個流程都在專案根目錄執行即可(script 已保證)。
- **`codex exec resume` 沒有 `--sandbox` 旗標**:script 改用 `-c 'sandbox_mode="workspace-write"'` 覆寫設定。
- **Antigravity 的 `--print-timeout` 預設只有 5 分鐘**:script 已對工作階段調高為 60 分鐘、審查階段 30 分鐘。

## 安全性注意事項

- **agy 引擎使用 `--dangerously-skip-permissions`**,會自動核准所有工具操作。只建議在隔離環境(獨立 branch 是基本,容器更好)使用,或改研究 `agy --sandbox` 是否符合你的需求。
- claude / codex 引擎都以最小權限運作:Claude Code 只放行 `TOOLS` 白名單內的指令與檔案編輯;Codex 限制在 workspace 內寫入、無網路。
- 預設會自動開新 branch(`AUTO_BRANCH=1`),失敗了整條 branch 刪掉即可,不影響主線。
- 兩個 AI 輪流工作與審查**很燒 token**,`MAX_ROUNDS` 與階段化 commit 就是止損機制。中止時,已通過的 stage 都已 commit,可以從斷點人工接手。

## 自訂 stage

Stage 定義在 script 底部,格式為:

```bash
stage "名稱" \
  "給 A 的工作指示" \
  "給 B 的審查範圍"
```

直接增刪或改寫即可。例如在最後追加一個文件 stage:

```bash
stage "更新文件" \
  "依本 branch 的變更更新 README 與 CHANGELOG。" \
  "文件是否與實際行為一致、範例是否可執行。"
```

## 疑難排解

- **`B 未產出 verdict.json,視為未通過`**:B 引擎執行失敗或沒照指示寫檔。看 `.workflow/logs/` 找原因;連續發生會被 `MAX_ROUNDS` 擋下。
- **卡在權限詢問不動**:非互動模式下沒有人能按「允許」。Claude Code 需要的指令要加進 `TOOLS`;codex 確認 sandbox 模式;agy 確認有帶自動核准旗標。
- **`codex exec` 拒絕執行**:Codex 預設要求在 git repo 內執行,本 script 本來就強制此條件;若你改了 script,注意這點。
- **Windows 下換行問題**:script 必須是 LF 換行。若 git 設定了 `autocrlf=true`,建議在 `.gitattributes` 加上 `*.sh text eol=lf`。

## 延伸方向

- **Codex 內建審查**:`codex exec review --uncommitted`(審未提交變更)/ `--base <branch>`(對基準分支審查)是現成的審查指令,可改造 `b_codex` 使用。
- **Script 變複雜時**:改用 [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview)(Python / TypeScript),它是 `claude -p` 的程式化介面,原生支援 structured output、session 物件與工具核准 callback。
- **規格模板**:前兩個 stage 可搭配 [GitHub spec-kit](https://github.com/github/spec-kit) 的 SDD 產物格式。
- **無人值守**:整支 script 可直接放進 CI(GitHub Actions 等);Claude Code 與 Codex 都有官方 CI 整合。

## 參考資料

- [Run Claude Code programmatically(官方 headless 文件)](https://code.claude.com/docs/en/headless)
- [Codex CLI Non-interactive mode(官方)](https://developers.openai.com/codex/noninteractive)
- [Codex CLI reference(官方)](https://developers.openai.com/codex/cli/reference)
- 各 CLI 的實際旗標以 `claude --help`、`codex exec --help`、`agy --help` 為準(本 script 依 2026-07 本機版本撰寫)
