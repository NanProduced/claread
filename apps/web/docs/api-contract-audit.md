# API 契约审计

> **状态**: `CURRENT` | **最后更新**: 2026-05-18

本文审计 Claread Web 首期需要的后端接口、字段、枚举和错误态，以及当前在 OpenAPI / response_model 中的稳定性。

审计基于 `services/api/` 当前代码（2026-05），对照小程序冻结基线和小程序 API 消费模式。

Web 首期不建议让浏览器直接消费 FastAPI 原始端点。推荐路径是：

```text
Browser -> Next.js BFF / RSC -> FastAPI internal API
```

因此本文中的 FastAPI 接口多为 BFF 的上游契约；浏览器侧应优先看到 Web 专用投影接口、RSC 数据流或 SSR HTML，而不是直接看到后端原始 DTO、内部 session token 和 workflow 结构。

## Web 首期接口审计

### 认证

| 接口 | response_model | 当前状态 | Web 注意事项 |
|------|---------------|---------|-------------|
| `POST /auth/wechat/login` | ✅ `WeChatLoginResponse` | 🟢 稳定 | 小程序继续使用；Web 后续走微信开放平台登录/绑定，不直接复用小程序 openid |
| `GET /auth/session/me` | ✅ `SessionInfoResponse` | 🟢 稳定 | BFF 上游可复用；浏览器侧由 `/api/web/session` 等 Web endpoint 投影 |
| `PATCH /auth/profile` | ✅ `ProfileUpdateResponse` | 🟢 稳定 | BFF 上游可复用 |
| `POST /auth/session/logout` | ✅ `LogoutResponse` | 🟢 稳定 | BFF 清除 httpOnly cookie 后调用上游登出 |
| `POST /auth/phone/request-code` | ✅ `PhoneCodeResponse` | 🟢 已落地 | Web 登录入口；开发期 mock code `888888`，生产 provider 使用阿里云 Dypnsapi |
| `POST /auth/phone/verify-code` | ✅ `WeChatLoginResponse` 兼容形状 | 🟢 已落地 | 验证手机号验证码，创建 `provider=phone` identity 和 `client_platform=web` session；BFF 写入 httpOnly cookie |
| `POST /auth/phone/bind` | ✅ `IdentityBindResponse` | 🟢 已落地 | 登录用户绑定手机号身份；冲突返回 409，不静默合并 |
| `POST /auth/wechat/bind` | ✅ `IdentityBindResponse` | 🟢 已落地 | 登录用户绑定微信小程序身份；冲突返回 409，不静默合并 |

微信身份归属规则：`openid` 按 provider/app 隔离，`unionid` 可空但一旦出现，应作为跨微信应用归属线索。同一非空 `unionid` 下允许存在多个 provider/openid identity，但必须归属同一个 Claread `user_id`；如果同 `unionid` 或同 provider identity 已归属其他 `user_id`，API 返回 409，由显式账号合并流程处理。

### 分析

| 接口 | response_model | 当前状态 | Web 注意事项 |
|------|---------------|---------|-------------|
| `POST /analyze` | ✅ `AnyRenderSceneModel` | 🟢 稳定 | 匿名/调试用，Web 可用 |
| `POST /analysis-tasks` | ✅ `TaskSubmitResponse` | 🟢 稳定 | Web 主链路，`wait_for_result=true` 对 Web 更友好 |
| `GET /analysis-tasks/current` | ✅ `ActiveTaskResponse` | 🟢 稳定 | Web 复用 |
| `GET /analysis-tasks/{task_id}` | ✅ `TaskStatusResponse` | 🟢 稳定 | Web 轮询或 SSE |

Web BFF 必须使用 `cloud_record_id` 作为 Reader 记录 ID。`record_id` 仍在 FastAPI response model 中保留给旧调用方兼容，但 Web 投影层不应回退读取该 deprecated 字段。任务失败、超时等待失败和活跃任务冲突等错误响应也应携带 `cloud_record_id`，便于 Web 展示“打开当前任务”等操作而不暴露 FastAPI 原始 DTO。

### 记录

