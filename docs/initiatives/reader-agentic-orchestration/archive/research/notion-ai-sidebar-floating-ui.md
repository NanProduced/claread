# Notion AI 侧边栏与浮窗 UI 调研报告

> 调研日期：2026-07-10
> 调研范围：Notion AI 的 Notion Agent 聊天面板在「侧边栏（Sidebar）」与「浮窗（Floating）」两种形态下的真实交互与布局行为。
> 来源约束：只使用一手来源（Notion 官方帮助中心、官方更新日志、官方产品页/指南、官方 YouTube 演示）。每个结论内联标注来源链接，报告末尾汇总完整来源列表。
>
> 重要说明：Notion 官方帮助中心与更新日志以「用户视角的行为描述」为主，对像素级布局参数（具体宽度、ARIA 角色、动画曲线、断点）几乎没有公开文档。本报告严格区分「一手来源已确认」与「未能从一手来源确认的开放问题」，后者统一列入第 9 节，不臆测。

---

## 1. 概述

Notion AI 的对话能力由 **Notion Agent** 承载。Notion Agent 是内置于 Notion 工作区的 AI 队友，可以读取工作区与已连接应用（Slack、Google Drive、GitHub 等）的上下文，并代用户创建/编辑页面与数据库（来源：[Notion Agent 帮助文档](https://www.notion.com/help/notion-agent)）。

Notion Agent 的聊天面板有两种显示形态，官方命名为 **Sidebar（侧边栏）** 与 **Floating（浮窗）**，用户可在两种形态间切换（来源：[Notion Agent 帮助文档 — Switch chat mode](https://www.notion.com/help/notion-agent#switch-chat-mode)）。

这一聊天面板随 **Notion 3.0** 正式上线。2025 年 9 月 18 日的发布日志标题为「Notion 3.0: Agents」，其中说明「Notion 3.0 is here! We've rebuilt Notion AI from the ground up as Agents.」（来源：[Notion 3.0 发布日志](https://www.notion.com/releases/2025-09-18)）。因此本报告所述的侧边栏/浮窗双形态属于 3.0 起 Notion AI 的标准交互。

Notion AI 帮助分类页对其能力的概括是「Search, chat, and write with Notion AI from anywhere in your workspace.」（来源：[Notion AI 帮助分类](https://www.notion.com/help/category/notion-ai)）。

---

## 2. 侧边栏模式（Sidebar）

### 2.1 定义与定位

官方对侧边栏模式的描述为：「Select `Sidebar` if you want your chat to display on the right side of your screen.」（来源：[Notion Agent 帮助文档 — Switch chat mode](https://www.notion.com/help/notion-agent#switch-chat-mode)）。

即：侧边栏模式下，聊天面板显示在屏幕右侧。

### 2.2 主内容区是否重新布局

**未能从一手来源直接确认。** 官方帮助文档只说明侧边栏「显示在屏幕右侧」，并未明确说明文档编辑区是收缩宽度让出空间，还是被侧边栏覆盖。

与之对照，浮窗模式被明确描述为「a separate window on top of your screen」（屏幕之上的独立窗口，即覆盖在内容之上），由此可推断侧边栏与浮窗在层级行为上是有意区分的——但侧边栏是否挤压主内容区的具体布局行为，官方文档未给出明确文字，列入开放问题（见第 9 节）。

### 2.3 宽度（固定 / 可拖拽 / 默认值）

**未能从一手来源确认。** 官方帮助中心未公开侧边栏的具体像素宽度，也未说明是否可拖拽调整。列入开放问题。

### 2.4 定位与滚动跟随

官方仅说明侧边栏位于「右侧」，未说明是否随页面滚动固定（sticky/fixed）。Notion 的页面级右侧侧边栏（如评论、页面信息）在产品中通常是固定占据右侧列、不随正文滚动的，但 AI 聊天侧边栏是否完全同此行为，官方文档未明确，列入开放问题。

### 2.5 打开/关闭的动画与过渡

**未能从一手来源确认。** 官方帮助文档与更新日志均未描述侧边栏的动画曲线或过渡时长。列入开放问题。

---

## 3. 浮窗模式（Floating）

### 3.1 定义与定位

官方对浮窗模式的描述为：「Select `Floating` if you want the chat to display as a separate window on top of your screen.」（来源：[Notion Agent 帮助文档 — Switch chat mode](https://www.notion.com/help/notion-agent#switch-chat-mode)）。

即：浮窗模式是一个「独立窗口」，显示在「屏幕之上（on top of）」——这表明浮窗是覆盖在内容区之上的，不会挤压主内容区布局。

### 3.2 默认位置

浮窗的触发入口是工作区右下角的圆形头像图标。官方指南原文：「click the circular face icon in the bottom-right corner of your workspace」（来源：[Get started with your Notion Agent 指南](https://www.notion.com/help/guides/get-started-with-your-personal-agent-in-notion)）；帮助文档亦称「Find the friendly face at the bottom of Notion to chat with your Agent.」（来源：[Notion Agent 帮助文档 — How to work with your Agent](https://www.notion.com/help/notion-agent#how-to-work-with-your-agent)）。

因此浮窗的触发锚点位于右下角。但浮窗打开后的**默认弹出位置**是否就是右下角，官方文档未逐字说明，列入开放问题。

### 3.3 尺寸

**未能从一手来源确认。** 官方未公开浮窗的具体尺寸（宽×高）。列入开放问题。

### 3.4 是否遮挡内容区 / 内容区是否重新布局

根据「on top of your screen」的描述，浮窗是覆盖在内容之上的独立窗口，不会触发内容区重新布局（来源：[Notion Agent 帮助文档 — Switch chat mode](https://www.notion.com/help/notion-agent#switch-chat-mode)）。这与侧边栏「右侧显示」的描述形成对照，是本报告中少数能从官方文字推断出层级关系的结论之一。

### 3.5 是否可拖动 / 拖动后吸附行为

**未能从一手来源确认。** 官方帮助文档未提及浮窗是否可拖动、是否有边缘吸附行为。列入开放问题。

---

## 4. 切换交互

### 4.1 如何在两种形态间切换

切换入口位于聊天窗口顶部。官方原文：「Select `Switch chat mode` at the top of the chat window. Select `Sidebar` … Select `Floating` …」（来源：[Notion Agent 帮助文档 — Switch chat mode](https://www.notion.com/help/notion-agent#switch-chat-mode)）。

即：聊天窗口顶部有一个「Switch chat mode」控件，点开后可选择 Sidebar 或 Floating。

### 4.2 切换时的状态保持（对话是否保留）

官方未在「Switch chat mode」一节中明确说明切换形态时当前对话是否保留。但 Notion Agent 提供了独立的对话持久化机制：

- **置顶（Pin）**：可将对话固定在 Chat 标签顶部，便于快速回到常用对话。置顶方式为「打开对话 → 点击聊天窗口角落的 pin 图标」，或在聊天侧边栏中悬停某条对话 → 三点菜单 `•••` → `Pin`（来源：[Notion Agent 帮助文档 — Pin or unpin a chat](https://www.notion.com/help/notion-agent#pin-or-unpin-a-chat)）。
- **聊天历史**：「Hover over `Notion AI` in your sidebar. Select `🕘`… Chats will be named based on what the conversation was about.」（来源：[Notion Agent 帮助文档 — Chat history](https://www.notion.com/help/notion-agent#chat-history)）。

这些机制说明对话本身是持久化的，但「切换形态的瞬间当前对话是否无缝保留在同一窗口」这一具体行为，官方未逐字说明，列入开放问题。

### 4.3 切换的可发现性

官方文档将切换控件命名为「Switch chat mode」（文字标签，非纯图标），位于聊天窗口顶部（来源：[Notion Agent 帮助文档 — Switch chat mode](https://www.notion.com/help/notion-agent#switch-chat-mode)）。即切换入口使用的是文字标签而非仅图标，可发现性较好。至于该控件的确切图标样式，官方文档未描述，列入开放问题。

---

## 5. 大纲（Outline / Table of Contents）与 AI 面板的关系

**未能从一手来源确认。** 在本调研覆盖的一手来源（Notion AI 帮助分类全部文档列表、Notion Agent 帮助文档、What is Notion AI FAQ、Research Mode 帮助文档、Get started 指南、3.0 发布日志）中，均未提及 AI 聊天面板与页面大纲（Outline / Table of Contents）之间的空间关系，包括：

- AI 侧边栏打开时，大纲是否仍显示、显示在何处；
- AI 浮窗打开时，浮窗是否堆叠在大纲之上；
- 大纲的 hover 展开/收起行为是否受 AI 面板影响。

Notion 的「Table of contents」属于页面内 block（来源：[Notion AI 帮助分类全文档列表](https://www.notion.com/help/category/notion-ai/all) 中未涉及，TOC 帮助页 `notion.com/help/table-of-contents` 为 JS 渲染页面，未能稳定抓取正文）。官方未在任何 AI 相关文档中描述二者关系，整体列入开放问题。

---

## 6. 响应式行为

### 6.1 桌面端不同屏幕宽度下的差异

**未能从一手来源确认。** 官方帮助文档与更新日志未公开侧边栏/浮窗在不同屏幕宽度下的行为差异或断点。列入开放问题。

### 6.2 移动端行为

从官方文档可确认的移动端事实：

- **置顶对话在移动端不可用**：「Pinning a chat isn't available on mobile.」（来源：[Notion Agent 帮助文档 — Pin or unpin a chat](https://www.notion.com/help/notion-agent#pin-or-unpin-a-chat)）。
- **日历调度在移动端不可用**：「Schedule or cancel calendar events on mobile (this is available on web and desktop only).」（来源：[Notion Agent 帮助文档 — What your Notion Agent can't do](https://www.notion.com/help/notion-agent#what-your-notion-agent-can%E2%80%99t-do)）。
- **iOS 快捷入口**：可通过 Siri 打开 Notion AI、通过 Spotlight 搜索 Notion、在「快捷指令（Shortcuts）」App 中找到 Notion 并点 `AI`、以及 iPhone 15 Pro 的操作按钮自定义打开 Notion AI（来源：[What is Notion AI? FAQ — Notion AI shortcuts on iOS](https://www.notion.com/help/notion-ai-faqs#notion-ai-shortcuts-on-ios)）。

但移动端是否会提供「侧边栏 vs 浮窗」的切换、移动端面板的具体尺寸与布局，官方文档未说明，列入开放问题。

### 6.3 全局快捷键

可作为「响应式/跨场景」补充：Notion AI 提供全局快捷键 `shift` + `cmd/ctrl` + `J`，「to engage Notion AI whenever you need it, even when you're not working in Notion」，并可在 `Settings → Preferences` 中自定义（来源：[What is Notion AI? FAQ — Notion AI keyboard shortcut](https://www.notion.com/help/notion-ai-faqs#notion-ai-keyboard-shortcut)）。这表明 Notion AI 面板可脱离当前 Notion 窗口被唤起（桌面端跨应用）。

---

## 7. 可访问性

**未能从一手来源确认。** 在本调研范围内：

- 未找到 Notion 官方关于 AI 面板 ARIA 角色（complementary / dialog 等）的公开说明；
- 未找到关于 AI 面板焦点管理（focus trap、打开/关闭后焦点回归）的官方描述；
- 未找到关于屏幕阅读器通告（aria-live、状态变化播报）的官方描述；
- `notion.com/help/accessibility` 页面存在但为 JS 渲染，未能抓取到正文（来源：[Notion Accessibility 帮助页（页面可达，正文未抓取）](https://www.notion.com/help/accessibility)）。

整体列入开放问题。建议如需确认，应直接登录 Notion Web 端用 DevTools 审查 AI 面板的 DOM 与 ARIA 属性，或联系 Notion 索取官方无障碍符合性报告（VPAT/ACR）——但这些都超出了「公开一手文档」的范围。

---

## 8. 来源链接汇总

以下是本报告引用的全部一手来源：

1. **Notion Agent 帮助文档**（含 Switch chat mode / Pin or unpin a chat / Chat history / How to work with your Agent / Personalize your Agent / What your Notion Agent can't do 等章节）
   https://www.notion.com/help/notion-agent
   - 锚点：
     - Switch chat mode: https://www.notion.com/help/notion-agent#switch-chat-mode
     - How to work with your Agent: https://www.notion.com/help/notion-agent#how-to-work-with-your-agent
     - Pin or unpin a chat: https://www.notion.com/help/notion-agent#pin-or-unpin-a-chat
     - Chat history: https://www.notion.com/help/notion-agent#chat-history
     - Personalize your Agent: https://www.notion.com/help/notion-agent#personalize-your-agent
     - What your Notion Agent can't do: https://www.notion.com/help/notion-agent#what-your-notion-agent-can%E2%80%99t-do

2. **What is Notion AI? FAQ**（含 Notion AI keyboard shortcut / Notion AI shortcuts on iOS / Notion AI settings）
   https://www.notion.com/help/notion-ai-faqs
   - 锚点：
     - Notion AI keyboard shortcut: https://www.notion.com/help/notion-ai-faqs#notion-ai-keyboard-shortcut
     - Notion AI shortcuts on iOS: https://www.notion.com/help/notion-ai-faqs#notion-ai-shortcuts-on-ios

3. **Get started with your Notion Agent 指南**（含 How your Agent works in Notion / Personalize your Agent）
   https://www.notion.com/help/guides/get-started-with-your-personal-agent-in-notion

4. **Notion 3.0: Agents 发布日志**（2025-09-18）
   https://www.notion.com/releases/2025-09-18

5. **Notion AI 帮助分类页**（"Search, chat, and write with Notion AI from anywhere in your workspace."）
   https://www.notion.com/help/category/notion-ai

6. **Notion AI 帮助分类全文档列表**
   https://www.notion.com/help/category/notion-ai/all

7. **Research Mode 帮助文档**（说明 Research Mode 经 Home 标签 → 搜索窗口底部 `Research` 进入，是区别于聊天面板的另一 AI 面）
   https://www.notion.com/help/research-mode

8. **Notion 产品页 / Meet the new Notion AI**
   https://www.notion.so/product/ai

9. **Notion 官方 YouTube 演示：Getting started with Notion Agent**（指南页内嵌的官方视频）
   https://www.youtube.com/watch?v=yasGTeAsV6s

10. **Notion Accessibility 帮助页**（页面可达，正文为 JS 渲染未能抓取）
    https://www.notion.com/help/accessibility

> 备注：`github.com/makenotion` 为 Notion 官方 GitHub 组织，其下 `notion-sdk-js` 等仓库为 Notion API 客户端 SDK，与 AI 面板 UI 无关，未用于本报告结论。

---

## 9. 未能从一手来源确认的开放问题

以下问题在 Notion 官方帮助中心、更新日志、产品页、指南与官方 YouTube 演示（标题层）中均未找到明确文字，需通过实际登录产品审查 DOM/样式，或索取 Notion 官方内部文档进一步确认：

### 侧边栏模式
- [ ] 侧边栏打开时，主内容区（文档编辑区）是收缩宽度让出空间，还是被覆盖？官方仅说「右侧显示」，未明确布局回流行为。
- [ ] 侧边栏宽度的具体像素值，以及是否可拖拽调整。
- [ ] 侧边栏是否随页面滚动固定（sticky/fixed），还是随滚动移动。
- [ ] 侧边栏打开/关闭的动画曲线与过渡时长。

### 浮窗模式
- [ ] 浮窗打开后的默认弹出位置（是否就是右下角触发锚点附近）。
- [ ] 浮窗的具体尺寸（宽×高）。
- [ ] 浮窗是否可拖动；若可拖动，是否有边缘吸附（snap）行为。
- [ ] 浮窗是否可缩放。

### 切换交互
- [ ] 切换形态的瞬间，当前对话是否在同一窗口无缝保留（官方有 Pin / Chat history 等持久化机制，但未逐字说明切换瞬间的状态衔接）。
- [ ] 「Switch chat mode」控件的确切图标样式与视觉呈现。
- [ ] 两种形态的默认值（首次打开 Agent 时是 Sidebar 还是 Floating）。

### 大纲与 AI 面板的关系
- [ ] AI 侧边栏打开时，页面大纲（Table of contents block / 右侧 outline）是否仍显示、显示在何处。
- [ ] AI 浮窗打开时，浮窗是否堆叠在大纲之上、是否会造成遮挡。
- [ ] 大纲的 hover 展开/收起行为是否受 AI 面板存在影响。

### 响应式行为
- [ ] 桌面端不同屏幕宽度下，侧边栏/浮窗的行为差异与具体断点。
- [ ] 移动端是否提供「侧边栏 vs 浮窗」切换；移动端面板的具体尺寸、定位与布局。
- [ ] 平板端的行为。

### 可访问性
- [ ] AI 面板的 ARIA 角色（complementary / dialog / 其它）。
- [ ] 焦点管理：打开面板时焦点是否进入面板、关闭后焦点是否回归触发元素、是否使用 focus trap。
- [ ] 屏幕阅读器通告：流式输出、状态变化是否有 aria-live 播报。
- [ ] 键盘可达性：是否可完全用键盘完成打开/切换形态/关闭/发送。
- [ ] Notion 是否提供官方无障碍符合性报告（VPAT/ACR）。

### 其它
- [ ] 「Switch chat mode」形态选择是按工作区记忆还是按页面/会话记忆（切换后下次打开是否保持上次选择）。
- [ ] 多窗口/多标签页下 AI 面板的行为是否各自独立。

---

## 附：调研方法与可信度说明

- **一手来源覆盖**：Notion 官方帮助中心（notion.com/help，含 Notion AI 全部分类文档与指南）、官方更新日志（notion.com/releases）、官方产品页（notion.so/product/ai）、官方 YouTube 演示（标题层）。
- **已知限制**：Notion 帮助中心与发布日志面向终端用户，描述「能做什么」而非「UI 怎么实现」，因此像素级布局参数、ARIA 属性、动画细节天然不在公开文档范围内。`notion.com/help/accessibility` 与 `notion.com/help/table-of-contents` 等页为客户端 JS 渲染，WebFetch 仅能拿到导航骨架，正文未能稳定抓取。
- **未采用的来源**：搜索过程中出现的大量中文二手博客、CSDN、知乎、少数派、应用商店介绍等，均未作为结论依据，仅用于辅助定位官方链接。
- **建议下一步**：若团队需要补全第 9 节的开放问题，最可靠的方式是登录 Notion Web 端，用浏览器 DevTools 审查 AI 面板的 DOM 结构、CSS 布局与 ARIA 属性，并将结果回填本报告；这属于「对官方产品的一手观察」，但超出「公开文档调研」范围，需另行授权与记录。
