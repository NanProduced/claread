# Claread 产品页方向

本文定义 Claread public product page 的正式方向。它用于约束 `/` 产品页的信息架构、叙事重点、视觉演示和文案原则，不是具体实现规格，也不是新增功能承诺。

最后更新：2026-06-08

## 文档定位

本文回答四个问题：

1. Claread 产品页应该向用户传达什么。
2. 哪个画面应该成为 Claread 的第一记忆点。
3. 哪些能力可以作为产品页承诺，哪些不能提前承诺。
4. 后续页面实现和视觉探索应遵守什么边界。

本文不回答：

- 具体组件拆分、路由实现和代码工单。
- 竞品完整分析。竞品判断见 `docs/product/competitive-landscape.md`。
- Web 全局设计系统。设计系统见 `apps/web/DESIGN.md` 和 `docs/product/design-context.md`。
- Grammar X-Ray 的产品命名和实现方案。

## 已确认方向

当前产品页方向已经确认以下判断：

- 首屏主卖 **语法级理解能力**，但用完整、安静的 **阅读体验** 承接。
- Claread 面向的是“需要读懂英文材料的人”，不是泛英语学习 App。
- 可以弱化提及中文母语者视角，但不要把它做成页面视觉中心。
- 入口是文章，记忆点是句子。
- AI 对话能力 `Ask Claread` 可以出现，但不能主导页面。
- 产品页必须尽早展示 Claread 如何把一句英文讲清楚。
- 不做 Grammar X-Ray 承诺。该能力当前没有排期，后续实现后再更新产品页。
- 核心产品哲学是：Claread 不是替你跳过句子，而是帮你看清句子如何工作。

## 核心定位

Claread 是面向英文深读的阅读产品。它主动帮用户把英文文章读懂、讲清楚，核心能力是句子层面的语法级理解。

对外品牌口径：

- **Name story**: `Cla = Clarify`。Claread 主动帮你把英文读懂、讲清楚。
- **Product slogan**: **Read Deeply, Understand Clearly**。
- **叙事关系**: `Clarify` 是产品做的事，`Clearly` 是用户得到的结果。

产品页应把这个定位讲成：

> Claread clarifies English articles sentence by sentence, so you can see the grammar, structure, and meaning behind the words.

中文表达可以是：

> Claread 帮你一句一句看清英文文章的语法、结构和意思。

这比“AI 阅读助手”“AI 翻译工具”“英语学习 App”更准确。

## 页面大想法

产品页本身应该成为一次轻量 Claread Reader 体验。

用户不是先读一堆功能介绍，再被要求相信 Claread 有用；用户应该在页面早期直接看到 Claread 如何处理一句英文。最有记忆点的设计是：

1. 页面先出现一段正常的英文产品描述。
2. 这段英文描述随后被 Claread Reader 化。
3. 用户看到主干、修饰、语法关系、译文和说明如何围绕原句展开。
4. 页面由此证明 Claread 的价值：它把自己也读清楚了。

这比使用外部范文更适合首版产品页，因为它把品牌叙事、产品说明和 Reader 签名动作合在一起。

## 签名 Demo

### 位置

签名 Demo 不放在首屏主视觉中央。首屏应负责建立品牌、定位和行动入口。Demo 应放在首屏之后的第一块核心内容区，成为用户向下滚动后立即看到的产品现场。

推荐节奏：

```text
Hero
  -> Signature Reader Demo
  -> Reader workflow
  -> What Claread is not
  -> Daily / public reading
  -> Closing CTA
```

### Demo 内容

Demo 使用产品页自身的一段英文描述，例如：

> Claread helps you read English articles sentence by sentence. It keeps the article at the center, then unfolds vocabulary, grammar, translation, and notes only when they help you understand.

这段文字有几个好处：

- 它直接说明产品价值。
- 句式不难，但有清楚的结构关系。
- `sentence by sentence`、`keeps the article at the center`、`unfolds vocabulary, grammar, translation, and notes` 都能自然对应 Claread 的能力。
- 第二句可以展示主干、并列动作、时间关系和目的状语。