| 接口 | response_model | 当前状态 | Web 注意事项 |
|------|---------------|---------|-------------|
| `POST /records` | ✅ `RecordUpsertResponse` | 🟢 稳定 | Web 需自己生成 `client_record_id` |
| `GET /records` | ✅ `RecordListResponse` | 🟢 稳定 | Web 需搜索/筛选扩展（按 goal/type/日期） |
| `GET /records/{record_id}` | ✅ `RecordResponse` | 🟢 稳定 | 详情接口无 `include_render_scene` 参数；当前返回完整记录详情，Web Reader 进入详情时消费此接口 |
| `GET /records/by-client-id/{id}` | ✅ `RecordResponse` | 🟢 稳定 | Web 复用 |
| `PATCH /records/{record_id}` | ✅ `RecordResponse` | 🟢 稳定 | Web 复用 |
| `DELETE /records/{record_id}` | ✅ `RecordDeleteResponse` | 🟡 可清理 | 已声明 response_model，当前返回 `{"deleted": True}` 裸 dict，可改为 model 实例 |

### 配额

| 接口 | response_model | 当前状态 | Web 注意事项 |
|------|---------------|---------|-------------|
| `GET /me/quota` | ✅ `QuotaResponse` | 🟢 稳定 | Web 复用 |
| `GET /me/quota/anonymous` | ✅ `AnonymousQuotaResponse` | 🟢 稳定 | Web 复用 |
| `POST /me/quota/check` | ✅ `QuotaCheckResponse` | 🟢 稳定 | Web 复用 |
| `GET /me/credit/ledger` | ✅ `LedgerListResponse` | 🟢 稳定 | Web 复用 |

### 词典

| 接口 | response_model | 当前状态 | Web 注意事项 |
|------|---------------|---------|-------------|
| `GET /dict` | ✅ `DictionaryLookupResult` | 🟢 稳定 | Web 浮层高频调用，已有 Cache-Control: max-age=3600 |
| `GET /dict/entry` | ✅ `DictionaryEntryResult` | 🟢 稳定 | Web 词典详情页 |
| `POST /dict/ai` | ✅ `DictionaryAIResponse` | 🟢 已接入 | Web Reader 通过同源 `/api/web/dict/ai` 调用；`context_explain` 仅用于正文点词后的 canonical entry，`missing_fallback` 仅用于正文点词后的 canonical not_found；`401 / 402 / 404 / 409 / 502 / 503` 需在词典卡内局部处理，不污染 canonical `/dict` 主状态 |

### 生词本

| 接口 | response_model | 当前状态 | Web 注意事项 |
|------|---------------|---------|-------------|
| `POST /vocabulary` | ✅ `VocabularyUpsertResponse` | 🟢 稳定 | Web Reader 已通过 BFF 写入生词 |
| `GET /vocabulary` | ✅ `VocabularyListResponse` | 🟢 稳定 | Web 生词本已接入 |
| `PATCH /vocabulary/{id}` | ✅ `VocabularyResponse` | 🟢 稳定 | Web 后续可用于资产管理 |
| `DELETE /vocabulary/{id}` | ✅ `VocabularyDeleteResponse` | 🟢 稳定 | Web 后续可用于资产管理 |
| `POST /vocabulary/highlights` | ✅ `VocabHighlightsResponse` | 🟢 稳定 | Web reader 高亮匹配 |
| `GET /vocabulary/review/due` | ✅ `ReviewResultResponse` | 🟢 稳定 | Web 复习页已接入 |
| `POST /vocabulary/review/submit` | ✅ `ReviewResultResponse` | 🟢 稳定 | Web 复习页已接入 |

### 收藏

| 接口 | response_model | 当前状态 | Web 注意事项 |
|------|---------------|---------|-------------|
| `POST /favorites` | ✅ `FavoriteCreateResponse` | 🟢 稳定 | 支持 `target_type='text_range' / 'multi_text'`，并校验 selected text、UTF-16 offset、hash 与 multi_text segments |
| `GET /favorites` | ✅ `FavoriteListResponse` | 🟡 需增强 | Web Reader 已接入；摘录页已不再依赖它做前端 fan-out 聚合 |
| `DELETE /favorites/target` | ✅ `FavoriteDeleteResponse` | 🟢 稳定 | Web Reader 取消收藏使用此接口 |
| `DELETE /favorites/{analysis_record_id}` | ✅ `FavoriteDeleteResponse` | 🟢 稳定 | 兼容按分析记录取消收藏 |

