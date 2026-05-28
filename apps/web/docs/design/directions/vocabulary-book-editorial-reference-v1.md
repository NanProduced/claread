# `/app/vocabulary` Direction: Editorial Vocabulary Book v1

> **状态**: `CURRENT`
> **最后更新**: 2026-05-28
> **用途**: `/app/vocabulary` 生词本页主方向定稿参考

![Claread /app/vocabulary editorial vocabulary book v1 default](./vocabulary-book-editorial-reference-v1-default.png)

![Claread /app/vocabulary editorial vocabulary book v1 detail](./vocabulary-book-editorial-reference-v1-detail.png)

## 1. Scope

本文件定义 Claread Web `/app/vocabulary` 的当前主方向参考图，以及把该方向拆成可实现的信息架构、视觉规则和评审基线。

它是 **方向稿**，不是像素级交付稿：

- 可以按结构、层级、气质和权重实现。
- 不要求逐像素复刻图片。
- 真实实现必须接入现有 Next.js 页面、真实生词数据、真实来源定位和真实复习链路。

## 2. Page Role

`/app/vocabulary` 不是词典页，也不是背单词工作台。

它承担 4 个职责：

1. Claread Web 的词汇资产入口。
2. 用户回看阅读中保存过的重点词与语境的目录页。
3. 连接来源文章、语境定位、掌握状态和复习链路的轻量管理页。
4. Reader 词典能力向长期沉淀资产的落点页。

因此它应被实现为：

**一张经过编辑整理的词汇目录页，右侧在默认态显示实用书签，在选中态切换成词条旁注。**

## 3. Direction Summary

本方向的关键词：

- Editorial vocabulary book
- Reading residue first
- Quiet product
- Warm paper
- Context before dictionary
- Review as capability, not personality

需要保留的核心感受：

- `/app/vocabulary` 与 `/app/library`、`/app/read` 并排看时，明显属于同一设计系统。
- 主列表首先像阅读里沉淀出来的词汇目录，不像对象管理表，也不像 SRS 应用。
- `复习` 必须保留为真实能力入口，但不能定义整页人格。
- 右侧默认是书签式摘要，不应默认常驻某个词的长详情。
- 点击词条后，右侧整块切到详情态，不再与书签并存。

## 4. Information Architecture

### 4.1 顶层分区

页面分为 5 个层级：

1. **App Shell**
2. **Vocabulary Header**
3. **Vocabulary List**
4. **Vocabulary Bookmark Rail**
5. **Vocabulary Inspect Rail**

其中 2 和 3 是主体验；4 与 5 互斥出现，属于同一右侧位的两种状态。

### 4.2 App Shell

左侧应用壳必须沿用 `/app/read` 与 `/app/library` 已确定的骨架和语气：

- Claread logo + 中英品牌字
- `新解读 / 阅读记录 / 生词本 / 设置`
- 底部搜索入口
- 一小段品牌理念或阅读说明
- 返回公共首页
- 当前用户信息

不应单独为生词本做一套更“学习工具化”的壳。

### 4.3 Vocabulary Header

中部主区顶部按从上到下组织：

1. **Eyebrow / Micro Label**
   - 使用低权重英文小标签，例如 `VOCABULARY`
   - 只负责给出 editorial cue

2. **Hero Headline**
   - 强 serif 标题 `Vocabulary Book.`
   - 允许一条很短的中文副说明

3. **Primary Review Action**
   - 右上角保留一个明确但克制的 `开始复习 N 个`
   - 它是真实功能入口，但只是一项动作，不是页面主题

4. **Search Row**
   - 一条长规则线式搜索区
   - 左侧输入，右侧显示 `共 N 个生词 · M 个待复习`
   - 不做厚输入框，不做工具面板堆叠

5. **State Filters**
   - `全部 / 学习中 / 已掌握`
   - 作为轻量目录筛选存在

### 4.4 Vocabulary List

主列表按纵向目录组织，不做卡片网格，也不做永久选中面板。

每条词条自上而下包含：

1. **Word Line**
   - 词头
   - 音标
   - 词性

2. **Short Meaning**
   - 一行简短中文释义

3. **Primary Context**
   - 一行英文语境句
   - 一行更轻的中文译文

4. **Metadata Line**
   - 加入日期
   - 复习状态
   - 语境数 / 文章数
   - 查看来源语境

