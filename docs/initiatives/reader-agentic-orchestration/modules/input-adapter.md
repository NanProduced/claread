# Input Adapter 与 Candidate Document

> 状态：`D6 输入链路第一轮实现校准`
> 最后更新：2026-07-26（同步 `services/api/tests/fixtures/markdown_structured_source/CONTRACT.md` 2026-07-25 re-frozen：`code_block` / `table` / `table_row` / `table_cell` 的 `default_route` 已改为 `main_reading`）
> 范围：用户输入如何转成 Claread 可阅读、可渲染、可解析的 Stable Reading Document。

## 目标

Input Adapter 的目标不是把所有来源压扁成纯文本，而是生成适合 Claread 英语阅读提升的稳定阅读文档。

Reader 内容区是 Notion-like 的文档型阅读页：Web 用 Plate.js read-only editor 渲染，但 Plate document 仍是 projection，不是后端 truth。后端 truth 应拆成：

- Stable Reading Document：用户确认后的稳定阅读文档，包含标题、blocks、source refs、block metadata。
- Stable Document Blocks：文档块结构 truth，例如 paragraph、heading、list、table、image、footnote、code block。
- Canonical Text Layer：从 Stable Reading Document 派生的线性文本层，用于 UTF-16 offsets、Reading Units、Anchor Segments 和主解析链路。
- Plate Reader Document：Web projection，不持久化 raw Plate path / Slate ops。

统一路径：

```text
Input Adapter
  -> Original Input
  -> Source Artifact / Extraction Result
  -> Input Suitability Gate
  -> low-impact Stable Reading Document 或 high-impact Candidate Document
  -> Stable Reading Document + Canonical Text Layer
  -> Reading Units + Anchor Segments
```

Stable Reading Document 进入 Unit Builder 前，已经完成输入适配和必要确认。因此 Unit Builder 不承担 OCR 修复、网页正文抽取、boilerplate 删除、多栏顺序修复、表格/图片/脚注保留策略或正文重写职责。

Canonical Text Layer 是主解析和 UTF-16 anchor 的文本事实源；Stable Document Blocks 是文档渲染、table/image/footnote 引用和 RAG block citation 的结构事实源。

## D4 范围

D4 最小纵切只实现：

- `input_type=text`
- 低影响文本清理
- Stable Reading Document 的过渡文本路径（当前实现为 `reading_bases.text`）
- Reading Units
- `article_ready`
- translation layer
- Parsed Decision

URL、PDF、OCR、文件上传和 Candidate Document UX 不进入 D4 最小纵切。D1/D3 只预留 schema 和状态入口。

## V1 产品输入范围

V1 产品承诺的输入方式：

- 粘贴文本。
- 链接导入：公开网页文章 URL。
- 上传 PDF：英文阅读材料，支持页码范围。
- 上传图片 / 截图 OCR：支持多图合并为一个 Reading Record。
- `.txt` / `.md` 作为上传文档的低风险子类型，不单独作为产品入口。

V1 不承诺：

- Office 文档、EPUB、批量导入、视频/音频转写、跨文件合并。
- 登录墙、付费墙、Google Docs / Notion / 飞书等私有协作文档。
- PDF 原版排版 100% 复刻、全书级导入、复杂公式语义解析或表格精确编辑。

## Input Suitability Gate

所有输入，包括粘贴文本、`.txt` 和 Markdown，都必须先经过 Input Suitability Gate。直进 Stable Reading Document 的条件不是“来源低风险”，而是：

- 有足够英文自然语言内容支撑 Claread 阅读解读。
- 文本/结构处理不会改变关键含义。
- 格式足够简单，能直接生成稳定 blocks 与 Canonical Text Layer。
- 不以代码、表格、链接列表、非英文文本或极短片段为主体。

Gate 结果：

| 结果 | 含义 | 下一步 |
|---|---|---|
| `stable_document_ready` | 适合直接冻结 | 生成 Stable Reading Document、Canonical Text Layer、Reading Units |
| `candidate_document_required` | 内容或结构需要用户确认 | 生成 Candidate Document，进入 `needs_confirmation` |
| `input_rejected_or_action_required` | 不适合当前阅读解读或需要用户处理 | 提示补充、缩短、换输入或选择页码范围 |

