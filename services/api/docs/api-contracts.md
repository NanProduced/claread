# API 契约

本文记录当前必须保持稳定的后端契约。

## 冻结原则

- Web 后续可以增加字段、adapter 和 render profile。
- 不能破坏小程序当前依赖的字段、状态码和 ID 语义。
- `@claread/contracts` 当前先承载跨端常量和轻量类型；OpenAPI 后续应作为完整 DTO 生成来源。

## 小程序强依赖接口

| 领域 | 接口 | 说明 |
|------|------|------|
| Auth | `POST /auth/wechat/login` | 小程序登录入口 |
| Auth | `GET /auth/session/me` | 登录态恢复和用户资料 |
| Auth | `PATCH /auth/profile` | 用户资料和阅读偏好更新 |
| Auth | `POST /auth/session/logout` | 退出登录 |
| Analyze | `POST /analyze` | 匿名用户直连分析 / 调试兼容 |
| Tasks | `POST /analysis-tasks` | 登录用户主分析链路 |
| Tasks | `GET /analysis-tasks/current` | 恢复活跃任务 |
| Tasks | `GET /analysis-tasks/{task_id}` | 任务轮询 |
| Records | `POST /records` | 云端记录同步 |
| Records | `GET /records` | 历史记录列表 |
| Records | `GET /records/{record_id}` | 记录详情 |
| Records | `GET /records/by-client-id/{client_record_id}` | 本地 ID 查云端记录 |
| Quota | `GET /me/quota` | 登录用户额度 |
| Quota | `GET /me/quota/anonymous` | 游客额度查询 |
| Quota | `POST /me/quota/check` | 额度检查；匿名路径会消耗试用次数 |
| Credit | `GET /me/credit/ledger` | 积分流水 |
| Dict | `GET /dict` | 查词 |
| Dict | `GET /dict/entry` | 词条详情 |
| Vocabulary | `POST /vocabulary` | 生词同步 |
| Vocabulary | `GET /vocabulary` | 生词列表 |
| Vocabulary | `POST /vocabulary/highlights` | 结果页高亮和已收藏生词匹配 |
| Vocabulary | `GET /vocabulary/review/due` | 生词复习 |
| Feedback | `POST /feedback` | 用户反馈 |
| User Annotations | `POST /user-annotations` | 用户高亮/笔记，支持句子、单句内 `text_range` 和跨句/跨段 `multi_text` |
| User Annotations | `GET /user-annotations` | 用户批注列表 |
| Favorites | `POST /favorites` | 收藏文章、句子、单句内 `text_range` 和跨句/跨段 `multi_text` |
| Favorites | `GET /favorites` | 收藏列表 |
| Daily Reader | `GET /daily-reader/today` | 今日精读 |
| Daily Reader | `GET /daily-reader` | 往期精读列表 |
| Daily Reader | `GET /daily-reader/{article_id}` | 精读详情 |

## ID 语义

| 字段 | 含义 |
|------|------|
| `client_record_id` | 客户端生成的稳定记录 ID |
| `cloud_record_id` | 后端 `analysis_records.id` |
| `task_id` | 分析任务 ID |
| `record_id` | 兼容字段；新代码优先区分 `client_record_id` 和 `cloud_record_id` |

## 当前契约状态

- `/dict` 和 `/dict/entry` 声明了 response model。
- 手机号验证码登录已通过 `provider=phone` 和 `client_platform=web` 接入统一身份模型。
- `source_type` 统一为 `user_input / daily_article / imported / ocr`。
- `RecordCreateRequest.source_type` 使用统一枚举。
- `TaskSubmitResponse` / `TaskStatusResponse` 兼容只传 `record_id` 时自动补 `cloud_record_id`。
- `user_annotations.anchor_type='text_range'` 使用 UTF-16 code unit offset，要求 `analysis_record_id`、`sentence_id`、`selected_text`、`start_offset`、`end_offset`、`text_hash`，并校验 render scene sentence 切片。
- `user_annotations.anchor_type='multi_text'` 使用 `payload_json.segments[]` 保存多段 anchor；每段都按同一套 UTF-16 offset 和 `fnv1a32-utf16` hash 校验，并要求顺序与 render scene article sentence 顺序一致。
- `favorite_records.target_type='text_range'` 使用同一套 `selected_text`、offset 和 hash payload。
- `favorite_records.target_type='multi_text'` 复用 `payload_json.segments[]`，并执行与 annotations 相同的 render scene 校验。
- `vocabulary_book.dict_entry_id` 指向 `dict_entries.id`；词典重导前必须处理 ID 稳定性。
- `VocabHighlight` 拒绝未知字段，避免 LLM 草稿把旧字段静默带入 canonical annotation。

## 后续增强方向

后续可结构化 Daily Reader payload，增强 records 搜索/筛选，补齐 contracts 生成策略，统一错误响应，并评估把匿名额度 check 拆分为 peek / consume。
