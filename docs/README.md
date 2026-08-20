# Claread 文档

> **状态**: `CURRENT` | **最后验证**: 2026-08-20

本目录是 Claread 的全局文档入口。

Claread 是一个多端英文阅读辅助产品。当前基线包含微信小程序、Web 产品客户端、通用 FastAPI 后端、本地 PostgreSQL/Redis、词典数据和 Claread Console 控制面。

## 文档分层

本仓库正式文档分三层：

1. 全局文档：位于 `docs/`，说明产品、通用架构、数据、运维、评测和 RAG。
2. 服务文档：位于 `services/api/`，说明通用后端服务。
3. 客户端文档：位于 `apps/miniprogram/`、`apps/web/` 等客户端目录，说明特定平台实现。

## 当前真相源

### 产品与主线

| 文档 | 用途 |
|------|------|
| `PRODUCT.md` | impeccable 跨端产品与品牌上下文，定义 Claread 总体定位和设计原则 |
| `DESIGN.md` | impeccable 跨端设计系统，定义 Claread 品牌调性、视觉规则和组件角色 |
| `docs/product/overview.md` | 产品定位、用户、核心链路 |
| `docs/product/current-state.md` | 当前可运行基线、下一步和已知边界 |
| `docs/product/daily-reader.md` | Daily Reader 产品边界、选题生产、内容解析、阅读体验与上线验收门 |
| `docs/product/competitive-landscape.md` | 阅读、笔记、英语学习和 AI 竞品格局，以及 Claread 差异化 |
| `docs/product/product-page-direction.md` | Claread public product page 的定位、信息架构、签名 Demo、文案和视觉方向 |
| `docs/product/design-context.md` | 产品气质、阅读体验原则、跨端设计方向 |
| `docs/product/ask-claread.md` | Ask Claread 当前正式产品说明与冻结边界 |
| `docs/product/learning-annotation-policy.md` | Reader 学习批注（vocabulary / grammar / translation）当前生成质量策略 |
| `docs/development/mainline.md` | 当前开发主线和近期方向 |
| `docs/architecture/reader-orchestration.md` | Reader orchestration 当前生产架构权威上下文 |

### 架构

| 文档 | 用途 |
|------|------|
| `docs/architecture/overview.md` | 架构总览与核心边界 |
| `docs/architecture/ask-claread.md` | Ask Claread 当前正式架构说明：article-bound agent-loop runtime、turn run、受控工具与文章 RAG |
| `docs/architecture/monorepo-boundaries.md` | monorepo 目录职责和跨端共享边界 |
| `docs/architecture/multi-client.md` | 多端架构原则：一套后端、多种客户端 |
| `docs/architecture/multi-client-capability-matrix.md` | 以用户能力为观测点追踪 Web、小程序和后端共享能力差异 |
| `docs/architecture/directus-console.md` | Claread Console 的当前定位、模块边界与 Example Lab 控制面契约 |
| `docs/architecture/reader-rag.md` | Reader RAG 总契约：per-record Article RAG 与 Grammar few-shot RAG 的独立边界，以及 Grammar 的 output_fragment、retrieval_text、grammar_tags 归一化、Zilliz schema 与联动更新清单 |
| `docs/architecture/dictionary.md` | 词典架构：数据来源、查询链路与增强方向 |
| `docs/architecture/ai-usage-audit-and-billing.md` | AI 使用审计与积分结算底座 |

### 运维

| 文档 | 用途 |
|------|------|
| `docs/operations/local-dev.md` | 本地开发环境 |
| `docs/operations/testing.md` | 测试与验证入口 |
| `docs/operations/directus-local-dev.md` | Directus 本地开发与热更新说明 |
| `docs/operations/langsmith.md` | LangSmith trace 规范 |
| `docs/operations/model-config.md` | 模型 profile / preset 配置 |
| `docs/operations/prompt-versioning.md` | prompt registry 和版本规则 |

### 服务与客户端

| 文档 | 用途 |
|------|------|
| `services/api/README.md` | 后端服务启动、结构和边界 |
| `services/api/docs/api-contracts.md` | 当前后端 API 契约与 ID / 枚举语义 |
| `services/api/docs/database.md` | 数据库 baseline、词典资产保护与恢复 |
| `services/api/docs/daily-reader.md` | Daily Reader 后端 workflow、reading unit 语义和后续收口项 |
| `services/worker/README.md` | 后台 worker 预留职责 |
| `packages/README.md` | contracts、shared-utils、design-tokens 边界 |
| `apps/miniprogram/README.md` | 微信小程序客户端启动、结构和平台限制 |
| `apps/web/README.md` | Web 客户端启动、路由边界和 BFF 接入 |
| `apps/web/docs/design/surface-daily-reader.md` | Daily Reader scoped surface、排版、学习模式和收藏交互契约 |
| `apps/directus/README.md` | Claread Console 本地 runtime 与扩展工作区 |

### 治理与历史

| 文档 | 用途 |
|------|------|
| `docs/documentation-guide.md` | 文档管理与巡检指南 |
| `docs/design/AGENTS.md` | 跨端设计决策规则 |
| `docs/architecture/workflow.md` | 旧 v3 workflow 架构历史文档（旧代码已物理删除，本文保留作历史证据） |
| `docs/architecture/workflow-history.md` | 旧 workflow v0-v3 的经验教训与当前仍有效的工程原则 |
| `docs/architecture/eval-center-integration-map.md` | 旧 Eval Center / Example Lab / grammar RAG 联动说明（历史文档；当前运行时契约见 `docs/architecture/reader-rag.md`） |

`docs/reference/` 子树（differentiated、grammar-xray）是参考资料，由各子目录自身 README 索引，不作为全局真相源。

## 文档原则

- 全局文档只写 Claread 通用事实，不写某个客户端的临时实现细节。
- 小程序限制写在 `apps/miniprogram/` 内，不污染后端和 Web 架构。
- 后端文档默认描述通用 API 服务，不把后端写成“小程序后端”。
- 历史探索、临时 handoff、review、tracker 不进入正式主线文档。
- 正式文档不记录阶段执行过程，只保留当前事实与长期决策。
- 任务分配、子任务拆分、agent prompt、执行跟踪等过程文档必须标注 `TMP`，优先放到对应目录的 `tmp/` 下。
- `tmp/` 文档不作为长期事实来源。任务完成后应删除，或将仍然有效的结论压缩回正式文档。
- 定期清理进度类文档，避免文档堆积、过期信息和开发 agent 业务漂移。

## 代码结构概览

```text
claread/
├── apps/
│   ├── miniprogram/   # 当前可运行客户端
│   ├── web/           # Web 产品客户端
│   └── directus/      # Claread Console 本地 Directus runtime 与控制面扩展
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
├── evals/             # 评测数据、harness 与样本集（独立 pytest 项目；Console/Eval 控制面尚未实现）
├── docs/
│   ├── design/
│   └── reference/
└── scripts/           # 后续 / 按需
```