## 输入类型与风险

| 输入 | 处理路径 | 进入 Candidate Document 的触发条件 |
|---|---|---|
| 纯文本粘贴 | 编码规范化、段落整理、语言检测、标题候选、轻量去重 | 内容过短/过长、非英文占比高、结构不适合阅读解读、明显 OCR 噪声 |
| Markdown / txt | Markdown AST 或纯文本 parser；生成 stable blocks 与 canonical text mapping | 表格/图片/脚注/HTML/math 等结构复杂，或降级会改变含义 |
| URL / 网页 | 保存 URL 与快照；正文抽取、boilerplate 去除、标题/作者/来源 metadata | V1 默认进入 Candidate Document；正文抽取置信低、广告残留、分页/登录墙、正文可能丢失 |
| PDF | 文本层 parser 优先；按页保存 artifact；页级 quality gate 决定是否 OCR | V1 默认进入 Candidate Document；多栏、脚注密集、公式/表格多、抽取顺序异常、扫描件 |
| OCR 图片 | 保存原图；OCR provider 输出文字、区域/页信息；多图按顺序合并为 Candidate Document | V1 默认进入 Candidate Document；低置信、断行严重、阅读顺序不确定、多语言混合 |
| Office / 富文档 | 转换为结构化抽取结果；保留标题、段落、列表和注释风险 | 批注/修订/表格/页眉页脚影响正文边界 |

## 影响等级

输入适配按是否可能改变作者可见文本或阅读顺序，分为低影响和高影响。

| 影响等级 | 定义 | 可直接冻结 Stable Reading Document | 用户确认 |
|---|---|---|---|
| Low-impact Input Adaptation | 不改变作者可见语义的确定性规范化，且通过 Input Suitability Gate | 可以写 Stable Reading Document | 不需要 |
| High-impact Input Adaptation | 可能改变内容边界、阅读顺序、文档块结构或作者可见文本的处理，如 OCR 修复、多栏 PDF 顺序修复、删除 boilerplate、表格/图片/脚注降级、正文重排 | 不可以；先写 Candidate Document | 必须 preview / edit / confirm |

低影响处理的核心要求是可预测、可审计、可重复。它可以降低 Unit Builder 的切分难度，但不能把作者可见文本改写成另一篇文章。

高影响处理的核心要求是用户可见。只要处理可能改变正文含义、删除内容、重排结构或让来源损失不可忽略，就必须进入 Candidate Document。

## Source Loss Flags

`extraction_results.source_loss_risk` 至少表达：

- `layout_order_uncertain`
- `ocr_low_confidence`
- `boilerplate_maybe_retained`
- `main_content_maybe_missing`
- `table_structure_uncertain`
- `image_ocr_uncertain`
- `footnote_or_caption_merged`
- `document_block_degraded`
- `non_english_or_mixed_language`
- `too_short_for_learning`
- `too_long_requires_envelope`
- `copyright_or_policy_review_required`

低风险且通过 Input Suitability Gate 的输入可直接生成 Stable Reading Document。高风险输入进入 `needs_confirmation`。

## Candidate Document 语义

Candidate Document 是确认前的候选阅读文档，不是纯文本候选。

Candidate Document 至少包含：

- 文档标题候选。
- normalized document blocks。
- 每个 block 的类型、顺序、source artifact/page refs 和 warnings。
- canonical text mapping。
- table、image、footnote、caption、code 等特殊块。
- 哪些 block 进入主解析，哪些只进入 Ask/RAG。

用户确认前，可以预览、编辑和有限控制 Candidate Document：

- 编辑普通文本块内容。
- 调整 heading / paragraph / list / quote 等基础块类型。
- 修改标题。
- 删除、恢复或编辑低置信 block。
- 对 PDF 页或图片重新 OCR。
- 在 text layer 与 OCR text 间选择。
- 对 image OCR text 选择“作为正文阅读”、“只供 Ask/RAG 引用”或“忽略 OCR text，仅保留图片”。
- 对 table 选择“保留表格”或“转为普通文本阅读”。

