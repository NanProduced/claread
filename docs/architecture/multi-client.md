# 多端架构

## 结论

Claread 使用一套后端业务内核，服务多个客户端。

不为 Web 端另写一套业务后端。客户端差异通过认证 adapter、render profile 和 capability profile 处理。

## 客户端

| 客户端 | 目录 | 定位 |
|--------|------|------|
| 微信小程序 | `apps/miniprogram/` | 当前第一个客户端，功能子集，受平台能力限制 |
| Web | `apps/web/` | 后续客户端，高保真阅读体验 |
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

用于异步任务、RAG ingestion、LLM-as-a-Judge 或 Directus action worker。当前迁移基线仍以 `services/api/` 为主。

## 数据真相源

PostgreSQL 是事务型数据真相源。

核心数据对象：

- users
- user_identities
- user_sessions
- analysis_tasks
- analysis_records
- analysis_results
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

分析结果应分成三层：

```text
canonical analysis result
  -> client render snapshot
  -> client local UI state
```

### Canonical Result

后端 workflow 生成的稳定语义结果，尽量跨端复用。

### Render Snapshot

面向某个客户端的渲染投影。

当前小程序 render scene 仍保存在分析记录相关 JSON 字段中。后续建议新增模型：

```text
analysis_render_snapshots
```

建议字段：

```text
record_id
target_client        # miniprogram / web / app / admin
schema_version
renderer_version
capability_profile   # mini_basic / web_rich / mobile_app
render_scene_json
created_at
```

### Client Local State

客户端本地 UI 状态，例如展开状态、滚动位置、本地同步队列，不应成为后端 canonical 模型的一部分。

## 来源元数据

记录应该保存来源，但来源不是访问边界。

当前 baseline 尚未包含以下字段。后续建议增加：

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
- 必要时为目标客户端懒生成新的 render snapshot。

## 认证策略

微信小程序使用：

```text
wx.login -> /auth/wechat/login -> Claread session_token
```

Web 端后续使用微信开放平台 OAuth2 或其他身份提供方。

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

当前先恢复小程序和后端稳定。随后再为记录增加客户端来源元数据，为 Web 引入更高保真的 render profile，并让 Directus、eval 和 RAG 读取同一套后端数据边界。
