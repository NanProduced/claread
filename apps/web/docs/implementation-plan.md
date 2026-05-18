# Web 实施计划

本文规划 Claread Web 端从功能页启动到高保真 Reader 的实施路径。Web 首期目标不是做完整 landing，也不是重做小程序，而是在兼容当前小程序功能子集的基础上，建立更完整的 Web 阅读体验。

## 当前阶段判断

Claread 已经进入双端稳定基线之后的产品迭代准备阶段。Web 端基于 Next.js App Router，已有跨端和 Web 专属 impeccable 设计上下文：

- 根目录 `PRODUCT.md` / `DESIGN.md`：跨端品牌和体验总纲。
- `apps/web/PRODUCT.md` / `apps/web/DESIGN.md`：Web 功能页和 Reader 方向。
- `apps/web/docs/design/`：Web UI 方向探索。

Web 首期遵循：

- 先做功能页面，不先做完整 landing / 产品介绍页。
- 先覆盖小程序已有主链路，再做 Web 独有增强。
- Reader 是 Web 品牌第一现场。
- Reader Canvas Workspace：中心原文画布 + 画布左侧词典详情层 + 短时上下文/设置浮层 + 画布右侧 AI 工作区。
- 产品区使用左侧可折叠 rail，不使用顶部导航作为主应用导航。
- 不为 Web 复制一套后端。

## 当前实现基线

Wave 1 的临时任务已完成并整合为 Web 可运行基线，随后进入“小程序 MVP 功能 Web 化”阶段：

