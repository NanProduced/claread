# 后端多端化适配计划

本文规划 Claread 后端从“小程序已验证 API”转向“多端通用 API”的路径。目标是在兼容小程序的前提下支持 Web，并为未来 App 留出空间。

## 核心判断

当前后端不需要重写。它已经支撑了小程序完整功能，说明 workflow、数据库、任务状态、词典、生词、历史和配额等核心能力有效。

真正需要做的是：

- 找出隐藏的小程序假设。
- 把客户端差异放进 auth adapter、BFF/session boundary、render profile、source metadata、capability profile。
- 保持 canonical result 稳定。
- 让 Web 先复用当前功能，再逐步拿到更丰富的渲染和导出能力。

禁止：

- 为 Web 复制一套业务后端。
- 把小程序本地 storage、页面路由、微信登录限制写入全局模型。
- 为了 Web 改破小程序 DTO。
- 让后端返回某个客户端 UI 结构，而不是返回可渲染语义。
- 把浏览器不可见当作安全边界；服务端仍必须只返回当前用户可见、当前页面需要的数据。

## 产品侧迁移策略

### 为什么先复刻小程序功能

Web 首期先实现小程序已有功能，是最低风险路线：

- 能验证同一套后端是否真正多端可用。
- 能保护小程序稳定基线。
- 能让 Web 尽快形成完整闭环。
- 能暴露 API 中隐含的小程序假设。

产品路径：

```text
小程序稳定基线
  -> Web 复刻已有主链路
  -> Web Reader / Grammar X-Ray / 分享导出增强
  -> 多端契约稳定
  -> 未来 App 复用契约
```

### Web 能力大于小程序的方式

Web 不应该通过 fork 后端获得更强能力，而应通过更丰富的 render / presentation profile 消费同一份 canonical result：

```text
canonical analysis result
  -> render scene
    -> miniprogram simplified renderer
    -> web interactive reader
    -> share/export artifact
    -> future app profile
```

## 工程分层目标

### Auth Adapter

当前：

- 小程序使用微信登录。
- session 机制已存在。

Web 目标：

- 新增 Web auth provider：手机号 + 短信验证码优先，后续接入微信开放平台登录和绑定。
- 复用 `users`、`user_identities`、`user_sessions`。
- 内部统一 Claread `user_id`，手机号、微信小程序、微信开放平台等身份都绑定到同一个内部用户。
- `client_platform` 支持 `web`。
- Web 浏览器使用 httpOnly cookie；Next.js BFF 用该 cookie 换取/携带后端内部 session token 调用 FastAPI。
- 小程序继续使用现有微信登录和本地 token 模式，不被 Web cookie 策略影响。

建议身份模型：

```text
users
  user_id = Claread internal user

user_identities
  provider = phone | wechat_miniprogram | wechat_open
  provider_user_id = normalized_phone | openid | unionid
  verified_at
  auth_payload_json

user_sessions
  user_id
  client_platform = web | wechat_miniprogram | app
  session_token_hash
  expires_at
```

绑定规则：

- 手机号登录创建或复用 `provider=phone` 的 identity。
- 用户登录后可绑定微信身份，微信 openid / unionid 只作为 identity，不直接成为业务用户 ID。
- 如果待绑定微信身份已归属另一个用户，不做静默合并，进入显式账号合并流程。
- 有 unionid 时优先用 unionid 打通小程序和开放平台；只有 openid 时只能在单一微信应用内识别。

### Web BFF / Session Boundary

Web 端采用：

```text
Browser
  -> Next.js BFF / RSC
    -> FastAPI internal API
      -> PostgreSQL / Redis / workflow
```

BFF 负责：

- Web 登录回调、短信验证码校验后的 httpOnly cookie 设置。
- 把浏览器 cookie 转换为 FastAPI 内部 `Authorization: Bearer <session_token>`。
- 聚合多个 FastAPI 接口，返回 Web view model 或 RSC 所需数据。
- 隐藏 FastAPI 源站地址、内部 token、原始 DTO 结构、workflow/prompt/model 细节。
- 处理 SSR/分享页需要的 metadata、OG 数据和权限判断。
- 统一 Web 的 CSRF、SameSite、CORS、错误态和重定向策略。

