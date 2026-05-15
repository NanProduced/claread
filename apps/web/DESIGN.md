---
name: Claread Web Functional UI
description: High-fidelity web reading, annotation, grammar visualization, and export design for Claread.
colors:
  web-canvas: "#F7F6F2"
  reader-paper: "#FAF9F6"
  surface: "#FFFFFF"
  surface-warm: "#FBFAF6"
  surface-raised: "#FDFCF8"
  ink: "#111111"
  ink-soft: "#1A1A1A"
  muted: "#62656D"
  subtle: "#858A92"
  hairline: "#E8E4DA"
  lens-blue: "#2563EB"
  lens-blue-soft: "#EAF1FF"
  vocab-amber: "#E4B000"
  phrase-lavender: "#B9A8E6"
  context-blue: "#4C91C2"
  grammar-violet: "#746694"
  structure-green: "#3C8C68"
  error-red: "#BE123C"
typography:
  display:
    fontFamily: "Source Serif Pro, Georgia, Times New Roman, Noto Serif SC, serif"
    fontSize: "clamp(2.25rem, 5vw, 4.5rem)"
    fontWeight: 700
    lineHeight: 1.06
    letterSpacing: "normal"
  headline:
    fontFamily: "Source Serif Pro, Georgia, Times New Roman, Noto Serif SC, serif"
    fontSize: "clamp(1.75rem, 3vw, 2.75rem)"
    fontWeight: 650
    lineHeight: 1.18
    letterSpacing: "normal"
  reading:
    fontFamily: "Source Serif Pro, Georgia, Times New Roman, serif"
    fontSize: "1.1875rem"
    fontWeight: 400
    lineHeight: 1.9
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Inter, PingFang SC, Helvetica Neue, Arial, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 650
    lineHeight: 1.35
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Inter, PingFang SC, Helvetica Neue, Arial, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.65
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Inter, PingFang SC, Helvetica Neue, Arial, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 650
    lineHeight: 1.2
rounded:
  xs: "4px"
  sm: "8px"
  md: "12px"
  note: "16px"
  panel: "20px"
  sheet: "28px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
  xxl: "48px"
components:
  app-shell:
    backgroundColor: "{colors.web-canvas}"
    textColor: "{colors.ink}"
  reader-surface:
    backgroundColor: "{colors.reader-paper}"
    textColor: "{colors.ink}"
    typography: "{typography.reading}"
  note-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.note}"
    padding: "18px 20px"
  primary-action:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.surface}"
    rounded: "{rounded.pill}"
    padding: "11px 18px"
  lens-focus:
    backgroundColor: "{colors.lens-blue}"
    textColor: "{colors.surface}"
    rounded: "{rounded.sm}"
---

# Design System: Claread Web Functional UI

## 1. Overview

**Creative North Star: "The Editorial Reading Instrument"**

Claread Web 是一个功能优先的高保真阅读空间。它不靠 landing 建立第一印象，而靠 `/app` 的粘贴即解读、`/app/reader/[id]` 的文章解析页、历史/生词/复习这些日常功能，以及后续分享和导出产物来建立品牌记忆。

Web 的默认体验是以原文画布为中心的 Reader 工作台。用户先看到一篇安静、可读、版心稳定的英文文章；当他需要理解时，词汇、语法、句子结构、用户笔记和 AI 追问都围绕原文锚点出现。Web 的能力要比小程序完整，但界面不应该把解析内容搬离正文，变成侧边列表或后台面板。

Web 可以拥有轻应用壳，但应用壳不是品牌。产品区第一版使用左侧可折叠 rail；顶部导航更适合后续 landing / 内容页，不作为登录后主应用导航。rail 必须像阅读仪器条一样服务阅读和产物，而不是把 Claread 拉成 SaaS 后台。

**Key Characteristics:**

- Functional pages first, but with premium craft.
- Article canvas first, with a permanent dictionary panel and contextual tools.
- Current Reader baseline adapts `grammar_note` and `sentence_analysis` faithfully before any Web-only grammar visualization.
- Grammar X-Ray remains a future Web-native capability that depends on a dedicated structured xray payload.
- Artifact Studio as a second-stage export/share surface.
- Calm app shell, expressive artifacts.

## 2. Colors

Web 使用更清洁的 paper/canvas 分层：页面外层可以稍冷更干净，Reader 内层保持暖纸。Lens Blue `#2563EB` 只用于 CTA 主按钮、当前激活状态、品牌符号和内嵌链接；禁止大面积铺色。

### Primary