- 已有 `/read`、`/library`、`/vocabulary`、`/settings`、`/login` 和 `/reader/[recordId]` 的首期产品化路由；开发阶段不保留已废弃功能前缀的兼容层。
- Web 已移除产品路径中的 mock/demo fixture；本地调试依赖真实 FastAPI、PostgreSQL、Redis 和测试手机号链路。
- `apps/web/src/types/view/` 提供首批 Web VM：`RecordListItemVm`、`VocabularyItemVm`、`QuotaVm`、`ReaderMockVm`。
- Web BFF/API 第一条窄路径已建立：`services/api/` 提供 server-only FastAPI upstream client，`services/bff/` 处理 Web session 投影，`adapters/records.adapter.ts` 将 `RecordResponse` / `render_scene_json` 投影为 Reader VM；真实记录不可用时返回明确错误态，不回落 mock。
- `/read` 已接入真实解析提交窄路径：页面提交到 `/api/web/analysis/submit`，BFF 调 FastAPI `/analysis-tasks`，同步等待超时后通过 `/api/web/analysis/tasks/[taskId]` 轮询，成功后进入 `/reader/[cloudRecordId]`。
- `/read` 最近记录和 `/library` 已通过 Web BFF 接入 FastAPI `/records` 列表，上游可用时使用云端 `analysis_records.id` 进入 Reader；匿名、登录态不可用或上游不可用时显示空态/错误态，不展示示例数据。列表请求默认不拉取 `render_scene_json`。
- Reader 已能把真实 `render_scene_json` 中的 `multi_text` anchor 作为“结构线索”展示在句子下方，不把非连续片段强行伪装成 inline highlight。旧的右侧集中说明形态已废弃，后续实现应回到原文画布。
- `/settings` 已通过 Web BFF 读取 FastAPI `/auth/session/me` 和 `/me/quota`；不可用登录态只显示明确不可用提示，不再伪造额度。
- 词典 BFF 已接入 FastAPI `/dict` 和 `/dict/entry`，返回 Web 专用 `entry` / `disambiguation` / `not_found` / `error` union，不向页面暴露原始 FastAPI DTO。
- Reader 词典 AI 已通过同源 `/api/web/dict/ai` 接入 FastAPI `POST /dict/ai`：`context_explain` 作为正文点词后的二级语境解读，`missing_fallback` 作为 canonical `not_found` 后的按需增强；AI 结果内嵌在同一张词典卡里，不覆盖正式词典，也不写入查词历史。
- Reader inline mark 已有基础词典浮层，调用同源 `/api/web/dict/lookup`，不让页面直连 FastAPI。
- `/vocabulary` 已通过 Web BFF 接入 FastAPI `GET /vocabulary`，匿名、不可用登录态和上游不可用时显示明确空态，不再展示 mock 生词。
- `/review` 已通过 Web BFF 接入 FastAPI `GET /vocabulary/review/due` 和 `POST /vocabulary/{id}/review`，支持真实待复习队列和复习结果提交。
- Reader 已接入收藏、生词写入、句子级高亮/笔记，以及单句内 `text_range`、跨句/跨段 `multi_text` 高亮/笔记/收藏；`/library/assets` 已作为“摘录与批注”页按文章聚合句子、局部文本和多段文本摘录资产，`/vocabulary` 继续保持独立词汇资产入口；Library 已接入真实记录删除；Settings 已接入应用反馈。手机号登录链路已具备开发期验证码、Web BFF cookie 投影和 FastAPI `aliyun_dypnsapi` provider，后续重点是补齐 Daily Reader、资料筛选、正式账号绑定 UI 和 Reader UI/UX 评审。
- Reader UI/UX 第一轮已废弃右侧轻旁注方向；当前在此基础上进一步从固定三栏改为 Canvas Workspace：中心原文画布是核心，外层内容容器在宽屏放宽到约 96ch，正文文本列继续保持 65-75ch；词典详情进入画布左侧工具层，句子操作和阅读设置使用短时浮层，AI chatbox 进入画布右侧工具层。
- Reader 词典交互当前采用三层模型：正文附近可关闭的轻释义小卡、画布左侧完整词典详情卡片、独立的本次查词轨迹 chips。左侧词典支持手动输入查词；无正文上下文的手动查词只展示词条，不直接加入生词本。
- Reader 词典 AI 交互当前遵循“词典为主、AI 为辅”：正文点词后的 canonical entry 可按需展开 `AI 语境解读`；canonical `not_found` 可按需触发 AI 未验证词条或未识别结果；manual search 和 disambiguation 不直接显示 AI 入口。
- SelectionToolbar 已落地第一版：`Ask Claread` 占位、3 色用户高亮、轻量笔记、收藏、查词、反馈占位、更多占位；普通正文不再逐词拆分为可点击节点，避免破坏浏览器原生选区。
- 文本选区数据契约已进入稳定 v1：Web 和小程序通过 `@claread/contracts` 共享 anchor/target/color/offset/hash 常量；后端按 UTF-16 offset、`fnv1a32-utf16` hash、multi_text segments 和 render scene sentence 切片校验局部/多段选区。
- ReaderWorkbench 已拆出 `ReaderCanvas`、`ReaderSentenceRow`、`ReaderAnnotationOverlay` 和 selection helper；后续新增 Reader 交互优先在这些组件边界内推进。

## UI/UX 第一版定稿约束

第一版页面形态已按“阅读镜头”方向收敛：

- 一级 rail 入口：新解读、阅读记录、生词本、设置。复习不作为一级入口，从生词本顶部按钮进入。
- `/read` 是 Web app home；`/` 保留极简 landing 占位，不跳转到 `/read`。
- 未登录不开放模型试用；后续可展示少量精选解析示例，不使用 mock fixture 或真实提交回退。
- `/read` 展示最近记录，但只作为克制索引，不做 feed。
- Reader 默认标准模式：译文柔和显示；词汇、短语、语境标注显示；语法下划线显示但不展开；结构链默认隐藏。
- Reader 桌面端使用画布边缘工具层：画布左侧词典详情卡片实时显示当前点击词/短语详细释义，原文附近只显示可关闭的轻释义预览；文本选区时通过 SelectionToolbar 显示高亮、写笔记、收藏、查词、问 Claread 占位，点击 `Aa` 时显示阅读设置。`grammar_note` 语法说明和 `sentence_analysis` 句子拆解应在相关句子下方展开，不使用 Grammar X-Ray 命名。
- 用户批注 v1 支持句子级、单句内 `text_range` 和跨句/跨段 `multi_text`；PDF/外部网页 anchor 和富文本笔记后置。
- Library 不做归档概念；删除为 7 天软删除，不单独做回收站页面。
- Library v1 做客户端标题 + 原文片段搜索，后端语义搜索后置。
- Vocabulary 只保留学习中 / 已掌握两态；查词历史不保存，只有主动加入生词本的原词和上下文进入资产。
- 移动 Web 底部导航保留四个入口：新解读、阅读记录、生词本、设置。Reader 的词典、批注、阅读设置和 AI 辅助走 bottom sheet；移动 Reader 形态后续单独评审。
- Lens Blue `#2563EB` 只用于 CTA 主按钮、当前激活状态、品牌符号和内嵌链接。