V1 不做：

- 任意拖拽复杂块。
- 复杂表格编辑器。
- 图片裁剪 / 标注。
- PDF page layout 编辑。
- 多文件合并编辑。

确认后生成 Stable Reading Document。Stable Reading Document 冻结后，不允许在同一 Reading Record 内改文档 truth；如果冻结后发现错误，创建新 Reading Record 或 supersede 旧 record。

D4 不实现 Candidate Document UI。D6+ 实现高影响输入确认体验。

## Stable Document Blocks 与 Plate Snapshot

输入适配应输出稳定文档块，而不是仅输出结构提示。

### block_type 枚举（与后端 schema/migration 一致）

> **与 CONTRACT.md 对齐说明**：`default_route` 与 `rag_eligible` 以 `services/api/tests/fixtures/markdown_structured_source/CONTRACT.md` Clause 4 为单一事实源。下表"默认进入 canonical text"列对齐 CONTRACT 2026-07-25 re-frozen：`code_block` / `table` / `table_row` / `table_cell` 的 `default_route` 已从早期的 `rag_ask_only` / `metadata_only` 改为 `main_reading`。

| block_type | text_content | 默认进入 canonical text | 说明 |
|------------|--------------|----------------------|------|
| `paragraph` | 段落纯文本 | 是 | 原文段落 |
| `heading` | 标题纯文本（不含 `#`） | 是 | level 放 payload_json |
| `list_item` | 项纯文本（不含 `-` / `1.` marker） | 是 | grouping 放 payload_json |
| `blockquote` | 引用纯文本 | 是 | |
| `caption` | 图表说明纯文本 | 是 | |
| `table` | — | 是（容器，无 text_content 直接贡献） | 容器，由 table_row/table_cell 组成；`default_route=main_reading`、`rag_eligible=false`（RAG 只针对 table_cell 叶子） |
| `table_row` | — | 是（容器，无 text_content 直接贡献） | `default_route=main_reading`、`rag_eligible=false` |
| `table_cell` | 单元格纯文本 | 是 | `default_route=main_reading`、`rag_eligible=true` |
| `footnote` | 脚注纯文本 | 否 | `default_route=rag_ask_only`；除非 Candidate confirm 显式提升 |
| `image` | — | 否 | `default_route=metadata_only`；图片 URL 放 payload_json |
| `image_ocr` | OCR 识别纯文本 | 否 | `default_route=rag_ask_only`；除非 Candidate confirm 显式提升 |
| `code_block` | 纯代码文本（不含 ``` 围栏） | 是 | `default_route=main_reading`、`rag_eligible=true`；语言放 payload_json.language |
| `unknown` | 原始文本兜底 | 否 | `default_route=metadata_only` |

> `divider` 文档希望有但后端 schema/migration 当前缺，待补。补上前用 `unknown` + payload_json 兜底。
> `degraded block notice` / `page/source artifact reference` 不做成正文 block，放入对应 block 的 `payload_json` / `source_refs_json` / `quality_json`。

### payload_json 子契约（V1）

后端当前只要求 `payload_json` 是 object，无 shape 约束。V1 子契约定如下：

**list_item**：
```json
{
  "list_id": "list-1",
  "ordered": false,
  "ordinal": 1,
  "depth": 0,
  "marker": "-"
}
```
`list_id` + `ordered` + `depth` 决定容器分组；`ordinal` 用 1-based 可见顺序；`marker` 可选。前端投影时按 `list_id` + `ordered` 还原 `ul`/`ol` 容器。

**heading**：
```json
{
  "level": 2
}
```
`level` 范围固定 1..6；超过 6 的来源 heading 降级到 6 并在 `quality_json` 标记 `heading_level_degraded`。

**code_block**：
```json
{
  "language": "python",
  "info_string": "python title=\"example.py\""
}
```
`language` 为代码语言标识（可选）；`info_string` 保留原始围栏 info（可选）。前端根据 `block_type=code_block` + `payload_json.language` 渲染代码块。

### canonical text 拼接规则

所有 `interpretation_policy.default_route == "main_reading"` 的 block 都用 `\n\n` 连接进入 canonical text：

- 默认进入：`paragraph` / `heading` / `list_item` / `blockquote` / `caption` / `table_cell` / `code_block`
- 容器型 `default_route == "main_reading"` 但无 `text_content`：`table` / `table_row`（不直接贡献文本，但其叶子 `table_cell` 进入）
- 默认不进入：`image` / `image_ocr`（`metadata_only` / `rag_ask_only`）、`footnote`（`rag_ask_only`）、`unknown`（`metadata_only`），除非 Candidate confirm 显式提升

canonical text 不含 Markdown 语法字符（`#` / `-` / `>` / ``` ``` ``` / GFM 表格语法 / 脚注语法不进入 offset 基准）。

