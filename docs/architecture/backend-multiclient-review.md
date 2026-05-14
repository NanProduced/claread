# 后端多端化架构评审

> **状态**: `DRAFT` | **最后验证**: 2026-05-14

本文记录进入 Web 第一版 UI/UX 前，对当前 FastAPI 后端支持双端并行的架构评审。它不是长期 tracker，也不代表所有方案已经拍板；后续产品与业务讨论应以这里的问题域为输入。

## 评审结论

当前后端已经能支撑微信小程序和 Web baseline 共享用户、记录、任务、词典、用户资产、配额和反馈。下一阶段不需要重写后端，但需要尽早收紧几个会影响多端并行的边界：

- API contracts 和 DTO 生成方式。
- 记录、任务、来源和 render profile 的长期语义。
- 用户资产 target / anchor / source metadata 的跨端一致性。
- Daily Reader 的结构化 schema。
- Web 需要的搜索、筛选、分页和错误态。

## 已稳定的基础

- `users` 与 `user_identities` 已分离；手机号和微信身份不直接写入 `users`。
- Web 通过 Next.js BFF/RSC 消费 FastAPI，不让浏览器直接持有内部 session token 或原始 DTO。
- `cloud_record_id` 表示后端 `analysis_records.id`；Web Reader 已使用它作为真实记录 ID。
- `analysis-tasks`、records、dict、vocabulary、review、favorites、annotations、feedback、quota 已可被 Web BFF 调用。
- 小程序构建/typecheck、Web build/typecheck/lint、后端全量测试已作为当前基线验证入口。

## 需要优先评估的问题

### 1. Contracts 生成与错误态统一

当前 Web 仍维护手写 TypeScript DTO。短期可继续，但随着 Web UI/UX 和业务增强推进，手写 DTO 会变成漂移风险。

建议评估：

- 以 FastAPI OpenAPI 作为 `packages/contracts` 的生成源。
- 生成 DTO 时保留 Web BFF 自己的 ViewModel，不把原始 DTO 直接推到页面。
- 统一错误响应形状，至少覆盖认证、额度、任务冲突、记录不存在、上游不可用和 schema mismatch。

### 2. Records / Tasks / Source Metadata

当前 `client_record_id`、`cloud_record_id`、`task_id` 已能跑通双端，但来源与渲染目标还不够清晰。

建议评估：

- 是否新增或规范 `created_client_type`、`created_client_version`、`requested_render_target`、`workflow_version`、`prompt_version`、`model_profile`。
- `GET /records` 是否增加 `reading_goal`、`source_type`、日期范围、关键词搜索和排序。
- 任务冲突、失败、额度不足时是否都返回足够的 `task_id` / `cloud_record_id` 供客户端恢复。

### 3. Render Profile / Snapshot

当前小程序和 Web 先共享 `render_scene_json`。这能支持 baseline，但不应把 Web 的高保真表现需求都塞回 canonical result。

建议评估：

- canonical analysis result、client render snapshot、client local UI state 三层边界。
- 是否需要 `analysis_render_snapshots` 或等价模型。
- Web Reader 增强是否通过 adapter/profile 表达，而不是修改小程序现有渲染假设。

### 4. 用户资产模型

favorites、vocabulary、annotations、feedback 已可用，但 Web 会比小程序更依赖精确选区、批注列表、筛选和跨记录资产管理。

建议评估：

- favorites 是否需要分页、target_type 过滤和 record 聚合状态接口。
- annotations 的 `text_range` 如何从 Web DOM selection 映射到 canonical sentence offset、occurrence 和 text hash。
- vocabulary source refs 是否需要更稳定的 source schema，避免每端自由扩展。
- feedback scope/type 常量应移入 contracts 或共享定义，避免前后端重复维护。

### 5. Daily Reader Schema

Daily Reader 当前可支撑小程序 baseline，但 response 中 `body`、`highlights`、`paragraph_notes`、`takeaways` 仍有宽 `dict` 字段。

建议评估：

- 对齐小程序已有 DTO，把 Daily Reader 详情模型结构化为 Pydantic model。
- 明确 Daily Reader 是否共享用户资产状态，例如已收藏、已加入生词本、已批注。
- Web 是否先延后 Daily Reader UI，等 schema 收紧后再设计。

### 6. Auth / Identity

手机号登录和微信小程序登录已经共存，但后续 Web 可能引入微信开放平台、邮箱或第三方登录。

建议评估：

- provider 命名、client_platform、session 生命周期和绑定/合并流程。
- 冲突账号不静默合并的交互与后台处理流程。
- Web BFF cookie、FastAPI session 和未来 refresh 机制的边界。

## 建议优先级

| 优先级 | 问题域 | 目的 |
| --- | --- | --- |
| A | Contracts、错误态、records/task/source metadata | 降低 Web UI 开发时的接口漂移和恢复态不清 |
| A | Daily Reader schema 结构化评估 | 避免 Web 后续直接消费宽 dict |
| A | annotations `text_range` 设计 | 避免 Web 高级批注返工 |
| B | favorites / vocabulary / feedback 共享常量和聚合接口 | 支撑资产管理和反馈入口扩展 |
| B | render profile / snapshot 方案 | 支撑高保真 Reader 和跨端表现差异 |
| C | SSE/WebSocket 任务进度、Directus 内部写入边界 | 体验和运营增强，非当前 UI/UX 前置阻塞 |

## 非目标

- 不重写现有小程序 API。
- 不把 Web BFF 发展成第二套业务后端。
- 不在本评审文档中决定 Web v1 产品范围。
- 不把后续 Directus/evals/RAG 的实现顺序提前定死。
