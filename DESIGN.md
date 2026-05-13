---
name: Claread Cross-Client Design System
description: A lucid, editorial, tactile design system for deep English reading and structured AI annotation.
colors:
  ink: "#111111"
  ink-soft: "#1A1A1A"
  paper: "#FAF9F6"
  paper-warm: "#FBF9F4"
  paper-deep: "#F6F3EC"
  surface: "#FFFFFF"
  surface-warm: "#FBFAF6"
  muted: "#7A7D86"
  subtle: "#9BA0A8"
  hairline: "#EAE7DF"
  lens-blue: "#155CFF"
  lens-blue-soft: "#EAF1FF"
  amber-marker: "#E4B000"
  lavender-note: "#B9A8E6"
  context-blue: "#4C91C2"
  grammar-violet: "#746694"
  structure-green: "#3C8C68"
  exam-red: "#BE123C"
typography:
  display:
    fontFamily: "Source Serif Pro, Georgia, Times New Roman, Noto Serif SC, serif"
    fontSize: "clamp(2.5rem, 6vw, 5rem)"
    fontWeight: 700
    lineHeight: 1.05
    letterSpacing: "normal"
  headline:
    fontFamily: "Source Serif Pro, Georgia, Times New Roman, Noto Serif SC, serif"
    fontSize: "clamp(1.75rem, 3vw, 3rem)"
    fontWeight: 650
    lineHeight: 1.18
    letterSpacing: "normal"
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Inter, PingFang SC, Helvetica Neue, Arial, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 650
    lineHeight: 1.35
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Inter, PingFang SC, Helvetica Neue, Arial, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.65
  reading:
    fontFamily: "Source Serif Pro, Georgia, Times New Roman, serif"
    fontSize: "1.125rem"
    fontWeight: 400
    lineHeight: 1.85
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Inter, PingFang SC, Helvetica Neue, Arial, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 650
    lineHeight: 1.2
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
  reading-surface:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.reading}"
  explanation-note:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "20px 24px"
  primary-action:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.surface}"
    rounded: "{rounded.pill}"
    padding: "12px 20px"
  lens-accent:
    backgroundColor: "{colors.lens-blue}"
    textColor: "{colors.surface}"
    rounded: "{rounded.sm}"
---

# Design System: Claread Cross-Client Design System

## 1. Overview

**Creative North Star: "The Reading Lens"**

Claread 的跨端设计系统不是某一种固定页面结构，而是一套可识别的阅读体验原则。小程序、Web、未来 App、分享图和 PDF 可以长得不同，但都应像同一枚阅读镜头照出的结果：清晰、克制、精密、有质感。

Claread 的第一印象应是友好的阅读器，而不是专业编辑器、学习后台或 AI chat。用户进入产品时看到的是文章、批注和解释；真正的技术能力藏在结构化解析、语法可视化、原文锚点和可导出产物里。设计要让用户觉得“这篇英文终于被读透了”，而不是“这里有很多 AI 功能”。

跨端设计遵循形态自由、气质统一。导航可以是小程序 tab、Web 顶栏、轻侧栏、浮窗或未来 App 原生结构；这些都不是品牌本身。品牌本身是 lucid / editorial / tactile 的阅读体验，是 Logo 光圈代表的聚焦和透读，是语法与句子结构被清楚呈现的能力。

**Key Characteristics:**

- Reading first, tools second.
- Grammar and sentence structure are visible, anchored, and beautiful.
- AI output is edited into pages, notes, and artifacts, not dumped as chat.
- Surfaces feel like paper, margin notes, lens glass, foil, ink, and precise instruments.
- Every client can adapt its shell, but must keep Claread's calm confidence.

## 2. Colors

The palette is ink, paper, and lens light. Neutrals create trust and long-form readability; the blue accent acts as focused light, never as a generic tech wash.

### Primary

- **Printed Ink**: The primary text and decisive action color. It should feel like black ink on paper, not harsh pure black.
- **Warm Paper**: The default reading atmosphere. It can be warmer in mobile or note-style surfaces and cleaner in Web interfaces.
- **Lens Blue**: Claread's focused light. Use for brand glints, selected structure, active focus, grammar X-Ray moments, and share artifacts. Do not flood product screens with blue.

### Secondary

- **Amber Marker**: Vocabulary, attention, and study emphasis.
- **Lavender Note**: Phrase and grammar note softness.
- **Context Blue**: Contextual explanation and source linkage.
- **Grammar Violet**: Grammatical structure and sentence analysis.
- **Structure Green**: Paragraph logic, success states, and resolved understanding.
- **Exam Red**: Rare warning or exam-specific emphasis only.

### Neutral

- **Surface White**: Raised notes, sheets, cards, inputs, export preview surfaces.
- **Muted Gray**: Metadata, inactive controls, timestamps, and supporting copy.
- **Hairline**: Borders and separators. Use quiet 1px lines, not heavy frames.

### Named Rules

**The Lens Blue Rule.** Blue is the brand light, not the background. If a whole screen turns blue, the brand loses its precision.

**The Paper Carries Reading Rule.** Long-form reading should sit on paper or a paper-adjacent surface. Dashboard gray is not the default Claread atmosphere.

**The Annotation Palette Rule.** Annotation colors must map to semantic reading layers. They are not decoration.

## 3. Typography

**Display Font:** Serif family with editorial character, such as Source Serif Pro / Georgia / Noto Serif SC fallback.  
**Body Font:** Apple system / Inter / PingFang SC stack for operational UI.  
**Label/Mono Font:** System mono only for technical IDs, token-like snippets, or export metadata.

