# Claread 产品页方向

本文定义 Claread public product page 的正式方向。它约束 `/` 产品页的定位、模块顺序、文案原则、视觉原则和能力承诺边界；不定义具体组件实现。

最后更新：2026-06-08

## 核心定位

Claread 的中文名是 **透读**。产品页第一版优先做中文页面，面向有一定英语基础、希望真正读懂英文材料的中国用户。

对外核心表达：

```text
Claread 透读
Read Deeply, Understand Clearly.
透读英文文章，一句一句看清语法、结构和意思。
```

品牌口径：

- **中文名**: 透读。不止翻译，不止查词，而是把词、句、段、篇都读透。
- **Name story**: `Cla = Clarify`。Claread 主动帮你把英文读懂、讲清楚。
- **Slogan**: `Read Deeply, Understand Clearly`。英文保留，不翻译。
- **产品中心**: 文章是入口，句子是记忆点，AI 是方法，不是界面中心。

## 页面目标

产品页需要完成三件事：

1. 让冷用户理解“透读”是什么。
2. 证明 Claread 能按阅读目标把英文讲清楚。
3. 引导用户开始自己的第一篇文章，或先读一篇 Daily。

用户核心疑问：

- “透读”是什么意思？
- 它和翻译、词典、通用 AI chat 有什么不同？
- 它如何帮我读懂一句英文？
- 日常阅读、考试阅读和专业文献阅读有什么不同？
- 我不登录能不能先看看？
- 读完后能留下什么？

## 页面骨架

### 1. Hero

目的：建立品牌记忆和核心承诺。

推荐内容：

```text
Claread 透读

Read Deeply,
Understand Clearly.

透读英文文章，一句一句看清语法、结构和意思。
不是替你跳过原文，而是帮你真正读懂它。

[解读我的第一篇文章] [打开 Daily]
```

视觉方向：可吸收品牌光圈的裁切和聚焦感，但不要让装饰压过阅读主张。

### 2. What Is 透读

目的：在进入 Demo 前解释新品类，降低理解成本。

推荐表达：

```text
透读不是把英文交给 AI 总结。
透读是围绕原文，把词汇、语法、句子结构和自然理解一层一层展开。
```

可展开为三个轻量层级：

- 看清词汇：不是孤立释义，而是语境中的意思。
- 拆开句子：先抓主干，再看修饰关系。
- 理解篇章：把句子放回段落和文章里。

### 3. How It Works

目的：说明从文章到理解的基本路径。

推荐流程：

```text
放入英文文章 -> 选择阅读目标 -> 锚定原文句子 -> 生成分层标注 -> 在 Reader 中展开理解
```

页面展示可压缩为四步：

- 放入文章：从自己的英文材料或 Daily 开始。
- 选择目标：日常阅读、考试阅读或 Academic。
- 围绕原句标注：词汇、短语、语法、句子拆解、术语或逻辑关系都回到原文锚点。
- 展开并沉淀：在 Reader 中查看解释，留下高亮、笔记和生词。

每一步都围绕文章和句子，不围绕工具按钮。不要把 workflow 讲成“AI 先总结文章”，也不要把阅读目标讲成普通筛选标签。

### 4. Four Layers Of Understanding

目的：把能力从功能名翻译成理解结果。

推荐四层：

- 词与短语：语境义、搭配、考试高频词或学术术语。
- 语法与结构：从句、非谓语、修饰关系、长难句主干。
- 句子理解：自然译文、句子拆解、解释性改写。
- 篇章逻辑：段落关系、论证功能、作者意图或学术逻辑。

不同 `reading_goal` 会改变每一层的解释重点：日常阅读更顺读，考试阅读更关注考点、定位和同义替换，Academic 更关注术语、限定条件和论证关系。

不要做普通 SaaS feature card grid。可以用一张文章剖面或四层阅读标注来表达。不要暗示每篇文章都会完整输出四层内容；标注应按文本真实需要出现。

### 5. Goal-Based Reader Demo

目的：展示 Claread 的第一记忆点，并证明“同一套 Reader，可按阅读目标调整解析策略”。

Demo 使用三段手写英文文章，分别对应三种 `reading_goal`。内容可服务产品说明，但 mock 数据必须贴近真实 Reader 数据结构和渲染方式。

- 日常阅读：用自然英文介绍 Claread，展示轻量、顺读、低打扰的解释。
- 考试阅读：承接上一段，写备考用户面对长难句、指代、转折、信息定位和选项判断时的痛点，展示考试策略化解析。
- Academic：用论文或专业说明文口吻介绍 Claread 的解析工作流，展示术语、限定、因果链、逻辑关系和解释性改写。

推荐交互：

- 顶部切换 `日常阅读` / `考试阅读` / `Academic`。
- `日常阅读` 默认可用 `intermediate_reading`；`考试阅读` 默认用 `cet`，并尽量支持全部考试 variant 切换；`Academic` 只展示 `academic_general`。
- 左侧展示对应英文段落，保留文章阅读感。
- 右侧或句后展开 Reader 解析面板。
- 日常 / 考试输出可展示 `词汇短语`、`语境义`、`语法旁注`、`句子拆解`、`译文`。
- Academic 输出可展示 `术语`、`逻辑`、`解释性改写`、`研究阅读级译文`。

要求：