5. **Selection Cue**
   - 默认不强调
   - 选中时只允许很轻的暖色底，不允许侧条、厚边框或重卡片感

### 4.5 Vocabulary Bookmark Rail

默认态右侧不是详情面板，而是一张词汇书签。

它分成三个功能层：

1. **Front Bookmark: Vocabulary Summary**
   - 当前词汇册的简短摘要
   - 生词总数、待复习数、已掌握数
   - 一条很短的编辑式提示

2. **Secondary Bookmark Block: Browse By Status**
   - `全部 / 学习中 / 已掌握`
   - 可以附轻量数量

3. **Reference Blocks**
   - 最近加入
   - 多语境词条

它应像 `Library` 的书签近亲，而不是另一个 sidebar 模块。

### 4.6 Vocabulary Inspect Rail

选中词条后，右侧整块从书签态切换为 inspect 态。

它承担：

1. 当前词条的阅读旁注。
2. 语境与来源定位。
3. 复习与掌握状态操作。
4. 词典扩展释义。

它不是：

- 永久 split pane
- 词典全文页
- 学习任务面板

## 5. State Model

这是本页区别于当前错误实现的关键。

### 5.1 Summary State

默认进入 `/app/vocabulary` 时：

- 左侧显示词条目录
- 右侧显示词汇书签
- 不预选某个词条长期展开详情

这保证页面首先被理解为“整本词汇资产”。

### 5.2 Inspect State

点击某条词条后：

- 左侧保留目录
- 右侧书签整块切换成词条详情
- 书签与详情不并存

这保证详情是按需展开的旁注层，而不是页面默认主角。

### 5.3 Mobile State

移动端继续使用 bottom sheet。

桌面端的“书签态 / 详情态”切换逻辑，不要求原样搬到移动端；移动端重点是：

- 列表先行
- 点击词条后 bottom sheet 展开详情
- 关闭后回到原列表位置

## 6. Visual Hierarchy

### 6.1 中部主区

视觉优先级应固定为：

1. Hero headline
2. Vocabulary row words
3. Search row
4. Context lines
5. Metadata
6. Row actions / chevron cue

### 6.2 右侧书签态

视觉优先级应固定为：

1. 书签标题
2. 本册摘要
3. 状态浏览
4. 最近加入
5. 多语境词条

### 6.3 右侧详情态

视觉优先级应固定为：

1. 词头
2. 阅读语境
3. 复习与状态
4. 词典释义
5. 搭配 / 例句

这意味着详情态必须明确执行：

**语境优先于词典。**

## 7. Vocabulary Row Rules

这是本页最关键的实现点。

### 必须做到

- 列表一眼看上去像词汇目录，不像对象管理表。
- 每条词条首先回答“这是什么词、我在哪个语境见过、它和哪些文章有关”。
- `今日复习` 这类状态必须存在，但不能比词头和语境更抢眼。
- 选中态必须足够轻，避免卡片化和 inspector 感。

### 建议实现方式

- 用细分隔线而不是卡片边框切行。
- 词头保持 serif 气质，元信息和状态保持低声量 sans。
- `词性` 只做非常轻的小标，不做强 badge。
- `查看来源语境` 保持次动作语义。

### 不应出现

- SaaS 式对象卡片
- 蓝色侧条选中态
- 厚重 badge 带
- 让 `今日复习` 或 `已掌握` 比词头更显眼

## 8. Bookmark Rail Rules

右侧书签是本方向的默认结构签名。

### 必须做到

- 看起来像夹在页边的实用词汇书签，而不是统计侧栏。
- 与 `Library` 的书签属于同一语系。
- 对浏览有帮助，而不是只展示数字。
- 有纸面感，但不做旧、不拟物过度。

### 推荐内容结构

书签上半部分：

- `我的词汇书签`
- `本册收录了 128 个生词，其中 6 个待复习，24 个已掌握。`

书签中段：

- `按状态浏览`
- `全部`
- `学习中`
- `已掌握`

书签下半部分：

- `最近加入`
- 2 个最近加入词条

书签尾部：

- `多语境词条`
- 2 个有多个来源的词条
- 允许极轻的 aperture 几何切角作为品牌收尾

### 不应出现

- `Overview / Metrics / Task Summary` 这类后台式文案
- 纵向 KPI 堆叠
- 书签与详情同时展示
- 为填满书签而堆过多说明

## 9. Inspect Rail Rules