### Demo 展示方式

首版推荐做高拟真静态演示，加少量交互：

- 默认展示一段英文产品描述。
- 某一句处于选中或展开状态。
- 句后出现解释层，说明主干、修饰关系和自然译文。
- hover 或 click 可以切换 1-2 个标注点。
- 不做完整 Reader，不调用真实解析后端，不承诺 Grammar X-Ray。

解释层应像编辑旁注一样从句子下方展开，不像聊天气泡，也不像 inspector 面板。

### Demo 的解释口径

推荐展示顺序：

1. **Main structure**: 先抓主干。
2. **What modifies what**: 再讲修饰关系。
3. **Natural meaning**: 给自然中文理解。
4. **Why it matters**: 用一句话说明这能帮助用户看清句子。

不要用太多语法术语。可以出现 grammar、structure、modifier 这类词，但解释必须服务理解，不服务炫技。

## 信息架构

### 1. Hero

目标：建立 Claread 的定位和气质。

应包含：

- 明确的 Claread 品牌信号。
- Product slogan: `Read Deeply, Understand Clearly`。
- 一句承接语法级理解的说明。
- 主 CTA: `Clarify your first article`。
- 副 CTA: `Open Daily` 或中文对应入口。

推荐文案结构：

```text
Read Deeply,
Understand Clearly.

Claread clarifies English articles sentence by sentence,
so you can see the grammar, structure, and meaning behind the words.

[Clarify your first article] [Open Daily]
```

中文页可以保留关键英文品牌表达，再用中文讲清楚：

```text
Read Deeply,
Understand Clearly.

Claread 帮你一句一句看清英文文章的语法、结构和意思。
不是替你跳过原文，而是帮你真正读懂它。
```

### 2. Signature Reader Demo

目标：让用户一眼看到 Claread 的核心能力。

内容：

- 产品页内一段英文描述被当作 Reader 内容。
- 某个句子被展开。
- 展示主干、修饰、译文和解释。
- 让用户看到“语法级理解”不是口号，而是界面动作。

推荐标题：

```text
See the sentence, not just the translation.
```

中文说明：

```text
Claread 不只给你一句翻译。它把句子的结构、关系和意思放回原文旁边。
```

### 3. Reader Workflow

目标：说明用户如何从文章进入深读。

推荐表达：

```text
Paste an article.
Open a sentence.
Keep what matters.
```

对应能力：

- 粘贴英文文章进入透读。
- 句后解释层。
- 选词查词。
- 高亮、笔记、生词和阅读记录。

该模块可以展示流程，但不要变成 feature grid。每一步都应围绕文章和句子，不围绕按钮和工具。

### 4. What Claread Is Not

目标：建立差异化，避免被用户误归类为普通 AI chat、翻译工具或打卡学习 App。

推荐保留 4 条，克制且有节奏：

```text
Claread is not a study app.
Claread is not a chat with your articles.
Claread is not a vocab list with streaks.
Claread is not a read-it-later inbox.
```

收尾中文：

```text
Claread 是一个阅读器。它围绕文章本身工作，不抢文章的位置。
```

这一段可以放在页面中后段。它不是首屏主卖点，而是用户已经看过 Demo 后的定位校准。

### 5. Daily / Public Reading

目标：给未登录用户一个低门槛体验入口。

Daily 可以作为产品页后段的真实内容承诺：

- 每天一篇公开精读。
- 不登录也能读。
- 让用户先看到 Claread 的阅读气质。

推荐标题：

```text
Open today's reading.
```

中文说明：

```text
先读一篇公开精读，再决定是否把自己的英文文章交给 Claread。
```

### 6. Closing CTA

目标：收束到一个清楚动作。

优先 CTA：

- `Clarify your first article`
- 中文：`解读我的第一篇文章`

次级入口：

- `Open Daily`
- 中文：`打开 Daily`

不要使用：