- 这是产品页 mock，不接真实 workflow。
- mock 数据应优先贴近 Web 的 Reader scene / ReaderMockVm 视图，而不是只准备视觉文案。
- mock 数据必须按真实 Reader 能力准备，不展示当前产品明确做不到的能力，也不承诺每种文本一定生成所有标注。
- 解释层像编辑旁注从句子下方展开。
- 不做 chat bubble。
- 不命名或承诺 Grammar X-Ray。
- 不把 Academic 讲成“更高级语法模式”；它是术语、逻辑和研究阅读理解模式。
- 首版可用高拟真静态 Demo 加少量 hover / click。
- 可以学习 Langik 产品页的表达方法：核心能力不用静态截图或普通 feature card，而是用可交互产品 mock 展示真实工作现场。但不要复制其暗色电子书气质，Claread 仍应保持暖纸、编辑台和原文透读气质。

### 6. Not Another AI Chat

目的：处理误解和竞品对比。

推荐表达：

```text
Claread is not a study app.
Claread is not a chat with your articles.
Claread is not a vocab list with streaks.
Claread is not a read-it-later inbox.
```

中文收束：

```text
Claread 是一个阅读器。它围绕文章本身工作，不抢文章的位置。
```

这一段不要写成攻击竞品，也不要变成长篇竞品分析。

### 7. Daily Preview

目的：给未登录用户低门槛体验入口。

推荐表达：

```text
先读一篇公开精读，再决定是否把自己的英文文章交给 Claread。
```

可展示 Daily 的真实阅读气质：公开文章、词汇 / 句子标注、低打扰阅读。

展示方式应优先使用小型可交互 Reader mock，而不是纯文字介绍或静态截图。

### 8. Reading Assets

目的：说明阅读不是一次性会话，会沉淀为个人资产。

当前可承诺：

- 高亮。
- 笔记。
- 生词。
- 阅读记录。
- 文章收藏。

暂不承诺导出、分享页、Notion 同步或 PDF / Markdown / 长图。

展示方式可以延续产品 mock 语言：从一段原文锚点引出高亮、笔记、生词和阅读记录，让资产看起来来自一次真实阅读，而不是四张孤立功能卡。

### 9. FAQ / Objections

目的：回答用户关键顾虑。

第一版建议问题：

- Claread 适合什么英语水平？
- 它和翻译软件有什么不同？
- 它和通用 AI 对话工具有什么不同？
- 不登录可以先体验吗？
- Ask Claread 是做什么的？
- 解释一定准确吗？如果我觉得不对怎么办？

FAQ 应短、具体、克制，不写营销口号。

### 10. Final CTA + Footer

目的：收束行动。

主 CTA：

```text
解读我的第一篇文章
```

次 CTA：

```text
打开 Daily
```

Footer 链接建议：

- Daily
- 示例
- 关于
- 帮助
- 隐私
- 反馈

## 语言原则

第一版产品页使用中文为主。英文只保留在：

- `Claread`
- `Read Deeply, Understand Clearly`
- `Ask Claread`
- `Daily`

文案应优先使用：

- 透读
- 英文文章
- 原文
- 句子
- 语法
- 结构
- 自然理解
- 高亮、笔记、生词

避免：

- AI-powered
- supercharge / transform / unlock
- productivity / 10x
- Get started / Sign up
- 泛泛的“提升英语能力”

## 视觉原则

产品页继承 Claread Web 的编辑台母语言。

应采用：

- Paper / Light 暖纸工作面。
- Source Serif 4 + Inter / 中文系统字体。
- 真实或高拟真的 Reader surface。
- 1px hairline、清楚 baseline、克制间距。
- 句后解释像旁注展开。
- Lens Blue 只用于焦点和少量品牌记忆点。
- 品牌光圈用于“聚焦、透读、展开”，不平铺装饰。

避免：

- 紫色 AI dashboard。
- 大面积深色 SaaS hero。
- 玻璃、glow、渐变球、抽象 3D。
- customer logo 墙、pricing、metrics。
- chatbox 中心。
- 应试培训 App 气质。
- 过度 feature card grid。

现代感应来自排版精度、真实产品现场、克制动效和信息层级。

## 能力边界

可以承诺：

- 粘贴英文文章进入透读。
- 句子层面的语法、结构和意思解释。
- 原文、译文、词汇、短语和语境标注。
- 选词查词。
- 高亮、笔记、生词、阅读记录、收藏。
- Daily 公开精读。
- Ask Claread 作为当前文章上下文内的辅助追问。

不应承诺：

- Grammar X-Ray。
- PDF / Markdown / 长图导出。
- 真实分享页产物。
- 移动 Web 完整适配。
- 跨文章知识库问答。
- 多文档工作台。
- 完整 read-it-later inbox。
- 打卡、排行榜、学习小组、连续学习天数。
- 与通用 AI chatbox 同级的全能 agent。

## 实现边界

第一版页面优先验证产品叙事和视觉方向：

- Demo 内容可手写，确保准确、克制、好读。
- 不接真实解析 API。
- 不引入未来功能命名。
- 不编造用户评价、客户 logo 或增长数字。
- 不把页面做成长篇营销站。

## 文档关系

- `docs/product/competitive-landscape.md`: 竞品和差异化。
- `docs/product/design-context.md`: 产品气质。
- `apps/web/DESIGN.md`: Web 视觉系统。
- `docs/product/current-state.md`: 当前真实能力边界。