右侧详情态是本页的第二签名结构。

### 必须做到

- 看起来像 Claread Reader 的旁注层近亲，而不是字典 split inspector。
- `阅读语境` 是第一重点。
- `复习` 作为能力出现，但不做强学习压力设计。
- `词典释义` 被后置成支持层，而不是页面主舞台。

### 推荐内容结构

详情上半部分：

- `decades`
- 音标
- 词性
- `原型: decade`
- 一行简短中文释义

详情中段第一块：

- `阅读语境`
- 语境引用卡
- `在原文定位`

详情中段第二块：

- `复习与状态`
- `2026/5/26 加入`
- `今日复习`
- `复习 0 次`
- `下次复习: 今天`
- `标记已掌握`
- `删除此词`

详情下半部分：

- `词典释义`
- 2-3 条精简释义
- 必要时再接 `搭配` 或 `例句`

### 不应出现

- 一打开详情就先看到长篇释义列表
- 多个厚卡片上下堆叠
- 把详情做成覆盖全屏的 modal 感
- 强仪表盘式复习状态模块

## 10. Review Language Rules

`复习` 在本页必须保留，但要服从 Claread 的产品边界。

实现要求：

- 保留页头 `开始复习 N 个`
- 保留列表中的 `今日复习 / N天后 / 已掌握`
- 保留详情中的 `下次复习`、`复习次数`、`标记已掌握`
- 所有复习表达都应安静、明确、无焦虑感

不应出现：

- `今日任务`
- `学习进度`
- `连续复习`
- `掌握率`
- 任何运营化学习压力文案

## 11. Visual Language Match With `/app/read` and `/app/library`

`/app/vocabulary` 必须与 `/app/read` 和 `/app/library` 共享这些语言：

- 同样的暖纸背景和低对比分层
- 同样的 serif headline 气质
- 同样克制的蓝色使用方式
- 同样安静的应用壳存在感
- 同样以细规则线而非重边框建立结构

允许不同的地方：

- `/app/read` 更像 intake stage
- `/app/library` 更像 archive directory
- `/app/vocabulary` 更像 vocabulary directory
- `/app/read` 右栏是编辑选读
- `/app/library` 右栏是归档书签
- `/app/vocabulary` 右栏在默认态是词汇书签，在选中态是词条旁注

## 12. Implementation Boundaries

Gemini 实现时应遵守以下边界：

1. 不按像素照抄图稿。
2. 不把主列表重新做成卡片化学习页。
3. 不把右侧默认态退化成普通统计 sidebar。
4. 不把桌面端重新做回永久 split inspector。
5. 不把详情态和书签态同时展示。
6. 不为了“词典完整”让释义压过语境和来源。
7. 不把 `复习` 改名为 `回看` 或移出页面。
8. 不引入打卡、进度、连续天数等学习压力元素。
9. 不引入重拟物纸张纹理、贴纸感或手账装饰。
10. 不把选中态做成蓝色侧条或重边框卡片。

## 13. Review Baseline

后续评审以这 10 条为准：

1. 切换到 `/app/vocabulary` 时，风格是否仍然属于 Claread，而不是进入另一套学习产品。
2. 默认态是否首先让用户理解“这是整本词汇资产”，而不是“这是一个词典详情页”。
3. 主列表是否首先帮助用户识别词条与阅读语境，而不是先管理状态。
4. 页头里的 `复习` 是否存在但不过度主导页面。
5. 右侧书签是否已经摆脱 KPI/status bar 感。
6. 选中词条后，右侧是否整块自然切换到详情态，而不是和书签并存。
7. 详情态是否真正执行了“语境优先于释义”。
8. 整体是否比当前实现更像杂志目录页，而不是词汇训练工具。
9. 与 `/app/library` 并排看时，是否明显属于同一设计系统。
10. 与现有 Reader 词典能力对照时，是否仍保留 Claread 的阅读语境导向。

## 14. Recommended Handoff Note

交给实现方时，建议附上这句说明：

> 这是 Claread `/app/vocabulary` 的主方向参考。实现目标不是复刻一张设计图，而是把它落成一个安静、可浏览、像编辑词汇目录一样的生词本界面。请优先保留首页同源的应用骨架、主列表的词汇目录感、右侧默认书签与右侧选中详情的两态切换，以及“语境优先、复习保留但降权”的结构纪律，不要把它退化成普通词典 split pane 或学习后台。
