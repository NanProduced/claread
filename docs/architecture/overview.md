# 架构概览

Claread 是多端英文阅读辅助产品。当前可运行基线包含一个 FastAPI 后端、一个微信小程序客户端、本地 PostgreSQL/Redis 开发环境和词典数据资产。

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

其中 `apps/miniprogram/`、`services/api/`、`infra/`、`docs/` 已进入当前可运行基线。`apps/web/`、`apps/directus/`、`services/worker/`、`packages/`、`evals/` 是后续扩展位置。

## 核心边界

| 模块 | 职责 |
|------|------|
| `services/api` | 通用后端 API、认证、分析任务、用户资产、词典、Daily Reader |
| `apps/miniprogram` | 微信小程序客户端，当前稳定基线 |
| `apps/web` | 后续 Web 端，高保真阅读体验 |
| `apps/directus` | 后续内部数据面板和运营工具 |
| `infra` | Docker、migration、数据库脚本、部署材料 |
| `evals` | 后续 LLM-as-a-Judge、数据集、rubric、运行记录 |

## 数据原则

PostgreSQL 是业务事实源。Redis 是缓存和任务辅助能力。词典三表是本地高成本资产，应单独保护。

不同客户端可以有不同 render profile，但应共享 canonical analysis result、用户资产和词典数据。

## 当前基线

当前包含后端 API 服务、微信小程序客户端、数据库 baseline 和词典数据资产。Web、Directus、LLM-as-a-Judge 和 Few-shot RAG 后续接入。

迁移过程本身不是新仓库主线事实。新仓库文档只保留多端化决策、当前可运行状态和必要的开发边界。
