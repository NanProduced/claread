# Claread 产品页方向

本文记录 Claread public landing page 当前冻结的页面骨架、叙事顺序、实现边界和后续优化原则。它描述当前 `/` 页面事实，不再作为早期方向探索稿使用。

最后更新：2026-06-16

## 页面定位

Claread 产品页面向有一定英语基础、希望真正读懂英文材料的中文用户。页面不把 Claread 讲成 AI chat、词典、翻译器或学习打卡工具，而是讲成一枚围绕原文工作的英文阅读镜头。

当前对外核心表达：

```text
Claread 透读
透读英文文章。
把词汇、语法、句子结构和译文贴回原文位置。
```

页面需要完成三件事：

1. 让用户知道英文阅读真正卡在哪里。
2. 证明 Claread 的解释贴着原文展开，不替代原文。
3. 引导用户进入 Claread，开始自己的第一篇文章。

## 当前页面骨架

当前 `/` 页面顺序以代码为准：

1. `PublicSiteHeader`
2. `ProductHero`
3. `ProductPainPoints`
4. `ProductCoreFeatures`
5. `ProductReaderDemo`
6. `ProductUtilityBento`
7. Final CTA
8. `ProductFooter`

实现入口：

- `apps/web/src/app/(public)/page.tsx`
- `apps/web/src/components/product-page/ProductHero.tsx`
- `apps/web/src/components/product-page/ProductPainPoints.tsx`
- `apps/web/src/components/product-page/TextDiagnosisPlate.tsx`
- `apps/web/src/components/product-page/ProductCoreFeatures.tsx`
- `apps/web/src/components/product-page/ProductReaderDemo.tsx`
- `apps/web/src/components/product-page/GoalReaderCropPreview.tsx`
- `apps/web/src/components/product-page/ProductUtilityBento.tsx`
- `apps/web/src/lib/product-page/reader-goal-demo.ts`

## 叙事结构

### 1. Hero

Hero 先建立 Claread 品牌和主行动入口，不在本轮冻结范围内重做。当前方向是明亮纸面、品牌可见、产品体验作为首屏信号。

Hero 的职责：

- 让 Claread 透读成为第一记忆点。
- 把行动导向 `打开 Claread`。
- 保持比后续说明区更强的产品现场感。

### 2. Pain Points: 常见阅读卡点

当前痛点区使用 `TextDiagnosisPlate`，用编辑感大字 `Text` 做 typographic diagnosis。四个字母引出四类阅读卡点：

- `T / Terms`：词义离开语境。
- `e / Edges`：结构边界断开。
- `x / X-ray`：长句主干失焦。
- `t / Translation`：译文替代阅读。

这一段的作用是诊断，不是功能列表。它说明用户卡住通常不是缺一个中文翻译，而是词义、边界、主干和译文依赖混在一起。它为下一段“四类输出标注”做铺垫。

交互原则：

- 桌面端以大字母、编辑线和分支 note 展开。
- 移动端降级为可切换诊断卡。
- 交互只用于理解四类痛点，不承诺真实 Reader 中存在完全相同的 UI。

### 3. Core Features: 四类输出标注

当前核心能力区使用 2x2 editorial grid，展示四类贴着原文出现的输出：

- `Vocabulary`：高阶词汇标注。
- `Grammar`：情境语法旁注。
- `Structure`：句级透视拆解。
- `Translation`：句间双语对照。

这一段是 Claread 的主要能力说明，不再另做普通 feature card grid。每一格都应该像一小段 Reader 工作现场，而不是营销插图。

约束：

- 词汇、语法、结构、译文必须锚定原文。
- `grammar_note` 和 `sentence_analysis` 是能力差异的重点。
- 不展示 chat bubble。
- 不暗示每篇文章一定生成所有标注。
- 不引入未定稿的 Grammar X-Ray 命名。

### 4. Goal-Based Reader Demo

当前 Demo 已改为页面级 sticky scroll sequence。滚动到该 block 后，整屏稳定，左侧文字 rail 随页面滚动切换阅读目标和 variant，右侧 preview 稳定展示对应解析样张。

数据结构在 `apps/web/src/lib/product-page/reader-goal-demo.ts`：

- `daily_reading`
  - `beginner_reading`
  - `intensive_reading`
- `exam`
  - `cet`
  - `kaoyan`
  - `ielts_toefl`
- `academic`
  - `academic_general`
  - 当前标记为 Beta

右侧 `GoalReaderCropPreview` 当前采用降级后的展示策略：

- Preview 卡片内只放一段英文原文和中文译文。
- 文中高亮随 variant 切换。
- `grammar_note` 以浮动 note 的方式贴在 preview 表面或边缘。
- 不展示 `sentence_analysis` chunk，不展示 vocabulary 词卡，不做真实 Reader 长页面。

这个 Demo 的目的不是复刻 Reader UI，而是证明同一类文章在不同阅读目标下，讲解重点会变。差异化载体优先级是：

