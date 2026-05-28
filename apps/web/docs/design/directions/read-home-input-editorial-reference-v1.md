# `/app/read` Direction: Editorial Intake v1

> **状态**: `CURRENT`
> **最后更新**: 2026-05-27
> **用途**: `/app/read` 首页/输入页主方向定稿参考

![Claread /app/read editorial intake v1](./read-home-input-editorial-reference-v1.png)

## 1. Scope

本文件定义 Claread Web `/app/read` 的当前主方向参考图，以及把该方向拆成可实现的信息架构、视觉规则和评审基线。

它是 **方向稿**，不是像素级交付稿：

- 可以按结构、层级、气质和权重实现。
- 不要求逐像素复刻图片。
- 真实实现必须接入现有 Next.js 页面、真实每日精读数据和真实提交链路。

## 2. Page Role

`/app/read` 同时承担两种职责：

1. Claread Web 的第一产品首屏。
2. 用户进入一轮透读任务的 intake page。

因此它不是普通 landing hero，也不是普通 textarea 工具页。它应被实现为：

**一张准备接收英文文章的编辑稿面，旁边附有 Claread 的编辑式选读判断。**

## 3. Direction Summary

本方向的关键词：

- Editorial intake
- High-end whitespace
- Warm paper
- Prepared manuscript
- Quiet product
- Curated picks

需要保留的核心感受：

- 左侧主区像一张留好版心的稿纸，而不是标准输入框。
- 用户一眼能理解这里是开始输入/导入文章的地方。
- 右侧像文学评论或编辑部侧栏，而不是资讯站 feed。
- 多种导入方式存在，但必须很轻，不抢主任务。
- 任何“工具栏感”都要被压下去。

## 4. Information Architecture

### 4.1 顶层分区

页面分为 3 个层级，而不是多个组件卡片拼接：

1. **App Shell**
2. **Main Editorial Intake**
3. **Curated Reading Sidebar**

其中 2 是绝对主角，3 是判断性补充，不可反客为主。

### 4.2 Main Editorial Intake

左侧主区按从上到下的顺序组织：

1. **Eyebrow / Micro Label**
   - 低权重的小标签。
   - 作用是给页面一个 editorial cue。
   - 不要太多，不要堆多个 badge。

2. **Hero Headline**
   - 强 serif 标题。
   - 句子数量要少。
   - 文案必须直指 Claread 的阅读动作和品牌判断，不做空泛抒情。

3. **Quiet Supporting Line**
   - 最多一行。
   - 只补足“这是进入深度阅读的入口”。
   - 不写长说明，不写步骤教学。

4. **Prepared Manuscript Surface**
   - 页面主体。
   - 必须表达“这里可以开始输入或导入文章”，但不能像普通输入框。
   - 用版心、页边、轻微起始提示、空白秩序来完成 affordance。

5. **Secondary Intake Cues**
   - 粘贴文本 / 导入链接 / 上传文档 / 选择示例
   - 这些不是大按钮组。
   - 它们更像页脚注记或低强调入口。

6. **Primary Action**
   - 保留明确 CTA。
   - 但它应该像一个坚定的 editorial action，而不是通用 SaaS 大按钮。

### 4.3 Curated Reading Sidebar

右侧边栏按从上到下的顺序组织：

1. **Section Label**
   - 例如 `EDITOR'S PICKS` / `今日值得透读`
   - 语气应像编辑部栏目，不像“推荐算法”

2. **Lead Pick**
   - 1 条重点内容
   - 图片只作小型辅助，不依赖大封面
   - 首要信息是标题和 Claread 判断，不是图

3. **Supporting Picks**
   - 2-4 条短条目
   - 小缩略图可有可无
   - 应能快速回答：值不值得读、难度、时长、训练点

4. **Low-emphasis Footer Utility**
   - 归档 / 更多阅读 / Newsletter 等
   - 必须低权重

## 5. Visual Hierarchy

