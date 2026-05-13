# 后续主线流程

本文给新会话 agent 和开发者说明 Claread 当前基线之后的推进顺序。

## 总原则

先稳定已有小程序和通用后端，再扩展 Web、Directus、评测和 RAG。

不要把后续工作理解为“重做小程序”。当前小程序是第一个客户端和可运行基线；后续新增能力应服务 Claread 多端产品。

## 当前主线状态（2026-05）

Claread 已从迁移后仓库整理进入业务开发阶段。

当前焦点：

1. 保持小程序和通用后端稳定。
2. 推进 Web 首期功能页、BFF/API 接入和高保真 Reader。
3. 审计并逐步改造后端，使其显式支持多端，而不是隐式面向小程序。
4. 并行启动 Directus / 内部工具调研，但不让 Directus 取代业务后端。

当前不做：

- 不重做小程序。
- 不为 Web 复制一套业务后端。
- 不先投入完整 landing / 产品介绍页。
- 不在 Reader 尚未稳定时优先做复杂导出系统。
- 不让 Directus 直接绕过 Claread API 写入关键业务状态。

## 当前并行开发策略

从 2026-05 起，主线按四条轨道并行推进，但合流点必须清晰：

| 轨道 | 目标 | 可并行范围 | 合流点 |
| --- | --- | --- | --- |
| Web 功能主线 | 从 mock 页面进入真实 BFF/API 接入和高保真 Reader | UI、adapter、BFF route、Reader 交互、浏览器验证 | Web 可完成“输入 -> 任务 -> Reader -> 查词 -> 历史” |
| 后端多端化 | 支撑 Web、未来 App 和小程序共享同一套用户/记录/任务/词典/配额 | auth/session、records/tasks、OpenAPI/contracts、错误态 | Web BFF 可稳定调用，且小程序回归不破 |
| Directus / 内部工具 | 调研并搭建内部数据面板、样本审核和运营工具能力 | 数据访问边界、只读/可写表、权限、extension/operation、部署方式 | 明确 Directus 只作为内部工具，不成为业务后端 |
| Eval / RAG 准备 | 为 LLM-as-a-Judge、样本集、few-shot/RAG 建模 | dataset/rubric/run schema、LangSmith trace、Zilliz ingestion 边界 | Directus 可管理样本，evals 可记录 judge run |

执行原则：

- Web 复杂开发可以先在 mock + BFF contract 层推进，但不能发明和后端冲突的数据语义。
- Directus 调研可以与 Web 并行，但只做需求分析、数据边界和最小部署方案，不抢 Web 主链路资源。
- 后端多端化改动必须以小程序兼容为硬约束。
- 临时调研任务、agent prompt 和阶段跟踪必须放在 tmp 文档或会话中，结论稳定后再压缩进正式文档。

## 阶段 1：稳定当前基线

目标：确保微信小程序和通用后端在新仓库内稳定运行。

主要事项：

- 后端核心测试保持通过。
- 微信小程序构建和 TypeScript 检查保持通过。
- 微信开发者工具 smoke test 覆盖登录、解析、结果页、词典、生词本、历史、每日精读和反馈。
- 模型 profile 切换后必须重新验证结构化输出是否包含词汇、语法、句式和翻译。
- 本地 PostgreSQL/Redis、词典三表和 Docker 命名保持 Claread 规范。

完成标准：

- 新仓库可独立启动后端和小程序。
- 解析链路可产生可渲染结果。
- 主链路问题不再需要回旧仓库排查。

## 阶段 2：收紧 API 契约

目标：让后端作为多端服务而不是小程序专属后端。

主要事项：

- 补齐关键接口 `response_model`。
- 明确 `client_record_id`、`cloud_record_id`、`task_id` 语义。
- 收紧 `source_type`、records、daily reader 和 user assets 相关枚举。
- 为 `packages/contracts/` 准备 OpenAPI / DTO 生成策略。
- 保持小程序兼容，不破坏当前客户端依赖字段。

完成标准：

- 新增 Web 端时不需要猜后端响应结构。
- 小程序端不再承担隐式契约说明职责。

## 阶段 3：Web 端启动

目标：建设 Claread 高保真阅读体验。

Web 端不是小程序页面的复刻。它应共享后端和数据，但可以拥有更强的阅读、批注、排版、快捷键、布局和管理能力。

主要事项：

- 保持 `apps/web/` Next.js App Router 基础项目可构建、可类型检查。
- 先开发功能页面：`/app`、Reader、history、vocabulary、review、profile、share。
- Web 首期先覆盖小程序已有功能，再推进 Web 独有增强。
- 默认 Reader 方向为单栏阅读 + 轻旁注。
- 设计 Web auth adapter：手机号短信登录优先，后续微信开放平台登录/绑定；不破坏微信小程序登录。
- Web 首期可直接消费现有 render scene，后续再引入 `render_profile` / `presentation_profile`。
- 建立浏览器 smoke test 和截图验证。
- 规划 Grammar X-Ray、分享页、导出产物作为第二阶段增强。

完成标准：

- Web 可登录或进入可控调试态。
- Web 可读取/创建分析记录并展示结果。
- Web reader 有独立体验方向，而不是小程序样式拉伸。
- 小程序构建、类型检查和主链路继续稳定。

