# Monorepo 边界

本文说明 Claread monorepo 的目录职责和共享边界。

## 总原则

Claread 是多端产品，但不是多端共享 UI 项目。

不同客户端应分别使用最适合自身平台的技术栈、交互和 UI 实现。当前只确定微信小程序使用 Taro/React；Web 端和未来 App 端技术栈尚未确定，后续在对应阶段单独决策。

跨端共享只发生在非 UI 层：

- API 契约。
- 纯业务工具。
- 设计 token。
- 数据库和后端业务核心。
- workflow、prompt、模型路由和评测数据。

## 目录职责

```text
apps/       # 各客户端，UI 独立
services/   # 后端服务和后台 worker
packages/   # 跨端非 UI 共享包
infra/      # 本地环境、migration、部署材料
evals/      # 评测、rubric、judge run、样本集
docs/       # 产品、架构、运维、设计和参考资料
scripts/    # 仓库级辅助脚本
```

## apps/

`apps/` 下每个目录都是独立客户端。

当前：

- `apps/miniprogram/`：微信小程序客户端。
- `apps/web/`：Web 客户端预留目录，技术栈未定。
- `apps/directus/`：Directus 内部工具预留目录。

未来可增加：

- `apps/mobile/` 或其他 App 端目录，技术栈未定。

规则：

- 不跨客户端共享页面组件。
- 不为了复用牺牲平台体验。
- 客户端可以共享 contracts、design tokens、纯工具函数。
- 小程序限制不应成为 Web/App 的默认限制。

## services/

`services/` 放服务端进程。

当前：

- `services/api/`：通用 FastAPI 后端。
- `services/worker/`：后台异步任务服务预留目录。

`services/api/` 当前包含 HTTP API、分析任务 worker、workflow、词典、用户资产、Daily Reader 等能力。后续如果后台任务变重，可逐步把长任务拆到 `services/worker/`。

规则：

- 不给 Web 单独复制一套业务后端。
- 后端面向 Claread 产品能力，而不是面向某个客户端。
- 认证、记录、词典、用户资产、配额和 workflow 共享同一业务核心。

## packages/

`packages/` 放跨端非 UI 共享包。

当前目录是预留结构，具体实现后续逐步建立。

- `packages/contracts/`：API 契约和生成类型。
- `packages/shared-utils/`：纯业务工具函数。
- `packages/design-tokens/`：跨端设计 token。

不应放入：

- React/Taro 页面组件。
- Web DOM 逻辑。
- 小程序 API 封装。
- 客户端 storage 具体实现。
- 平台路由逻辑。

## infra/

`infra/` 放本地环境、migration、数据库脚本和部署材料。

当前关键内容：

- `infra/docker/docker-compose.local.yml`
- `infra/migrations/0001_initial_schema.sql`
- `infra/scripts/`

规则：

- Docker project 和 volume 使用 Claread 命名。
- 真实 `.env` 不提交。
- 数据库 schema 变动必须考虑本地 volume 初始化和已有数据升级差异。

## evals/

`evals/` 是后续 LLM-as-a-Judge、样本集、rubric 和运行记录的位置。

当前不迁入旧脚本式 regression suite。后续评测应与 Directus、LangSmith 和 Zilliz 形成闭环：

- Directus 管理样本和审核状态。
- LangSmith 观察单次运行 trace。
- evals 记录 rubric、judge run 和对比结果。
- Zilliz 承载审核后的 few-shot/RAG 示例。

## 决策提醒

新会话 agent 在新增代码前，应先判断改动属于：

- 某个客户端独立实现。
- 通用后端能力。
- 跨端非 UI 共享包。
- infra / eval / docs。

不要因为多个客户端都需要某个能力，就默认应该共享 UI。优先共享契约和数据语义，再让各端做最适合自己的体验。
