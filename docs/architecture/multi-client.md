# 多端架构

## 结论

Claread 使用一套后端业务内核，服务多个客户端。

不为 Web 端另写一套业务后端。客户端差异通过认证 adapter、render profile 和 capability profile 处理。

当前 Web、小程序和后端的能力差异，按用户可感知功能追踪在 `docs/architecture/multi-client-capability-matrix.md`。该矩阵区分某端“可操作”和“仅展示”，例如 Web 局部 text range 批注可由小程序复现展示，但小程序不一定提供同样的选区操作。

## 客户端

| 客户端 | 目录 | 定位 |
|--------|------|------|
| 微信小程序 | `apps/miniprogram/` | 当前第一个客户端，功能子集，受平台能力限制 |
| Web | `apps/web/` | 当前 baseline 已接入真实后端，后续推进高保真阅读体验 |
| Directus / Admin | `apps/directus/` | 后续内部运营、数据管理、评测样本、RAG 示例管理 |

小程序是第一个客户端，不是一次性冻结的旧客户端。迁移完成后，小程序仍会继续迭代，只是它的新增能力应在多端契约下推进。

## 后端

后端位于：

```text
services/api/
```

职责：

- 用户与身份。
- 分析任务。
- Workflow 编排。
- 模型调用。
- 结构化结果生成。
- 记录、收藏、生词、批注、反馈。
- 词典查询。
- 每日精读。
- 配额和积分。

后续可拆分出 worker 服务：

```text
services/worker/
```

用于异步任务、RAG ingestion、LLM-as-a-Judge 或 Directus action worker。当前稳定基线仍以 `services/api/` 为主。

## 数据真相源

PostgreSQL 是事务型数据真相源。

核心数据对象：

- users
- user_identities
- user_sessions
- analysis_tasks
- analysis_records
- analysis_results
- analysis_debug_snapshots
- vocabulary_book
- favorite_records
- user_annotations
- feedback
- daily_readers
- dict_entries
- dict_lookup_targets
- dict_redirects

Redis 用于缓存和多 worker 场景下的共享状态。

Zilliz 用于 Grammar RAG few-shot 示例检索。

`analysis_render_snapshots` 是后续多端 render profile 的建议表，当前 `0001` baseline 中尚不存在。

## 结果分层

分析结果应分成四层：

```text
canonical analysis result
  -> persisted render scene snapshot
  -> reader scene view
  -> client local UI state
```

### Canonical Result

后端 workflow 生成的稳定语义结果，尽量跨端复用。

### Persisted Render Scene Snapshot

后端当前已经把全量结果快照持久化到：

```text
analysis_results.render_scene_json
```

它的职责是：

- 作为当前结果真相源
- 支撑 Directus observability / Inspector
- 支撑后续 compare / eval / debug

它不是长期面向客户端阅读页的最小 contract。

### Reader Scene View

面向阅读页消费的精简视图层。

当前稳定事实：

- Web / 小程序阅读页后续统一切到专用 `reader scene view`
- `/records` 继续承担通用记录详情 / 同步真相源职责
- `reader scene view` 可以保留服务端 supplements 合并与 fallback，但不要求返回全量 render scene

长期如果要继续做多端 render profile，仍可在此基础上继续评估：

```text
analysis_render_snapshots
```

但它不是当前开发基线的前置条件。

### Client Local State

客户端本地 UI 状态，例如展开状态、滚动位置、本地同步队列，不应成为后端 canonical 模型的一部分。

## 来源元数据

记录应该保存来源，但来源不是访问边界。

当前已经稳定落地的来源 / 请求快照字段：

```text
request_payload_json
```

它当前挂在：

```text
analysis_records.request_payload_json
```

用途：

- 持久化 reading goal / variant / source_type 等请求侧事实
- 支撑 Reader 页来源信息展示
- 支撑后续 `reader scene view` 组装

除了 `request_payload_json` 之外，下面这些更细的跨客户端来源元数据仍属于后续增强项：

```text
created_client_type
created_client_version
requested_render_target
workflow_version
prompt_version
model_profile
schema_version
source_input_type
```

用途：

- 追踪记录生成来源。
- 判断当前客户端是否能直接展示。
- 必要时为目标客户端懒生成新的 render snapshot 或 reader scene view。

## 调试摘要层

当前已经有独立调试摘要表：

```text
analysis_debug_snapshots
```

它的职责不是替代 `render_scene_json`，而是补充：

- preprocess summary
- normalize / drop summary
- runtime summary
- few-shot provenance
- grammar RAG provenance
- academic quality summary

当前 v1 采用：

- 一 task 一行
- `task_id UNIQUE`
- 历史 task 允许没有 snapshot

## 认证策略

微信小程序使用：

```text
wx.login -> /auth/wechat/login -> Claread session_token
```

Web 端当前优先使用手机号验证码登录，后续可评估微信开放平台 OAuth2 或其他身份提供方。

后端不应把微信 `openid` 当业务用户主键。统一模型：

```text
users
user_identities
user_sessions
```

后端签发 Claread 自己的 session token。微信只负责证明用户身份。

## 共享与不共享

应该共享：

- API 契约。
- DTO / OpenAPI 生成类型。
- 数据库模型。
- workflow。
- 词典服务。
- 评测数据。
- 设计 token。
- 纯业务工具函数。

不应强行共享：

- 小程序页面组件。
- Web 页面组件。
- 复杂 reader UI。
- 平台 API 封装。
- 本地 storage 具体实现。

## 后续扩展方向

当前先在双端稳定基线之上评估记录来源元数据、Web 高保真 render profile、Directus、eval 和 RAG 的数据边界。具体落地顺序以后续产品与技术评审为准。
