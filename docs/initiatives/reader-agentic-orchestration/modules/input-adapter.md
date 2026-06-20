# Input Adapter 与 Candidate Base

> 状态：`D1 草案`
> 最后更新：2026-06-18
> 范围：用户输入如何转成 Claread 可阅读的英文 Stable Reading Base。

## 目标

Input Adapter 的目标不是保留原文件格式，而是生成适合 Claread 英语阅读提升的英文正文。

统一路径：

```text
Input Adapter
  -> Original Input
  -> Source Artifact / Extraction Result
  -> low-impact Stable Base 或 high-impact Candidate Base
  -> Stable Reading Base
  -> Reading Units
```

Stable Reading Base 进入 Unit Builder 前，已经完成输入适配和必要确认。因此 Unit Builder 不承担 OCR 修复、网页正文抽取、boilerplate 删除、多栏顺序修复或正文重写职责。

Web Reader 使用 Plate.js 渲染 Article Body。Input Adapter 可以保留低风险结构 metadata，用于生成 Base Plate Snapshot；但 Stable Base 的 canonical text 仍是 anchor 和 RAG citation 的文本事实源。

## D4 范围

D4 最小纵切只实现：

- `input_type=text`
- 低影响文本清理
- Stable Reading Base
- Reading Units
- `article_ready`
- translation layer
- Parsed Decision

URL、PDF、OCR、文件上传和 Candidate Base UX 不进入 D4 最小纵切。D1/D3 只预留 schema 和状态入口。

## 输入类型与风险

| 输入 | 处理路径 | 进入 Candidate Base 的触发条件 |
|---|---|---|
| 纯文本粘贴 | 编码规范化、段落整理、语言检测、标题候选、轻量去重 | 内容过短/过长、非英文占比高、明显 OCR 噪声 |
| Markdown / txt | Markdown AST 或纯文本 parser；保留标题、列表、引用、代码块边界；输出阅读正文 | 表格/代码/引用占比高，删除结构会改变含义 |
| URL / 网页 | 保存 URL 与快照；正文抽取、boilerplate 去除、标题/作者/来源 metadata | 正文抽取置信低、广告残留、分页/登录墙、正文可能丢失 |
| PDF | 文本层优先；按页保存 artifact；抽取正文顺序、页码、脚注和图表风险 | 多栏、脚注密集、公式/表格多、抽取顺序异常、扫描件 |
| OCR 图片 | 保存原图；OCR/VL 输出文字、置信度、区域/页信息；合并为候选正文 | 低置信、断行严重、阅读顺序不确定、多语言混合 |
| Office / 富文档 | 转换为结构化抽取结果；保留标题、段落、列表和注释风险 | 批注/修订/表格/页眉页脚影响正文边界 |

## 影响等级

输入适配按是否可能改变作者可见文本或阅读顺序，分为低影响和高影响。

| 影响等级 | 定义 | 可直接写 Stable Base | 用户确认 |
|---|---|---|---|
| Low-impact Input Adaptation | 不改变作者可见语义的确定性规范化，如 line endings、Unicode space/invisible character、首尾空白、重复空行 | 可以 | 不需要 |
| High-impact Input Adaptation | 可能改变内容边界、阅读顺序或作者可见文本的处理，如 OCR 修复、多栏 PDF 顺序修复、删除 boilerplate、表格/代码降级、正文重排 | 不可以；先写 Candidate Base | 必须 preview / edit / confirm |

低影响处理的核心要求是可预测、可审计、可重复。它可以降低 Unit Builder 的切分难度，但不能把作者可见文本改写成另一篇文章。

高影响处理的核心要求是用户可见。只要处理可能改变正文含义、删除内容、重排结构或让来源损失不可忽略，就必须进入 Candidate Reading Base。

## Source Loss Flags

`extraction_results.source_loss_risk` 至少表达：

- `layout_order_uncertain`
- `ocr_low_confidence`
- `boilerplate_maybe_retained`
- `main_content_maybe_missing`
- `table_or_figure_dropped`
- `footnote_or_caption_merged`
- `non_english_or_mixed_language`
- `too_short_for_learning`
- `too_long_requires_envelope`
- `copyright_or_policy_review_required`

低风险输入可直接生成 Stable Reading Base。高风险输入进入 `needs_confirmation`。

## Candidate Base 语义

Candidate Reading Base 是确认前的候选正文。

- 用户确认前，可以预览和编辑 Candidate Base。
- 确认后生成 Stable Reading Base。
- Stable Reading Base 冻结后，不允许在同一 Reading Record 内改正文。
- 如果冻结后发现正文错误，创建新 Reading Record 或 supersede 旧 record。
- Candidate Base preview 是输入适配阶段的产品边界，不是 Enhancement Layer preview。

D4 不实现 Candidate Base UI。D6 实现高影响输入确认体验。

## 结构保留与 Plate Snapshot

输入适配可以输出结构提示：

- heading
- paragraph
- list item
- blockquote
- code block boundary
- table risk marker
- page / source artifact reference

这些提示只能帮助生成 Base Plate Snapshot 和 Candidate Base preview。它们不能绕过 source loss risk，也不能让未经确认的高影响处理直接写 Stable Base。

Markdown / HTML / OCR / PDF 转 Plate fragment 前必须：

- 保留 canonical text mapping。
- 记录 dropped / degraded structures。
- 对 raw HTML、links、media、tables 做 allowlist 或 risk flag。
- 在高影响时进入 Candidate Base preview。

## 外部服务边界

阶段性建议：

- 文件上传测试阶段使用阿里云 OSS，上线目标为 OSS + CDN。
- OCR / 富文档解析优先评估阿里云百炼图像理解、Qwen OCR/VL 和文档解析能力。
- PDF/富文本 parser 只能生成 Extraction Result / Candidate Base，不能直接写 Stable Base。
- 所有 provider 通过 adapter 接入，不能成为 Claread 业务事实源。

## D2 Spike

D2 需要验证：

- Markdown/txt parser 的正文保留与结构降级。
- PDF 文本层和 OCR/VL 输出能否形成可审计 Extraction Result。
- Source loss flags 是否能稳定路由到 Candidate Base。
- OSS object metadata、checksum、权限和过期清理。