BFF 不负责：

- 复制 workflow、任务状态机、词典、生词、配额、记录、收藏等核心业务逻辑。
- 把所有数据变成“不可被 F12 看见”。浏览器最终渲染所需的数据、HTML、RSC stream 或接口返回仍可能被观察到。

大型网站常见的“看不清接口”主要来自 SSR/RSC stream、BFF 聚合、GraphQL persisted query、字段投影/重命名、压缩或二进制协议、前端混淆和关闭 sourcemap。这些只能提高逆向成本，不能替代权限校验、最小数据返回和服务端安全设计。

### Canonical API

应保持通用：

- records
- analysis tasks
- vocabulary
- favorites
- user annotations
- feedback
- quota / credits
- dictionary

这些 API 不应带“小程序页面”概念。

### Render Layer

短期：

- Web 直接消费现有 `AnyRenderSceneModel`。
- 小程序继续按当前方式渲染。

中期：

- 显式引入 `render_target` 或 `render_profile`。
- 支持 `miniprogram_compact`、`web_reader`、`share_artifact` 等投影。
- 保持 canonical result 不被客户端 UI 污染。

### Client Capability

客户端能力差异应显式化：

| 能力 | 小程序 | Web |
| --- | --- | --- |
| hover | 无 | 有 |
| text selection | 受限 | 强 |
| keyboard shortcuts | 弱 | 强 |
| side rail | 弱 | 强 |
| export PDF/image | 弱 | 强 |
| share metadata | 小程序分享 | Web SSR/OG |
| auth provider | WeChat mini program | Phone SMS + WeChat binding + cookie |

## 改造阶段

### 阶段 0：现状审计

目标：确认当前代码事实。

审计项：

- Auth 路由和 session 表。
- `client_platform` / provider 枚举。
- records 是否默认返回 Web 需要的 `render_scene_json`。
- task 状态机是否有小程序字段假设。
- user annotations 是否能支持 `text_range`。
- favorites response model 是否准确。
- daily reader schema 是否仍为宽泛 dict。
- CORS / cookie / token 生产策略。
- OpenAPI 是否足够生成 Web contracts。

当前已确认的代码事实（2026-05-13）：

- `user_identities.provider` 是自由文本，已有 `UNIQUE(provider, provider_user_id)`，可承载 `phone` / `wechat_open` 等新身份。
- `user_sessions.client_platform` 是自由文本，但 DDL 和 `create_session()` 默认值仍是 `wechat_miniprogram`；Web auth 实现时应显式传 `client_platform="web"`，并逐步去掉代码层默认值。
- `analysis_records.source_type` 和 `SourceType` 当前包含 `user_input / daily_article / imported / ocr`；Web 首期粘贴文本继续用 `user_input`，URL 导入等新来源等真实需求出现再扩展。
- `GET /records/{id}` 当前无 `include_render_scene` 参数，详情接口直接返回 `RecordResponse`；Web Reader 进入详情时使用该接口，列表页继续避免返回大体积 render scene。
- `GET /records` 当前只有 `page / limit / include_render_scene`，搜索、目标、来源、日期筛选属于 Web P1 增强。
- `TaskStatus`、Vocabulary、Annotations、Quota、Dictionary 基本无小程序平台假设，可作为 Web 首期复用基础。
- Daily Reader 详情中的 `body / highlights / paragraph_notes / takeaways` 仍是宽泛 `dict` / `list[dict]`，Web 首期可延后。
- CORS 只在浏览器直连 FastAPI 时是阻塞点；采用 Next.js BFF 后，生产优先通过服务端上游调用和同源 Web endpoint。

### 阶段 1：Web 兼容层

目标：Web 可跑通小程序已有主链路。

后端改动优先级：

