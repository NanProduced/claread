---
name: Claread Web Design System
description: A unified editorial web system for deep reading, product surfaces, and shareable learning artifacts.
colors:
  paper-canvas: "#F3EFE6"
  paper-stage: "#F8F4EA"
  paper-panel: "#FBF7EE"
  paper-ink: "#171511"
  paper-muted: "#6E685E"
  paper-hairline: "#D9D1C3"
  light-canvas: "#F7F5F0"
  light-stage: "#FBFAF6"
  light-panel: "#FFFFFF"
  light-ink: "#151515"
  light-muted: "#666A73"
  light-hairline: "#E3DED3"
  dark-canvas: "#161412"
  dark-stage: "#221F1A"
  dark-panel: "#2A2621"
  dark-ink: "#F4EEDF"
  dark-muted: "#B1A899"
  dark-hairline: "#3A342D"
  lens-blue: "#1F5EFF"
  lens-blue-night: "#8CAEFF"
  vocab-amber: "#D49A18"
  phrase-plum: "#8E779F"
  context-blue: "#4F89B3"
  grammar-violet: "#6E6389"
  structure-green: "#557B5C"
  stamp-umber: "#8A5A2B"
typography:
  display:
    fontFamily: "Source Serif 4, Source Han Serif SC, Georgia, Times New Roman, serif"
    fontSize: "clamp(3rem, 5.2vw, 5.4rem)"
    fontWeight: 400
    lineHeight: 0.92
    letterSpacing: "normal"
  headline:
    fontFamily: "Source Serif 4, Source Han Serif SC, Georgia, Times New Roman, serif"
    fontSize: "clamp(2rem, 3vw, 3rem)"
    fontWeight: 500
    lineHeight: 1.06
    letterSpacing: "normal"
  title:
    fontFamily: "Inter, PingFang SC, Hiragino Sans GB, Microsoft YaHei, sans-serif"
    fontSize: "1rem"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "0.01em"
  body:
    fontFamily: "Inter, PingFang SC, Hiragino Sans GB, Microsoft YaHei, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.65
    letterSpacing: "normal"
  label:
    fontFamily: "Inter, PingFang SC, Hiragino Sans GB, Microsoft YaHei, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "0.12em"
  reading:
    fontFamily: "Source Serif 4, Source Han Serif SC, Georgia, Times New Roman, serif"
    fontSize: "1.18rem"
    fontWeight: 400
    lineHeight: 1.92
    letterSpacing: "normal"
rounded:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "18px"
  xl: "24px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
  xxl: "48px"
components:
  app-shell-default:
    backgroundColor: "{colors.paper-canvas}"
    textColor: "{colors.paper-ink}"
    rounded: "{rounded.lg}"
    padding: "24px 24px"
  reader-stage-default:
    backgroundColor: "{colors.paper-stage}"
    textColor: "{colors.paper-ink}"
    typography: "{typography.reading}"
    rounded: "{rounded.xl}"
    padding: "32px 40px"
  mode-switch-active:
    backgroundColor: "{colors.paper-panel}"
    textColor: "{colors.paper-ink}"
    rounded: "{rounded.pill}"
    padding: "10px 16px"
  primary-action:
    backgroundColor: "{colors.paper-ink}"
    textColor: "{colors.paper-stage}"
    rounded: "{rounded.pill}"
    padding: "12px 18px"
  reader-translation-layer:
    backgroundColor: "{colors.paper-stage}"
    textColor: "{colors.paper-muted}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "0 0 0 0"
  sentence-note-card:
    backgroundColor: "{colors.paper-panel}"
    textColor: "{colors.paper-ink}"
    rounded: "{rounded.lg}"
    padding: "16px 18px"
---

# Design System: Claread Web Design System

## Overview

**Creative North Star: "带批注的编辑台"**

Claread Web 是 Claread 在浏览器中的完整设计系统，不是“当前先做功能页，所以设计只服务功能页”的阶段性风格说明。它必须同时约束功能页、Reader、未来产品页、分享页、导出页与后续内容型页面，让这些页面看起来像同一份被认真编辑过的出版物，而不是几套彼此脱节的 UI 风格碰巧共存于同一个产品中。

这套系统的母语言来自“编辑性的阅读对象”，而不是“软件模板”。它像一张带批注的编辑台，也像一份尚在工作中的长杂志母本。这个母语言既要能够支撑产品页的开场气氛，也要能够支撑功能页的任务秩序、Reader 的深度阅读、以及分享导出的产物感。Reader 是 Claread 最完整的品牌现场，但不是唯一现场。产品页、分享页和导出页也必须说同一门语言，只是语气、密度和浓度不同。

