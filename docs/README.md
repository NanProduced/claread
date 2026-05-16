# Claread 文档

本目录是 Claread 的全局文档入口。

Claread 是一个多端英文阅读辅助产品。当前稳定基线包含微信小程序、Web baseline、通用 FastAPI 后端、本地 PostgreSQL/Redis 和词典数据。后续会继续建设 Web 产品体验、内部运营工具、评测系统和 RAG 数据流。

## 文档分层

新仓库文档分三层：

1. 全局文档：位于 `docs/`，说明产品、通用架构、数据、运维、评测和 RAG。
2. 服务文档：位于 `services/api/`，说明通用后端服务。
3. 客户端文档：位于 `apps/miniprogram/`、`apps/web/` 等客户端目录，说明特定平台实现。

## 当前真相源

| 文档 | 用途 |
|------|------|
| `PRODUCT.md` | impeccable 跨端产品与品牌上下文，定义 Claread 总体定位和设计原则 |
| `DESIGN.md` | impeccable 跨端设计系统，定义 Claread 品牌调性、视觉规则和组件角色 |
| `docs/product/overview.md` | 产品定位、用户、核心链路 |
| `docs/product/current-state.md` | 当前可运行基线、下一步和已知边界 |
| `docs/product/competitive-landscape.md` | 阅读、笔记、英语学习和 AI 竞品格局，以及 Claread 差异化 |
| `docs/development/mainline.md` | 当前开发主线和近期方向 |
| `docs/product/design-context.md` | 产品气质、阅读体验原则、跨端设计方向 |
| `docs/architecture/monorepo-boundaries.md` | monorepo 目录职责和跨端共享边界 |
| `docs/architecture/multi-client.md` | 多端架构原则：一套后端、多种客户端 |
| `docs/architecture/multi-client-capability-matrix.md` | 以用户能力为观测点追踪 Web、小程序和后端共享能力、文本选区、批注收藏与学习资产差异 |
| `docs/architecture/backend-multiclient-review.md` | 后端多端化架构评审和待评估问题域 |
| `docs/architecture/workflow.md` | 当前 workflow 基线 |
| `docs/operations/langsmith.md` | LangSmith trace 规范 |
| `docs/operations/model-config.md` | 模型 profile / preset 配置 |
| `docs/operations/prompt-versioning.md` | prompt registry 和版本规则 |
| `docs/design/AGENTS.md` | 跨端设计决策规则 |
| `services/api/README.md` | 后端服务启动、结构和边界 |
| `services/worker/README.md` | 后台 worker 预留职责 |
| `packages/README.md` | contracts、shared-utils、design-tokens 边界 |
| `apps/miniprogram/README.md` | 微信小程序客户端启动、结构和平台限制 |

## 文档原则

- 全局文档只写 Claread 通用事实，不写某个客户端的临时实现细节。
- 小程序限制写在 `apps/miniprogram/` 内，不污染后端和 Web 架构。
- 后端文档默认描述通用 API 服务，不把后端写成“小程序后端”。
- 历史探索、临时 handoff、review、tracker 不进入新仓库主线文档。
- 不把迁移过程写成主线文档；必要时只记录多端化决策、当前目录边界和可运行状态。
- 任务分配、子任务拆分、agent prompt、执行跟踪等过程文档必须标注 `TMP`，优先放到对应目录的 `tmp/` 下。
- `tmp/` 文档不作为长期事实来源。任务完成后应删除，或将仍然有效的结论压缩回正式文档。
- 定期清理进度类文档，避免文档堆积、过期信息和开发 agent 业务漂移。

## 代码结构概览

```text
claread/
├── apps/
│   ├── miniprogram/   # 当前可运行客户端
│   ├── web/           # Web baseline 与后续 Web 产品体验
│   └── directus/      # 后续
├── services/
│   ├── api/           # 当前通用后端
│   └── worker/        # 后续
├── packages/
│   ├── contracts/     # 已落地：跨端契约常量和类型，后续接 OpenAPI 生成
│   ├── design-tokens/ # 品牌资产与设计 token
│   └── shared-utils/  # 后续
├── infra/
│   ├── docker/
│   ├── migrations/
│   └── deploy/        # 后续
├── evals/             # 后续
├── docs/
│   ├── design/
│   └── reference/
└── scripts/           # 后续 / 按需
```