### 阶段 3 当前子阶段

| 子阶段 | 目标 | 状态 |
| --- | --- | --- |
| 3.0 Web mock baseline | 功能页骨架、统一 mock data、Reader demo | 已完成，见 `apps/web/docs/implementation-plan.md` |
| 3.1 Web BFF/API 接入 | Next.js BFF、server-only upstream client、session 投影、错误态 | 已完成第一轮；解析任务主链路已接入 |
| 3.2 Reader 真实渲染 | 真实 `render_scene` adapter、inline mark 交互、词典浮层、轻旁注 | BFF/API 后推进 |
| 3.3 Web 主链路闭环 | 输入分析、任务状态、Reader、历史、查词、配额 | Web 首期验收 |
| 3.4 Web 增强 | X-Ray schema 需求、分享页、导出产物、高级批注 | 首期闭环后推进 |

## 阶段 3A：后端多端化适配

目标：在兼容小程序的前提下，让当前后端从“已验证的小程序基线 API”显式演进为多端通用 API。

主要事项：

- 审计 auth、records、tasks、vocabulary、favorites、annotations、feedback、quota、dictionary 是否存在小程序假设。
- 新增或设计 Web auth adapter：手机号短信验证码登录优先，开发期可保留受控调试登录，后续接入微信开放平台登录/绑定。
- Web 浏览器通过 Next.js BFF / RSC 消费后端能力，BFF 负责 cookie/session 投影和聚合，不复制 workflow、records、quota、dictionary 等业务后端。
- 明确 `client_platform`、auth provider、source metadata、render profile / presentation profile 的演进方式。
- 补齐影响 OpenAPI / contracts 生成的 response model 和错误态。
- 保持 canonical result 稳定，客户端差异通过 adapter / projection / render profile 表达。

完成标准：

- Web 可以通过同一套后端跑通小程序已有主链路。
- 小程序依赖字段不被破坏。
- 后端文档能说明哪些是 canonical，哪些是客户端投影。
- 不出现复制的 Web 业务后端。

## 阶段 4：Directus 与内部工具

目标：用 Directus 承载内部数据可视化、运营、样本审核和部分 workflow 管理。

Directus 是内部管理面板和扩展平台，不是业务后端替代品。业务事实仍在 Claread 后端和 PostgreSQL 中。

主要事项：

- 调研 Directus 本地部署方式和 monorepo 落点，必要时再建立 `apps/directus/`。
- 明确 Directus 访问哪些表、哪些只读、哪些可写，默认从只读面板开始。
- 设计样本集、审核状态、rubric、judge run 的数据模型。
- 规划 Directus extension / operation 与后端 worker 的边界。
- 明确 Directus 对生产数据的写入规则：关键业务状态仍走 Claread API 或受控 worker。

完成标准：

- 可用面板查看核心业务数据。
- 可维护评测样本和人工审核状态。
- 不绕过后端写入关键业务状态。

### 阶段 4 当前子阶段

| 子阶段 | 目标 | 状态 |
| --- | --- | --- |
| 4.0 Directus 需求调研 | 使用场景、角色权限、数据对象、部署边界 | 可与 Web 3.1 并行 |
| 4.1 数据访问边界 | 哪些表只读、哪些表可写、哪些必须经后端 API | 调研后决策 |
| 4.2 本地最小部署 | `apps/directus/` 配置、环境变量、连接本地 PostgreSQL | 边界确认后推进 |
| 4.3 样本/eval 面板 | dataset、rubric、judge run、人工审核状态 | 与阶段 5 合流 |
| 4.4 扩展/operation | 与 worker、LangSmith、Zilliz ingestion 协作 | 后续 |

## 阶段 5：LLM-as-a-Judge 与 Few-Shot RAG

目标：把解析质量验证、样本沉淀和 few-shot 检索形成闭环。

主要事项：

- 在 `evals/` 下定义 dataset、rubric、run 记录格式。
- 使用 LangSmith trace 观察单次运行过程。
- 使用 Directus 管理样本、人工标注和审核状态。
- 将高质量审核样本进入 Zilliz，服务 Grammar RAG 和后续 few-shot。
- 明确 judge 输出如何回写、如何对比版本、如何避免污染生产数据。

完成标准：

- 可以对同一批样本比较不同 prompt/model/profile 的效果。
- few-shot 示例来自审核过的样本，不再依赖临时脚本堆积。

## 新会话 Agent 工作流

新会话开始时建议按顺序阅读：

1. `AGENTS.md`
2. `README.md`
3. `docs/README.md`
4. `docs/product/current-state.md`
5. 本文档
6. 目标目录最近的 `AGENTS.md`

执行任务前先判断任务属于哪个阶段。不要因为看到旧的小程序实现，就默认 Claread 只是微信小程序项目。

## 暂不做

- 不清理旧 Docker volume，直到新仓库稳定运行一段时间。
- 不把旧脚本式 regression suite 迁入新仓库主线。
- 不为 Web 复制一套业务后端。
- 不在全局文档里写小程序平台专属限制。
- 不把真实模型 key、微信 secret、Zilliz token 或本地 `.env` 提交到仓库。