### 摘录资产

| 接口 | response_model | 当前状态 | Web 注意事项 |
|------|---------------|---------|-------------|
| `GET /excerpt-assets` | ✅ `ExcerptAssetsResponse` | 🟢 已落地 | 正式摘录聚合接口；按文章分组返回 merged anchor asset，保留 `target_key` / `sentence_id` / `start_offset` / `end_offset` / `segments[]` 和 `insights[]` sidecar，供 Web `/library/assets` 与小程序 `packageA/excerpts` 共用 |

### 反馈

| 接口 | response_model | 当前状态 | Web 注意事项 |
|------|---------------|---------|-------------|
| `POST /feedback` | ✅ `FeedbackResponse` | 🟢 稳定 | Web Settings 已接入；Reader 反馈待 UI/UX 评审 |
| `GET /feedback` | ✅ `FeedbackListResponse` | 🟢 稳定 | Web 后续可用于反馈历史或内部入口 |

### 每日精读

| 接口 | response_model | 当前状态 | Web 注意事项 |
|------|---------------|---------|-------------|
| `GET /daily-reader/today` | ✅ `DailyReaderTodayResponse` | 🟡 需增强 | `body`/`highlights`/`paragraph_notes`/`takeaways` 均为 `dict` |
| `GET /daily-reader` | ✅ `DailyReaderListResponse` | 🟢 稳定 | 列表项不含 body，结构已明确 |
| `GET /daily-reader/{article_id}` | ✅ `DailyReaderArticleResponse` | 🟡 需增强 | 同 today，详情的 body 等字段未结构化 |

### 批注

| 接口 | response_model | 当前状态 | Web 注意事项 |
|------|---------------|---------|-------------|
| `POST /user-annotations` | ✅ `UserAnnotationResponse` | 🟢 稳定 | Web 批注核心；`text_range` / `multi_text` 会校验 selected text、UTF-16 offset、hash、segments 和 render scene sentence 切片 |
| `GET /user-annotations` | ✅ `UserAnnotationListResponse` | 🟢 稳定 | Web 复用 |
| `PATCH /user-annotations/{id}` | ✅ `UserAnnotationResponse` | 🟢 稳定 | Web 复用 |
| `DELETE /user-annotations/{id}` | ✅ `{"ok": True}` | 🟢 稳定 | Web 复用 |

### 健康

| 接口 | response_model | 当前状态 | Web 注意事项 |
|------|---------------|---------|-------------|
| `GET /health` | ✅ | 🟢 稳定 | Web 前端健康检查 |

## 枚举审计

### 已稳定枚举（Web 完整复用）

| 枚举 | 当前值 | 来源 |
|------|--------|------|
| `SourceType` | `user_input / daily_article / imported / ocr` | `schemas/analysis.py` |
| `ReadingGoal` | `exam / daily_reading / academic` | `schemas/internal/analysis.py` |
| `ReadingVariant` | 9 种：`gaokao / cet / kaoyan / tem / ielts_toefl / beginner_reading / intermediate_reading / intensive_reading / academic_general` | `schemas/internal/analysis.py` |
| `TaskStatus` | `queued / running / finalizing / succeeded / failed / cancelled / expired` | `schemas/tasks.py` |
| `UserFacingState` | `normal / degraded_light / degraded_heavy` | `schemas/analysis.py` |
| `MasteryStatus` | `new / learning / review / mastered / archived` | DB CHECK |
| `AnnotationType` | `highlight / note` | DB CHECK + `@claread/contracts` |
| `AnchorType` | `sentence / paragraph / text_range / multi_text` | DB CHECK + `@claread/contracts` |
| `AnnotationColor` | `soft_green / soft_blue / soft_purple / warm_yellow / sage_green` | DB CHECK + `@claread/contracts` |
| `FavoriteTargetType` | `analysis_record / sentence / paragraph / phrase / vocab / text_range / multi_text` | DB CHECK + `@claread/contracts` |
| `FeedbackScope` | `analysis_result / annotation / sentence / dictionary / app` | `schemas/feedback.py` |
| `Sentiment` | `positive / negative / neutral` | `schemas/feedback.py` |
| `InlineMarkRenderType` | `background / underline` | `schemas/analysis.py` |
| `VisualTone` | `vocab / phrase / context / grammar` | `schemas/analysis.py` |
| `AnnotationType (mark)` | `vocab_highlight / phrase_gloss / context_gloss / grammar_note` | `schemas/analysis.py` |
| `AcademicAnnotationType` | `term_note / logic_note` | `schemas/analysis.py` |
| `AcademicVisualTone` | `term / logic` | `schemas/analysis.py` |
| `SchemaVersion` | `3.0.0 / 3.0.0-academic` | `schemas/analysis.py` |

