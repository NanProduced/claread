# `/app/library` Direction: Editorial Archive v1

> **状态**: `CURRENT`
> **最后更新**: 2026-05-28
> **用途**: `/app/library` 阅读记录页主方向定稿参考

![Claread /app/library editorial archive v1](./library-archive-editorial-reference-v1.png)

## 1. Scope

本文件定义 Claread Web `/app/library` 的当前主方向参考图，以及把该方向拆成可实现的信息架构、视觉规则和评审基线。

它是 **方向稿**，不是像素级交付稿：

- 可以按结构、层级、气质和权重实现。
- 不要求逐像素复刻图片。
- 真实实现必须接入现有 Next.js 页面、真实阅读记录数据、真实收藏/删除/继续阅读链路。

## 2. Page Role

`/app/library` 不是资产后台，也不是运营式内容列表。

它承担 3 个职责：

1. Claread Web 的阅读记录入口。
2. 用户回找、回读、续读既有文章的 archive page。
3. 连接收藏、笔记、生词和阅读目标的轻量浏览页。

因此它应被实现为：

**一张经过编辑整理的阅读目录页，右侧附有实用而克制的归档书签。**

## 3. Direction Summary

本方向的关键词：

- Editorial archive
- Quiet product
- Warm paper
- Re-reading first
- Practical bookmark
- Goal-led browsing

需要保留的核心感受：

- 左侧和中部延续 `/app/read` 的应用骨架和气质，切换 tab 不突兀。
- 主列表像阅读目录，不像管理后台。
- 每条记录首先回答“这是什么、我何时碰过、里面留下了什么痕迹”。
- 右侧书签是实用索引，不是 KPI 面板，也不是装饰挂件。
- `reading_goal` 快速筛选必须自然地长在页边书签里，而不是再做一排工具栏。

## 4. Information Architecture

### 4.1 顶层分区

页面分为 4 个层级：

1. **App Shell**
2. **Archive Header**
3. **Reading Record List**
4. **Archive Bookmark Rail**

其中 2 和 3 是主体验，4 是页边索引，不得反客为主。

### 4.2 App Shell

左侧应用壳必须沿用 `/app/read` 已确定的骨架和语气：

- Claread logo + 中英品牌字
- `新解读 / 阅读记录 / 生词本 / 设置`
- 底部搜索入口
- 一小段品牌理念或阅读说明
- 返回公共首页
- 当前用户信息

不应重新设计成另一套更“功能化”的侧边栏。

### 4.3 Archive Header

中部主区顶部按从上到下组织：

1. **Eyebrow / Micro Label**
   - 使用低权重英文小标签，例如 `LIBRARY`
   - 只负责给出 editorial cue

2. **Hero Headline**
   - 强 serif 标题 `Reading Archive.`
   - 允许一个很小的蓝色句点作为品牌记忆点

3. **Quiet Supporting Line**
   - 一行中文说明
   - 说明这是回看与续读的地方，不写成长段教学

4. **New Reading Action**
   - 右上角保留一个明确但克制的 `新解读` 按钮
   - 作用是从 archive 快速回到 intake

5. **Search Row**
   - 一条长规则线式搜索区
   - 左侧输入，右侧显示记录数
   - 不做厚输入框，不做筛选工具条堆叠

### 4.4 Reading Record List

主列表按纵向目录组织，不做卡片网格。

每条记录自上而下包含：

1. **Status + Tags Line**
   - 轻量状态点和状态文案
   - `reading_goal`
   - `reading_variant`
   - `source_type`

2. **Title**
   - 中文标题为主识别信号

3. **Excerpt**
   - 一到两行原文片段
   - 用于二次识别，不承担解释任务

4. **Metadata Line**
   - 日期
   - 词数
   - 笔记数
   - 生词数

5. **Row Actions**
   - 收藏
   - 更多
   - 继续阅读

### 4.5 Archive Bookmark Rail

右侧不是普通 sidebar，而是一条页边书签。

它分成两个功能层：

1. **Front Bookmark: Archive Summary**
   - 当前 archive 的简短摘要
   - 最近一次阅读痕迹
   - 一条很短的编辑式提示

2. **Secondary Bookmark Block: Browse By Goal**
   - `reading_goal` 快速筛选
   - `全部 / 日常阅读 / 备考精读 / 学术阅读`

第二层可以作为前书签内部的一个区块，也可以作为背后露出的窄书签，但本质都应是页边索引，不是功能面板。

## 5. Visual Hierarchy

### 5.1 中部主区

视觉优先级应固定为：

1. Hero headline
2. Record titles
3. Search row
4. Excerpts
5. Metadata
6. Row actions

### 5.2 右侧书签

视觉优先级应固定为：