因此下一阶段开发应沿已有 BFF / adapter 边界继续接入，不要继续扩散临时 mock 结构，也不要让页面直接消费 FastAPI 原始 DTO。

## 产品实施策略

### 阶段 A：小程序功能 Web 化

目标：证明同一套后端和数据可以支撑 Web。

必须跑通：

| 模块 | Web 页面 | 后端依赖 | 首期要求 |
| --- | --- | --- | --- |
| 输入与分析提交 | `/read` | `POST /analysis-tasks` | 可提交文本并进入任务状态 |
| 任务状态 | `/read` / reader pending state | `GET /analysis-tasks/{id}` | 轮询成功/失败/额度不足 |
| 结果阅读 | `/reader/[recordId]` | `GET /records/{id}` 或任务结果 | 渲染现有 `render_scene` |
| 历史记录 | `/library` | `GET /records` | 列表、进入详情 |
| 词典查词 | Reader popover | `GET /dict` / `GET /dict/entry` | 点击词或标注查词 |
| 登录 / 配额 | `/login`, `/settings` | Next.js BFF + session / quota APIs | 手机号短信登录为目标；开发期可用受控调试态 |
| 收藏 | Reader / history | favorites APIs | Reader 记录收藏、句子收藏、局部 `text_range` 和跨句/跨段 `multi_text` 收藏已接入 |
| 生词本 | `/vocabulary` | vocabulary APIs | 已接真实列表；Reader 可写入 |
| 生词复习 | `/review` | review APIs | 已接真实 due queue 和提交；不放一级导航，从 `/vocabulary` 进入 |
| 批注 | Reader | user-annotations APIs | 已接句子级、单句内 `text_range` 和跨句/跨段 `multi_text` 高亮/笔记 |
| 反馈 | Settings / Reader feedback | feedback APIs | Settings 应用反馈已接入；Reader 反馈待 UI/UX 评审 |

验收标准：

- Web 可完成“输入 -> 分析 -> Reader -> 查词 -> 历史回看”。
- 小程序构建和主链路继续通过。
- Web 不引入新的后端 fork。

### 阶段 B：Web Reader 高保真化

目标：建立 Web 端区别于小程序的核心体验。

重点：

- 单栏文章版心，控制 65-75ch 行宽。
- inline marks 语义化渲染：vocab / phrase / context / grammar。
- 点击/hover/选中触发轻浮层或 SelectionToolbar。
- 原文附近显示可关闭的轻释义预览；词汇详细释义进入画布左侧词典详情卡片；句子操作和阅读设置进入短时浮层；语法说明、句子拆解和用户笔记回到中心原文画布。
- 翻译显示、注释密度、字体大小、阅读模式可切换。
- Reader 偏好持久化。
- SelectionToolbar 稳定化：当前已通过本地浏览器回归验证出现位置、无横向溢出和滚动跟随；提交到仓库的浏览器自动化仍待补齐创建/取消高亮、笔记和局部收藏。

暂不做：

- 完整多窗口资料工作台。
- AI 工作区：右侧默认收起，展开后围绕当前句子、选区或全文上下文对话，不承载批注列表。
- 复杂协作文档。
- 过重的 dashboard shell。

验收标准：

- Reader 在桌面和移动 Web 都不重叠。
- 解释锚点能回到原文。
- 工具不抢正文。
- 浏览器截图能体现 Claread 品牌调性。

