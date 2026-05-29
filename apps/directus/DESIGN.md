---
name: Claread Console Design System
description: A Directus-native, evidence-first design system for internal observability, governance, and data diagnostics.
colors:
  ink: "#172940"
  ink-soft: "#30445F"
  ink-muted: "#6B7280"
  surface: "#FFFFFF"
  surface-subtle: "#FAFBFC"
  surface-page: "#F5F7FA"
  border: "#D9DEE7"
  border-soft: "#E3E7EE"
  info: "#245CB8"
  info-soft: "#EEF5FF"
  success: "#11795B"
  success-soft: "#ECFDF3"
  warning: "#9A5B00"
  warning-soft: "#FFF7E8"
  danger: "#BE123C"
  danger-soft: "#FFF1F2"
  accent: "#0F6CBD"
typography:
  headline:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, Inter, PingFang SC, Helvetica Neue, Arial, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 700
    lineHeight: 1.35
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, Inter, PingFang SC, Helvetica Neue, Arial, sans-serif"
    fontSize: "1rem"
    fontWeight: 650
    lineHeight: 1.4
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, Inter, PingFang SC, Helvetica Neue, Arial, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, Inter, PingFang SC, Helvetica Neue, Arial, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 600
    lineHeight: 1.4
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, Liberation Mono, monospace"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  sm: "8px"
  md: "12px"
  lg: "16px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
  xxl: "32px"
components:
  section-card:
    backgroundColor: "{colors.surface}"
    borderColor: "{colors.border-soft}"
    rounded: "{rounded.md}"
    padding: "16px"
  verdict-chip:
    rounded: "{rounded.pill}"
    padding: "0 8px"
  diagnostic-table:
    backgroundColor: "{colors.surface}"
    borderColor: "{colors.border-soft}"
  json-fallback:
    backgroundColor: "#0F172A"
    textColor: "#E5EDF7"
    rounded: "{rounded.md}"
---

# Design System: Claread Console Design System

## 1. Overview

`Claread Console` 是内部控制面，不是对外产品页，也不是用户端阅读器的延伸皮肤。

它的默认承载是 Directus Data Studio，因此整体视觉应优先靠近 Directus 原生工作台：干净、克制、可维护、利于高频操作。与此同时，在 `render scene inspector`、`workflow eval`、`RAG / few-shot editor` 这类深诊断模块中，又要具备更强的分层、异常高亮、证据联动和下钻能力，接近 LangSmith / Sentry 的问题详情页。

这个系统的核心不是“统一把所有页面做漂亮”，而是建立一套适用于内部诊断工作的显示规则：

- 什么信息应首屏直出
- 什么信息应折叠
- 什么信息必须先转成摘要和结构，再允许看原始 JSON
- 什么数据应该表格化、对照化、时间线化或句级联动化

## 2. Visual Direction

### Default Direction

- 默认视觉语气：冷静、直接、低装饰、数据优先
- 默认背景：浅色工作台，不做沉重暗色默认
- 默认容器：轻边框、轻阴影、少层级
- 默认字体：系统 sans，保证长时间扫描和 Directus 一致性

### Native First

当需求能被 Directus 原生组件、布局、列表、表格、badge、sidebar、split view 承载时，优先沿用 Directus 风格，不主动“重设计”。

只有在以下场景，才允许明显增强自定义表现：

- 诊断结论区
- 异常高亮与风险排序
- 句级 / span 级联动
- workflow / trace / runtime 的证据下钻
- 复杂 JSON 的结构化可视化解析

## 3. Color Strategy

这是一个典型的 `product / restrained` 色彩策略：

- 中性底色承载主要信息
- 颜色主要用于状态语义，而不是装饰
- 不把品牌色铺成大面积背景

### Semantic Roles

- **Info**：进行中、补充信息、结构密集、次要高亮
- **Success**：已完成、已生成、结构正常
- **Warning**：缺失、未回写、证据不完整、可疑状态
- **Danger**：失败、强异常、重度降级、明确风险

### Rules

- 状态色只用于状态，不用于无意义点缀
- 一个区块内同时出现多种高饱和状态色时，必须重新审视信息优先级
- 若信息没有明确状态语义，优先使用中性色

## 4. Typography

`Claread Console` 不需要 Claread 用户端的 editorial serif 语言。它是内部工作台，应优先强调扫描效率和字段辨识。

### Rules

- 标题与分区名使用系统 sans 粗体，不用装饰性字体
- 正文说明、表格内容、状态说明统一使用系统 sans
- 仅对技术字段、ID、schema version、workflow version、JSON key、事件类型使用 mono
- 避免大面积全英文标题；文案默认中文，只有业务字段、模型名、schema 名、事件 code 等确有必要时保留英文

