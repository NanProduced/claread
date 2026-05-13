---
name: Claread WeChat Mini Program Frozen UI
description: Paper-like, quiet, Apple-flavored mobile reading UI for Claread's stable WeChat baseline.
colors:
  paper: "#FAF9F6"
  paper-warm: "#FBF9F4"
  paper-deep: "#F6F3EC"
  surface: "#FFFFFF"
  sheet-surface: "#FBFAF6"
  ink: "#111111"
  ink-soft: "#1A1A1A"
  muted: "#7A7D86"
  subtle: "#9BA0A8"
  border-light: "#F0EFEC"
  annotation-vocab: "#E4B000"
  annotation-phrase: "#B9A8E6"
  annotation-context: "#4C91C2"
  annotation-grammar: "#746694"
  annotation-analysis: "#3F6FB6"
  vocab-surface: "#FFF8EB"
  grammar-surface: "#F5F3FF"
  term-surface: "#EFF6FF"
  logic-surface: "#FFF7ED"
typography:
  display:
    fontFamily: "Source Serif Pro, Georgia, PT Serif, serif"
    fontSize: "52rpx"
    fontWeight: 700
    lineHeight: 1.25
    letterSpacing: "-0.01em"
  headline:
    fontFamily: "Source Serif Pro, Georgia, PT Serif, serif"
    fontSize: "42rpx"
    fontWeight: 700
    lineHeight: 1.35
    letterSpacing: "0.01em"
  title:
    fontFamily: "-apple-system, SF Pro Text, PingFang SC, Helvetica Neue, Arial, sans-serif"
    fontSize: "30rpx"
    fontWeight: 600
    lineHeight: 1.45
  body:
    fontFamily: "-apple-system, SF Pro Text, PingFang SC, Helvetica Neue, Arial, sans-serif"
    fontSize: "28rpx"
    fontWeight: 400
    lineHeight: 1.6
  reading:
    fontFamily: "Source Serif Pro, Georgia, PT Serif, serif"
    fontSize: "36rpx"
    fontWeight: 400
    lineHeight: 1.8
  label:
    fontFamily: "-apple-system, SF Pro Text, PingFang SC, Helvetica Neue, Arial, sans-serif"
    fontSize: "22rpx"
    fontWeight: 600
    lineHeight: 1.2
rounded:
  xs: "8rpx"
  sm: "16rpx"
  note: "18rpx"
  slip: "24rpx"
  card: "28rpx"
  sheet: "34rpx"
  pill: "999rpx"
spacing:
  xs: "8rpx"
  sm: "16rpx"
  md: "24rpx"
  lg: "32rpx"
  xl: "48rpx"
  xxl: "64rpx"
components:
  primary-action:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.surface}"
    rounded: "{rounded.pill}"
    padding: "0 32rpx"
    height: "80rpx"
  paper-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.card}"
    padding: "40rpx"
  note-slip:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.slip}"
    padding: "20rpx"
  bottom-sheet:
    backgroundColor: "{colors.sheet-surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sheet}"
    padding: "0 44rpx 40rpx"
---

# Design System: Claread WeChat Mini Program Frozen UI

## 1. Overview

**Creative North Star: "The Quiet Magazine Notebook"**

小程序冻结 UI 是一套手机上的纸面阅读系统。它把英文材料当成被编辑整理过的杂志页面，把 AI 解释、词典、生词、反馈和个人资产处理成轻量旁注，而不是工具面板。整体气质是纸质感、安静、苹果味，参考 Notion 的克制组织、Notebook 的纸面亲和力和高级杂志的留白。

系统以暖纸背景、近黑墨色、低饱和批注色和柔和 sheet 构成。卡片、浮层和底部工具栏可以存在，但必须像纸张上的便签、注脚和文具，不得变成 dashboard 模块。小程序端需要尊重移动屏幕和微信平台限制，所以信息密度比 Web 更克制，复杂能力通过底部 sheet、上下文浮层和历史入口逐步出现。

