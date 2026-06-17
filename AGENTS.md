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
- 使用 Serena 做符号级检索、引用分析或重构时，按当前任务激活对应子项目。
- `services/api/`：Python 后端项目，Serena 项目名 `claread-api`。
- `evals/`：Python 评测项目，Serena 项目名 `claread-evals`。
- `apps/web/`：Web TypeScript 项目，Serena 项目名 `claread-web`。
- `apps/miniprogram/`：微信小程序 TypeScript 项目，Serena 项目名 `claread-miniprogram`。
- `apps/directus/`：Directus TypeScript 项目，Serena 项目名 `claread-directus`；如任务只涉及具体 extension package，可进一步收窄范围。
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