### 与 Plate Snapshot 的关系

这些 stable blocks 是 Reader document truth 的一部分。Plate Snapshot 从 blocks、Canonical Text Layer、Reading Units 和 Anchor Segments 投影生成；不能把 Plate document 反向作为 truth。

Markdown / HTML / OCR / PDF 转 Plate fragment 前必须：

- 保留 canonical text mapping。
- 记录 degraded structures，不静默丢弃 table、image、footnote。
- 对 raw HTML、links、media、tables 做 allowlist 或 risk flag。
- 在高影响时进入 Candidate Document preview。

当前 D5 低风险 plain-text 路径仍只冻结 canonical text。D6+ 正式方向应是：Candidate Document / Base Composer 在确认前保留 normalized block metadata（或 extraction structure），用户确认后再把 Stable Document Blocks 与 Canonical Text Layer 一起冻结为 domain facts；Plate projection 继续只消费这些 facts，不把 Plate document 当 truth。

## Markdown 支持边界

V1 支持 Markdown 作为文档输入格式，而不是把 Markdown 源码当普通纯文本。

V1 应保留并投影：

- heading
- paragraph
- ordered / unordered list
- blockquote
- code block / inline code
- divider
- link text 与 URL metadata
- table
- image / alt text
- footnote / reference

raw HTML、math、复杂嵌套结构和不安全 link 协议必须 sanitize 或进入 Candidate Document warning。Markdown 的目标是形成 Claread Stable Reading Document，不是无损 Markdown 编辑器。

## PDF 页级处理策略

PDF V1 采用 parser 优先、OCR 按需的页级策略：

1. 先用 deterministic PDF parser 抽取文本层、页码、基础 blocks、图片引用、表格/脚注候选。
2. 计算页级质量信号：文本长度、英文字符/词占比、乱码比例、重复/空白比例、行顺序异常、多栏/表格/脚注风险、text layer bounding box 覆盖率、视觉区域覆盖一致性。
3. deterministic quality gate 直接分类：
   - `text_layer_usable`
   - `ocr_required`
   - `review_needed`
4. `review_needed` 时可调用 LLM reviewer。Reviewer 只看 page image + parser diagnostics，输出结构化决策：
   - `use_text_layer`
   - `run_ocr`
   - `run_ocr_and_compare`
   - `needs_user_confirmation`
5. reviewer 决策写入 Extraction Result，不直接冻结 Stable Reading Document。
6. OCR 页数、页码范围、成本和重试由 Authorization Envelope 控制。

LLM reviewer 是输入适配节点，不是 Reader orchestration planner。

## OCR 图片策略

图片 / 截图 OCR V1 支持多图组成一个 Reading Record：

- 用户可上传 1 到 N 张图片，V1 默认上限建议 10 张。
- 默认按上传顺序合并，Candidate Document 中允许顺序确认。
- 每张图作为 Source Artifact 保留。
- OCR 文本进入 Candidate Document，用户确认后才成为 Stable Reading Document 的一部分。
- 图片本身作为 image block 保留；OCR text 可以作为 associated text block。
- 低置信、模糊、倾斜、遮挡、手写、复杂版面都必须显示 warning。

产品承诺是“可识别并生成 Candidate Document”，不是自动无误。

## 当前实现状态（D6-I3 第一轮）

当前后端已形成 artifact-backed input pipeline 的第一轮可运行基线：