`纸质 Paper` 是 Claread 的母主题，`浅色 Light` 与 `深色 Dark` 是同一语言的浓度调节，而不是三套彼此陌生的皮肤。Paper 保留 Claread 最完整的气味；Light 把暖意调淡，更偏功能化工作面；Dark 把暖意换夜，更偏夜读。三者共享同一套批注语法、同一套间距纪律、同一组组件系统。变化的是氛围，不是系统。

Reader 只有两种模式。`精读 Intensive` 是主工作模式，强调批注感与校读纪律；`沉浸 Immersive` 是回到文章，强调编排感与篇章开场。它们不是“预设 + 自定义”的关系，也不是一套控制面板调出来的显示组合，而是同一阅读系统中的两种阅读意图。

**Key Characteristics:**

- 全站是一套统一编辑语言，而不是若干页面风格拼接
- 产品页、功能页、Reader、分享页共享同一母语言
- 阅读舞台优先于应用壳，但产品页也必须有开场仪式感
- 纸感随距离原文的远近递增
- 精读模式说批注语言，沉浸模式说编排语言
- 句后批注展开是 Claread 最有记忆点的动作

## Colors

Claread 的色彩不是“暖纸 + 蓝色按钮”，而是一套可跨全站复用的编辑阅读配色。离原文越近，纸感越明确；离原文越远，纸感越克制。外层是干净的工作台面，内层是摊开的稿纸。进入 Reader 时，用户应当感到一点纸的气味；进入功能页或产品页时，这种纸感应当被稀释成受控氛围，而不是变成表面装饰。

### Primary

- **纸面母色组** (`#F3EFE6`, `#F8F4EA`, `#FBF7EE`)：`paper-canvas` 是工作台面与产品级背景，`paper-stage` 是正文舞台与核心内容区，`paper-panel` 是贴近内容的面板、旁注层与精选内容卡。三者共同构成 Claread 的母主题。
- **Lens Blue** (`#1F5EFF`)：只用于焦点、当前选择、模式激活和少量品牌记忆点。在夜读里转成 **Lens Blue Night** (`#8CAEFF`)，不变成霓虹。

### Secondary

- **Vocabulary Amber** (`#D49A18`)：词汇高亮与重点词标记。它应像克制的荧光笔，不像装饰色块。
- **Grammar Violet** (`#6E6389`)：语法关系与句法提示。它是一种结构信号，不是一层紫色背景。
- **Structure Green** (`#557B5C`)：理解完成、句子结构、冷静的分析感。

### Tertiary

- **Phrase Plum** (`#8E779F`)：短语单位、搭配关系与轻分组。
- **Context Blue** (`#4F89B3`)：语境说明、回源与上下文连接。
- **Stamp Umber** (`#8A5A2B`)：极少量签名印章。它只能稀疏存在，不能膨胀成装饰主题。

### Neutral

- **浅色工作面** (`#F7F5F0`, `#FBFAF6`, `#FFFFFF`)：Paper 的稀释版本，用于更功能化的工作页与更克制的产品层。
- **夜读工作面** (`#161412`, `#221F1A`, `#2A2621`)：Paper 的夜读版本。必须暖、深、有层次，但不能是企业后台黑，也不能像游戏启动器。
- **Editorial Ink** (`#171511`, `#151515`, `#F4EEDF`)：三主题通用的主文字家族。
- **Muted Reading Voice** (`#6E685E`, `#666A73`, `#B1A899`)：译文、元信息、辅文与第二层界面声音。
- **Hairline Rule** (`#D9D1C3`, `#E3DED3`, `#3A342D`)：1px 引线、边界、分隔和结构规则。

### Named Rules

**The Mother Theme Rule.** `纸质 Paper` 定义系统，`浅色 Light` 与 `深色 Dark` 只调浓度，不换语法。未来产品页也必须从这里出发。

**The Distance Rule.** 越远离原文，纸感越干净；越靠近原文，纸感越明确。

**The No-Fake-Paper Rule.** 禁止纸面纹理、旧化肌理、纤维噪点和伪书页做旧。只要纸感来自装饰，而不是来自层次和用色，它就失败了。

## Typography

**Display Font:** Source Serif 4 搭配 Source Han Serif SC 回退。  
**Body Font:** Inter 搭配 PingFang SC 回退。  
**Label/Mono Font:** 核心产品不需要另一套 mono 主语汇，标签仍由 Inter 承担。