1. 书签标题
2. 简短 archive 摘要
3. `reading_goal` 筛选项
4. 最近重读条目
5. 编者手记

书签中绝不应出现“巨大的统计数字压过内容”的情况。

## 6. Archive Row Rules

这是本页最关键的实现点。

### 必须做到

- 列表一眼看上去像阅读目录，不像对象管理表。
- 标题、片段、阅读目标和个人痕迹共同构成识别系统。
- `继续阅读` 是明确动作，但不能像粗重 CTA。
- 收藏、状态、更多操作必须克制，不喧宾夺主。

### 建议实现方式

- 用细分隔线而不是卡片边框切行。
- 标题保持 serif 气质，元信息和标签保持低声量 sans。
- 状态点和收藏图标使用极少量品牌蓝或语义色。
- 行尾圆形箭头可保留，但必须细、轻、安静。

### 不应出现

- SaaS 式卡片列表
- 大量按钮堆在每一行右侧
- 过重的 badge 系统
- 让状态文案比文章标题更抢眼

## 7. Bookmark Rail Rules

右侧书签是这一版的签名结构。

### 必须做到

- 看起来像夹在页边的实用书签，而不是 sidebar 模块。
- 有纸面感，但不做旧、不拟物过度。
- 对浏览有帮助，而不是只展示统计。
- 与主页右栏的 editorial rail 保持同一语系。

### 推荐内容结构

书签上半部分：

- `我的归档书签`
- `本册收录 3 篇文章，6 条笔记，9 个生词。`
- `最近一次阅读在 2026 年 5 月 27 日。`

书签中段：

- `最近重读`
- 一条最近回读记录的缩略信息

书签下半部分：

- `按阅读目标浏览`
- `全部`
- `日常阅读`
- `备考精读`
- `学术阅读`

书签尾部：

- 允许出现极轻的 aperture 几何切角作为品牌收尾

### 不应出现

- `Overview / Articles Read / Search Tips` 这类后台式文案
- 纵向 KPI 堆叠
- 发光、悬浮、3D 或过强阴影
- 为了填满书签而硬塞内容

## 8. Quick Filter Rules

`reading_goal` 快速筛选是这一版新增的重要功能，但必须像页边索引，而不是工具栏。

实现要求：

- 默认存在 `全部`
- 点击后仅筛选中部列表，不跳页
- 当前选中态用蓝点、细蓝线或更深字重表达
- 每项后可带轻量数量，但不要做大数字
- 与搜索联动：先按关键词，再按 `reading_goal`

第一版可以本地筛选当前已加载记录；后续再考虑服务端参数化。

## 9. Visual Language Match With `/app/read`

`/app/library` 必须与 `/app/read` 共享这些语言：

- 同样的暖纸背景和低对比 grain
- 同样的三栏比例与分隔纪律
- 同样的 serif headline 气质
- 同样克制的蓝色使用方式
- 同样安静的应用壳存在感

允许不同的地方：

- `/app/read` 更像 intake stage
- `/app/library` 更像 archive directory
- `/app/read` 右栏是编辑选读
- `/app/library` 右栏是归档书签

## 10. Implementation Boundaries

Gemini 实现时应遵守以下边界：

1. 不按像素照抄图稿。
2. 不把主列表重新做成卡片化 SaaS 页面。
3. 不把右侧书签退化成普通统计 sidebar。
4. 不为了“实用”重新做出一整排筛选工具栏。
5. 不引入重拟物纸张纹理、贴纸感或手账装饰。
6. 不把收藏、删除、更多操作做得比文章本身更显眼。
7. 不把 `reading_goal` 筛选做成 tabs 栏盖过 archive 标题区。

## 11. Review Baseline

后续评审以这 8 条为准：

1. 切换到 `/app/library` 时，风格是否仍然属于 Claread，而不是进入另一套产品。
2. 主列表是否首先帮助用户“找回一篇文章”，而不是“管理一条记录”。
3. 标题、片段、元信息和痕迹信息是否形成了清楚的回读判断。
4. 右侧是否已经摆脱了 KPI/status bar 感。
5. 书签是否既有存在感，又没有抢走主列表的舞台。
6. `reading_goal` 快速筛选是否自然，是否不像工具栏。
7. 整体是否比当前实现更像杂志目录，而不是内容后台。
8. 与 `/app/read` 并排看时，是否明显属于同一设计系统。

## 12. Recommended Handoff Note

交给实现方时，建议附上这句说明：

> 这是 Claread `/app/library` 的主方向参考。实现目标不是复刻一张设计图，而是把它落成一个安静、可回读、像编辑目录页一样的 archive 界面。请优先保留首页同源的应用骨架、主列表的目录感、右侧实用书签和 `reading_goal` 的页边筛选语义，不要把它退化成普通后台列表或统计侧栏。
