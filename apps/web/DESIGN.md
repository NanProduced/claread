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
  muted: "#757780"
  subtle: "#A0A4AB"
  hairline: "#E8E4DA"
  lens-blue: "#155CFF"
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

Web 的默认体验是单栏阅读 + 轻旁注。用户先看到一篇安静、可读、版心稳定的英文文章；当他需要理解时，词汇、语法、句子结构和段落逻辑再以行内标注、note slip、轻旁注轨道、浮层和模式切换出现。Web 的能力要比小程序完整，但界面不应该显得更重。

Web 可以拥有轻应用壳，但应用壳不是品牌。窄 rail、顶栏、command/search、右侧轻旁注、导出工作台都可以存在；它们必须服务阅读和产物，而不是把 Claread 拉成 SaaS 后台。

**Key Characteristics:**

- Functional pages first, but with premium craft.
- Single-column reader by default, progressive marginalia when needed.
- Grammar X-Ray as the memorable Web-native capability.
- Artifact Studio as a second-stage export/share surface.
- Calm app shell, expressive artifacts.

## 2. Colors

Web 使用更清洁的 paper/canvas 分层：页面外层可以稍冷更干净，Reader 内层保持暖纸。Lens Blue 只用于聚焦、选中、品牌光线和 Grammar X-Ray 的关键节点。

### Primary

- **Reader Paper**: Reader 主阅读面，承载文章和轻标注。
- **Web Canvas**: 功能页外层背景，比小程序纸色更干净，避免 Web 端显得发黄。
- **Printed Ink**: 正文、标题、主操作和关键结构。
- **Lens Blue**: 品牌光、当前聚焦句子、X-Ray 关键线索、导出/分享中的 Claread 记忆点。

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
- **Hairline**: 分隔、面板边界、旁注轨道边界。

### Named Rules

**The Reader Paper Rule.** 文章所在区域必须有明确阅读面。不要让文章漂在普通后台灰底上。

**The Blue As Focus Rule.** Lens Blue 只用于焦点和品牌记忆，不用于普通按钮海洋。

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
- **Title** (650, 1.125rem): 解释卡、旁注、历史项标题、设置组。
- **Body** (400, 0.9375rem, 1.65): 中文解释、定义、按钮说明、面板正文。
- **Label** (650, 0.75rem): 注释类别、模式切换、元信息和导出控件。

### Named Rules

**The Reader Weight Rule.** 正文不能被控件字体压过。控件可以清晰，但不应比文章更有存在感。

**The Sentence Room Rule.** 长难句分析必须给足水平和垂直空间。不要把语法树塞进小卡片。

## 4. Elevation

Web 的层级应像纸面、旁注和仪器面板，而不是卡片堆。Reader 内保持低阴影；浮层、旁注和导出预览才允许更明确的 elevation。

### Shadow Vocabulary

- **Surface Quiet** (`0 1px 2px rgba(17, 17, 17, 0.03), 0 8px 24px rgba(17, 17, 17, 0.04)`): 普通 note、历史项、输入面。
- **Reader Float** (`0 14px 42px rgba(28, 24, 18, 0.11)`): 词典浮层、语法解释、selection toolbar。
- **Rail Layer** (`inset 1px 0 0 rgba(17, 17, 17, 0.06)`): 轻旁注轨道和边界。
- **Artifact Lift** (`0 28px 90px rgba(17, 17, 17, 0.16)`): 导出/分享预览。

### Named Rules

**The Shell Recedes Rule.** 应用壳永远比 Reader 轻。侧栏、顶栏、历史面板不能抢正文。

**The Artifact Performs Rule.** 分享/导出预览可以更有材质和舞台感，因为它是传播对象。

## 5. Components

### App Shell

- **Role:** 承载 `/app`、history、vocabulary、profile 等功能页。
- **Character:** 轻、克制、可隐藏。可用顶部导航、窄 rail、command/search，但避免传统 SaaS sidebar。
- **State:** Reader 中 shell 应自动弱化，阅读优先。

### Paste-to-Read Entry

- **Role:** `/app` 的主入口。
- **Character:** 像一张干净纸面或编辑台，而不是表单。
- **Content:** 输入区、解析目标、最近记录、少量模式入口。
- **Constraint:** 不要让用户先配置复杂选项。

### Editorial Reader

- **Role:** 默认 Reader。
- **Character:** 单栏文章、暖纸阅读面、行内标注、浮动 note slip。
- **Width:** 正文 65-75ch，旁注不破坏版心。
- **Behavior:** 点击/hover/选中才展开更多解释。

### Light Marginalia Rail

- **Role:** 桌面端轻旁注轨道。
- **Character:** 可收起、低密度、只显示当前段落或当前焦点句相关解释。
- **Constraint:** 不是常驻知识库侧栏，也不是聊天框。

### Grammar X-Ray

- **Role:** Web 标志性句法分析模式。
- **Character:** 精密、分层、可记住。显示主干、从句、修饰、非谓语、指代和逻辑关系。
- **Placement:** Reader 内切换模式 + 分享/导出模板。
- **Constraint:** 不默认压过阅读模式。

### History / Library List

- **Role:** 复看文章和用户资产。
- **Character:** 安静列表，不做复杂 read-it-later inbox。
- **Content:** 标题、时间、目标、状态、收藏、生词/语法数量摘要。

### Vocabulary / Review

- **Role:** 生词资产和轻复习。
- **Character:** 降权但可用，不做打卡中心。
- **Content:** 词、语境句、来源文章、掌握状态、复习入口。

### Artifact Studio

- **Role:** 分享和导出预览。
- **Character:** 中央预览 + 少量模板和导出控制。
- **Templates:** Grammar X-Ray、Magazine Brief、Notebook Study、Minimal Card。
- **Constraint:** 第二阶段，不阻塞首期 Reader。

## 6. Do's and Don'ts

### Do:

- **Do** 先做 `/app`、Reader、history、vocabulary、review、profile、share 等功能页。
- **Do** 让 Reader 成为 Web 端的品牌第一现场。
- **Do** 默认单栏阅读，让轻旁注渐进出现。
- **Do** 给 Grammar X-Ray 足够视觉空间和品牌记忆点。
- **Do** 保持小程序功能兼容，同时利用 Web 做更完整的 render profile。
- **Do** 为后续导出/分享预留 Artifact Studio 的信息架构。

### Don't:

- **Don't** 先投入完整 landing 和产品介绍页。
- **Don't** 使用传统 SaaS dashboard 作为 Web 默认形态。
- **Don't** 把右侧栏做成 AI chat。
- **Don't** 让历史、生词、设置这些功能页决定 Claread 的品牌气质。
- **Don't** 让标注颜色失去语义。
- **Don't** 为了视觉效果牺牲阅读行长、对比度和原文锚点。
