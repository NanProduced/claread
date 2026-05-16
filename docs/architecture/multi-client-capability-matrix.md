# 多端能力对照

本文以用户可感知能力为观测点，追踪 Claread 在 Web、小程序和通用后端之间的实现状态。它不是技术模块清单，也不是某个端的阶段计划；目的是让后续开发清楚区分：

- 同一套后端服务支撑多个客户端。
- Web 已完成小程序 baseline 对齐，正在进入 Web 端能力增强。
- 小程序可能因为平台交互限制不能操作某些能力，但仍应能展示或复现 Web 端产生的共享数据。
- 多端能力分叉必须写清“可操作”“可展示”“暂未接入”和兼容备注，避免把某端限制误写成全局产品限制。

## 状态标记

| 标记 | 含义 |
|------|------|
| 已接入 | 用户可以完整使用该能力，且数据进入共享后端 |
| 部分接入 | 主链路可用，但仍缺少某些交互、边界或管理能力 |
| 仅展示 | 该端不主动创建/编辑，但可以读取并复现其他端产生的数据 |
| 端内能力 | 能力只在该端当前体验中存在，尚未抽象为跨端能力 |
| 未接入 | 当前端没有该能力入口或后端尚未支持 |

## 总体定位

| 观察项 | Web | 小程序 | 后端/数据层 | 备注 |
|--------|-----|--------|-------------|------|
| 产品阶段 | 已完成 baseline 对齐，正在做 Reader UI 和 Web 端能力增强 | 第一个可运行客户端，仍持续迭代 | 一套通用 FastAPI 服务和 PostgreSQL 数据 | 小程序不是冻结基线；Web 也不复制后端 |
| 交互形态 | 鼠标、键盘、选区、hover、侧栏、浮动工具条 | tap、长按、弹窗、轻量卡片、分包页面 | 共享 records、annotations、favorites、vocabulary、feedback 等表 | UI 可以分叉，数据语义应共享 |
| 能力分叉原则 | 可以增强精确选区、搜索查词、历史轨迹、密集管理 | 可以保留整句操作、轻量查词、移动学习路径 | 后端按 anchor、target、payload 表达能力，而不是按客户端复制模型 | 某端不能操作不等于不能展示 |

## 输入与解析

| 用户能力 | Web | 小程序 | 后端/数据层 | 备注 |
|----------|-----|--------|-------------|------|
| 粘贴英文文本并发起解析 | 已接入：`/read` 提交、轮询任务、跳转 Reader | 已接入：输入页提交，进入解析结果页 | `analysis_tasks`、workflow、`analysis_records` | 双端共享分析主链路 |
| 查看解析进度和失败态 | 部分接入：Web baseline 错误态可用，细分恢复流程仍可增强 | 已接入基础状态 | 任务状态机共享 | Web 后续可做更强的 retry/import 状态 |
| URL、文件、批量导入 | 未接入 | 未接入 | 后端尚未产品化 | 未来应作为输入源扩展，不 fork workflow |

## Reader 阅读与解析结果

| 用户能力 | Web | 小程序 | 后端/数据层 | 备注 |
|----------|-----|--------|-------------|------|
| 阅读原文与译文 | 已接入：Web Reader 工作台 | 已接入：结果页阅读 | 共享 record 和 render scene | Web 后续可推进专属 `web_reader` render profile |
| 查看词汇、短语、语境义标注 | 已接入：词汇固定搭配与语法标注已拆分为不同视觉层级 | 已接入 | inline marks / render scene | 三类词汇标注保持可区分，但语义共享 |
| 查看语法旁注卡片 | 已接入：卡片与原文联动，默认轻标注、激活后强调原文 | 已接入 | sentence entries | 小程序展开卡片时强调对应原文；Web 使用同类逻辑 |
| 查看句子拆解卡片 | 已接入：原文片段带序号，卡片序号同色 | 已接入：原文片段带序号，卡片序号同色 | sentence_analysis content | Web 与小程序都以 chunks 顺序绑定原文与卡片 |
| 解析卡片展开/折叠 | 已接入 Web 交互 | 已接入移动端交互 | 客户端 UI state | 展开状态不进入后端 canonical 数据 |

## 文本选择、批注与收藏

