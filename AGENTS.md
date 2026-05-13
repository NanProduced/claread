# Claread Agent 指令

本仓库是 Claread 多端 monorepo。当前可运行基线包含微信小程序客户端、通用后端、本地 PostgreSQL/Redis 和词典数据。后续继续开发 Web、Directus 内部工具、LLM-as-a-Judge 和 few-shot RAG。

## 全局原则

- 后端是通用 Claread API 服务，不是“小程序后端”。
- 小程序、Web、未来 App 共享 PostgreSQL 数据和后端业务核心。
- 客户端差异通过 auth adapter、source metadata、render profile / render snapshot 和客户端 UI 处理，不复制一套后端。
- 小程序当前稳定基线只是多端化前的可运行起点，不代表 Claread 完整功能上限。
- 不把微信小程序平台限制写成全局产品限制。

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