### Web 可能需要扩展的枚举

| 枚举 | 当前值 | Web 扩展建议 |
|------|--------|-------------|
| `SourceType` | `user_input / daily_article / imported / ocr` | 新增 `web_clip` 或 `url_import`（Web 端粘贴 URL 导入） |
| `client_platform` (session) | `wechat_miniprogram` | 新增 `web` |
| `provider` (identity) | `wechat_miniprogram` | 新增 `phone`，后续新增 `wechat_open` |

## 错误态审计

### HTTP 状态码

| 状态码 | 触发场景 | 当前处理 | Web 需要补充 |
|--------|---------|---------|-------------|
| 400 | 请求参数无效 | FastAPI 自动 422 | Web 需表单验证 + 友好提示 |
| 401 | token 无效/过期 | `HTTPException(401, detail=...)` | Web 需自动跳登录页 + token 刷新 |
| 404 | 记录/词条不存在 | `HTTPException(404, detail=...)` | Web 需空态/404 页面 |
| 422 | 模型选择失败 / 参数校验失败 | `ModelSelectionError → 422` / FastAPI validation | Web 需友好提示 |
| 500 | 服务器内部错误 | `HTTPException(500, detail=...)` | Web 需通用错误页 + 重试 |
| 502 | 上游服务错误 | analyze 路由 | Web 需重试 UI |
| 503 | 词典服务不可用 | `HTTPException(503)` | Web 浮层需降级提示 |

### 业务错误态

| 场景 | 当前处理 | Web 需要补充 |
|------|---------|-------------|
| 额度不足 | `InsufficientCredits` → 403 或 422 | Web 需额度引导页 + 充值/反馈入口 |
| 活跃任务冲突 | `ActiveTaskConflict` → 409 | Web 需冲突处理 UI：跳转到当前任务 |
| 匿名额度耗尽 | `check_and_consume_anonymous_trial` | Web 需引导登录 |
| 词典未找到 | `DictionaryNotFoundResult` (HTTP 200) | Web 浮层需空态设计 |
| 词典歧义 | `DictionaryDisambiguationResult` (HTTP 200) | Web 浮层需候选选择 UI |
| 分析结果降级 | `user_facing_state: degraded_light / degraded_heavy` | Web 可对 degraded 做更精细降级提示 |
| 分析任务失败 | `failure_code + failure_message` | Web 需失败详情页 + 重试 |

## 需后端新增/增强的接口

### ✅ 已补齐的 Web 首期阻塞项

1. **手机号验证码登录**：`POST /auth/phone/request-code`、`POST /auth/phone/verify-code` 已落地，Web BFF 同源端点负责写入 httpOnly cookie。
2. **Web baseline 用户资产**：Reader 收藏、生词写入、句子级批注、Settings 反馈已通过 Web BFF 接入真实后端。
3. **记录删除**：Library 已通过 Web BFF 调用 `DELETE /records/{record_id}`。

### 🟡 需要增强（Web 体验提升）

1. **Daily Reader schema 结构化** — `body`/`highlights`/`paragraph_notes`/`takeaways` 从 `dict` 升级为 Pydantic model
   - 当前 `DailyReaderArticleResponse` 中这四个字段是 `dict`，前端无法类型安全消费
   - 小程序已有完整 DTO 定义（`daily-reader.dto.ts`），可直接对齐

2. **`GET /records` 搜索/筛选增强** — Web 需要按 `reading_goal`、`source_type`、日期范围筛选
   - 当前只有 `page`/`limit`/`include_render_scene` 参数
   - 建议新增：`reading_goal`/`source_type`/`date_from`/`date_to`/`search` 参数