### 阶段 C：Web 独有能力

目标：让 Web 逐步超过小程序。

优先级：

1. **Grammar X-Ray**：后续 Web 高保真语法透视能力，需要结构化 xray payload 和专门渲染组件；当前 workflow schema 不支持，不能用 `grammar_note` 或 `sentence_analysis` 冒充。
2. **Share page**：公开分享页，SSR metadata 和 OG image。
3. **Artifact Studio**：长图、PDF、Markdown 导出预览。
4. **高级批注**：复杂重叠规则、跨文章批注索引、筛选、批量导出。
5. **更丰富历史/资产管理**：搜索、筛选、批量导出。

这些能力可以逐步加入，不阻塞首期 Web 功能页。

## 页面 IA

首期建议页面：

| 路由 | 优先级 | 说明 |
| --- | --- | --- |
| `/` | P2 | 占位入口，不做完整 landing |
| `/read` | P0 | 粘贴即解读首页，最近记录入口 |
| `/login` | P0/P1 | Web auth 入口，取决于后端 auth readiness |
| `/reader/[recordId]` | P0 | 核心 Reader |
| `/library` | P0 | 历史记录 |
| `/vocabulary` | P1 | 生词本 |
| `/review` | P1 | 生词复习路由，从生词本进入，不作为一级入口 |
| `/settings` | P1 | 用户、配额、设置 |
| `/share/[shareId]` | P1/P2 | 分享页，SSR metadata |
| `/export/[recordId]` | P2 | Artifact Studio |
| `/about`, `/help`, `/blog` | P3 | 先占位，后续内容站阶段再做 |

## 前端架构建议

```text
apps/web/src/
  app/
    (marketing)/            # 占位，不做完整 landing
    (app)/                  # Web 功能页，使用 /read、/library、/reader 等语义路由
    share/[shareId]/        # 公开分享页
  components/
    app-shell/
    reader/
    dict/
    records/
    vocabulary/
    export/
  services/
    api/                    # server-only FastAPI upstream client，不直接泄露内部 token
    bff/                    # Web 投影、session cookie、错误态映射
  adapters/
    records.adapter.ts      # RecordResponse/render_scene_json -> Reader VM
  types/
    api/                    # BFF 上游 DTO，后续可由 packages/contracts 生成
    view/
  stores/
```

状态分层：

- TanStack Query：服务端状态，records、tasks、dict、quota、vocabulary。
- Zustand：Reader UI 状态，阅读模式、面板开关、字体大小、当前焦点。
- Next.js Server Components：公开分享页、metadata、静态占位页。
- Next.js Route Handlers / Server Actions：Web BFF，处理手机号登录、httpOnly cookie、FastAPI 上游调用和 Web view model 投影。
- Client Components：Reader 交互、查词、选区、批注。

## 验证入口

每次 Web 功能推进后至少运行：

```powershell
pnpm --filter=@claread/web lint
pnpm --filter=@claread/web typecheck
pnpm --filter=@claread/web build
```

关键 UI 改动需要浏览器验证：

- `/read`
- `/reader/[recordId]`
- `/library`
- `/vocabulary`
- `/settings`
- `/share/demo`

当前已做的 Reader 交互回归检查：

1. Reader 选中文本后 SelectionToolbar 出现。
2. SelectionToolbar 不横向溢出。
3. 页面滚动后 SelectionToolbar 跟随选区。

后续补提交到仓库的真实数据浏览器自动化回归：

1. 首页非空。
2. 提交分析。
3. Reader 渲染正文和标注。
4. 创建/取消 `text_range` 和 `multi_text` 高亮。
5. 保存/删除选区笔记。
6. 局部收藏和取消收藏。
7. 点击查词弹出词典。
8. 历史列表进入记录。
9. 配额显示。
10. 分享页 metadata 正确。

## 临时任务分配

Agent 分工、一次性 prompt 和执行跟踪不写入本文。此类内容放到 `apps/web/docs/tmp/`，完成后应删除或压缩为稳定结论，避免长期计划文档变成进度流水账。
