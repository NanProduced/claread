# Claread Agent 指令

本仓库是 Claread 多端 monorepo。当前可运行基线包含微信小程序客户端、通用后端、本地 PostgreSQL/Redis 和词典数据。后续继续开发 Web、Directus 内部工具、LLM-as-a-Judge 和 few-shot RAG。

## 全局原则

- 后端是通用 Claread API 服务，不是“小程序后端”。
- 小程序、Web、未来 App 共享 PostgreSQL 数据和后端业务核心。
- 客户端差异通过 auth adapter、source metadata、render profile / render snapshot 和客户端 UI 处理，不复制一套后端。
- 小程序当前稳定基线只是多端化前的可运行起点，不代表 Claread 完整功能上限。
- 不把微信小程序平台限制写成全局产品限制。

## Serena 使用约定

- 本仓库是 monorepo，不把仓库根目录注册或激活为单一 TypeScript / Python Serena 项目。
- 在单一子项目内进行代码阅读、符号定位、引用分析、重构或批量改名时，优先使用 Serena tools，而不是先退回到纯 shell / `rg` / 整文件通读。
- 使用 Serena 做符号级检索、引用分析或重构时，按当前任务激活对应子项目。
- `services/api/`：Python 后端项目，Serena 项目名 `claread-api`。
- `evals/`：Python 评测项目，Serena 项目名 `claread-evals`。
- `apps/web/`：Web TypeScript 项目，Serena 项目名 `claread-web`。
- `apps/miniprogram/`：微信小程序 TypeScript 项目，Serena 项目名 `claread-miniprogram`。
- `apps/directus/`：Directus TypeScript 项目，Serena 项目名 `claread-directus`；如任务只涉及具体 extension package，可进一步收窄范围。
- 优先顺序：已知目标文件时先用 Serena `get_symbols_overview`、`find_declaration`、`find_referencing_symbols`、`find_implementations`；需要符号级修改时优先 `rename_symbol`、`replace_symbol_body`、`insert_before_symbol`、`safe_delete_symbol`。
- 目标落点尚不明确时，先用 Serena `search_for_pattern` 做项目内粗搜，再进入符号工具；只有跨多个子项目、处理非代码文件、或 Serena 当前不支持该文件类型时，才优先用 RTK / shell / `rg`。
- TypeScript 子项目如果需要跨 sibling package 的符号引用，优先考虑在 Serena project 配置 `additional_workspace_folders`，不要默认把仓库根目录当成单一 Serena 项目。
- 只有在以下情况优先不用 Serena：跨多个子项目做仓库级摸排；处理非代码文档或配置文本；不知道目标大致落点，需要先用 RTK / shell / `rg` 粗搜；或 Serena 当前未覆盖该文件类型。
- 跨多个子项目的任务，优先用 RTK / shell / git diff 做仓库级检索；只有确实需要跨项目符号能力时，才专门配置 root Serena monorepo project，并显式配置多语言，不使用自动推断出的单一语言项目。
- 未经用户明确要求，不写 Serena memory；需要沉淀长期事实时更新正式文档或对应 `AGENTS.md`。如果 Serena memory 与代码、测试或正式文档冲突，以后者为准，并删除或覆盖过期 memory。

## 目录边界

| 目录 | 职责 |
|------|------|
| `services/api/` | 通用 API、workflow、LLM、prompt、数据库访问 |
| `apps/miniprogram/` | 微信小程序客户端 |
| `apps/web/` | Web 客户端，初期可为空 |
| `apps/directus/` | Directus 本地部署和扩展，后续 |
| `packages/` | 跨端 contracts、design tokens、shared utils，后续逐步建立 |
| `infra/` | Docker、migration、部署脚本 |
| `evals/` | LLM-as-a-Judge、样本集、few-shot/RAG 评测流，后续 |
| `docs/` | 全局产品、架构、运维、参考资料 |

## 文档规则