**Key Characteristics:**

- Warm paper surfaces, not sterile white app chrome.
- Serif-led reading moments, system sans for operational UI.
- Annotation colors behave like stationery marks, not category badges.
- Cards and sheets are tactile but quiet, using soft shadows and paper borders.
- The UI should feel designed, edited, and calm before it feels powerful.

## 2. Colors

The palette is warm paper plus ink, with low-noise stationery colors for annotations.

### Primary

- **Printed Ink**: Primary text and main actions. Use for navigation titles, selected filter pills, primary circular add buttons, and important numbers. It should read as ink, not pure black.
- **Warm Paper**: Default page background. It keeps long reading sessions softer than white and gives the app a magazine-like physical scene.

### Secondary

- **Editorial Blue**: Academic and analysis accents. Use with restraint for info states, context lines, and analysis annotations.
- **Soft Amber**: Vocabulary, focus, favorite and highlight moments. Use as marker color, not as a large brand wash.
- **Lavender Note**: Phrase and grammar notes. It should stay muted and paper-like.
- **Quiet Green**: Sentence and success states. Use only for positive confirmation or sentence-level meaning.

### Neutral

- **Sheet White**: Cards, sheets, input portals and modal surfaces. It may be slightly warm, especially in dictionary and reading settings.
- **Muted Gray**: Secondary text, timestamps, helper copy and inactive tab labels.
- **Hairline Border**: Structural separators. Use 1rpx lines and very low opacity.

### Named Rules

**The Paper First Rule.** The page background is warm paper by default. Use pure white only for raised surfaces, sheets, cards, input portals and selected tabs.

**The Stationery Color Rule.** Annotation colors must look like pencil, highlighter or notebook tabs. Never use high-saturation colors as large blocks.

**The Ink Rarity Rule.** Filled ink surfaces are reserved for primary actions and selected state confirmation. They should appear rarely enough to feel decisive.

## 3. Typography

**Display Font:** Source Serif Pro / Georgia / PT Serif fallback  
**Body Font:** Apple system stack with PingFang SC and SF Pro Text  
**Label/Mono Font:** SF Mono / Menlo / Monaco only for technical fragments

**Character:** Reading moments use serif typography to feel editorial and literary. Operational controls use Apple system sans to stay precise, quiet and native to mobile.

### Hierarchy

- **Display** (700, 52rpx, 1.25): Home greetings and large profile numbers. Use sparingly.
- **Headline** (700, 42rpx, 1.35): Article title and major reading headings.
- **Title** (600 to 700, 30 to 36rpx, 1.35 to 1.45): Card titles, section titles and menu labels.
- **Reading** (400 to 600, 34 to 42rpx, 1.6 to 1.8): English paragraphs, example sentences and dictionary context.
- **Body** (400 to 500, 26 to 30rpx, 1.55 to 1.7): Chinese explanations, subtitles, definitions and notes.
- **Label** (600 to 800, 18 to 24rpx, 1.2): Tags, metadata, section eyebrows and compact controls.

### Named Rules

**The Editorial Serif Rule.** Use serif type where the user is reading, reflecting or recognizing a text. Do not use serif for dense controls, toggles or operational copy.

**The Quiet Label Rule.** Labels should be small, high-weight, and low-opacity. They create structure without shouting.

## 4. Elevation

Depth is paper-like, not glass-like. Most surfaces are flat at rest, with hairline borders and warm tonal layering. Shadows appear when a surface physically floats above the reading plane: cards, bottom sheets, selection toolbars, batch bars and word slips.

### Shadow Vocabulary

- **Paper Shadow** (`0 4rpx 16rpx rgba(0, 0, 0, 0.04), 0 1rpx 2rpx rgba(0, 0, 0, 0.02)`): Default card and profile panels.
- **Reader Small** (`0 4rpx 14rpx rgba(17, 17, 17, 0.045)`): Word slip, compact note and subtle floating affordance.
- **Reader Medium** (`0 10rpx 30rpx rgba(17, 17, 17, 0.08)`): Active sheets and stronger overlays.
- **Toolbar Float** (`0 18rpx 46rpx rgba(39, 34, 27, 0.12), 0 4rpx 12rpx rgba(39, 34, 27, 0.05)`): Selection toolbar and copy menu.