- `Get started`
- `Sign up`
- `Try AI`
- `Boost your productivity`

## Ask Claread 的位置

`Ask Claread` 只作为上下文辅助能力出现，不进入首屏主视觉，不做页面中心。

推荐定位：

> When a sentence still feels unclear, ask Claread in context.

中文表达：

> 如果一句话仍然卡住，可以在当前文章上下文里继续追问。

注意边界：

- 不把 Ask Claread 画成通用 chatbox。
- 不与 Notion AI、Kimi、DeepSeek 等通用助手拼“全能”。
- 不承诺跨文章检索、多会话、知识库问答或独立 AI 工作台。

## 文案原则

### 应该强调

- read / reading / reader
- sentence / paragraph / article
- clarify / clearly / understand
- grammar / structure / vocabulary / translation
- highlight / note / mark
- Daily / article / archive

### 应该少用或不用

- AI-powered
- supercharge
- transform
- unlock
- productivity
- 10x
- revolutionary
- seamless
- Get started
- Sign up

### 文案语气

文案应该像编辑在解释一个认真阅读工具，不像销售在推销 AI 产品。

自检问题：

- 这句话是否仍然把文章放在中心。
- 这句话是否能被当前产品兑现。
- 这句话是否把 Claread 说成了通用 AI 工具。
- 这句话是否承诺了尚未实现的功能。
- 这句话是否听起来像学习 App、SaaS 或模型发布页。

## 视觉原则

产品页必须继承 Claread Web 的编辑台母语言。

应采用：

- Paper / Light 方向的暖纸工作面。
- Source Serif 4 + Inter 的字体组合。
- 真实或高拟真的 Reader surface。
- 1px hairline、清楚 baseline、克制间距。
- 句后解释像旁注展开。
- Lens Blue 只作为焦点和少量品牌记忆点。

避免：

- 紫色或蓝紫色 AI dashboard。
- 大面积深色 SaaS hero。
- 玻璃、glow、渐变球、抽象 3D。
- feature card 网格堆叠。
- customer logo 墙、pricing、metrics。
- 把 chatbox 放到页面中心。
- 把产品页做成泛学习 App 或应试英语页面。

现代感应来自排版精度、真实产品现场、克制动效和信息层级，而不是来自科技风装饰。

## 可承诺与不可承诺

### 可以承诺

- 粘贴英文文章进入透读。
- 句子层面的语法、结构和意思解释。
- 原文、译文、词汇、短语和语境标注。
- 选词查词。
- 高亮、笔记、生词和阅读记录。
- Daily 公开精读。
- Ask Claread 作为文章上下文内的辅助追问。

### 不应承诺

- Grammar X-Ray。
- 导出 PDF / Markdown / 长图。
- 真实分享页产物。
- 移动 Web 完整适配。
- 跨文章知识库问答。
- 多文档工作台。
- 完整 read-it-later inbox。
- 打卡、排行榜、学习小组、连续学习天数。
- 与 Notion AI 或通用 chatbox 同级的全能 AI agent。

## 实现边界建议

第一版产品页应优先验证方向，不应被复杂交互拖慢：

- 先做高拟真静态 Demo。
- Demo 文案和解析内容手写，确保准确、克制、好读。
- 只做必要 hover / click 状态。
- 不接真实解析 API。
- 不引入未来功能命名。
- 不把页面做成长篇营销站。

如果后续产品确认 Grammar X-Ray、导出、分享页或更完整语法资产，再更新本文和页面。

## 与其他文档的关系

- `docs/product/competitive-landscape.md`: 回答 Claread 和竞品的差异，以及长期护城河。
- `docs/product/design-context.md`: 定义 Claread 的 Calm / Precise / Editorial 产品气质。
- `apps/web/DESIGN.md`: 定义 Web 视觉系统、token、组件角色和设计禁区。
- `docs/product/current-state.md`: 校验当前能承诺哪些真实能力。
- 本文：把上述判断压缩成 public product page 的叙事和方向。