- `source_artifacts` 保存上传对象引用、checksum、owner scope、状态机和 source metadata；二进制对象存 OSS，不写 PostgreSQL。
- `/reader/source-artifacts/init-upload` 与 `/complete-upload` 支持原始文件上传初始化和完成确认；开发 bucket 为 `claread-dev`，真实 presigned upload 通过 OSS presigner adapter，缺凭证时 fail closed 到 pending-credentials 形态。
- 本地 OSS 配置不要求复制 AccessKey secret：`ALIYUN_OSS_PRESIGN_ENABLED=true` 开启签名；成对的 `ALIYUN_OSS_ACCESS_KEY_ID` / `ALIYUN_OSS_ACCESS_KEY_SECRET` 优先，二者都为空时 fallback 到通用 `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET`；不允许混用半组 OSS 专用凭证和半组通用阿里云凭证。API 与 `reader-artifact-pipeline-worker` 的 Python 环境都必须安装 `oss` extra，且 OSS bucket 需要允许浏览器来源的 PUT/OPTIONS CORS。
- `/reader/source-artifacts/{artifact_id}/submit-input` 把 available artifact 绑定为 `reading_records` + `original_inputs`，随后 enqueue `input_artifact_extraction` job。
- `reader-artifact-pipeline-worker` 先处理 extraction job，再 enqueue / drain `extracted_artifact_materialization` job；API submit 不同步执行文件解析。
- extraction provider router 当前支持 text/Markdown、PDF text-layer extraction、OCR provider 三类 provider。`text/plain`、`text/markdown`、`.txt` / `.md` 可直接抽取文本；`application/pdf` 通过 deterministic PDF text extractor 抽取可复制文本；`image/*` 委派 OCR provider。
- OCR 默认未配置时 image job terminal fail closed 为 `ocr_provider_unconfigured`。本地启用 qwen3.5-ocr 需要 `READER_OCR_PROVIDER_ENABLED=true`、`READER_OCR_PROVIDER_NAME=qwen`、`READER_OCR_QWEN_MODEL=qwen3.5-ocr`，并提供 `DASHSCOPE_API_KEY`（进程 env 或 `services/api/.env`）。
- materialization 阶段根据 artifact content type 派生 `txt_file` / `markdown_file` / `pdf_text` / `ocr_text`。`pdf_text` 和 `ocr_text` 默认进入 Candidate Document required，不会绕过用户确认直写 Stable Reading Document。

仍未完成：

- PDF 页级 quality gate、text-layer vs OCR 决策、页码范围和 optional LLM reviewer。
- 真实 OCR provider adapter、模型 profile、网络错误分类、成本/配额接线。
- 多图 Candidate Document 的顺序确认、图像块和 OCR 文本的用户可控提升策略。
- Source Artifact 过期清理、失败重试运维面和生产部署配置。

## 外部服务边界

阶段性建议：

- 文件上传正式产品路径使用阿里云 OSS，开发环境提供 local artifact adapter；object metadata、checksum、owner scope 必须与 OSS adapter 一致。
- 后端不把二进制文件塞进 PostgreSQL。
- Source Artifact 对已确认 Reading Record 默认随 record 生命周期保留；未完成、失败或放弃导入的临时 artifacts 可过期清理。
- OCR / 富文档解析通过 `OcrProviderAdapter` / `DocumentParserAdapter` 接入。当前本地 OCR provider 可配置为 `qwen3.5-ocr`；产品和领域合同不绑定具体模型名。
- PDF/富文本 parser 只能生成 Extraction Result / Candidate Document，不能直接写 Stable Reading Document。
- 所有 provider 通过 adapter 接入，不能成为 Claread 业务事实源。

## D2 Spike

D2 需要验证：

- Markdown/txt parser 的 Stable Document Blocks、canonical text mapping 和 source loss flags。
- PDF 文本层、页级 quality gate、LLM reviewer 和 OCR 输出能否形成可审计 Extraction Result / Candidate Document。
- OCR 多图合并、顺序确认和 warning 能否稳定进入 Candidate Document。
- Source loss flags 是否能稳定路由到 Candidate Document。
- OSS object metadata、checksum、权限和过期清理。