| 用户能力 | Web | 小程序 | 后端/数据层 | 备注 |
|----------|-----|--------|-------------|------|
| 收藏整篇文章 | 已接入 | 已接入 | `favorite_records.target_type=analysis_record` | 双端共享记录收藏 |
| 整句选择后高亮/笔记/收藏 | 已接入：toolbar 可选择当前句子，支持高亮、笔记、收藏、反显和取消 | 已接入：长按/句子级操作为主 | `anchor_type=sentence`；`target_type=sentence` | baseline 共享能力，双端都可操作和展示 |
| 句内局部文本高亮/笔记/收藏 | 已接入：Web 支持单句内精确选区创建、渲染、反显、取消和资产聚合 | 仅展示：不主动创建局部选区，但结果页和摘录跳转可复现 Web 局部资产 | `anchor_type=text_range`、`target_type=text_range`、`start_offset`、`end_offset`、`text_hash` | 坐标系为 UTF-16 code unit；后端校验 render scene sentence 切片和 `fnv1a32-utf16` hash |
| 选中文本查词/查短语 | 已接入：selection toolbar 触发 | 未接入选区操作；保留点词查词路径 | `/dict` | Web 选区能力增强，不要求小程序复刻交互 |
| 取消高亮或删除笔记 | 已接入：Web 支持 PATCH/DELETE BFF，toolbar 反显已有状态 | 部分接入：按已有句子级操作为主；局部资产先只展示 | `/user-annotations/{id}` | 小程序可先展示局部资产，不必提供同等编辑入口 |
| 跨句/跨段选择后批注 | 已接入：Web 可创建、渲染、回跳 `multi_text` 选区 | 仅展示：结果页和摘录页可兼容显示并回跳 Web 创建的跨句资产 | `anchor_type=multi_text`、`target_type=multi_text`、`payload_json.segments[]` | 每段使用 UTF-16 offset + hash；后端按 render scene 顺序和切片校验 |
| 段落级选择后批注 | 未接入 UI | 未接入 | `anchor_type=paragraph` 字段预留 | 当前没有产品化操作入口，不能视作已完成能力 |

## 查词、生词与词典

| 用户能力 | Web | 小程序 | 后端/数据层 | 备注 |
|----------|-----|--------|-------------|------|
| 点击词汇查词 | 已接入：原文词/短语可查 | 已接入：ClickableWord/WordPopup | `/dict`、`/dict/entry` | baseline 共享能力 |
| 选中文本后查词/查短语 | 已接入：selection toolbar 触发 | 未接入操作；可继续保留点词路径 | `/dict` | 选区能力状态见“文本选择、批注与收藏” |
| 搜索框手动查词 | 部分接入：Web 词典面板已有手动查询入口，体验未完整产品化 | 未接入 | `/dict` | 这是 Web 端可增强能力 |
| 查词历史记录 | 端内能力：Web Reader 维护 lookup trail | 未接入 | 当前主要是客户端状态 | 若未来跨设备同步，再抽象为后端数据 |
| 保存到生词本 | 已接入 | 已接入 | `/vocabulary` | 双端共享生词资产 |
| 生词复习 | 已接入 Web review baseline | 已接入小程序复习路径 | `/vocabulary/review/due`、`/vocabulary/{id}/review` | Web 可做更密集管理，小程序保留轻学习 |

## 学习资产与摘录

| 用户能力 | Web | 小程序 | 后端/数据层 | 备注 |
|----------|-----|--------|-------------|------|
| 按文章查看摘录与批注 | 已接入：`/library/assets` 作为摘录资产索引页 | 已接入：`packageA/excerpts` 以文章为父级聚合 | `GET /excerpt-assets` + favorites + annotations | 本轮边界明确为摘录资产，不把 `vocabulary` 混入该聚合页 |
| 展示句子级高亮/收藏/笔记 | 已接入 | 已接入 | sentence anchor | 句子级资产是双端 baseline |
| 展示句内局部文本高亮/笔记/收藏 | 已接入：Reader 和 `/library/assets` 都能展示 | 仅展示：结果页/摘录页可复现 Web 局部资产 | `text_range` anchor + favorite target | 这是多端能力分叉的核心追踪点 |
| 展示跨句/跨段高亮/笔记/收藏 | 已接入：Reader、`/library/assets` 和 targetKey 回跳可识别 `multi_text` | 已接入展示：结果页/摘录页可渲染多段高亮并按 targetKey 回跳首段 | `multi_text` anchor + favorite target + segment payload | 小程序当前不创建 `multi_text`，但需要接得住 Web 资产 |
| 查看解析 sidecar / insight | 低调展示：摘录页只作为补充标签显示 | 已接入筛选：`解析` 过滤保留 | `/excerpt-assets.insights[]` | insight 是 excerpt sidecar，不等同于用户资产本体 |
| 跨端编辑同一资产 | 部分接入：Web 支持编辑/删除批注和局部收藏，跨端管理页待做 | 部分接入：句子级路径为主 | 共享 id、target_key、anchor metadata | 小程序可先只显示局部资产，不必提供编辑入口 |

