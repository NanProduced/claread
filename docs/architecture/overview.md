# 架构概览

Claread 是多端英文阅读辅助产品。当前可运行基线包含一个通用 FastAPI 后端、微信小程序客户端、Web baseline、本地 PostgreSQL/Redis 开发环境和词典数据资产。

## 目标结构

```text
claread/
├── apps/
│   ├── miniprogram/
│   ├── web/
│   └── directus/
├── services/
│   ├── api/
│   └── worker/
├── packages/
│   ├── contracts/
│   ├── design-tokens/
│   └── shared-utils/
├── infra/
├── evals/
└── docs/
```

其中 `apps/miniprogram/`、`apps/web/`、`apps/directus/`、`services/api/`、`infra/`、`packages/contracts/`、`packages/design-tokens/`、`evals/`、`docs/` 已进入当前可运行基线。`services/worker/`、`packages/shared-utils/` 是后续扩展位置。

## 核心边界

| 模块 | 职责 |
|------|------|
| `services/api` | 通用后端 API、认证、Reader orchestration、Ask Claread、用户资产、词典、Daily Reader |
| `apps/miniprogram` | 微信小程序客户端，当前稳定基线 |
| `apps/web` | Web baseline 与后续高保真阅读体验 |
| `packages/contracts` | 跨端契约常量和类型，当前覆盖批注/收藏/text range 基础常量 |
| `apps/directus` | 内部控制面（Claread Console），当前承载 metadata 展示、LLM Config、reader-orch 只读诊断和 Example Lab |
| `infra` | Docker、migration、数据库脚本、部署材料 |
| `evals` | 离线 artifact / evaluation harness（数据集、rubric、baselines），当前已有可运行离线基线 |

## 数据原则

PostgreSQL 是业务事实源。Redis 是缓存和任务辅助能力。词典三表是本地高成本资产，应单独保护。

不同客户端可以有不同 render profile，但应共享 canonical Reader 事实（Stable Document / Reading Units / Anchor Segments / Enhancement Layers）、用户资产和词典数据。

## 当前基线

当前包含后端 API 服务、微信小程序客户端、Web baseline、Directus 内部控制面、数据库 baseline、词典数据资产，以及 `evals/` 下可离线运行的 artifact / evaluation harness。真实 provider evaluation 必须显式 opt-in，默认离线门禁不调用 provider；具体命令与当前验收方式见 `docs/operations/testing.md`。尚未实现：Claread Console / Eval 治理化控制面、Directus → seed → Zilliz few-shot RAG promotion，以及文章解析标注质量提升。

迁移过程本身不是新仓库主线事实。新仓库文档只保留多端化决策、当前可运行状态和必要的开发边界。