**Character:** Claread 的字体系是 `60%` 学术骨架与 `40%` 文学气味。它不是 JAMA 式冷白学术，也不是 Granta 式文学封面。它更接近 LRB、The Atlantic、Aeon 那种长杂志语气：骨架严谨，开场有气味，正文始终可读。这个判断同时适用于产品页 hero、Reader 标题、精选卡、分享封面和导出物。

### Hierarchy

- **Display** (`400`, `clamp(3rem, 5.2vw, 5.4rem)`, `0.92`)：只用于开篇时刻。首页级功能页、未来产品页 hero、分享封面可以像杂志封面一样使用它，但不能把所有页面都做成封面。
- **Headline** (`500`, `clamp(2rem, 3vw, 3rem)`, `1.06`)：文章标题、精选卡标题、沉浸模式开篇标题。存在感强，但不喊叫。
- **Title** (`600`, `1rem`, `1.3`)：面板小标题、设置组标题、句后卡标题。清楚、克制、产品化。
- **Body** (`400`, `0.9375rem`, `1.65`)：中文说明、辅助文字、元信息、控制文案。
- **Label** (`700`, `0.75rem`, `0.12em`)：模式标签、主题标签、分组标签、轻元信息。
- **Reading** (`400`, `1.18rem`, `1.92`)：正文主阅读层。精读模式使用它的稳定节奏，沉浸模式在同一家族内增强段落开场与篇章感。

### Named Rules

**The Low-Voice Translation Rule.** 译文是段级句下辅层，是“低声脚注”，不是并列正文，不是对照表，也不是旁注。

**The Serif Alignment Rule.** 译文出现在原文下方时，必须与原文的衬线节奏对位，即使字号和颜色降一档，也不能失去“同属一段”的归属性。

**The Cover-Moment Rule.** 大标题只属于开场时刻。如果每一页都像封面，就没有任何一页真正像封面。

## Elevation

Claread 不靠厚重阴影制造层级，而靠纸面分层、1px 规则、边距纪律与结构性留白建立秩序。深度存在，但应该是结构深度，不是戏剧深度。正文舞台始终是最值得看的平面；产品页与功能页也必须遵守同样的层级纪律，只是在开场与密度上有所区别。

### Shadow Vocabulary

- **Desk Quiet** (`0 1px 2px rgba(23, 21, 17, 0.03), 0 8px 20px rgba(23, 21, 17, 0.04)`)：应用外层和普通容器的最低层阴影。几乎不应被察觉。
- **Reader Lift** (`0 12px 28px rgba(23, 21, 17, 0.08)`)：正文舞台从外层台面轻轻抬起时使用。
- **Note Unfold** (`0 14px 32px rgba(23, 21, 17, 0.10)`)：句后批注卡、短时 Reader 面板和贴近原文的解释层。它是 Claread 的签名抬升。
- **Night Layer** (`0 18px 36px rgba(0, 0, 0, 0.20)`)：深色夜读层的暖暗分层，不得发硬发脏。

### Named Rules

**The Shell-Recedes Rule.** 应用壳必须比正文舞台更轻。如果用户先看到壳而不是文章，层级就错了。

**The One-Pixel Discipline Rule.** 引线、边界和分隔线是结构工具，不是装饰。它们必须直、薄、准。

**The No-Theatrical-Shadow Rule.** 如果阴影让界面变得“更像组件”而不是“更像结构”，就应该删掉。

## Components

### Buttons

- **Shape:** 克制的圆角按钮与 pill 控件（`18px` 到 `999px`），不软塌，不游戏化。
- **Primary:** 纸上墨，或墨上纸，取决于当前主题。主动作应果断，但不发亮。
- **Hover / Focus:** 动作幅度极小。焦点来自边界、对比与对齐，而不是发光效果。
- **Secondary / Ghost:** 应像工作台上的阅读工具，不是被稀释的 CTA。

### Product Page Hero

- **Role:** Claread Web 的产品页开场。虽然开发顺序靠后，但不属于系统边角。
- **Character:** 杂志封面式开场，带有明确标题场与留白秩序，但仍服务“进入 Claread”这件事，不做纯视觉展台。
- **Typography:** 允许使用 Display 级大标题，但正文说明与入口动作必须迅速把用户带回产品。
- **Constraint:** 不做 SaaS landing clichés，不做空洞的 hero + metrics 模板。

### Feature / Editorial Card

- **Role:** 首页精选、未来产品页 feature block、分享前置内容卡的统一母组件。
- **Character:** 像编辑选题卡，而不是运营卡片网格。
- **Structure:** 标题、摘要、元信息、少量图像与明确去向。
- **Constraint:** 不堆相同卡片网格，不靠过多彩色边框建立层次。