- 开发前先看距离最近的 `AGENTS.md`。
- 全局文档只写跨端事实；平台限制写到对应客户端目录。
- 旧仓库 `specs/`、`docs/uiux/`、临时 tracker 不作为新仓库事实来源。
- 如果代码和旧文档冲突，以当前代码和测试为准，再决定是否补改文档或建立任务。
- 任务分配、子任务拆分、agent prompt、执行跟踪等过程文档必须标注 `TMP`，优先放到对应目录的 `tmp/` 下。
- `tmp/` 文档不作为长期事实来源。任务完成后应删除，或将仍然有效的结论压缩回正式文档。
- 定期清理进度类文档，避免文档堆积、过期信息和开发 agent 业务漂移。

## 当前基线注意

- 不迁移真实密钥、个人本地路径、旧 AI 工具目录、缓存、构建产物。
- 不迁移旧脚本式 regression suite；评测路线后续用 Directus + LLM-as-a-Judge 重建。
- 后端核心测试、小程序构建和 TypeScript 检查是当前基线验证入口。
- 新仓库文档描述当前事实和架构决策，不记录搬迁过程细节。

# 💻 PowerShell 执行规范 （Codex\ChatGPT必须遵守）

在执行任何脚本、命令或处理环境交互时，所有 CLI 命令和脚本执行必须严格遵守以下 PowerShell 规则：

1. **统一 Shell 环境**：所有命令必须使用 PowerShell (推荐 `pwsh` 即 PowerShell 7 以上)。禁止使用 Windows 旧版 `cmd.exe` 或默认 `powershell.exe`。
2. **转义字符约束**：Bash 与 PowerShell 的转义规则不同，在编写跨平台脚本时，禁止混用 Shell 语法。
3. **严格的错误处理**：所有脚本首部必须包含 `$ErrorActionPreference = 'Stop'`，确保遇到错误时立即中断并报错，避免静默失败。
4. **编码规范**：脚本文件生成与读写必须强制指定 `-Encoding UTF8`，防止中文字符或特殊符号乱码。
5. **管道与对象优先**：在处理数据解析时，优先使用 PowerShell 的对象管道特性（如 `Select-Object`, `Where-Object`），避免过度依赖传统的文本截取。

## Ponytail

本仓库**不** vendoring ponytail 的 ruleset 或 skill 副本。各 agent harness 通过官方 plugin 安装与更新，见 <https://github.com/DietrichGebert/ponytail>。

### 安装（按宿主）

| Host | 安装 |
|------|------|
| Grok Build | `grok plugin install DietrichGebert/ponytail --trust`，并在 `~/.grok/config.toml` 中 `[plugins] enabled = ["ponytail"]` |
| Claude Code | `/plugin marketplace add DietrichGebert/ponytail` 后 `/plugin install ponytail@ponytail` |
| Codex | `codex plugin marketplace add DietrichGebert/ponytail` 后 `codex plugin add ponytail@ponytail` |

其他宿主按上游 README 的官方路径安装；**不要**把 skill 复制进仓库 `.agents/skills/`，也**不要**把完整 ruleset 粘进本文件。

### 使用约定（本仓库）

- **编码任务**（写代码、改代码、重构、修 bug、选依赖、做设计落地）默认走 ponytail：加载/调用官方 skill，按 skill 内 ladder 取最短可行路径。Grok Build 会按 skill description 自动匹配；未自动触发时用 `/ponytail`（或宿主等价命令）。
- **显式强度**：`/ponytail lite|full|ultra`；关闭：`/ponytail off` 或用户说 “stop ponytail” / “normal mode”。默认 `full`。
- **专用技能**（需要时再开，不替代日常 coding 模式）：
  - `/ponytail-review` — 当前 diff 的 over-engineering 审查
  - `/ponytail-audit` — 全仓 bloat 审计
  - `/ponytail-debt` — 收集代码里的 `ponytail:` 欠债注释
  - `/ponytail-gain` / `/ponytail-help` — 收益看板 / 命令速查
- **非编码请求**（纯知识、翻译、摘要、产品文案等）不要为了「省字」去开 ponytail。
- 刻意留下的简化用 `ponytail:` 注释标出天花板与升级路径，便于以后 `/ponytail-debt` 回收。
- 更新与卸载用宿主官方命令（如 `grok plugin update ponytail` / `grok plugin uninstall ponytail`），不要在仓库内维护第二份副本。
