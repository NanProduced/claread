# Web Reader 信息架构

本文设计 Claread Web Reader 的信息架构、页面结构、核心交互、快捷键、词典浮层、批注系统和历史回看。

设计原则来自 `docs/product/design-context.md`：Calm / Precise / Editorial。Web Reader 不复刻小程序 ScrollView 单栏布局，而是面向桌面宽屏设计；移动 Web 适配需在第一版 UI/UX 评审中明确范围。

## 设计原则

- **桌面优先**：利用宽屏空间做信息分层，不是手机页拉伸。
- **信息分层**：正文沉浸 → 渐进披露 → 按需展开。先展示文章和解释，再展示工具。
- **不被小程序限制反向约束**：小程序 ScrollView 降级不是 Web 上限。
- **阅读主线优先**：复杂能力渐进披露，不在第一屏暴露所有操作。
- **反馈、收藏、生词、批注围绕阅读上下文出现**。

## 页面结构

### 桌面布局

Reader 桌面端采用“原文画布工作台”。中心区域是最大、最稳定的文章画布；左侧是常驻工具区；右侧是 AI 工作区。所有和文本强绑定的解析内容必须优先贴回正文附近，不再把批注数据统一收纳到右侧列表。

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ 弱化 rail │ 左侧工具区                 │ 中心原文画布             │ AI 工作区 │
├──────────┼────────────────────────────┼─────────────────────────┼──────────┤
│          │ 常驻词典                    │ 文章标题 / 模式 / 收藏    │ 默认收起  │
│          │ 当前点击词/短语详细释义       │ 65-75ch 正文              │ Ask       │
│          │ 多释义 / 例句 / 短语          │ inline marks / 译文        │ Claread   │
│          │ 加入生词本 / 本次查词         │ grammar_note 句后卡        │ handle    │
│          ├────────────────────────────┤ sentence_analysis 句后卡  ├──────────┤
│          │ 动态操作区                  │ 用户高亮 / 笔记锚点        │ 展开后：  │
│          │ 选句：高亮 / 笔记 / 收藏      │ selection toolbar          │ 当前上下文 │
│          │ Aa：字号 / 行距 / 背景 / 译文 │                         │ chatbox   │
└──────────┴────────────────────────────┴─────────────────────────┴──────────┘
```

桌面 v1 的空间职责：

- **中心原文画布**：主阅读和主解析展示区。词汇、语法说明、句子拆解、用户笔记都必须能从原文附近找到锚点。
- **左上常驻词典**：点击任何可查词词汇/短语后实时显示详细释义。它不是 tab，也不是二级路由。
- **左下动态操作区**：根据当前上下文切换。选中句子时显示高亮、写笔记、收藏、问 Claread；点击 `Aa` 时显示阅读设置。
- **右侧 AI 工作区**：默认收起；展开后只围绕当前句子、选区或全文上下文对话。它不是批注列表。

### 移动 Web 布局（第二波）

- 单栏：主阅读区全宽。
- rail 全部隐藏，底部导航只保留新解读、阅读记录、生词本、设置四个入口。
- 词典、批注、阅读设置和 AI 辅助都使用 bottom sheet。
- 分享页不主动弹窗引导打开小程序，只保留安静入口。

### 阅读模式

| 模式 | 中心原文画布 | 左侧工具区 | 右侧 AI 区 |
|------|---------|-----------|---------|
| 沉浸 (Immersive) | 纯正文或仅保留最低限度词汇高亮 | 词典保持可用，动态区弱化 | 收起 |
| 标准 (Standard) | 正文 + 译文 + 词汇/短语/语境标注 + 语法下划线 | 常驻词典 + 当前操作 | 收起，仅保留 Ask 入口 |
| 精读 (Intensive) | 正文 + inline marks + grammar_note / sentence_analysis 句后卡 | 常驻词典 + 选句操作/阅读设置 | 可展开上下文问答 |

默认使用标准模式。语法下划线默认显示但不展开脚注；结构链默认不显示。用户可切换到原文 / 沉浸模式。

## 核心交互模型

### 主阅读区交互

| 交互 | 触发 | 行为 |
|------|------|------|
| 点击 inline_mark | 点击高亮词/短语 | 原文附近出现轻释义浮层，左上词典面板实时显示详细释义 |
| 点击任意词 | 点击非标注英文词 | 原文附近出现轻释义浮层，左上词典同步显示完整词条 |
| 选句操作 | 点击或选中句子 | 左下动态区显示高亮 / 写笔记 / 收藏 / 问 Claread |
| 语法说明 | 点击 grammar_note 锚点 | 在当前句子下方展开 label + note_zh 卡 |
| 句子拆解 | 点击 sentence_analysis 入口 | 在当前句子下方展开 analysis_zh 和 chunks 列表 |
| 阅读设置 | 点击 `Aa` | 左下动态区切换为字号、行距、背景、译文、标注显示控制 |

### 工作区联动

| 交互 | 触发 | 行为 |
|------|------|------|
| 翻译切换 | 快捷键 `T` 或阅读设置 | 逐句翻译显示/隐藏，正文版心不跳动 |
| 句子聚焦 | 点击主区句子 | 句子轻高亮，左下显示句子操作 |
| 查词历史 | 当前会话内多次查词 | 左上词典下方显示本次查词 chips，不进入长期资产 |
| 写笔记 | 左下动态区保存 | 正文句子处显示高亮和笔记痕迹 |
| Ask Claread | 左下操作或右侧入口 | 右侧 AI 展开，并带入当前句子/选区上下文 |

Grammar X-Ray 是未来 Web 高保真语法透视能力，不属于当前 baseline。当前 `grammar_note` 和 `sentence_analysis` 不应在 UI 中命名为 X-Ray，也不应被渲染成完整句法图。

### 词典浮层交互

| 交互 | 触发 | 行为 |
|------|------|------|
| 查词 | 点击 inline_mark 或普通英文词 | 原文附近展示轻释义，左上词典展示完整词条 |
| 歧义选择 | 返回 `disambiguation` | 候选列表 → 点击选择 → 加载词条 |
| 未找到 | 返回 `not_found` | 空态提示 + 建议反馈 |
| 加入生词本 | 左上词典按钮 | `POST /vocabulary`，保存原词、上下文句和来源记录 |
| 查看完整词条 | 左上词典面板 | `GET /dict/entry` → 完整释义进入常驻词典面板 |

## 快捷键体系

### 全局

| 按键 | 功能 |
|------|------|
| `?` | 快捷键帮助面板 |
| `Esc` | 关闭当前面板/退出模式 |

### 阅读导航

| 按键 | 功能 |
|------|------|
| `J` | 下一段落 |
| `K` | 上一段落 |
| `N` | 下一个标注（inline_mark） |
| `M` | 上一个标注 |
| `G` | 跳转到指定段落（输入段落号） |

### 显示控制

| 按键 | 功能 |
|------|------|
| `T` | 翻译显示切换 |
| `I` | 沉浸模式（隐藏所有标注） |
| `X` | 精读模式（显示全部标注+翻译） |
| `D` | 关闭词典浮层 |
| `[` | 缩小字体 |
| `]` | 增大字体 |

### 操作

| 按键 | 功能 |
|------|------|
| `H` | 对选中文本添加高亮 |
| `E` | 对选中文本添加笔记 |
| `S` | 分享/导出当前结果 |
| `Cmd/Ctrl+F` | 文内搜索（浏览器原生） |

## 词典浮层设计

### 触发方式

1. **点击 inline_mark**：直接查 `lookup_text`，`lookup_kind` 决定查词/查短语。
2. **点击普通英文词**：用该词和当前句子上下文调用词典 BFF。
3. **选区后点查词**：精确选区工具栏后置，待 DOM selection 到 canonical offset 映射稳定后再启用。

### 展示位置

- **桌面**：原文附近只显示轻量释义浮层；详细释义始终进入左上常驻词典面板
- **移动**：底部 sheet

### 内容结构

原文附近的轻释义浮层只承担即时反馈：

```text
┌─────────────────────────┐
│  word  /wɜːrd/           │
│  当前语境下的简短释义      │
│  左侧查看完整释义          │
└─────────────────────────┘
```

左上常驻词典承担完整词条：

```text
┌─────────────────────────┐
│  word  /wɜːrd/           │
│  词性 / 本文含义           │
│  详细释义                 │
│  例句 / 短语 / 搭配        │
│  [加入生词本]              │
└─────────────────────────┘
```

### 歧义处理

当 `/dict` 返回 `disambiguation` 时，原文轻浮层只提示“多个候选词条”，具体候选在左上词典面板中选择：

```
┌─────────────────────────┐
│  "light" 有多个释义       │
│                          │
│  ○ light¹  n. 光，光线   │  ← candidate 1
│  ○ light²  adj. 轻的     │  ← candidate 2
│  ○ light³  v. 点燃       │  ← candidate 3
│                          │
│  左侧请选择要查看的释义     │
└─────────────────────────┘
```

### 空态

当 `/dict` 返回 `not_found` 时：

```
┌─────────────────────────┐
│  "xyz" 未收录             │
│                          │
│  词典暂未收录此词。       │
│  [反馈缺失]               │
└─────────────────────────┘
```

## 批注系统设计

### 前期技术调研摘录（非定稿）

Web Reader 当前是只读 `render_scene` 渲染，不建议首期引入 ProseMirror / Tiptap 作为正文渲染引擎。更稳妥的方向是：

- 以 `article.render_text` 的绝对 offset 作为文本锚点坐标系。
- Reader DOM 渲染时给 paragraph / sentence / text segment 标记 `data-start`、`data-end`，用浏览器 Selection / Range API 将用户选区反算回 offset。
- 后端 `inline_marks` 和用户 `user_annotations` 分层渲染：前者是不可编辑的解释标注，后者是可编辑/删除的个人高亮和笔记。
- 持久标注优先用 DOM segment / span 渲染；临时搜索、当前聚焦或 hover preview 可后续评估 CSS Custom Highlight API。
- `multi_text` 结构线索不要伪装成连续高亮，应按 parts 分段标记并用编号、颜色或 hover 联动表达同一条解释。
- PDF / 外部网页批注如果后续进入 Web，需要单独设计 anchor resolver；不要直接复用文本 Reader 的 DOM offset 实现。

### 数据模型

复用 `user_annotations` API：

| 字段 | 说明 | Web 增强 |
|------|------|---------|
| `annotation_type` | `highlight / note` | Web 完整支持 |
| `anchor_type` | `sentence / paragraph / text_range` | Web v1 先使用句子级；精确 `text_range` 后置 |
| `selected_text` | 选中文本 | Web 用浏览器 Selection API 获取 |
| `start_offset / end_offset` | 字符偏移 | Web 基于 `render_text` 坐标 |
| `color` | 5 色高亮 | Web 完整支持 |
| `note` | 笔记文本 | Web 支持富文本（后续） |

### 选区操作工具栏

用户选中文本后，浮出工具栏：

```
┌─────────────────────────────────────────┐
│  🟢  🔵  🟣  🟡  🟢  │  📝  │  🔍  │
│   高亮颜色选择        笔记   查词       │
└─────────────────────────────────────────┘
```

v1 操作流程：
1. 用户点击或选中句子。
2. 左下动态操作区显示高亮、写笔记、收藏、问 Claread。
3. 保存本句高亮或笔记。
4. 正文画布中的对应句子显示高亮、书签或笔记卡，左下区域只负责编辑和状态反馈。
5. 精确选区工具栏后置，待 DOM selection 到 canonical offset 映射稳定后再启用。

### 批注展示

批注不再作为右侧汇总列表默认展示。v1 中批注展示优先级：

1. 句子高亮：直接覆盖在原文句子上。
2. 用户笔记：以句后 note slip 或句子边缘标记呈现。
3. 收藏：句子角标或边缘书签。
4. Library / Excerpts 后续可提供跨文章批注索引，但不进入 Reader 默认右栏。

筛选维度：
- 按颜色
- 按类型（高亮/笔记）
- 按段落

点击批注 → 主阅读区滚动到对应位置并高亮。

### 与 render_scene inline_marks 的关系

- `inline_marks` 是后端分析产出的标注（vocab_highlight / phrase_gloss / context_gloss / grammar_note）
- `user_annotations` 是用户手动创建的批注（highlight / note）
- 两者独立存在，UI 上可叠加显示
- inline_marks 不可编辑，user_annotations 可编辑/删除

## 历史回看设计

### 列表页

Library 第一版是安静的阅读资产索引，不做卡片墙或后台 dashboard。默认以列表方式呈现标题、状态、时间、阅读目标、收藏状态和少量资产摘要；搜索框只做客户端标题和原文片段搜索，后端语义搜索后置。

### 筛选维度

- `reading_goal`：exam / daily_reading / academic
- `source_type`：user_input / daily_article / imported / ocr
- 日期范围：date_from / date_to
- 搜索：标题/原文片段客户端搜索；后端语义搜索后置

### 详情进入

- 点击记录 → `GET /records/{id}?include_render_scene=true` → 进入 Reader 页
- Reader 页 URL：`/reader/{record_id}`

### 与小程序差异

| 维度 | 小程序 | Web |
|------|--------|-----|
| 列表布局 | 纵向列表 | 高密度资产列表 |
| 筛选 | 无 | reading_goal / source_type / 日期 |
| 搜索 | 无 | 标题/原文片段客户端搜索 |
| 详情入口 | `client_record_id` | `cloud_record_id`（URL 友好） |
| 批量操作 | 无 | 后续可加：批量删除/导出 |

## Academic 模式增强

Academic `RenderSceneModel` 包含 Web 可结构化渲染的额外字段：

| 字段 | 小程序处理 | Web 增强 |
|------|-----------|---------|
| `content_summary` | 可能忽略 | 结构化渲染：overview / research_question / methodology / key_findings / limitations |
| `term_note` inline_marks | 降级为普通标注 | 独立渲染：术语卡片，按 term_category 分组 |
| `logic_note` inline_marks | 降级为普通标注 | 独立渲染：逻辑关系标注，按 logic_type 分色 |
| `interpretation_note` sentence_entries | 降级 | 独立面板：解读笔记 |
| `title` | 可能忽略 | 页面标题 + 历史记录标题 |

## 状态管理设计

### 服务端状态（TanStack Query）

| Query Key | 数据 | 缓存策略 |
|-----------|------|---------|
| `["records", { page, goal, type }]` | 历史记录列表 | staleTime: 30s |
| `["record", id]` | 单条记录详情 | staleTime: 5min |
| `["task", id]` | 任务状态 | 轮询期间 staleTime: 0 |
| `["dict", query]` | 词典结果 | staleTime: 1h（与后端 Cache-Control 对齐） |
| `["quota"]` | 用户配额 | staleTime: 1min |
| `["vocabulary", { page }]` | 生词列表 | staleTime: 30s |
| `["annotations", recordId]` | 当前记录批注数据 | staleTime: 30s |

### 客户端 UI 状态（Zustand）

| Store | 数据 |
|-------|------|
| `useAuthStore` | 登录态、session_token、用户信息 |
| `useReaderStore` | 阅读模式、字体大小、翻译显示、面板开关 |
| `useLayoutStore` | 侧栏宽度、面板折叠状态 |
| `useAnnotationStore` | 当前选区、批注工具栏状态 |

### 持久化

| 数据 | 存储位置 | 说明 |
|------|---------|------|
| session_token | httpOnly cookie | 安全 |
| 阅读偏好（字体/模式） | localStorage | 跨 session 保持 |
| 面板布局状态 | localStorage | 跨 session 保持 |
| 滚动位置 | sessionStorage | 同 session 内恢复 |