### 5.1 左侧主区

视觉优先级应固定为：

1. Hero headline
2. Manuscript surface
3. Primary action
4. Intake cues
5. Supporting line
6. Hidden/weak settings

### 5.2 右侧边栏

视觉优先级应固定为：

1. 栏目标题
2. Lead pick 标题
3. Supporting picks 标题
4. Metadata
5. Thumbnail

右栏内图片永远不能比标题更重。

## 6. Manuscript Surface Rules

这是本页最关键的实现点。

### 必须做到

- 用户在 3 秒内能理解这是输入/导入入口。
- 它看起来不像浏览器默认 textarea。
- 它即使为空，也成立。
- 它的大面积空白必须显得昂贵，而不是显得没做完。

### 建议实现方式

- 不使用完整四边明确描框。
- 用版心、内页边距、轻提示、起始插入点、弱规则线来暗示文本开始区域。
- placeholder 只保留一句，不要解释整套流程。
- 允许用户理解这里不仅能 paste，也能通过其他方式导入。

### 不应出现

- 厚边框输入框
- 明显表单底色块
- 大块说明文字
- 明显 segmented toolbar 紧贴输入面
- “先选模式再开始”的工具感流程

## 7. Intake Method Rules

Claread 后续不止支持 paste，因此本页不能被实现成“只有粘贴”的视觉结构。

可支持的入口语义：

- 粘贴文本
- 导入链接
- 上传文档
- 选择示例

实现要求：

- 作为低强调入口存在
- 不做主导航
- 不做并列大按钮
- 不打断稿面空白

推荐把它们实现为：

- 一行轻量入口
- 微型图标 + 文本
- 页脚脚注式分布

## 8. Settings and Toolbar Rules

这一版方向明确要求：

**不要出现明显底部工具栏。**

阅读模式、预估时长、快捷键和设置，最多只能以这些形态存在：

- 页脚注记
- metadata
- 很轻的 secondary controls
- tucked-away popover entry

不允许出现：

- 粗重 bottom bar
- 大面积按钮组
- 先配置再操作的工作台感

## 9. Sidebar Image Policy

每日精读封面来自抓取，真实数据里分辨率可能不高，因此右栏必须遵守：

- 不依赖大封面图撑质感
- 图片仅用作小缩略图或轻量视觉锚点
- 更依赖 typography、spacing、metadata 和裁切
- 即使封面质量一般，页面也要成立

实现时优先考虑：

- 小图
- 灰度/低饱和处理的可能性
- 图像缺失时的 typographic fallback

## 10. Implementation Boundaries

Gemini 实现时应遵守以下边界：

1. 不按像素照抄图稿。
2. 不为追图增加无意义装饰。
3. 不把主区重新做成普通表单页。
4. 不把右栏做成资讯 feed 或卡片列表。
5. 不把底部重新做成显性工具栏。
6. 不把主文案写回冗长说明。
7. 不为低清封面预留大图位。

## 11. Review Baseline

后续评审以这 8 条为准：

1. `/app/read` 首屏是不是先让人想开始，而不是先研究工具。
2. 主输入区是不是摆脱了“标准输入框”感。
3. 空白是不是有秩序，而不是空洞。
4. 多种导入方式是不是存在但不过度显眼。
5. 右栏是不是像编辑判断，而不是内容推荐流。
6. 即使封面图质量一般，右栏是不是仍然高级。
7. 底部是不是已经没有明显 toolbar 感。
8. 整体是不是比“普通工具页”更像 Claread，而不是更像媒体站。

## 12. Recommended Handoff Note

交给实现方时，建议附上这句说明：

> 这是 Claread `/app/read` 的主方向参考。实现目标不是复刻一张设计图，而是把它落成一个安静、编辑化、输入优先的产品首页。请优先保留稿面感、留白秩序、右栏的编辑判断和弱工具感，不要把它退化成普通 textarea + toolbar 页面。