## 历史、资料库与记录管理

| 用户能力 | Web | 小程序 | 后端/数据层 | 备注 |
|----------|-----|--------|-------------|------|
| 查看解析历史 | 已接入 `/library` | 已接入 history | `/records` | 共享 records |
| 删除记录 | 已接入 | 已接入或按现有入口支持 | record 状态/删除接口 | 需保证删除后的资产处理策略一致 |
| 按收藏筛选 | 部分接入 | 已接入 | favorites + records | Web 后续可增强搜索、来源、日期筛选 |
| 搜索历史记录 | 未接入完整产品体验 | 未接入或基础能力有限 | 需要 records query 扩展 | Web 更适合承载复杂筛选 |

## 反馈、账户与配额

| 用户能力 | Web | 小程序 | 后端/数据层 | 备注 |
|----------|-----|--------|-------------|------|
| 提交问题反馈 | 部分接入：设置页/Reader 入口逐步接入 | 已接入基础反馈 | `/feedback` | feedback scope/type 应进入共享 contracts |
| 查看登录状态 | 已接入：Web session cookie + BFF | 已接入：小程序 session token | `user_sessions` | provider 差异不影响业务用户 |
| 查看配额/积分 | 已接入基础设置页 | 已接入 profile/credit detail | `/me/quota`、`/me/credit/ledger` | UI 展示分叉，数据共享 |

## 分享与导出

| 用户能力 | Web | 小程序 | 后端/数据层 | 备注 |
|----------|-----|--------|-------------|------|
| 小程序平台分享 | 不适用 | 已接入或保留平台路径 | 共享 record/share metadata 预留 | 平台能力不写成全局限制 |
| Web 分享页/OG/PDF/Markdown/图片导出 | 未接入，属于后续 Web 增强 | 不适用或只展示导出结果 | 未来 share snapshot/export job | 应复用 records/render profile，不复制业务后端 |

## 共享契约当前状态

| 契约 | 状态 | 备注 |
|------|------|------|
| `@claread/contracts` | 已接入 Web 和小程序 | 当前先沉淀用户批注、收藏、颜色、offset/hash 常量；仍不是完整 OpenAPI 生成包 |
| `TEXT_RANGE_OFFSET_UNIT` | `utf16` | 与 Web DOM Selection / JS string offset 对齐 |
| `TEXT_RANGE_HASH_ALGORITHM` | `fnv1a32-utf16` | 前端 selected text hash 与后端校验一致 |
| API DTO 生成 | 未接入 | 后续再评估 OpenAPI -> contracts 生成，避免继续手写漂移 |

## 文档取舍

以下阶段性文档使命已完成，不再作为长期事实来源：

- `apps/miniprogram/docs/freeze-baseline.md`：冻结口径已过期。小程序不是冻结客户端，当前能力以代码和本文为准。
- `apps/web/docs/backend-adaptation-plan.md`：后端多端化适配的关键结论已沉淀到 `docs/architecture/multi-client.md` 和本文；任务清单部分已不适合作为事实源。
- `apps/web/docs/baseline-adaptation-plan.md`：Web baseline 对齐任务已完成，剩余事项应进入具体 Web Reader、API audit 或 annotation 文档，而不是保留 baseline tracker。

仍保留的相关文档：

- `docs/architecture/multi-client.md`：多端架构原则。
- `docs/architecture/backend-multiclient-review.md`：后端多端化评审问题域。
- `apps/web/docs/api-contract-audit.md`：Web 接口审计。
- `apps/web/docs/annotation-toolbar-text-range-plan.md`：Web selection toolbar 和 `text_range` 细节。
- `apps/web/docs/reader-ia.md`：Web Reader 信息架构。
