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

## Current Baseline And Future Surfaces

当前 Web baseline 已经落在真实产品代码上，而不是早期方向图或占位 mock。正式实现以 `apps/web/docs/reader-ia.md`、`apps/web/docs/design/component-system.md`、`apps/web/docs/design/component-library-v0.md` 和当前页面代码共同校准。

当前已经进入 baseline 的设计事实：

- Reader 2.0 使用 `render_scene -> Plate document -> Plate readOnly runtime` 作为 Web 阅读投影。
- Reader 只有 `精读 Intensive` 和 `沉浸 Immersive` 两种阅读意图；阅读设置只校准字号、字体和主题，不反向拼出模式效果。
- `/app/read` 是粘贴开始与每日精选并存的阅读入口，不是后台首页。
- Library 是阅读记录入口，Vocabulary 是词汇资产入口；二者不再合并成独立学习资产中心。
- `grammar_note` 与 `sentence_analysis` 当前是句后解释层和原文锚点组合，不命名为 Grammar X-Ray。

仍属于目标设计系统、但不是当前 baseline 的 surface：

- 完整产品页 hero 与后续营销/内容页。
- 分享页与导出页的最终 artifact 形态。
- Grammar X-Ray 作为未来高保真语法透视能力。
- Artifact Studio、PDF、Markdown、Notion 同步等更完整沉淀能力。

这些未来 surface 必须从本文的同一母语言出发，但不能提前占据当前 Reader、词典、批注和句后解释的主视觉权重。占位页可以存在；占位页不是设计标准。

## Brand Assets

Claread Web 的品牌资产不是临时装饰，也不是页面最后补上的角标。Logo 光圈、横版标识、App icon 和品牌探索图共同定义 Claread 的“阅读镜头”记忆点。任何 Web 端视觉工作都必须先检查品牌资产，再决定页面如何表达品牌。

### Source of Truth

- 品牌源资产目录：`packages/design-tokens/assets/brand/`
- 资产说明：`packages/design-tokens/assets/brand/README.md`
- Web 运行时资产目录：`apps/web/public/brand/`
- Web 品牌组件：`apps/web/src/components/brand/BrandMarks.tsx`

`packages/design-tokens/assets/brand/` 是设计源资产目录，不是客户端运行时代码依赖。Web 页面只引用已经复制或导出到 `apps/web/public/brand/` 的图片。新增运行时资产时，先从源目录选择合适版本，再按 Web 需要压缩、命名和复制。

### Current Asset Roles

- `logos/claread-horizontal-bilingual.png`：横版中英品牌标识，优先用于公开页页眉、登录页、分享页和导出页。
- `logos/claread-primary-fullcolor.png`、`logos/claread-primary-reversed.png`：完整 Logo 的正色和反白版本，用于品牌感更强的页面或深浅背景切换。
- `icons/claread-icon-fullcolor.png`：独立光圈图标，用于侧栏、品牌水印、低频视觉记忆点和 favicon/app icon 派生。
- `icons/app-icon.png`、`icons/claread-app-logo-dark.png`：App icon 与深色场景参考，不直接替代横版标识。
- `design/`：品牌探索、营销视觉和 UI 参考图。它们用于理解气质，不直接进入运行包。

### Usage Rules

- 需要 Logo、横版标识、水印或小印章时，优先复用 `BrandLockup`、`ApertureWatermark`、`ClareadStamp`，不要用纯文字标题、临时 SVG 或重新绘制的几何图形代替品牌资产。
- 公开产品页、登录页、分享页和导出页的首屏必须有明确 Claread 品牌信号。品牌信号可以来自横版标识、光圈图标、阅读镜头构图或三者组合，但不能只靠导航里的小字。
- 功能页可以更克制。侧栏、页头或空态中的品牌资产应像编辑台上的印记，不抢正文舞台。
- 光圈图标用于“聚焦、透读、标记、展开”的场景，不作为普通装饰图案平铺使用。
- 不重新发明 Claread 的 Logo、品牌色、App icon 或装饰符号。如果现有资产不够，先记录设计缺口，再补源资产和运行时导出。
- 品牌资产必须服从 Claread 的 editorial / tactile 气质：清楚、克制、像阅读工具，不做高饱和 SaaS logo 墙、玻璃霓虹或学习 App 海报风。

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

### Implementation Alignment