## 5. Layout Principles

### 5.1 Page Hierarchy

所有深诊断页面默认分为四层：

1. **诊断判断层**
   - 首屏摘要、状态、风险结论、异常聚焦
2. **主排查层**
   - 表格、联动列表、对照视图、主信息区
3. **证据层**
   - 任务、事件、usage、snapshot、trace、runtime
4. **原始回查层**
   - 原始 JSON、原始 payload、完整结构

### 5.2 Navigation

- 页面内存在多个大分区时，侧边栏应提供快速跳转
- 不能要求用户只能靠滚动来寻找信息
- 锚点名称必须是诊断语义，而不是技术实现名

### 5.3 Density

- 高信息密度是允许的，但必须可扫描
- 首屏展示高优先信息，长明细进入表格、折叠块或右侧 pane
- 不能把每个字段都做成一张独立卡片

## 6. Component Patterns

### Verdict Summary

用于首屏回答：

- 当前 run 是否可用
- 当前问题更像在哪一层
- 证据链是否完整

形式建议：

- 一条主判断
- 少量关键状态卡
- 不超过 6 个首屏状态块

### Highlight Table

用于集中展示重点异常：

- 缺翻译
- 缺讲解
- 全局告警
- 调试快照缺失
- overview 未回写

规则：

- 异常项必须是“可行动”的
- 异常列表中的详情要经过压缩和语义化，不能直接原样吐出长串结构

### Triage Table

这是 inspector / eval / data QA 类页面的核心模式。

适用于：

- 句级排查
- 记录级异常清单
- 任务级诊断列表

规则：

- 表格列必须围绕判断任务设计，不是字段罗列
- 默认按风险或诊断优先级排序
- 点击行后在右侧 pane 展开细节

### Evidence Sections

用于承载：

- 任务状态
- 事件时间线
- usage
- debug snapshots
- trace refs

规则：

- 每个 evidence section 先给摘要，再给原始结构
- 摘要要能帮助判断是否值得展开

### Sidebar Jump List

适用于分区较多的诊断页，例如 `Render Scene Inspector`。

规则：

- 侧边导航应表现为单层列表，而不是一组独立按钮
- 当前分区必须有稳定高亮，并尽量随页面滚动同步
- 可使用细竖线或活动指示条表达当前位置，优先贴近 Directus 原生侧栏语义
- 导航文案应直接对应诊断任务，例如 `结果概览 / 调用消耗 / 运行证据 / 调试快照`
- 导航只负责跳转，不承载统计数据

### Cost Review Panel

适用于 `调用消耗` 这类直接服务治理与成本分析的模块。

规则：

- 在 Inspector 中优先级应高于异常明细，通常放在结果概览之后
- 先看总调用、总 tokens、总积分、总耗时，再拆主解析与概览提示的分组消耗
- 分组卡优先展示输入 / 输出 / 总 tokens、耗时和积分，不把模型、Prompt 版本放在第一层
- 单次调用明细保留在表格回查层，避免首屏只看到流水账

### Retrieval Evidence Panel

适用于 `RAG 检索` 这类需要展示命中、召回、重排和淘汰链路的诊断面板。

规则：

- 命中样例正文必须优先显示
- ANN / rerank / dropped 只显示治理所需的紧凑证据
- `query_text`、候选句、样例标签和分数必须能直接回看
- 原始 JSON 仅作为折叠回查层

### JSON Explorer

复杂 JSON 不应默认原样展示。

必须遵守：

- 先结构化拆解
- 再语义摘要
- 最后才允许查看原始 JSON

适用手段：

- 键值摘要
- 分组 section
- 层级折叠
- 数组计数
- 特定 schema 的专用可视化

## 7. Content Rules

- 文案默认中文
- 枚举优先翻译成中文状态词
- 技术字段名、模型名、schema / workflow / prompt 版本、事件 code 可保留英文
- 如果一段原始文本过长，必须先做截断预览，再允许展开
- “原始数据”“完整 JSON”“调试快照”等表述应明确它们是回查层，不应伪装成主判断

## 8. Do's and Don'ts

### Do

- 优先表格化、对照化、联动化展示结构化数据
- 为复杂 JSON 提供结构化拆解和可视化解析
- 把异常、缺失、失败、未回写等状态直接前置
- 使用侧边栏、split view、折叠块和锚点降低排查成本
- 让页面先回答“哪里坏了”，再回答“原始数据是什么”

### Don't

- 不做营销站式大 hero、大留白、大情绪设计
- 不把所有数据都塞成统一卡片或统一 badge
- 不把长 JSON、长列表、长句子原样全部铺开
- 不为了“设计感”牺牲 Directus 工作台的一致性
- 不让用户通过无目的滚动和肉眼扫字段来完成诊断