### Mode Switch

- **Role:** 精读模式顶部控制带中的第一主控件。
- **Character:** 模式切换表达阅读意图，不表达“显示选项”。因此它必须视觉上压过阅读设置。
- **Shape:** 靠近标题区的短 segmented capsule，当前项使用激活 pill。
- **Language:** 中文主，英文辅。英文是注记，不是装饰。

### Reader Header / Control Strip

- **Presence:** 中等偏弱。初始进入时可见，滚动后应逐步淡出存在感。
- **Structure:** 标题、元信息、模式切换，然后才是阅读设置。设置必须次于模式。
- **Constraint:** 它不能像生产力软件的一整排 toolbar。

### Translation Layer

- **Role:** 原文段下方的低声脚注。
- **Placement:** 表面是句下，实际服从段级归属。译文必须清楚属于上方那一段原文。
- **Style:** 字号降一档、颜色 muted、与原文保持衬线对位；段尾约 `12px` 收束，段间约 `24px` 保持归属关系。
- **Constraint:** 它绝不能像第二篇正文，也不能像对照翻译表。

### Sentence Note Card

- **Role:** Reader 的签名组件。真正让 Claread 被记住的，不是切模式，而是句后批注像编辑旁注一样被翻出来。
- **Character:** 批注应像从句子下方展开，而不是从一个浮动 inspector 里蹦出来。
- **Language:** 序号、1px 直引线、margin 锚点、克制标签、正文内联高亮。
- **Constraint:** 禁止 sticky-note 拟物、纸片拼贴、装饰性校读符号。

### Annotation Grammar

- **Core Vocabulary:** 序号、1px 直引线、克制单一的 margin 标记、荧光笔式内联高亮、稀疏印章签名。
- **Inline Highlight:** 应该像认真画下的一笔，不像一团涂抹。
- **Theme Behavior:** `Light / Dark / Paper` 共享同一语法。Paper 只在靠近原文时更有物感，不增加额外装饰。

### Immersive Paragraph Surface

- **Role:** 回到文章本身。
- **Character:** 杂志开篇、段落优先、隐藏批注噪声。
- **Typography:** 同一衬线家族，更强的开篇编排感，可使用首字母下沉，但不引入厚重工具感。
- **Constraint:** 沉浸模式不是精读模式的“关闭若干项”，而是另一种排版表达。

### Share / Export Surface

- **Role:** 把一次 Claread 阅读结果沉淀成可传播的阅读产物。
- **Character:** 可以比功能页更有文学气味，但始终受学术骨架约束。它应像“可保存的编辑成果”，而不是社交海报模板。
- **Typography:** 可放大标题、强化版式节奏，但不能脱离 Claread 的正文逻辑。
- **Constraint:** 不做花哨学习海报，不做高饱和传播图。

## Do's and Don'ts

### Do:

- **Do** 把 `纸质 Paper` 视为母主题，再从它推导 `浅色 Light` 与 `深色 Dark`。
- **Do** 让纸感随着离原文的距离而增强，而不是在整站平均铺开。
- **Do** 让精读模式成为工作台，让沉浸模式成为回到文章。
- **Do** 把句后批注展开做成 Claread 最有识别度的交互瞬间。
- **Do** 把译文保持为原文段下方的低声辅层。
- **Do** 让 margin、引线、分隔和 baseline 对齐精确到像素。
- **Do** 让功能页继承 Reader 的抽象纪律：节奏、层级、对齐、克制。
- **Do** 让未来产品页、分享页、导出页也从同一母语言出发，而不是补一套“营销皮肤”。

### Don't:

- **Don't** 把阅读模式做成设置 preset，也不要让阅读设置反向拼出模式效果。
- **Don't** 在精读模式里把 Claread 的核心标注价值变成可有可无的隐藏层。
- **Don't** 用后台暗黑逻辑套一层暖色补丁，然后把它叫夜读主题。
- **Don't** 用纸张纹理、旧化肌理、纤维噪点、做旧边缘来伪造纸感。
- **Don't** 用 Post-it 贴纸、装饰性校对符号、手账式拼贴来伪装编辑气质。
- **Don't** 让应用壳定义品牌。定义品牌的是文章舞台。
- **Don't** 让译文读起来像第二篇文章。
- **Don't** 让功能页直接挪用 Reader 的显性符号。它们只能继承纪律，不能继承戏服。
- **Don't** 把产品页当成系统之外的例外页。它只是后开发，不是后设计。