本文中的 `Paper / Light / Dark` 色组是设计目标层。当前运行时 token 已经接入三主题，但若 `Dark` token 偏向冷黑后台感，应以后续 token 收口为准，把夜读重新拉回暖暗、低刺激、有层次的阅读场景。不要为了匹配现有实现而把本文的夜读原则降级成后台暗色主题。

## Typography

**Display Font:** Source Serif 4 搭配 Source Han Serif SC 回退。  
**Body Font:** Inter 搭配 PingFang SC 回退。  
**Label/Mono Font:** 核心产品不需要另一套 mono 主语汇，标签仍由 Inter 承担。

当前 Web 运行时以 `next/font/google` 接入 `Source Serif 4` 和 `Inter`。如果 `packages/design-tokens/src/web/tokens.css` 中仍保留 `Newsreader` 等旧 reading token，应视为实现待清理项；设计判断以本文和运行时字体槽位为准，不再把旧 token 名称当作新的视觉方向。

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

**The No-Default-Uppercase Rule.** CSS `text-transform: uppercase` 默认禁止使用。全大写只在两种例外下允许：

1. **必须全大写的词或编号**：品牌印章（如 `CLAREAD EDITION`）、功能性编号（如 `A01`, `A02`）。这些词在语义上就是大写形式，不是设计选择。
2. **全大写视觉效果确实更佳的特殊情况**：极少数 1 词 eyebrow 标签（如 `Library`, `Vocabulary`），uppercase 作为编辑排版中的结构性标记确实比 Title Case 更有区分度。但必须逐例判断，不能批量套用。

以下场景一律不得使用 uppercase：

- **3 词及以上的英文短语**：`Sentence Analysis`, `Editor's Note`, `Key Expressions` 等。多词全大写破坏词间边界，降低可读性，是最典型的 AI 生成痕迹。使用 Title Case。
- **操作按钮和链接文案**：`Close`, `Expand`, `Show Translation` 等。操作文案需要被快速识别，全大写反而制造阅读障碍。使用 Title Case。
- **所有中文文案**：CSS `uppercase` 对 CJK 字符无效，在中文标签上添加 `uppercase` 类是代码噪音。中文标签的视觉层级通过 `font-weight`、`font-size`、`letter-spacing` 和 `color` 控制，不依赖大小写变换。
- **面包屑导航**：`Preferences`, `Feedback` 等。导航路径使用 Title Case。
- **主题 / 字体选项标签**：`Paper`, `Light`, `Dark`, `Editorial`, `Book`, `Sans` 等。选项标签使用首字母大写。
- **区块标题**：`Analysis`, `Discussion`, `Writing Moves` 等。区块标题使用 Title Case，靠字号和字重建立层级，不靠全大写。

判断原则：如果去掉 uppercase 后标签仍然能清楚表达其层级和功能，就不应该使用 uppercase。uppercase 是例外，不是默认。

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
- **Shape:** 在 Reader Record 解析页中，模式切换已收敛为 hairline control strip 内的等高 action cell；当前项使用底部 amber underline，不再使用独立 pill segmented control。
- **Language:** 中文主，英文辅。英文是注记，不是装饰。

### Reader Header / Control Strip

- **Status:** `/app/reader-record/{recordId}` Header 线已冻结。后续只接受可用性、响应式或数据状态 bugfix，不再重排骨架。
- **Presence:** 中等偏弱。初始进入时可见，滚动后应逐步淡出存在感；它是文章 masthead，不是应用 toolbar。
- **Structure:** 顶部 eyebrow（阅读模式 + 日期）、中文 masthead H1、单条 hairline action bar、底部低权重 metadata。Header 使用比正文更宽的 editorial column，正文仍保持阅读列宽。
- **Title Source:** 成功态只使用 `snapshot.record.display_title_zh`。`pending` / `failed_retryable` 使用占位 masthead；旧 snapshot 只有在 `title_generation_status` 缺失时才允许用 `record.title` 做 migration fallback。
- **Control Strip:** 左侧是解析状态 chip、source-only word count、reading goal/variant；右侧是收藏、精读、沉浸、阅读设置四个等高 cell。active 模式使用 amber underline。
- **Metadata:** 底部只显示可读来源、日期、词数和原文入口；不暴露 raw `source_type`，不展示句数或估算阅读分钟。
- **Constraint:** 它不能像生产力软件的一整排 toolbar，也不能把设置控件升级成主视觉。

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
