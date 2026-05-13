# Web 实施计划

本文规划 Claread Web 端从功能页启动到高保真 Reader 的实施路径。Web 首期目标不是做完整 landing，也不是重做小程序，而是在兼容当前小程序功能子集的基础上，建立更完整的 Web 阅读体验。

## 当前阶段判断

Claread 已经从“迁移后仓库整理”进入“业务开发”阶段。Web 端已经初始化为 Next.js App Router 项目，并已有跨端和 Web 专属 impeccable 设计上下文：

- 根目录 `PRODUCT.md` / `DESIGN.md`：跨端品牌和体验总纲。
- `apps/web/PRODUCT.md` / `apps/web/DESIGN.md`：Web 功能页和 Reader 方向。
- `apps/web/docs/design/`：Web UI 方向探索。

Web 首期遵循：

- 先做功能页面，不先做完整 landing / 产品介绍页。
- 先覆盖小程序已有主链路，再做 Web 独有增强。
- Reader 是 Web 品牌第一现场。
- 默认单栏阅读 + 轻旁注。
- 不为 Web 复制一套后端。

## 当前实现基线

Wave 1 的临时任务已完成并整合为当前 Web mock 基线：

- 已有 `/app`、`/app/history`、`/app/vocabulary`、`/app/profile` 和 `/app/reader/[recordId]` 的功能页骨架。
- `apps/web/src/lib/mock-data.ts` 提供 history、vocabulary、quota、reader demo 数据。
- `apps/web/src/types/view/` 提供首批 Web VM：`RecordListItemVm`、`VocabularyItemVm`、`QuotaVm`、`ReaderMockVm`。
- Reader mock 数据覆盖 `translations`、`inlineMarks`、`sentenceEntries`，并覆盖 `vocab_highlight`、`phrase_gloss`、`context_gloss`、`grammar_note`、`sentence_analysis`。
- 当前仍是静态 / mock UI；真实 API client、TanStack Query、BFF session 和后端 auth 尚未接入。

因此下一阶段开发应从“mock 页面整合与验证”进入“BFF/API 接入”，不要继续扩散临时 mock 结构。

## 产品实施策略

### 阶段 A：小程序功能 Web 化

目标：证明同一套后端和数据可以支撑 Web。

必须跑通：

| 模块 | Web 页面 | 后端依赖 | 首期要求 |
| --- | --- | --- | --- |
| 输入与分析提交 | `/app` | `POST /analysis-tasks` | 可提交文本并进入任务状态 |
| 任务状态 | `/app` / reader pending state | `GET /analysis-tasks/{id}` | 轮询成功/失败/额度不足 |
| 结果阅读 | `/app/reader/[recordId]` | `GET /records/{id}` 或任务结果 | 渲染现有 `render_scene` |
| 历史记录 | `/app/history` | `GET /records` | 列表、进入详情 |
| 词典查词 | Reader popover | `GET /dict` / `GET /dict/entry` | 点击词或标注查词 |
| 登录 / 配额 | `/app/login`, `/app/profile` | Next.js BFF + session / quota APIs | 手机号短信登录为目标；开发期可用受控调试态 |
| 收藏 | Reader / history | favorites APIs | 可延后到第一波后段 |
| 生词本 | `/app/vocabulary` | vocabulary APIs | 先读列表，再支持写入 |
| 生词复习 | `/app/review` | review APIs | 第二波 |
| 反馈 | Reader feedback | feedback APIs | 第二波 |

验收标准：

- Web 可完成“输入 -> 分析 -> Reader -> 查词 -> 历史回看”。
- 小程序构建和主链路继续通过。
- Web 不引入新的后端 fork。

### 阶段 B：Web Reader 高保真化

目标：建立 Web 端区别于小程序的核心体验。

重点：

- 单栏文章版心，控制 65-75ch 行宽。
- inline marks 语义化渲染：vocab / phrase / context / grammar。
- 点击/hover/选中触发轻浮层。
- 当前段落或焦点句相关解释进入轻旁注轨道。
- 翻译显示、注释密度、字体大小、阅读模式可切换。
- Reader 偏好持久化。

暂不做：

- 完整多窗口资料工作台。
- AI chat 侧栏。
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

1. **Grammar X-Ray**：长难句 / 语法结构可视化。
2. **Share page**：公开分享页，SSR metadata 和 OG image。
3. **Artifact Studio**：长图、PDF、Markdown 导出预览。
4. **高级批注**：文本选区、用户笔记、批注列表、筛选。
5. **更丰富历史/资产管理**：搜索、筛选、批量导出。

这些能力可以逐步加入，不阻塞首期 Web 功能页。

## 页面 IA

首期建议页面：

| 路由 | 优先级 | 说明 |
| --- | --- | --- |
| `/` | P2 | 占位入口，不做完整 landing |
| `/app` | P0 | 粘贴即解读首页，最近记录入口 |
| `/app/login` | P0/P1 | Web auth 入口，取决于后端 auth readiness |
| `/app/reader/[recordId]` | P0 | 核心 Reader |
| `/app/history` | P0 | 历史记录 |
| `/app/vocabulary` | P1 | 生词本 |
| `/app/review` | P1 | 生词复习 |
| `/app/profile` | P1 | 用户、配额、设置 |
| `/share/[shareId]` | P1/P2 | 分享页，SSR metadata |
| `/app/export/[recordId]` | P2 | Artifact Studio |
| `/about`, `/help`, `/blog` | P3 | 先占位，后续内容站阶段再做 |

## 前端架构建议

```text
apps/web/src/
  app/
    (marketing)/            # 占位，不做完整 landing
    (app)/app/              # 登录后功能页
    share/[shareId]/        # 公开分享页
  components/
    app-shell/
    reader/
    dict/
    records/
    vocabulary/
    export/
  services/
    api/                    # server-only/BFF upstream client，不直接泄露内部 token
    bff/                    # Web 投影、session cookie、错误态映射
  adapters/
  types/
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
pnpm web:typecheck
pnpm web:lint
pnpm web:build
```

关键 UI 改动需要浏览器验证：

- `/app`
- `/app/reader/demo` 或 mock record
- `/app/history`
- `/app/vocabulary`
- `/share/demo`

后续补 Playwright smoke：

1. 首页非空。
2. 提交分析。
3. Reader 渲染正文和标注。
4. 点击查词弹出词典。
5. 历史列表进入记录。
6. 配额显示。
7. 分享页 metadata 正确。

## 临时任务分配

Agent 分工、一次性 prompt 和执行跟踪不写入本文。此类内容放到 `apps/web/docs/tmp/`，完成后应删除或压缩为稳定结论，避免长期计划文档变成进度流水账。
