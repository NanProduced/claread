# Claread 产品概览

> **状态**: `CURRENT` | **最后验证**: 2026-08-31

## 产品定位

Claread 是阅读优先的英文辅助产品。

Claread 的中文名是“透读”。“透读”强调把一篇英文文章读透：不止翻译，不止查词，而是看清词汇、句子、语法和篇章关系。

产品优先级：

1. 阅读产品。
2. 笔记与批注产品。
3. 英语学习和词典辅助。

Claread 不应该像应试英语 App、通用词典 App 或 AI dashboard。阅读文本是主角，AI、词典、语法和翻译能力是安静的边缘智能。

## 用户

主要用户是有一定英语基础、希望更高效阅读英文内容的人：

- 大学生和备考用户。
- 阅读英文新闻、论文摘要、行业材料的专业人士。
- 英文阅读爱好者。

他们需要的是低打扰、可渐进展开的阅读辅助，而不是把每篇文章变成课堂练习。

## 核心链路

```text
用户提交英文文本 / 文件
  -> Reader orchestration：候选确认或稳定冻结（Reading Record + Stable Reading Document）
  -> 确定性生成 Reading Units / Anchor Segments
  -> 渐进生成 Enhancement Layers（译文、词汇、语法、长难句、语义大纲）
  -> snapshot projection 供各端渲染
  -> 客户端展示阅读、批注、词典、生词、Ask Claread、反馈等交互
```

首期核心能力：

- 重点词汇和语境义。
- 语法提示。
- 长难句拆解。
- 高质量翻译。
- 历史记录。
- 生词本。
- 收藏与反馈。
- 每日精读。

## 多端策略

微信小程序是 Claread 的第一个客户端，不是 Claread 的架构中心。

当前和计划中的客户端：

- `apps/miniprogram/`：微信小程序，功能子集，受小程序渲染和平台能力限制。
- `apps/web/`：Web baseline 已接入真实后端，当前是 Reader 提交主链的唯一客户端，后续推进高保真阅读体验和更强交互。
- `apps/directus/`：内部控制面（Claread Console），当前承载通用 metadata 展示、LLM Config、reader-orch 只读诊断和 Example Lab Collection；治理化控制面仍在建设中。

后端服务、数据库、workflow、词典和评测体系应尽量复用。

## 设计气质

关键词：

- Calm
- Precise
- Editorial

界面方向：

- 阅读面优先。
- 纸面感和编辑感。
- 批注像文具，而不是 UI chip。
- AI 解释可用但克制。
- 词典是支持层，不是产品中心。

避免：

- 紫色 AI dashboard。
- 密集词典面板。
- 大量卡片堆叠。
- 应试培训产品气质。
- 把所有解释默认展开。

## 当前状态

微信小程序、Web baseline 和 Directus 控制面当前共享通用后端、本地 PostgreSQL/Redis 和词典数据。LLM-as-a-Judge 与更完整的 RAG 治理仍会继续建设，但 Directus / Eval Center 已不再只是规划项。当前状态细节见 `docs/product/current-state.md`。