- **Reader Paper**: Reader 主阅读面，承载文章和轻标注。
- **Web Canvas**: 功能页外层背景，比小程序纸色更干净，避免 Web 端显得发黄。
- **Printed Ink**: 正文、标题、主操作和关键结构。
- **Lens Blue**: CTA 主按钮、当前激活状态、品牌光、内嵌链接和分享中的 Claread 记忆点。

### Secondary

- **Vocabulary Amber**: 重点词和词义。
- **Phrase Lavender**: 短语、搭配和轻语法提示。
- **Context Blue**: 语境说明和原文回源。
- **Grammar Violet**: 语法结构和句法关系。
- **Structure Green**: 段落逻辑、篇章结构、理解完成状态。
- **Error Red**: 分析失败、风险和极少数考试重点。

### Neutral

- **Surface White**: note card、历史列表、输入区、设置面板。
- **Surface Warm**: Reader 里的解释浮层和导出预览。
- **Muted / Subtle**: 元信息、辅助说明、inactive 状态。
- **Hairline**: 分隔、面板边界、工具区和 AI 工作区边界。

### Named Rules

**The Reader Paper Rule.** 文章所在区域必须有明确阅读面。不要让文章漂在普通后台灰底上。

**The Blue As Focus Rule.** Lens Blue 只用于焦点和品牌记忆，不用于普通按钮海洋。普通按钮使用墨色、灰色、透明或纸面边界。

**The Semantic Mark Rule.** 标注颜色必须对应语义层，不允许随意装饰。

## 3. Typography

**Display Font:** Editorial serif for share/export and rare brand moments.  
**Body Font:** Apple system / Inter / PingFang SC for controls and Chinese explanation.  
**Reading Font:** Serif for English article body and example sentences.

**Character:** Web 端应该比小程序更像一份被排版过的英文阅读材料。Reader 正文有文学和杂志感，解释和工具有系统 UI 的清晰度。

### Hierarchy

- **Display** (700, clamp 2.25rem to 4.5rem): 分享、导出、少量品牌封面，不用于普通功能页堆标题。
- **Headline** (650, serif): 文章标题、Reader 模式标题、导出笔记标题。
- **Reading** (400, 1.1875rem, 1.9): 英文正文。桌面默认行长控制在 65-75ch。
- **Title** (650, 1.125rem): 解释卡、工具区、历史项标题、设置组。
- **Body** (400, 0.9375rem, 1.65): 中文解释、定义、按钮说明、面板正文。
- **Label** (650, 0.75rem): 注释类别、模式切换、元信息和导出控件。

### Named Rules

**The Reader Weight Rule.** 正文不能被控件字体压过。控件可以清晰，但不应比文章更有存在感。

**The Sentence Room Rule.** 长难句拆解必须给足水平和垂直空间。当前 `sentence_analysis` 是 chunks + 文本解释，不要把它伪装成语法树。

## 4. Elevation

Web 的层级应像纸面、工具面板和 AI 工作区，而不是卡片堆。Reader 内保持低阴影；词典、句子卡片、selection toolbar、AI 面板和导出预览才允许更明确的 elevation。

### Shadow Vocabulary

- **Surface Quiet** (`0 1px 2px rgba(17, 17, 17, 0.03), 0 8px 24px rgba(17, 17, 17, 0.04)`): 普通 note、历史项、输入面。
- **Reader Float** (`0 14px 42px rgba(28, 24, 18, 0.11)`): 词典浮层、语法解释、selection toolbar。
- **Workbench Layer** (`inset 1px 0 0 rgba(17, 17, 17, 0.06)`): 左侧工具区、右侧 AI 工作区和边界。
- **Artifact Lift** (`0 28px 90px rgba(17, 17, 17, 0.16)`): 导出/分享预览。

### Named Rules

**The Shell Recedes Rule.** 应用壳永远比 Reader 轻。侧栏、顶栏、历史面板不能抢正文。

**The Artifact Performs Rule.** 分享/导出预览可以更有材质和舞台感，因为它是传播对象。

## 5. Components

### App Shell

- **Role:** 承载 `/read`、Library、Vocabulary、Settings 等功能页。
- **Character:** 左侧可折叠 rail，默认显示图标 + 文字，手动折叠后只保留图标。它是阅读仪器条，不是传统 SaaS sidebar。
- **State:** Reader 中 rail 应自动弱化，阅读优先。移动 Web 退化为四个底部入口，不含复习。

### Paste-to-Read Entry

- **Role:** `/read` 的主入口。
- **Character:** 像一张干净纸面或编辑台，而不是表单。
- **Content:** 输入区、解析目标、最近记录、少量模式入口。
- **Anonymous:** 未登录不开放模型试用；可展示精选解析示例让用户理解效果。
- **Constraint:** 不要让用户先配置复杂选项。