**Character:** Claread's typography should feel like a high-quality reading object. Serif type gives article titles, English passages, examples and share artifacts an editorial voice. System sans keeps controls precise and native.

### Hierarchy

- **Display** (700, large responsive size, tight line-height): Brand moments, landing headlines, share covers, major artifacts.
- **Headline** (650 to 700, editorial serif): Article titles, Reader section heads, exported note titles.
- **Title** (600 to 700, system sans or serif depending context): Cards, explanation notes, tool panels, settings groups.
- **Reading** (400 to 500, serif, relaxed line-height): English paragraphs, examples and deep reading passages.
- **Body** (400 to 500, system sans, generous line-height): Chinese explanations, definitions, metadata paragraphs.
- **Label** (600 to 750, small size): Annotation categories, tabs, chips, source labels and export controls.

### Named Rules

**The Reader Serif Rule.** Use serif type where the user is reading language. Use sans where the user is operating the product.

**The 75ch Rule.** Long reading lines should stay within 65 to 75 characters when possible. If a layout is wider, use margins, side notes or line-length constraints.

**The No Shouting Labels Rule.** Labels should guide quietly. Avoid large uppercase category banners and noisy badges.

## 4. Elevation

Elevation should express physical layers: page, annotation, note, sheet, artifact. Claread should not feel like stacked SaaS cards. Depth is subtle until an object floats above text or becomes an exported artifact preview.

### Shadow Vocabulary

- **Paper Lift** (`0 4px 18px rgba(17, 17, 17, 0.05)`): Cards, note slips, small reader panels.
- **Floating Note** (`0 14px 44px rgba(28, 24, 18, 0.12)`): Word popup, grammar note, contextual toolbar.
- **Artifact Preview** (`0 24px 80px rgba(17, 17, 17, 0.14)`): Export preview, PDF/long-image composition, share mockup.
- **Hairline Layer** (`inset 0 0 0 1px rgba(17, 17, 17, 0.06)`): Quiet boundaries without visual weight.

### Named Rules

**The Text Wins Rule.** If elevation makes the UI object more noticeable than the article, reduce it.

**The Artifact Can Perform Rule.** Export and share surfaces may use stronger paper, foil, lens, or material effects because they are designed to be remembered and shared.

## 5. Components

Components are described by role, not by a single cross-client implementation. Each client may implement its own UI, but the component character should remain consistent.

### Reading Surface

- **Role:** The primary place where the article is read.
- **Character:** Quiet, line-length controlled, generous spacing, clear typography.
- **Constraint:** Tools must recede until needed. The first impression is reading, not configuration.

### Annotation Marks

- **Role:** Visual anchors for vocabulary, grammar, sentence and paragraph insights.
- **Character:** Stationery-like, semantic, low-noise.
- **Behavior:** Hover, tap or selection may reveal more detail; the mark itself must not block reading.

### Explanation Notes

- **Role:** Structured AI output rendered as edited notes.
- **Character:** Like margin notes, note slips or grammar cards.
- **Content:** Short title, reason, anchored excerpt, structured detail. Avoid chat transcript language.

### Grammar X-Ray

- **Role:** Signature Claread component for sentence and syntax visualization.
- **Character:** Precise, layered, memorable. It should make主干、修饰、从句、非谓语、指代和逻辑关系 visible without turning the page into a diagram wall.
- **Constraint:** It should be available as an interactive view and as an export/share template.

### Navigation / Shell

- **Role:** Let users move between input, reader, history, vocabulary, exports and settings.
- **Character:** Light, not dashboard-like. The shell is allowed to differ per client.
- **Constraint:** Do not let navigation define the brand. Reading and annotation define the brand.

### Export / Share Artifact

- **Role:** Turn one analysis into a saved or shared object.
- **Character:** More expressive than the working UI, but still editorial and precise.
- **Templates:** Grammar X-Ray, Magazine Brief, Notebook Study, Minimal Share Card.

### Primary Actions

- **Shape:** Ink-filled or lens-accent actions, compact and decisive.
- **Usage:** Submit, analyze, export, share. Avoid filling every toolbar with primary actions.
- **State:** Clear loading, success, failure and retry states.

## 6. Do's and Don'ts

### Do:

- **Do** make the article and its anchored explanations the center of every reading experience.
- **Do** use Claread's lens metaphor to guide focus, clarity and reveal behavior.
- **Do** make grammar and sentence analysis visually memorable.
- **Do** keep the product quiet even when the underlying AI capability is strong.
- **Do** allow different clients and artifacts to adopt different structures when the user experience benefits.
- **Do** make export and share outputs feel like designed objects, not screenshots.
- **Do** preserve source anchoring for every explanation that claims to interpret text.

### Don't:

- **Don't** define Claread by a fixed sidebar, fixed dashboard, fixed card grid or fixed mobile layout.
- **Don't** make Web copy the miniprogram UI, and don't make miniprogram inherit Web complexity.
- **Don't** turn the Reader into Word, WPS, a database dashboard, or a generic AI chat.
- **Don't** build learning anxiety into the brand through streaks, rankings, pressure metrics or noisy gamification.
- **Don't** let vocabulary study dominate the experience. Vocabulary is important, but syntax and篇章 are Claread's moat.
- **Don't** use decorative gradients, glassmorphism, side-stripe cards or generic AI purple as brand shortcuts.
- **Don't** ship share templates that look like ordinary study-app posters.