1. Web auth adapter：手机号短信验证码登录，后续微信开放平台登录/绑定。
2. Next.js BFF session：浏览器 httpOnly cookie，BFF 持内部 session token 调 FastAPI。
3. `create_session()` 去掉微信默认假设或在 Web auth 中强制显式传 `provider` / `client_platform`。
4. `GET /records/{id}` 确认 Web 能拿完整 render scene。
5. 统一错误码和错误体，便于 Web 友好错误态。
6. BFF projection：不要让浏览器直接消费 `/analysis-tasks`、`/records/{id}`、`/vocabulary` 等 FastAPI 原始端点。
7. 补齐影响 OpenAPI 生成的 response model；已声明 model 但返回裸 dict 的路由可逐步清理，不阻塞 Web 首期。

验收：

- Web 可创建任务并展示结果。
- Web 可读取历史。
- Web 可查词。
- Web 可读取配额。
- 小程序主链路不变。

### 阶段 2：多端契约显式化

目标：把客户端差异从隐式字段变为显式模型。

候选新增/规范：

- `client_platform`: `wechat_miniprogram | web | app`
- `auth_provider`: `phone | wechat_miniprogram | wechat_open`
- `source_metadata`: 粘贴、URL、OCR、每日精读、导入来源。
- `render_profile`: `miniprogram_compact | web_reader | share_artifact`
- `capability_profile`: hover、selection、keyboard、export、side_rail。

注意：

- 不要一次性建过度复杂模型。
- 优先从真实 Web 需求反推字段。
- 新字段必须有默认值，不破坏小程序。

### 阶段 3：Web 增强 API

目标：支撑 Web 超过小程序。

候选 API：

- Share snapshot：生成公开分享页数据。
- Export job：长图 / PDF / Markdown。
- Rich annotations：更细粒度 text range、用户 note、批注筛选。
- Records search：按标题、原文、目标、日期筛选。
- Render profile projection：按 Web / share 返回更适合的渲染结构。
- Sentence X-Ray schema：新增结构化 `sentence_xray` / `sentence_structure`，表达主干、修饰、从句、非谓语、指代和逻辑关系。

当前 `sentence_analysis` 偏 chunks + explanation，`grammar_note` 偏 anchor + explanation，足够支撑小程序精读卡片和 Web 初期轻展开，但不足以支撑真正高保真的 Grammar X-Ray。不要在 Web 首期强行把直出文本型 schema 伪装成结构化 X-Ray。

## 风险与控制

| 风险 | 表现 | 控制方式 |
| --- | --- | --- |
| 小程序回归 | Web 改动破坏小程序字段 | 后端新增字段默认兼容，保留小程序构建和 smoke |
| Web fork 后端 | 出现 `/web/*` 业务复制 | 只允许 adapter / projection，不复制 workflow |
| 过早抽象 | render profile 设计过大 | 先 Web 复用现有 scene，按真实需求新增 |
| Auth 扩散 | 微信和 Web 用户体系割裂 | 统一 users / identities / sessions |
| 账号误合并 | 手机号、openid、unionid 冲突 | 绑定前检查归属，冲突时走显式合并 |
| F12 误解 | 以为 BFF 能让浏览器完全看不到数据 | BFF 做投影和最小返回，安全仍靠服务端鉴权 |
| UI 污染模型 | 后端返回具体 Web 卡片结构 | 返回语义数据和投影，不返回具体组件 |
| Share/export 过早 | 未稳定 Reader 就做导出 | Artifact Studio 第二阶段 |

## 后端验收命令

在 `services/api/` 下至少运行：

```powershell
uv run pytest tests/test_health.py -q
```

核心回归：

```powershell
uv run pytest tests/test_analyze_workflow.py tests/test_academic_workflow.py tests/test_task_center.py tests/test_quota_credits.py tests/test_user_assets.py tests/test_vocabulary_review.py -q
```

涉及 schema / OpenAPI 时补充：

```powershell
uv run python -m compileall app tests
```

涉及小程序兼容时回到根目录：

```powershell
pnpm miniprogram:build
pnpm miniprogram:typecheck
```

## 临时任务分配

Agent 分工、一次性 prompt 和执行跟踪不写入本文。此类内容放到 `apps/web/docs/tmp/`，完成后应删除或压缩为稳定结论，避免后端适配计划被过期任务污染。