3. **Favorites 列表增强** — Web Reader 仍需要按 `target_type` / `target_key` 查询收藏状态；摘录页已改走 `/excerpt-assets`

4. **Contracts 生成策略** — `@claread/contracts` 已先承载批注/收藏/text range 常量，后续应评估 OpenAPI -> `packages/contracts` 生成完整 DTO 的方式

5. **Delete / create response model 代码风格清理** — records 等少数路由已声明 response_model 但返回裸 dict，可改为 Pydantic model 实例

### 🟢 可选增强（后续优化）

6. **SSE / WebSocket 任务进度推送** — 替代轮询，Web 可选
7. **微信开放平台登录/绑定** — 后续打通手机号账号与微信身份
8. **CORS / 同源代理配置** — 若浏览器直连 FastAPI，开发环境需要支持 Next.js 本地端口；采用 Next.js BFF 后，生产优先走服务端上游调用和同源 Web endpoint，减少浏览器跨域面

## 小程序 DTO 与后端 Schema 对齐状态

小程序已有完整的 DTO 层（`src/types/api/`），与后端 Pydantic schema 基本对齐。Web 可参考但不直接复用（小程序是 Taro 上下文，字段命名和类型映射不同）。

| 后端 Schema | 小程序 DTO | 对齐状态 |
|-------------|-----------|---------|
| `RenderSceneModel` | `AnalyzeResponseDto` | ✅ 完整对齐 |
| `AcademicRenderSceneModel` | `AcademicAnalyzeResponseDto` | ✅ 完整对齐 |
| `DictionaryLookupResult` | `DictResponseDto` | ✅ 完整对齐 |
| `DailyReaderArticleResponse` | `DailyReaderArticleDto` | 🟡 小程序 DTO 更细致（body/paragraphs/highlights 已结构化），后端仍是 `dict` |
| `TaskSubmitResponse` | 内联处理 | ✅ 对齐 |
| `VocabularyResponse` | `VocabularyResponseDto` | ✅ 对齐 |
| `UserAnnotationResponse` | 无独立 DTO | 🟡 小程序直接用 VM |

## Web 前端 adapter 层设计

Web 需要自己的 DTO → VM adapter，模式参考小程序 `services/api/adapters/`。当页面由 Server Component 或 BFF Route Handler 获取数据时，adapter 可放在 server-only 模块中，避免浏览器直接暴露 FastAPI 原始 DTO：

```
apps/web/src/
├── types/
│   ├── api/                    # 来自 @claread/contracts 的类型重导出
│   └── view/                   # Web 前端 VM 类型
├── adapters/
│   ├── analysis.adapter.ts     # AnyRenderSceneModel → Web RenderSceneVm
│   ├── dict.adapter.ts         # DictionaryLookupResult → Web DictVm
│   ├── records.adapter.ts      # RecordResponse → Web RecordVm
│   └── daily-reader.adapter.ts # DailyReaderArticleResponse → Web DailyReaderVm
└── services/
    └── api/
        ├── upstream.ts         # server-only FastAPI fetch 封装 + auth/error 处理
        ├── auth.ts             # 手机号登录、登出、session 投影
        ├── analysis.ts         # 分析任务 API
        ├── records.ts          # 记录 API
        ├── dict.ts             # 词典 API
        └── vocabulary.ts       # 生词本 API
```

adapter 层职责：
- snake_case → camelCase 字段映射
- 轻量结构适配（如 `dict` 字段的结构化解析）
- 不引入业务逻辑
- 不在其他位置做字段转换

当前已落地的第一步：
- `services/api/upstream.ts`、`services/api/tasks.ts` 和 `services/api/records.ts` 只在服务端调用 FastAPI。
- `services/bff/session.ts` 支持 httpOnly cookie 预留和开发期 `CLAREAD_WEB_DEBUG_SESSION_TOKEN`；用户可见页面不再依赖 mock 数据回退。
- `app/api/web/session` 和 `app/api/web/reader/[recordId]` 提供 Web BFF 投影接口。
- `app/api/web/analysis/submit` 和 `app/api/web/analysis/tasks/[taskId]` 提供真实解析任务提交与状态轮询投影。
- `/reader/[recordId]` Server Component 直接复用同一 BFF reader 服务，不让浏览器直连 FastAPI。
