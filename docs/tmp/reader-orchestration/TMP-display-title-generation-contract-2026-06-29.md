# TMP Display Title Generation Contract

> 日期：2026-06-29
> 状态：已落地后端/数据链路首版
> 范围：Reader Agentic Orchestration 中文标题生成，不包含新版 Plate Header UI 和旧 `/app/reader/{recordId}` 前端。

## 结论

新版 `/app/reader-record/{recordId}` Header 的中文标题必须由后端生成。前端只消费 snapshot 的 `record.display_title_zh`，不得在正常路径用源标题、base title 或客户端逻辑生成 fallback。

当前代码此前没有独立的中文标题生成事实源、状态机或 prompt。已有 `reading_records.title`、`reading_bases.title_snapshot`、Stable Reading Document `title` 都是源/冻结标题语义，不等同于生成的中文 masthead 标题。

## 数据合同

中文标题状态归属 `reading_records`：

- `generated_title_zh`
- `title_generation_status`: `pending` / `succeeded` / `failed_retryable`
- `title_generation_error_code`
- `title_generation_error_message`
- `title_generation_attempt_count`
- `title_generation_updated_at`

归属理由：

- Header 中文标题是用户面对的 Reading Record 产品元数据，不是 Stable Reading Document 或 Canonical Text Layer 的源事实。
- 它可以因模型失败、重试、prompt 策略调整而重新生成，不应改变 `reading_bases` 的不可变文本事实。
- Supersede / generation fence 仍由 record/base/job 一起保护，避免旧 base 的标题写回新 record generation。

`succeeded` 必须伴随非空 `generated_title_zh`。`pending` 和 `failed_retryable` 都不能被 snapshot/API 伪装成有标题。

## Runtime 合同

新增 job：

- job type: `generate_display_title_zh`
- target scope: `record`
- target key: `reading_record_id`
- operation fingerprint: `display_title_zh_v1`
- model route / usage capability: `reader_title_generation`
- model profile：必须显式配置 `reader_title_model_profile`；不能静默回退到 translation 或 annotation profiles
- output length：硬上限 32 个字符，推荐 8-24 个中文字符

Drain 顺序为：

```text
display title -> translation -> vocabulary -> grammar bundle
```

失败处理：

- LLM/provider/validation/configuration 失败写入 `failed_retryable`。
- job 进入 `retry_later`，run 进入 `failed_retryable`。
- 记录错误 code/message 和 attempt count。
- 后台 worker 或 compensation bootstrap 可以再次把状态置为 `pending` 并重试。
- generation fence 失败时不能写 retryable 结果到当前 record；claimed job 应被 supersede。

## 输入策略

标题模型输入必须是 bounded context，不允许直接传全文。

优先级：

1. Active Stable Reading Document title。
2. Stable Document Blocks 中的 heading、abstract-like paragraph、first meaningful paragraphs、caption、image_ocr 等块。
3. 迁移期无 Stable Document Blocks 时，使用 active base 的 reading units / Canonical Text Layer bounded preview。
4. 最后才使用 `reading_bases.text` 的短 preview。

普通纯文本：

- 使用源标题、前若干 reading units 或 base preview。
- preview 有明确字符上限。

超长文本：

- 只取标题、章节标题、前部有信息量段落和已存在摘要/section summary。
- 不把全文放进 title prompt。

PDF/OCR：

- 使用 artifact extraction / Stable Document Blocks 产出的结构化块。
- 优先 heading、caption、text-layer/OCR 的前部正文块。
- 若 OCR 噪声过高，状态可以失败为 `failed_retryable`，等待后续 extraction repair 或人工确认后重试。

调研依据：

- OpenAI cookbook 的长文档处理建议是分块/摘要后再做下游任务，避免一次性把长文档全部塞进模型。
- OpenAI Structured Outputs 适合把标题生成收敛为 typed output，避免自由文本说明污染结果。
- Google Document AI Layout Parser 和 Azure Document Intelligence Layout 都强调从 PDF/OCR 输入提取结构、段落、表格和布局信息；Claread 的标题生成应消费已归一化块，而不是绕过 extraction 直接读原始 PDF/OCR 全量文本。

## Snapshot/API 合同

`ReaderPlateSnapshot.record` 暴露：

- `display_title_zh`
- `title_generation_status`
- `title_generation_error_code`
- `title_generation_error_message`

规则：

- `display_title_zh` 只在 `title_generation_status = "succeeded"` 时返回。
- 如果 DB 中 `succeeded` 但标题为空，snapshot serializer 必须 fail closed。
- `failed_retryable` 表示标题缺失是可恢复状态，前端可展示重试入口或等待后台补偿，但不能用源标题冒充成功中文标题。

## 测试覆盖

已补后端测试覆盖：

- 成功生成中文标题并进入 snapshot `record.display_title_zh`。
- LLM 失败进入 `failed_retryable` 和 `retry_later`。
- 超长文本不会把全文传给标题生成器。
- schema baseline 校验 `succeeded` 必须有非空标题。
- pipeline runner / worker loop 把 display title 纳入首个 enhancement job。