### Named Rules

**The No Heavy Card Rule.** Cards should feel like stacked paper, not plastic panels. If the shadow is visible before the content, it is too strong.

**The Sheet Is Physical Rule.** Bottom sheets may have clearer elevation because they physically slide above the page and need separation from text.

## 5. Components

### Buttons

- **Shape:** Primary actions use capsule or circular shapes. Secondary actions use transparent or lightly bordered rectangles.
- **Primary:** Ink background, warm surface text, 80 to 96rpx height, soft shadow only when the button floats.
- **Active:** Scale down slightly and reduce opacity. Use short, tactile feedback.
- **Secondary:** Transparent background, low-opacity border, muted ink text. It should support the primary action, not compete with it.

### Chips

- **Style:** Filter chips and compact tags use pill shapes, small labels and minimal borders.
- **Selected:** Ink fill with white text for important filters. Soft tonal fill for domain states.
- **Unselected:** White or barely tinted surface with muted text.

### Cards / Containers

- **Corner Style:** 24 to 28rpx for cards, 34 to 36rpx for sheets.
- **Background:** White or warm white on paper background.
- **Shadow Strategy:** Paper shadow only. Avoid hard shadows and high contrast outlines.
- **Internal Padding:** 32 to 48rpx. Reading-related cards need more internal air than operational rows.

### Inputs / Fields

- **Style:** The home input portal is a white rounded paper well, not a form field.
- **Focus:** Cursor and primary action should be enough. Avoid heavy outlines.
- **Placeholder:** Serif, warm ink, and slightly editorial wording. It should invite reading, not command data entry.

### Navigation

- **Top Nav:** Minimal, center brand title, platform-native controls respected. Do not add decorative nav chrome.
- **Bottom Tab:** White translucent surface, tiny icons, low-contrast inactive state, ink active state.
- **Reader Nav:** Back/home/title controls stay lighter than the article body.

### Reader Annotation

Inline annotations are the signature component. They must preserve reading rhythm:

- Vocabulary uses warm amber marker backgrounds.
- Phrase uses pale lavender sweep.
- Context and grammar use pencil-like underlines or subtle lines.
- Active state may deepen opacity but should not invert or block the sentence.
- Explanation cards behave like note slips: white surface, soft radius, quiet close affordance.

### Word Lookup Slip

The word popup is a compact floating note. It uses serif word title, muted phonetic line, short definition, and a soft card shadow. It should appear as a reading aid, not a modal interruption.

### Bottom Sheets

Dictionary, settings and note sheets are physical paper layers. They use warm sheet surfaces, large top radius, a small drag handle, and clear but quiet section hierarchy.

## 6. Do's and Don'ts

### Do:

- **Do** keep page backgrounds warm paper and content surfaces white or warm white.
- **Do** use serif type for English reading, article titles, context examples and reflective content.
- **Do** use Apple system sans for controls, tabs, menus and compact metadata.
- **Do** keep annotation colors translucent and stationery-like.
- **Do** reveal tools in context: selection toolbar after selection, dictionary after word tap, actions near article end.
- **Do** preserve the current small-program constraints: safe areas, bottom tabs, Taro pages and WeChat platform controls.

### Don't:

- **Don't** turn Claread into a generic AI chat interface.
- **Don't** use dashboard-style card grids on the reading page.
- **Don't** use high-saturation color blocks, neon accents, decorative gradients or glassmorphism as default.
- **Don't** place dense settings before reading starts.
- **Don't** make annotation cards louder than the sentence they explain.
- **Don't** copy this mobile layout as the Web design ceiling.
- **Don't** add side-stripe borders, gradient text or decorative blur blobs.