```text
grammar_note > sentence_analysis > vocabulary > translation
```

当前冻结版本只保留 `grammar_note`，因为它在有限空间里最能展示策略差异。

### 5. Reading Tools Bento

当前二级功能区使用 Magic UI bento grid 形态，组件为 `ProductUtilityBento`，底层通用组件为 `apps/web/src/components/ui/bento-grid.tsx`。

四个格子：

- `Ask Claread`：围绕当前句子的指代、语法和含义追问，答案回到原文坐标。
- `点词查询`：词义从原句里抬起，不离开阅读位置。
- `高亮 / 笔记`：把自己的判断贴回句子边缘。
- `生词本`：从文章中留下的词片自动归档，复习带着原文来源。

视觉策略：

- 使用 Light Vercel Grid 风格：浅纸面、细分隔、少量 Claread 语义色。
- 插画不再使用高保真真实页面，而是意象化线稿或轻量 PNG 基底。
- `Ask Claread` 和 `生词本` 使用 `apps/web/public/product/utility-bento/` 下的 PNG 基底，运行时通过 `next/image` 加载，再叠加少量 SVG hover 动效。
- hover 反馈只发生在插画内部，不改变整张卡片材质。

这一段只承接二级工具，不应压过前面的核心解析能力。

### 6. Final CTA

Final CTA 当前是轻量纸面行动区：

```text
选一篇英文，开始透读。
从公开示例开始，或进入工作区解读自己的第一篇文章。
```

主按钮沿用 `打开 Claread`，根据 session 状态指向读文章入口或登录相关入口。

### 7. Footer

Footer 当前包含品牌说明、法律/项目链接和 `ProductStickerWall`。这一段更偏品牌收束和视觉记忆，不承担新的功能说明。

## 能力边界

当前产品页可以承诺：

- 粘贴或打开英文文章进入 Claread。
- 围绕原文句子展开词汇、语法、结构和译文。
- 按阅读目标调整解释重点。
- 点词查询。
- Ask Claread 作为文章上下文内追问。
- 高亮、笔记、生词本。
- Daily / 公开示例入口。

当前产品页不应承诺：

- PDF、Markdown、长图或 Notion 导出。
- 多文档知识库问答。
- 完整 read-it-later inbox。
- 学习打卡、排行榜、社区小组。
- 全能 agent 工作台。
- 每篇文章都固定输出所有标注。
- Academic 策略已经完全定稿。

## 视觉原则

当前 landing 页可以继续做 UI polish，但不应推翻已经冻结的叙事骨架。

保留：

- 明亮纸面背景，只作用于公开 landing 页面，不改全局。
- Claread 的 lucid / editorial / tactile 气质。
- 原文、译文、旁注、编辑线、纸面样张、阅读镜头这些品牌语汇。
- Lens Blue 作为少量品牌焦点，不大面积铺色。
- Core Features 的四格结构。
- Goal-Based Reader Demo 的页面级 sticky scroll sequence。
- Reading Tools 的 bento grid 位置和四个功能。

后续可优化：

- 各 section 的背景层次和过渡。
- Core Features 的细节动效和排版精度。
- Goal-Based Reader Demo 的 preview 样张、浮动 note 和滚动节奏。
- Reading Tools bento 的插画比例、材质融合和 hover 反馈。
- Footer 的文案、链接和视觉密度。
- 移动端降级体验。

避免：

- 把页面改回普通 SaaS feature card grid。
- 新增 customer logo、pricing、增长数字或虚构评价。
- 把 AI chat 放到核心叙事中心。
- 过度使用高保真 Reader 截图，让页面变成截图堆叠。
- 大面积紫色 AI SaaS 风格、玻璃拟态、渐变球或 3D 装饰。

## 实现边界

当前 landing 页中的 demo 数据是手写静态数据，不接真实 workflow。它必须贴近真实产品能力和 prompt policy，但不作为后端 schema、API 或真实解析结果的事实来源。

新增或调整 demo 时应遵守：

- mock 文案必须准确、克制、好读。
- 解析差异必须来自 reading goal / variant 的策略差异。
- 展示载体优先使用 `grammar_note`。
- 只展示当前产品合理可达的能力。
- 不用 mock 数据绕过真实 Reader 的长期架构。

## 冻结说明

本次冻结锁定的是页面流程和模块骨架，不锁定最终 UI 细节。后续工作可以继续打磨视觉，但默认不再重排模块顺序，也不再恢复旧文档里的 What Is、How It Works、Not Another AI Chat、Daily Preview、Reading Assets、FAQ 等独立章节。

如果未来确实需要恢复这些内容，应先重新评审页面信息密度和转化目标，再作为新一轮产品页改版处理。

## 相关文档

- `docs/product/overview.md`
- `docs/product/current-state.md`
- `docs/product/design-context.md`
- `docs/product/competitive-landscape.md`
- `docs/product/ask-claread.md`
- `apps/web/DESIGN.md`
- `apps/web/docs/design/README.md`