### Editorial Reader

- **Role:** 默认 Reader。
- **Character:** 中心文章画布、暖纸阅读面、行内标注、句后解释卡、用户笔记痕迹。
- **Width:** 正文 65-75ch，左右工具区不能压缩正文到不可读。
- **Behavior:** 词典、语法说明、句子拆解、用户笔记都从原文锚点触发；主解释优先贴回正文。

### Reader Workbench Side Areas

- **Role:** 桌面端 Reader 的辅助工作区。
- **Left Top:** 常驻词典面板。用户点击词/短语时实时显示详细释义、例句、短语、加入生词本和本次会话查词痕迹。
- **Left Bottom:** 动态操作区。选择句子时显示高亮、写笔记、收藏、问 Claread；点击 `Aa` 时切换为阅读设置。
- **Right:** AI 工作区。默认可收起，仅作为 Ask Claread 入口；展开后围绕当前句子、选区或全文对话。
- **Constraint:** 左侧不是 tabbed 资料库，右侧不是批注列表。解析卡片不应被统一收纳到边栏。

### Grammar Notes / Sentence Analysis Baseline

- **Role:** 首版 Reader 适配当前 workflow schema 中的两类结构说明。
- **Character:** `grammar_note` 是原文锚点 + 语法标签 + 中文说明；`sentence_analysis` 是长难句 `analysis_zh` + chunks 列表。
- **Placement:** `grammar_note` 和 `sentence_analysis` 优先在相关句子下方展开；词典轻释义可贴近原文弹出，详细释义进入左侧常驻词典面板。
- **Constraint:** 不使用 Grammar X-Ray 命名，不把 chunks 或普通语法说明渲染成未来语法透视模式。

### Future Grammar X-Ray

- **Role:** Web 后续标志性语法可视化能力。
- **Character:** 基于结构化 xray payload，用专门组件表达结构骨架、搭配槽、修饰挂靠、指代链、辨析卡等不同语法类型。
- **Placement:** 后续 Reader 模式、分享/导出模板或按需展开能力。
- **Constraint:** 当前 workflow schema 不支持。不得用 `grammar_note` 或 `sentence_analysis` 冒充 Grammar X-Ray。

### History / Library List

- **Role:** 复看文章和用户资产。
- **Character:** 安静列表，不做复杂 read-it-later inbox。
- **Content:** 标题、时间、目标、状态、收藏、生词/语法数量摘要、客户端标题/片段搜索。

### Vocabulary / Review

- **Role:** 生词资产和轻复习。Vocabulary 是一级入口；Review 是 Vocabulary 内的动作按钮，不放入一级导航。
- **Character:** 降权但可用，不做打卡中心。
- **Content:** 词、语境句、来源文章、学习中/已掌握状态、复习入口。查词历史不保存，只有主动加入生词本的词进入资产。

### Artifact Studio

- **Role:** 分享和导出预览。
- **Character:** 中央预览 + 少量模板和导出控制。
- **Templates:** Magazine Brief、Notebook Study、Minimal Card；Grammar X-Ray 模板后置到结构化 xray payload 可用之后。
- **Constraint:** 第二阶段，不阻塞首期 Reader。

## 6. Do's and Don'ts

### Do:

- **Do** 先做 `/read`、Reader、Library、Vocabulary、Settings 和后续 share 等功能页。
- **Do** 让 Reader 成为 Web 端的品牌第一现场。
- **Do** 让原文画布承载主要解析展示，左右区域只做工具、详情和 AI 辅助。
- **Do** 先把 `grammar_note` 和 `sentence_analysis` 做成可读、可定位、可回到原文的 baseline 体验。
- **Do** 把 Grammar X-Ray 保留为后续增强，不在首版 Reader 中提前命名或伪装。
- **Do** 保持小程序功能兼容，同时利用 Web 做更完整的 render profile。
- **Do** 为后续导出/分享预留 Artifact Studio 的信息架构。

### Don't:

- **Don't** 先投入完整 landing 和产品介绍页。
- **Don't** 使用传统 SaaS dashboard 作为 Web 默认形态。
- **Don't** 把右侧栏做成批注仓库；AI 可以在右侧，但必须围绕当前原文上下文工作。
- **Don't** 让历史、生词、设置这些功能页决定 Claread 的品牌气质。
- **Don't** 让标注颜色失去语义。
- **Don't** 为了视觉效果牺牲阅读行长、对比度和原文锚点。
