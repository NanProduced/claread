# 文件上传→解析完整业务链路（Markdown 格式适配重点）

> 追踪范围：`init-upload → complete-upload → submit-input → extraction → materialization → stable_document`
> 工作区：`services/api`

---

## 1. 上传后入口：Upload API 契约

### 1.1 `init-upload`（初始化上传）

**路由**: `POST /source-artifacts/init-upload`  
**文件**: [app/api/routes/reader_orchestration.py](file://c:/Users/nanpr/claread/claread/services/api/app/api/routes/reader_orchestration.py#L504-L578)

**请求字段** (`ReaderSourceArtifactUploadInitRequest`):
```python
- artifact_kind: str  # 如 "original_upload"
- reading_record_id: UUID | None
- original_input_id: UUID | None
- content_type: str  # 如 "text/markdown", "text/plain", "application/pdf"
- byte_size: int | None
- content_sha256: str | None
- source_filename: str | None  # 如 "document.md"
- source_refs: dict[str, Any] | None
- metadata: dict[str, Any] | None
- quality: dict[str, Any] | None
```

**响应字段** (`ReaderSourceArtifactUploadInitResponse`):
```python
- artifact_id: UUID  # 生成的 artifact ID
- status: str  # "pending"
- object_key: str  # OSS 对象键
- presigned_url: str | None  # OSS 预签名 PUT URL
- content_type: str
- byte_size: int | None
- content_sha256: str | None
- source_filename: str | None
```

**关键逻辑**:
1. 调用 `SourceArtifactService.register_source_artifact()` 在 DB 中创建记录
2. 构建 OSS 对象引用（bucket、endpoint、object_key）
3. 如果配置了 presigner，生成预签名 URL；失败则回退到 `oss_put_object_pending_credentials`

---

### 1.2 `complete-upload`（完成上传）

**路由**: `POST /source-artifacts/{artifact_id}/complete-upload`  
**文件**: [app/api/routes/reader_orchestration.py](file://c:/Users/nanpr/claread/claread/services/api/app/api/routes/reader_orchestration.py#L588-L614)

**请求字段** (`ReaderSourceArtifactUploadCompleteRequest`):
```python
- content_type: str  # 必须与 init 时一致或更精确
- byte_size: int | None
- content_sha256: str | None
- metadata: dict[str, Any] | None  # 如 {"ocr_confidence": 0.95}
- quality: dict[str, Any] | None
```

**响应字段** (`ReaderSourceArtifactUploadCompleteResponse`):
```python
- artifact_id: UUID
- status: str  # "available"
- content_type: str
- byte_size: int | None
- content_sha256: str | None
```

**关键逻辑**:
1. 调用 `SourceArtifactService.complete_source_artifact_upload()`
2. 验证 content_type 兼容性（允许更精确，不允许冲突）
3. 更新 byte_size、content_sha256、metadata、quality
4. 状态从 "pending" → "available"

---

### 1.3 `submit-input`（提交输入）

**路由**: `POST /source-artifacts/{artifact_id}/submit-input`  
**文件**: [app/api/routes/reader_orchestration.py](file://c:/Users/nanpr/claread/claread/services/api/app/api/routes/reader_orchestration.py#L617-L643)

**请求字段** (`ReaderSourceArtifactSubmitInputRequest`):
```python
- title: str | None
- language: str | None  # 如 "en", "zh"
- client_record_id: str | None
- source_metadata: dict[str, Any] | None  # 如 {"ocr_confidence": 0.95, "layout_order_confidence": 0.92}
- reading_goal: str | None
- reading_variant: str | None
```

**响应字段** (`ReaderSourceArtifactSubmitInputResponse`):
```python
- reading_record_id: UUID
- original_input_id: UUID
- artifact_id: UUID
- pipeline_jobs: list[ReaderArtifactPipelineJobSummary]  # enqueued jobs
- suitability: InputSuitabilityResult | None  # 早期适用性评估
```

**关键逻辑**:
1. 调用 `ArtifactInputApplicationService.submit_available_artifact_as_input()`
2. 验证 artifact 状态为 "available"
3. 创建 `reading_records` 和 `original_inputs` 记录
4. **触发 artifact pipeline worker**（extraction + materialization）
5. 返回入队的工作 job 摘要

---

## 2. Artifact Pipeline Worker 启动装配

### 2.1 入口脚本

**文件**: [scripts/run_reader_artifact_pipeline_worker.py](file://c:/Users/nanpr/claread/claread/services/api/scripts/run_reader_artifact_pipeline_worker.py)

### 2.2 `build_storage_reader()` (L70-L106)

```python
def build_storage_reader(settings: Settings) -> StorageObjectReader | None:
    """Build an OSS storage reader from settings, or None (fail-closed)."""
    ak_id, ak_secret = settings.resolve_aliyun_oss_credentials()
    if not ak_id or not ak_secret:
        return None  # fail-closed
    
    try:
        import oss2
    except ImportError:
        return None  # fail-closed
    
    return AliyunOssObjectReader(
        access_key_id=ak_id,
        access_key_secret=ak_secret,
        bucket=settings.aliyun_oss_bucket,
        endpoint=settings.aliyun_oss_endpoint,
    )
```

**行为**:
- OSS 凭证缺失或 SDK 未安装 → 返回 `None`
- 返回 `None` 时，pipeline 使用 `UnconfiguredArtifactExtractionProvider`（fail-closed）
- 永远不会 crash

---

### 2.3 `build_default_extraction_provider_router()` (L113-L147)

**文件**: [artifact_extraction_provider_router.py](file://c:/Users/nanpr/claread/claread/services/api/app/services/reader_orchestration/artifact_extraction_provider_router.py#L113-L147)

```python
def build_default_extraction_provider_router(
    *,
    reader: StorageObjectReader,
    ocr_extractor: OcrTextExtractor | None = None,
    ocr_min_text_confidence: float = DEFAULT_MIN_TEXT_CONFIDENCE,
    ocr_min_layout_confidence: float = DEFAULT_MIN_LAYOUT_CONFIDENCE,
) -> ArtifactExtractionProviderRouter:
    text_provider = TextArtifactExtractionProvider(reader=reader)
    pdf_provider = PdfArtifactExtractionProvider(reader=reader)
    ocr_provider = OcrArtifactExtractionProvider(
        reader=reader,
        extractor=ocr_extractor,  # UnconfiguredOcrTextExtractor by default
        min_text_confidence=ocr_min_text_confidence,
        min_layout_confidence=ocr_min_layout_confidence,
    )
    return ArtifactExtractionProviderRouter(
        text_provider=text_provider,
        pdf_provider=pdf_provider,
        ocr_provider=ocr_provider,
    )
```

**装配依赖**:
- Text Provider: `TextArtifactExtractionProvider`（UTF-8 解码器）
- PDF Provider: `PdfArtifactExtractionProvider`（pypdf 提取器）
- OCR Provider: `OcrArtifactExtractionProvider`（DashScope Qwen OCR，可选）

---

## 3. ExtractionProviderRouter 路由规则

**文件**: [artifact_extraction_provider_router.py](file://c:/Users/nanpr/claread/claread/services/api/app/services/reader_orchestration/artifact_extraction_provider_router.py#L51-L110)

### 3.1 路由矩阵

| content_type | source_filename 扩展名 | 路由 Provider | 失败码 |
|-------------|---------------------|--------------|--------|
| `text/plain` | 任意 | `TextArtifactExtractionProvider` | - |
| `text/markdown` | 任意 | `TextArtifactExtractionProvider` | - |
| `text/x-markdown` | 任意 | `TextArtifactExtractionProvider` | - |
| `application/octet-stream` | `.txt` 或 `.md` | `TextArtifactExtractionProvider` | `unsupported_content_type` |
| `application/octet-stream` | 其他 | ❌ 拒绝 | `unsupported_content_type` |
| `application/pdf` | 任意 | `PdfArtifactExtractionProvider` | `unsupported_content_type` |
| `image/jpeg`, `image/png` | 任意 | `OcrArtifactExtractionProvider` | - |
| 其他 | 任意 | ❌ 拒绝 | `unsupported_artifact_content_type` |

### 3.2 路由逻辑 (L76-L110)

```python
async def extract(self, context: ArtifactExtractionJobContext) -> ArtifactExtractionResult:
    ct = (context.content_type or "").strip().lower().split(";")[0].strip()
    
    # 1. Text / markdown path
    if ct in SUPPORTED_CONTENT_TYPES:  # text/plain, text/markdown, text/x-markdown
        return await self._text_provider.extract(context)
    
    # 2. Octet-stream allowed only with .txt/.md extension
    if ct == "application/octet-stream":
        lower_name = context.source_filename.lower()
        if any(lower_name.endswith(ext) for ext in OCTET_STREAM_ALLOWED_EXTENSIONS):  # .txt, .md
            return await self._text_provider.extract(context)
    
    # 3. PDF path
    if ct == "application/pdf":
        return await self._pdf_provider.extract(context)
    
    # 4. Image path
    if ct.startswith("image/"):
        return await self._ocr_provider.extract(context)
    
    # 5. Unknown content type
    raise ArtifactExtractionError(
        f"unsupported artifact content_type {content_type!r}",
        retryable=False,
        failure_code=FAILURE_CODE_UNSUPPORTED_ARTIFACT_CONTENT_TYPE,  # "unsupported_artifact_content_type"
    )
```

### 3.3 支持的常量

```python
SUPPORTED_CONTENT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown"})
OCTET_STREAM_ALLOWED_EXTENSIONS = frozenset({".txt", ".md"})
```

---

## 4. Markdown 解析/规范化

### 4.1 Input Suitability Gate（适用性门控）

**文件**: [input_suitability_gate.py](file://c:/Users/nanpr/claread/claread/services/api/app/services/reader_orchestration/input_suitability_gate.py#L126-L349)

#### 4.1.1 `evaluate_input_suitability()` (L344-L349)

```python
def evaluate_input_suitability(
    request: InputSuitabilityRequest,
    *,
    preparsed: MarkdownParseResult | None = None,
) -> InputSuitabilityResult:
    return InputSuitabilityGate().evaluate(request, preparsed=preparsed)
```

#### 4.1.2 评估流程 (L129-L341)

1. **文本标准化** (L459-L463):
   ```python
   def _normalize_text(text: str) -> str:
       text = text.replace("\r\n", "\n").replace("\r", "\n")  # CRLF → LF
       text = re.sub(r"[ \t]+", " ", text)  # 合并空白
       text = re.sub(r"\n{3,}", "\n\n", text)  # 多于2个换行 → 2个
       return text.strip()
   ```

2. **Markdown 解析** (L163-L167):
   ```python
   parse_result = _MARKDOWN_PARSER.parse(normalized_text)
   ```

3. **内容格式检测** (L170-L173):
   ```python
   detected_format = detect_input_format(
       source_type=request.source_type,
       parse_result=parse_result,
   )
   ```
   - 如果有非 paragraph 块 → `markdown`
   - 只有段落 → `plain_text`

4. **代码主导检测** (L174, L702-L773):
   ```python
   code_metrics = _compute_code_structure_metrics(normalized_text, parse_result)
   is_code_dominant = _is_code_dominant(code_metrics)
   ```
   - Shebang (`#!`) 或 editor modeline (`vim:`, `-*- coding:`)
   - 无 prose 结构且代码行 ≥50%
   - 代码行 ≥80% 且 prose ≤1 个块

5. **质量检查**:
   | 检查项 | 阈值 | 失败结果 |
   |-------|------|---------|
   | English 词数 | <50 词 | `too_short_for_learning` → 拒绝 |
   | 总词数 | >8000 词 | `too_long_requires_envelope` → candidate |
   | English 词比例 | <70% | `non_english_or_mixed_language` → 拒绝 |
   | 链接主导 | link-only line ≥50% 或 URL≥4 且英文词≤URL×3 | `link_list_dominant` → 拒绝 |
   | 表格结构不确定 | `table_structure_uncertain` warning | `table_structure_uncertain` → candidate |
   | 图片 | 存在 `![alt](url)` | `image_ocr_uncertain` → candidate |
   | 脚注 | `footnote_reference` warning | `footnote_or_caption_merged` → candidate |
   | 数学公式 | `$...$` / `$$...$$` / `\(...\)` / `\[...]`（成对+LaTeX） | `document_block_degraded` → candidate |
   | 未闭合 fence | `has_unclosed_fence` warning | `document_block_degraded` → candidate |
   | OCR 置信度 | <0.85 | `ocr_low_confidence` → candidate |
   | 布局顺序不确定 | layout_confidence <0.90 或 `multi_column=True` | `layout_order_uncertain` → candidate |

6. **Outcome 判定** (L313-L321):
   ```python
   if reject_reasons:
       outcome = "input_rejected_or_action_required"
   elif candidate_reasons:
       outcome = "candidate_document_required"
   else:
       outcome = "stable_document_ready"
   ```

#### 4.1.3 Markdown 复杂度检测 (L537-L596)

```python
def _detect_markdown_complexity(...) -> _MarkdownComplexity:
    block_types = {block.block_type for block in parse_result.blocks}
    warning_codes = {warning.code for warning in parse_result.warnings}
    
    return _MarkdownComplexity(
        has_table="table" in block_types,
        has_table_structure_uncertain="table_structure_uncertain" in warning_codes,
        has_image=bool(_MARKDOWN_IMAGE_PATTERN.search(text)),
        has_footnote="footnote_reference" in warning_codes or "footnote" in block_types,
        has_raw_html="raw_html_block" in warning_codes or "inline_html" in warning_codes,
        has_math=_has_math_syntax(text),  # $...$ / $$...$$ / \(...\) / \[...\]（成对+LaTeX）
        has_unclosed_fence="has_unclosed_fence" in warning_codes,
        has_simple_markdown=is_markdown_source and bool(block_types & {"heading", "list", ...}),
        has_complex_structure=any([...]),
    )
```

#### 4.1.4 数学公式判定 (L519-L534)

```python
def _has_math_syntax(text: str) -> bool:
    # 单行公式：$...$ 本身即要求成对
    if _INLINE_MATH_PATTERN.search(text):
        return True
    # 块级公式：$$...$$ 本身即要求成对
    if _BLOCK_MATH_PATTERN.search(text):
        return True
    # 转义公式：\(...\) / \[...\] 必须成对且内容像公式
    for match in _ESCAPED_MATH_PAIR_PATTERN.finditer(text):
        inner = match.group(1) if match.group(1) is not None else match.group(2)
        if inner and _MATHLIKE_CONTENT_PATTERN.search(inner):
            return True
    return False
```

**注意**：单独的 `\[(Video)\]` 转义方括号、`\(2019)` 引用不再误判。

---

### 4.2 Markdown Source Parser（Markdown 源码解析器）

**文件**: [markdown_source_parser.py](file://c:/Users/nanpr/claread/claread/services/api/app/services/reader_orchestration/markdown_source_parser.py)

#### 4.2.1 身份标识 (L57-L59)

```python
PARSER_NAME = "markdown_it_py"
PARSER_VERSION = "v1"
PROFILE = "commonmark_gfm_v1"
```

#### 4.2.2 解析范围

- **语法支持**: CommonMark + GFM table + strikethrough + footnote (degraded)
- **块类型**: heading, paragraph, list, list_item, blockquote, table, table_row, table_cell, code_block, thematic_break, footnote
- **内联扁平化**: emphasis / strong / strikethrough / inline_code 被扁平化到父块 text_content
- **链接安全**: 协议白名单（http/https/mailto），不安全协议剥离但保留文本
- **Raw HTML**: html_block 聚合，inline HTML 剥离，可执行结构不进入 text

#### 4.2.3 诊断分类（三级分类）

| 分类 | 含义 | 影响 |
|-----|------|------|
| `silent` | 确定性、语义保留的规范化 | 用户不可见 |
| `adaptation_notice` | 内容被清理/降级，文档继续 | 非阻塞通知 |
| `content_check` | 内容/边界可能变化 | 路由到 candidate review |

#### 4.2.4 诊断消息

下表曾是早期摘录，code 名已过期（如 `unclosed_fence` / `footnote_ref` / `unsafe_link`）。**权威闭合集见 §9**。

#### 4.2.5 Parse 逻辑概要

```python
class MarkdownSourceParser:
    def parse(self, text: str) -> MarkdownParseResult:
        # 1. 规范化换行
        text = _normalize_newlines(text)
        
        # 2. markdown-it-py 解析
        md = MarkdownIt('commonmark').use(gfm_table_plugin).use(footnote_plugin)
        tokens = md.parse(text)
        
        # 3. Token → Block 转换
        blocks = []
        warnings = []
        for token in tokens:
            block = self._token_to_block(token)
            blocks.append(block)
            warnings.extend(self._extract_warnings(token, block))
        
        # 4. 诊断 + Outcome 路由
        has_content_check = any(w.classification == "content_check" for w in warnings)
        outcome = "candidate_document_required" if has_content_check else "stable_document_ready"
        
        return MarkdownParseResult(
            parser_name=PARSER_NAME,
            parser_version=PARSER_VERSION,
            profile=PROFILE,
            blocks=tuple(blocks),
            warnings=tuple(warnings),
            unsupported=tuple([]),
            outcome=outcome,
        )
```

---

### 4.3 Input Document Normalizer（输入文档规范化器）

**文件**: [input_document_normalizer.py](file://c:/Users/nanpr/claread/claread/services/api/app/services/reader_orchestration/input_document_normalizer.py)

#### 4.3.1 `normalize()` (L87-L185)

```python
def normalize(self, request: InputSuitabilityRequest, *, preparsed: MarkdownParseResult | None = None) -> NormalizedInputDocument:
    # 1. 适用性评估
    suitability = evaluate_input_suitability(request, preparsed=preparsed)
    if suitability.outcome != "stable_document_ready":
        raise InputDocumentNormalizationError(suitability=suitability)
    
    # 2. 源类型检查
    if request.source_type not in _SUPPORTED_SOURCE_TYPES:  # pasted_text, txt_file, markdown_file
        raise InputDocumentNormalizationError(...)
    
    # 3. 文本标准化
    source_text = _normalize_source_text(request.text)
    
    # 4. 解析（共享 parse result）
    parse_result = preparsed if preparsed else _MARKDOWN_PARSER.parse(source_text)
    
    # 5. 格式检测
    detected_format = detect_input_format(
        source_type=request.source_type,
        parse_result=parse_result,
    )
    
    # 6. 路径分支
    if request.source_type in _PLAIN_TEXT_SOURCE_TYPES:  # pasted_text, txt_file
        if detected_format == "markdown":
            # 升级为 typed-block markdown 路径
            drafts, title = _normalize_markdown_blocks(source_text, parse_result=parse_result)
            warnings.append("plaintext_upgraded_to_markdown")
        else:
            # Legacy plain-text 路径
            drafts, title = _normalize_plain_text_blocks_from_parser(parse_result)
    else:
        # markdown_file 始终走 markdown 路径
        drafts, title = _normalize_markdown_blocks(source_text, parse_result=parse_result)
    
    # 7. Block → StableDocumentBlock
    blocks = [_draft_to_block(draft, index, source_type, filename, used_markdown_parser) for index, draft in enumerate(drafts)]
    
    # 8. 语义分类
    blocks = attach_semantic_to_stable_blocks(blocks)
    
    return NormalizedInputDocument(
        source_type=request.source_type,
        title=title,
        blocks=blocks,
        suitability=suitability,
        ...
    )
```

#### 4.3.2 Plain Text 路径 (L200-L243)

```python
def _normalize_plain_text_blocks_from_parser(parse_result: MarkdownParseResult) -> tuple[list[_BlockDraft], str | None]:
    """Build plain-text drafts from parser paragraph blocks."""
    drafts = []
    for block in parse_result.blocks:
        if block.block_type != "paragraph":
            continue  # 跳过非段落块
        text_content = block.text_content.replace("\n", " ")  # 软换行 → 空格
        links = list(block.payload_json.get("links", []))
        drafts.append(_BlockDraft(
            block_type="paragraph",
            text_content=text_content,
            payload_json=dict(block.payload_json),
            line_start=block.source_range.line_start,
            line_end=block.source_range.line_end,
            links=links,
        ))
    return drafts, None  # 无标题
```

#### 4.3.3 Markdown 路径 (L246-L275)

```python
def _normalize_markdown_blocks(
    source_text: str,
    *,
    parse_result: MarkdownParseResult | None = None,
) -> tuple[list[_BlockDraft], str | None]:
    """Build typed-block drafts from parser blocks."""
    title = None
    drafts = []
    
    for block in result.blocks:
        # 第一个 heading 作为标题
        if title is None and block.block_type == "heading":
            title = block.text_content
        
        drafts.append(_BlockDraft(
            block_type=block.block_type,  # heading, paragraph, list, code_block, ...
            text_content=block.text_content,
            payload_json=dict(block.payload_json),
            line_start=block.source_range.line_start,
            line_end=block.source_range.line_end,
            links=[],
            parent_block_id=block.parent_block_id,
        ))
    
    return drafts, title
```

**支持的块类型**:
- `heading` → 标题
- `paragraph` → 段落
- `list` / `list_item` → 列表
- `blockquote` → 引用
- `table` / `table_row` / `table_cell` → 表格
- `code_block` → 代码块
- `thematic_break` → 水平线

**规范化行为**:
- 标题：第一个 heading 作为文档标题
- 列表：保留嵌套结构（通过 `parent_block_id`）
- 代码块：原始文本保留，payload_json 含语言信息
- 引用：文本保留，可能包含嵌套块
- 表格：确定性 GFM 表格冻结；结构不确定表路由到 candidate

---

## 5. 提取 Provider：Text Artifact Extraction

**文件**: [text_artifact_extraction_provider.py](file://c:/Users/nanpr/claread/claread/services/api/app/services/reader_orchestration/text_artifact_extraction_provider.py)

### 5.1 `TextArtifactExtractionProvider.extract()` (L254-L355)

```python
async def extract(self, context: ArtifactExtractionJobContext) -> ArtifactExtractionResult:
    # 1. Content type 门控
    if not _is_content_type_supported(content_type, source_filename, warnings):
        raise ArtifactExtractionError(
            retryable=False,
            failure_code=FAILURE_CODE_UNSUPPORTED_CONTENT_TYPE,  # "unsupported_content_type"
        )
    
    # 2. 下载字节
    read_result = await self._reader.read_object(
        bucket=context.bucket,
        endpoint=context.endpoint,
        object_key=context.object_key,
    )
    
    # 3. byte_size 验证
    if context.byte_size is not None and len(raw_bytes) != context.byte_size:
        raise ArtifactExtractionError(
            retryable=False,
            failure_code=FAILURE_CODE_BYTE_SIZE_MISMATCH,  # "byte_size_mismatch"
        )
    
    # 4. content_sha256 验证
    if context.content_sha256 is not None:
        actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        if actual_sha256 != context.content_sha256:
            raise ArtifactExtractionError(
                retryable=False,
                failure_code=FAILURE_CODE_SHA256_MISMATCH,  # "sha256_mismatch"
            )
    
    # 5. UTF-8 / BOM 解码
    text, encoding = _decode_utf8(raw_bytes)
    if text is None:
        raise ArtifactExtractionError(
            retryable=False,
            failure_code=FAILURE_CODE_DECODE_ERROR,  # "decode_error"
        )
    
    # 6. 空文本检查
    if not text.strip():
        raise ArtifactExtractionError(
            retryable=False,
            failure_code=FAILURE_CODE_EMPTY_TEXT,  # "extraction_empty_text"
        )
    
    return ArtifactExtractionResult(
        extracted_text=text,
        extractor_name=EXTRACTOR_NAME,  # "deterministic_text_artifact_extractor_v1"
        quality={
            "content_type": content_type,
            "source_filename": source_filename,
            "byte_size": len(raw_bytes),
            "content_sha256_verified": content_sha256_verified,
            "encoding": encoding,  # "utf-8" or "utf-8-bom"
        },
        warnings=warnings,
    )
```

### 5.2 失败码汇总

| 失败码 | 含义 | Retryable |
|-------|------|-----------|
| `unsupported_content_type` | content_type 不支持 | ❌ Non-retryable |
| `byte_size_mismatch` | byte_size 不匹配 | ❌ Non-retryable |
| `sha256_mismatch` | SHA-256 哈希不匹配 | ❌ Non-retryable |
| `decode_error` | UTF-8 解码失败 | ❌ Non-retryable |
| `extraction_empty_text` | 提取文本为空 | ❌ Non-retryable |
| `storage_read_error` | 存储读取失败 | ✅ Retryable |
| `oss_sdk_missing` | OSS SDK 未安装 | ❌ Non-retryable |
| `oss_object_not_found` | OSS 对象不存在 (404) | ❌ Non-retryable |
| `oss_access_denied` | OSS 访问被拒 (403) | ❌ Non-retryable |
| `oss_bucket_endpoint_mismatch` | Bucket/Endpoint 不匹配 | ❌ Non-retryable |
| `oss_network_error` | OSS 网络错误 | ✅ Retryable |
| `oss_error` | 其他 OSS 错误 | ✅ Retryable (保守默认) |

### 5.3 UTF-8/BOM 解码 (L391-L409)

```python
def _decode_utf8(data: bytes) -> tuple[str | None, str]:
    if data.startswith(b"\xef\xbb\xbf"):  # UTF-8 BOM
        try:
            text = data[3:].decode("utf-8")
            return text, "utf-8-bom"
        except UnicodeDecodeError:
            return None, ""
    
    try:
        text = data.decode("utf-8")
        return text, "utf-8"
    except UnicodeDecodeError:
        return None, ""
```

---

## 6. Materialization 阶段

**文件**: [extracted_artifact_materialization_service.py](file://c:/Users/nanpr/claread/claread/services/api/app/services/reader_orchestration/extracted_artifact_materialization_service.py)

### 6.1 `materialize_extracted_artifact()` (L449-L543)

```python
async def materialize_extracted_artifact(...) -> MaterializationResult:
    # 1. 加载 artifact、input、confirmed_source
    artifact_row = await self._repository.get_source_artifact(...)
    input_row = await self._repository.get_original_input(...)
    confirmed_source = await self._repository.get_confirmed_source_document(...)
    source_text = confirmed_source.markdown_text
    
    # 2. 派生 source_type
    source_type = _derive_source_type(artifact_row["content_type"], artifact_row["source_filename"])
    
    # 3. 构建suitability请求
    suitability_request = InputSuitabilityRequest(
        source_type=source_type,
        text=source_text,
        filename=filename,
        source_metadata=source_metadata,
    )
    
    # 4. 单次解析，共享给 gate + normalizer + candidate
    preparsed = _MARKDOWN_PARSER.parse(_normalize_source_text(source_text))
    suitability = evaluate_input_suitability(suitability_request, preparsed=preparsed)
    
    # 5. 分支
    if suitability.outcome == "stable_document_ready":
        return await self._materialize_stable(...)
    elif suitability.outcome == "candidate_document_required":
        return await self._materialize_candidate(...)
    else:
        return await self._materialize_rejected(...)
```

### 6.2 Stable Document 路径 (L549-L678)

```python
async def _materialize_stable(self, ...) -> MaterializationResult:
    # 1. Normalize → freeze plan → persist
    request = InputSuitabilityRequest(...)
    normalized = normalize_input_document(request, preparsed=preparsed)
    
    # 2. 构建 source profile
    source_profile_json = {
        "source_type": source_type,
        "filename": filename,
        "source_metadata": source_metadata,
        "suitability": {...},
        "materialization_source": "artifact_pipeline",
    }
    if normalized.title:
        source_profile_json["title"] = normalized.title
    
    # 3. 构建 freeze plan
    plan = build_stable_document_freeze_plan(
        reading_record_id=str(record_id),
        record_generation=generation,
        document_version=generation,
        title=normalized.title,
        blocks=normalized.blocks,
        source_profile_json=source_profile_json,
    )
    
    # 4. 持久化 freeze plan
    freeze_result = await persist_stable_document_freeze_plan(
        conn,
        plan=plan,
        canonicalizer_version=EXACT_CANONICAL_TEXT_VERSION,
        builder_version=DETERMINISTIC_READING_BASE_BUILDER_VERSION,
        segmenter_version=AUTO_SEGMENTER_POLICY,
        language=language,
        user_id=user_id,
        now=now,
    )
    
    # 5. 冻结 confirmed_source
    await freeze_confirmed_source(conn, confirmed_source.id, now=now)
    
    # 6. 设置 active_base 并标记 article_ready
    await self._repository.set_active_base_and_mark_article_ready(
        conn, record_id, freeze_result.base_id, generation, now
    )
    
    # 7. Article RAG index auto-ensure (fail-soft)
    rag_result = await self._get_auto_ensure_service().ensure_in_transaction(...)
    
    # 8. 发布 article_ready 事件
    event_envelope = await self._event_runtime.publish_event_in_transaction(...)
    
    return MaterializationResult(
        outcome="stable_document_ready",
        stable_document_id=freeze_result.stable_document_id,
        base_id=freeze_result.base_id,
        ...
    )
```

### 6.3 Reading Base 构建

**文件**: [base_builder.py](file://c:/Users/nanpr/claread/claread/services/api/app/services/reader_orchestration/base_builder.py)

#### 6.3.1 `build_low_impact_reading_base()` (L314-L329)

```python
def build_low_impact_reading_base(build_input: LowImpactReadingBaseBuildInput) -> ReadingBaseBuildResult:
    text = canonicalize_low_impact_text(build_input.source_text)
    if not text:
        raise ValueError("canonical low-impact text must not be empty")
    
    return _build_reading_base_core(
        reading_record_id=build_input.reading_record_id,
        base_id=build_input.base_id,
        text=text,
        title=build_input.title,
        language=build_input.language,
        canonicalizer_version=build_input.canonicalizer_version,
        builder_version=build_input.builder_version,
        segmenter_version=build_input.segmenter_version,
    )
```

#### 6.3.2 文本规范话 (L306-L311)

```python
def canonicalize_low_impact_text(source_text: str) -> str:
    text = source_text.replace("\r\n", "\n").replace("\r", "\n")
    text = _INVISIBLE_CHAR_PATTERN.sub("", text)  # 移除不可见字符
    text = text.translate(_UNICODE_SPACE_MAP)  # Unicode 空间映射
    text = _BLANK_LINE_RUN_PATTERN.sub("\n\n", text)  # 合并连续空行
    return text.strip()
```

#### 6.3.3 核心构建 (L408-L650)

```python
def _build_reading_base_core(...):
    # 1. 构建 UTF-16 前缀数组（用于 offset 计算）
    utf16_prefix = _build_utf16_prefix(text)
    
    # 2. 分割结构块（基于空行分隔）
    block_spans = _split_structure_blocks(text)
    
    # 3. 解析句子（spaCy 或 regex fallback）
    sentence_policy, spacy_pipeline = _resolve_sentence_policy(...)
    
    # 4. 遍历每个块
    for block_index, (char_start, char_end) in enumerate(block_spans):
        block_text = text[char_start:char_end]
        
        # 分割句子段
        segment_spans, sentence_provider = _build_segment_spans(
            block_text, sentence_policy=sentence_policy, spacy_pipeline=spacy_pipeline
        )
        
        # 构建单位
        unit = BuiltReadingUnit(
            unit_id=f"u{index}",
            char_start=char_start,
            char_end=char_end,
            segments=segment_spans,
            unit_type=_classify_unit_type(block_text),  # heading / paragraph / code / ...
            sentence_provider=sentence_provider,
        )
        units.append(unit)
        
        # 锚点段（heading / 首句）
        if unit.unit_type == "heading" or is_first_paragraph:
            anchor = BuiltAnchorSegment(...)
            anchor_segments.append(anchor)
    
    # 5. 验证
    validate_reading_base_build_result(build_result)
    
    return build_result
```

#### 6.3.4 单元类型分类

```python
def _classify_unit_type(block_text: str) -> str:
    if block_text.startswith("#"):
        return "heading"
    elif block_text.startswith("```"):
        return "code"
    elif block_text.startswith(">"):
        return "blockquote"
    elif re.match(r"^\d+\.", block_text):
        return "list"
    else:
        return "paragraph"
```

---

## 7. PDF 提取

**文件**: [pdf_artifact_extraction_provider.py](file://c:/Users/nanpr/claread/claread/services/api/app/services/reader_orchestration/pdf_artifact_extraction_provider.py)

### 7.1 `PdfArtifactExtractionProvider.extract()` (L141-L263)

```python
async def extract(self, context: ArtifactExtractionJobContext) -> ArtifactExtractionResult:
    # 1. Content type 门控
    ct = (context.content_type or "").strip().lower().split(";")[0].strip()
    if ct != "application/pdf":
        raise ArtifactExtractionError(
            retryable=False,
            failure_code=FAILURE_CODE_UNSUPPORTED_CONTENT_TYPE,
        )
    
    # 2. 下载字节
    read_result = await self._reader.read_object(...)
    
    # 3. byte_size 验证
    if context.byte_size is not None and len(raw_bytes) != context.byte_size:
        raise ArtifactExtractionError(retryable=False, failure_code="byte_size_mismatch")
    
    # 4. content_sha256 验证
    if context.content_sha256 is not None:
        if hashlib.sha256(raw_bytes).hexdigest() != context.content_sha256:
            raise ArtifactExtractionError(retryable=False, failure_code="sha256_mismatch")
    
    # 5. 提取文本
    extraction = self._extractor.extract_text(raw_bytes)  # PypdfPdfTextExtractor
    
    # 6. 合并页面
    pages = extraction.pages
    extracted_text = "\n\n".join(pages).strip()
    
    # 7. 非空检查
    if not extracted_text:
        raise ArtifactExtractionError(
            retryable=False,
            failure_code=FAILURE_CODE_PDF_NO_EXTRACTABLE_TEXT,  # "pdf_no_extractable_text"
        )
    
    # 8. 质量警告
    warnings = []
    if empty_pages:
        warnings.append(f"empty_pages: {len(empty_pages)} of {page_count} pages")
    if len(extracted_text) / len(raw_bytes) < 0.05:
        warnings.append(f"low_text_density: {len(extracted_text)} chars from {len(raw_bytes)} bytes")
    
    return ArtifactExtractionResult(
        extracted_text=extracted_text,
        extractor_name=EXTRACTOR_NAME,  # "deterministic_pdf_text_extractor_v1"
        quality={
            "content_type": content_type,
            "page_count": page_count,
            "extractor_name": extraction.extractor_name,
            "has_extractable_text": True,
            "byte_size": len(raw_bytes),
            "content_sha256_verified": content_sha256_verified,
        },
        warnings=warnings if warnings else None,
    )
```

### 7.2 PypdfPdfTextExtractor (L80-L119)

```python
class PypdfPdfTextExtractor:
    def extract_text(self, data: bytes) -> PdfTextExtractionResult:
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ArtifactExtractionError(
                retryable=False,
                failure_code=FAILURE_CODE_PDF_EXTRACTOR_UNAVAILABLE,  # "pdf_extractor_unavailable"
            )
        
        try:
            reader = PdfReader(io.BytesIO(data))
            pages = []
            for page in reader.pages:
                text = page.extract_text() or ""
                pages.append(text)
        except Exception as exc:
            raise ArtifactExtractionError(
                retryable=False,
                failure_code=FAILURE_CODE_PDF_EXTRACTION_ERROR,  # "pdf_extraction_error"
            )
        
        return PdfTextExtractionResult(pages=pages, extractor_name=EXTRACTOR_NAME)
```

### 7.3 PDF 失败码汇总

| 失败码 | 含义 | Retryable |
|-------|------|-----------|
| `unsupported_content_type` | content_type 不是 application/pdf | ❌ Non-retryable |
| `pdf_extractor_unavailable` | pypdf SDK 未安装 | ❌ Non-retryable |
| `pdf_extraction_error` | pypdf 解析失败 | ❌ Non-retryable |
| `pdf_no_extractable_text` | PDF 无可提取文本（扫描/图片-only） | ❌ Non-retryable |
| `byte_size_mismatch` | byte_size 不匹配 | ❌ Non-retryable |
| `sha256_mismatch` | SHA-256 不匹配 | ❌ Non-retryable |
| `storage_read_error` | 存储读取失败 | ✅ Retryable |

---

## 8. Markdown 适配总结

### 8.1 支持的 Markdown 格式

| 格式 | 支持级别 | 处理方式 |
|-----|---------|---------|
| 标题 (H1-H6) | ✅ 完全支持 | 第一个作为文档标题，其余作为 heading 块 |
| 段落 | ✅ 完全支持 | paragraph 块 |
| 列表 (有序/无序) | ✅ 完全支持 | list + list_item 块，保留嵌套 |
| 引用 (blockquote) | ✅ 完全支持 | blockquote 块，可能包含嵌套块 |
| 代码块 ( fenced) | ✅ 完全支持 | code_block 块，保留语言标识 |
| 行内代码 | ✅ 完全支持 | 扁平化到 text_content |
| 粗体/斜体 | ✅ 完全支持 | 扁平化到 text_content |
| 删除线 | ✅ 支持 (GFM) | 扁平化，发出 adaptation_notice |
| GFM 表格 | ✅ 确定性冻结 | table + table_row + table_cell 块 |
| 表格结构不确定 | ⚠️ 路由到 candidate | table_structure_uncertain warning |
| 链接 (http/https/mailto) | ✅ 完全支持 | 扁平化文本，payload_json 存 links |
| 不安全链接 (javascript/data) | ⚠️ 剥离但保留文本 | unsafe_link adaptation_notice |
| 图片 | ⚠️ 路由到 candidate | `image_ocr_uncertain` content_check（gate） |
| 脚注 | ⚠️ 部分支持 | `footnote_reference` content_check |
| Raw HTML 块 | ⚠️ 剥离可执行结构 | `raw_html_block` adaptation_notice |
| Raw HTML 内联 | ⚠️ 剥离 | `inline_html` adaptation_notice |
| 数学公式 ($...$) | ⚠️ 路由到 candidate | `document_block_degraded` content_check（gate） |
| 数学公式 (\(...\)) | ✅ 成对+LaTeX 才识别 | 避免误判普通括号 |
| 未闭合 fence | ⚠️ 路由到 candidate | `has_unclosed_fence` content_check |
| 任务列表 (GFM) | ⚠️ 保留可见标记 | `task_list_unsupported` content_check |

### 8.2 拒绝的 Markdown 输入

| 拒绝原因 | Failure Code | Outcome |
|---------|--------------|---------|
| 文本为空 | `extraction_empty_text` | ❌ 拒绝 |
| UTF-8 解码失败 | `decode_error` | ❌ 拒绝 |
| English 词 <50 | `too_short_for_learning` | ❌ 拒绝 |
| English 词比例 <70% | `non_english_or_mixed_language` | ❌ 拒绝 |
| 链接主导 (≥50% link-only line) | `link_list_dominant` | ❌ 拒绝 |
| 代码主导 + 无散文结构 | `code_dominant` | ⚠️ Candidate |
| 词数 >8000 | `too_long_requires_envelope` | ⚠️ Candidate |

### 8.3 规范化行为

| 输入 | 规范化后 |
|-----|---------|
| `\r\n` / `\r` | `\n` |
| 多个连续空白 | 单个空格 |
| 3+ 连续空行 | 2 个空行 |
| Plain text 但有标题/列表结构 | 升级为 markdown 路径，发出 `plaintext_upgraded_to_markdown` 警告 |
| UTF-8 BOM | 剥离，encoding 记为 `"utf-8-bom"` |
| Soft line break (`\n` in paragraph) | Plain text 路径：→ 空格；Markdown 路径：保留 |

### 8.4 数据流图

```
Upload File (MD/PDF/TXT)
    ↓
init-upload → DB: source_artifacts (status="pending")
    ↓
Client: PUT to OSS (presigned URL)
    ↓
complete-upload → DB: source_artifacts (status="available", content_type, byte_size, sha256)
    ↓
submit-input → DB: reading_records + original_inputs
             → Enqueue: artifact_extraction_job
    ↓
[Artifact Pipeline Worker]
    ↓
Extraction Router (by content_type)
    ├─ text/markdown → TextArtifactExtractionProvider
    │   ├─ Download from OSS
    │   ├─ Validate: byte_size, sha256
    │   ├─ Decode: UTF-8 (strip BOM)
    │   └─ Output: ArtifactExtractionResult (extracted_text)
    │
    ├─ application/pdf → PdfArtifactExtractionProvider
    │   ├─ Download from OSS
    │   ├─ Validate: byte_size, sha256
    │   ├─ Extract: pypdf (per-page text)
    │   └─ Output: ArtifactExtractionResult (pages joined by "\n\n")
    │
    └─ image/* → OcrArtifactExtractionProvider
        ├─ Download from OSS
        ├─ Validate: image type
        ├─ Extract: DashScope Qwen OCR
        └─ Output: ArtifactExtractionResult (OCR text + confidence)
    ↓
Persist: confirmed_source_documents (markdown_text)
    ↓
Enqueue: artifact_materialization_job
    ↓
[Materialization Worker]
    ↓
evaluate_input_suitability() ← MarkdownSourceParser (single parse)
    ├─ stable_document_ready → _materialize_stable()
    │   ├─ normalize_input_document() ← MarkdownSourceParser (shared)
    │   ├─ build_stable_document_freeze_plan()
    │   ├─ persist_stable_document_freeze_plan()
    │   │   ├─ Stable Document Blocks
    │   │   ├─ Canonical Text Layer
    │   │   └─ Reading Base (build_low_impact_reading_base)
    │   │       ├─ UTF-16 prefix array
    │   │       ├─ Structure block split
    │   │       ├─ Sentence segmentation (spaCy / regex)
    │   │       ├─ Unit classification (heading/paragraph/code/...)
    │   │       └─ Anchor segments (headings + first paragraph)
    │   ├─ set_active_base_and_mark_article_ready()
    │   ├─ Article RAG index ensure
    │   └─ Publish: article_ready event
    │
    ├─ candidate_document_required → _materialize_candidate()
    │   ├─ Build candidate blocks (parser shared)
    │   └─ Create candidate_document with parse results
    │
    └─ input_rejected_or_action_required → _materialize_rejected()
        └─ Mark reading_record as action_required
```

---

## 9. content_check 权威闭合集

> 盘点日期：2026-08-17。来源以代码为准，不是 §4.2.4 旧表。  
> **前端合同**：`code` / `message` / `classification` 三字段冻结。`message` 是英文开发诊断，BFF 保留透传供「技术详情」与调试；常规 UI 禁止直接渲染 `message`。用户文案以前端 `apps/web/src/app/(private)/app/read/content-check-guidance.ts` 的 `GUIDANCE_BY_CODE` 为单一真相源。  
> **不改判定**：`pdf_text` 默认 candidate 保持。`pdf_text` 是 `source_type`，不是 adaptation `code`。

### 9.1 谁产出 AdaptationRecord

| 层 | 文件 | 角色 |
|----|------|------|
| Parser | `markdown_source_parser.py` | 按 flag 发出 `DiagnosticWarning`（自带 classification） |
| Gate | `input_suitability_gate.py` `_build_adaptations` | parser warning 原样流入；gate-only 信号一律 `content_check` |
| OCR / PDF provider | `ocr_artifact_extraction_provider.py` / `pdf_artifact_extraction_provider.py` | **不**发 AdaptationRecord；只写字符串 warning / quality。gate 读 metadata 后再编码 |
| Materialization | `extracted_artifact_materialization_service.py` | **不**发新 code；把 `suitability.adaptations` 写入 `quality_json` |

### 9.2 content_check（黄卡 / candidate 路由）

`tier` 供前端分层：`routine` = 常规过目；`attention` = 高影响风险。

| code | 来源 | 触发 | 当前英文 message | tier |
|------|------|------|------------------|------|
| `source_type_review_default` | gate | `pdf_text` / `url_text` 默认 candidate（除非显式高置信且文本明显简单）；`ocr_text` 恒为 true | `{source_type} defaults to candidate review unless extraction confidence is explicitly high and the text is clearly simple.` | routine |
| `ocr_low_confidence` | gate | OCR 低置信、噪声文本，或 metadata / extractor warning 喂入 | `OCR confidence or text noise suggests degraded extraction quality.` | routine |
| `image_ocr_uncertain` | gate | Markdown 含图片 | `Markdown image blocks require candidate review so media truth is not lost.` | routine |
| `document_block_degraded` | gate | 检出数学公式语法 | `Math syntax requires candidate review instead of deterministic downgrade.` | routine |
| `footnote_reference` | parser | 脚注引用 | `Footnote reference encountered; the reference marker is dropped from body text while the definition is captured as a footnote block.` | routine |
| `task_list_unsupported` | parser | GFM task list | `GFM task-list checkbox state is preserved as visible text but task-list semantics are not supported; candidate review is required.` | routine |
| `has_unclosed_fence` | parser | 未闭合围栏 | `Fenced code block is missing its closing fence; captured as code_block but requires candidate review for boundary correctness.` | attention |
| `table_structure_uncertain` | parser | 表格行列与表头不对齐 | `Table row/column structure does not match the header definition; cells would be dropped or padded during deterministic normalization.` | attention |
| `missing_source_range` | parser | token 无 source range | `Parser token missing source range; requires candidate review for boundary correctness.` | attention |
| `layout_order_uncertain` | gate | 阅读顺序 / 版面置信不足（OCR warning 或 metadata） | `Reading order or layout confidence is too uncertain for direct freeze.` | attention |
| `code_dominant` | parser + gate | 代码主导、无散文结构 | parser：`Input is code-dominant with no narrative blocks; rejected from stable document freeze, action required.` / gate：`Input appears to be code-dominant without Markdown prose structure.` | attention |
| `too_long_requires_envelope` | gate | 词数 > 8000 | `Input is too long to process as a single low-impact stable document.` | attention |
| `unclosed_html_aside` | parser | `<aside>` 未闭合 | `HTML <aside> opening tag has no matching closing tag; the wrapper was removed and visible content was downgraded for candidate review.` | attention |

### 9.3 adaptation_notice / silent（通知轨，非黄卡）

| code | classification | 触发 | 当前英文 message |
|------|----------------|------|------------------|
| `raw_html_block` | adaptation_notice | Raw HTML 块 | `Raw HTML block detected; executable structure removed, text preserved as a plain paragraph.` |
| `inline_html` | adaptation_notice | 行内 HTML 剥离 | `Inline HTML tag stripped from paragraph text.` |
| `unsafe_link_protocol` | adaptation_notice | 不安全协议链接 | `Links with unsafe protocols (javascript/data/vbscript) were stripped from paragraph text; link text preserved.` |
| `definition_list_degraded` | adaptation_notice | 定义列表降级为纯文本 | `Definition-list syntax is preserved as plain text; definition-list structure is not supported in the first phase.` |
| `mermaid_static_only` | adaptation_notice | mermaid 代码块只存静态文本 | `Mermaid code block is stored as static text; diagram is not rendered or executed.` |
| `strikethrough_extension` | silent | 删除线当纯文本捕获 | `Strikethrough syntax captured as plain text; rendering is preserved.` |

### 9.4 过期别名（后端不下发，前端不要映射）

`unclosed_fence`（现 `has_unclosed_fence`）、`footnote_ref`（现 `footnote_reference`）、`unsafe_link`（现 `unsafe_link_protocol`）、`image_content`、`math_content`、`pdf_text`（这是 `source_type`，对应 code 是 `source_type_review_default`）。

### 9.5 extractor 字符串 warning（不是 AdaptationRecord.code）

这些出现在 artifact `warnings[]` / quality，由 gate 再编码：

- OCR：`ocr_low_confidence: …`、`layout_order_uncertain: …`、`empty_regions`
- PDF：`empty_pages: …`、`low_text_density: …`

失败码（`pdf_no_extractable_text` 等）走 extraction failure，不进 `content_check`。

---

## 10. 关键类/函数索引

| 功能 | 文件 | 类/函数 |
|-----|------|---------|
| Upload Init | `app/api/routes/reader_orchestration.py` | `init_reader_source_artifact_upload()` |
| Upload Complete | `app/api/routes/reader_orchestration.py` | `complete_reader_source_artifact_upload()` |
| Submit Input | `app/api/routes/reader_orchestration.py` | `submit_reader_source_artifact_as_input()` |
| Worker Entry | `scripts/run_reader_artifact_pipeline_worker.py` | `build_storage_reader()`, `build_pipeline_service()` |
| Router | `artifact_extraction_provider_router.py` | `ArtifactExtractionProviderRouter`, `build_default_extraction_provider_router()` |
| Text Provider | `text_artifact_extraction_provider.py` | `TextArtifactExtractionProvider.extract()` |
| PDF Provider | `pdf_artifact_extraction_provider.py` | `PdfArtifactExtractionProvider.extract()` |
| OCR Provider | `ocr_artifact_extraction_provider.py` | `OcrArtifactExtractionProvider.extract()` |
| Suitability Gate | `input_suitability_gate.py` | `evaluate_input_suitability()` |
| Markdown Parser | `markdown_source_parser.py` | `MarkdownSourceParser.parse()` |
| Normalizer | `input_document_normalizer.py` | `InputDocumentNormalizer.normalize()` |
| Materialization | `extracted_artifact_materialization_service.py` | `ExtractedArtifactMaterializationService.materialize_extracted_artifact()` |
| Freeze Plan | `document_freeze_plan.py` | `build_stable_document_freeze_plan()` |
| Reading Base | `base_builder.py` | `build_low_impact_reading_base()`, `_build_reading_base_core()` |
| Semantic Classifier | `semantic_classifier.py` | `attach_semantic_to_stable_blocks()` |

---

## 11. 测试设计建议

### 10.1 单元测试覆盖

#### Upload API
- [ ] init-upload: valid request → returns presigned URL
- [ ] init-upload: missing OSS credentials → falls back to pending-credentials
- [ ] complete-upload: valid upload → status="available"
- [ ] complete-upload: content_type mismatch → raises conflict error
- [ ] submit-input: available artifact → creates reading_record + enqueues job
- [ ] submit-input: unavailable artifact → raises not found error

#### Extraction Router
- [ ] text/markdown → routes to TextArtifactExtractionProvider
- [ ] text/plain → routes to TextArtifactExtractionProvider
- [ ] application/octet-stream + .md → routes to TextArtifactExtractionProvider
- [ ] application/octet-stream + .xyz → fails with unsupported_content_type
- [ ] application/pdf → routes to PdfArtifactExtractionProvider
- [ ] image/png → routes to OcrArtifactExtractionProvider
- [ ] application/json → fails with unsupported_artifact_content_type

#### Text Provider
- [ ] Valid UTF-8 markdown → extracts text
- [ ] UTF-8 BOM → strips BOM, encoding="utf-8-bom"
- [ ] Binary content → fails with decode_error
- [ ] Empty text → fails with extraction_empty_text
- [ ] byte_size mismatch → fails with byte_size_mismatch
- [ ] sha256 mismatch → fails with sha256_mismatch
- [ ] OSS 404 → fails with oss_object_not_found
- [ ] OSS network error → fails with oss_network_error (retryable)

#### Suitability Gate
- [ ] 50+ English words, simple → stable_document_ready
- [ ] <50 English words → too_short_for_learning
- [ ] <70% English ratio → non_english_or_mixed_language
- [ ] ≥50% link-only lines → link_list_dominant
- [ ] Has image → image_ocr_uncertain → candidate
- [ ] Has math formula → document_block_degraded → candidate
- [ ] Unclosed fence → document_block_degraded → candidate
- [ ] Code-dominant (shebang) → code_dominant → candidate
- [ ] >8000 words → too_long_requires_envelope → candidate

#### Markdown Parser
- [ ] Headings → heading blocks, first as title
- [ ] Nested lists → list + list_item with parent_block_id
- [ ] Fenced code blocks → code_block with language
- [ ] GFM table (deterministic) → table blocks
- [ ] GFM table (uncertain) → table_structure_uncertain warning
- [ ] Unsafe links (javascript:) → stripped, unsafe_link warning
- [ ] Raw HTML blocks → raw_html_block warning
- [ ] Inline math $E=mc^2$ → has_math → candidate
- [ ] Escaped bracket \(2019\) → NOT detected as math

#### Normalizer
- [ ] markdown_file → always markdown path
- [ ] pasted_text + paragraphs → plain-text path
- [ ] pasted_text + headings → upgrades to markdown path
- [ ] Plain text soft line breaks → joined with space
- [ ] Markdown blocks → preserved with types

#### Materialization
- [ ] stable_document_ready → creates stable_document + reading_base
- [ ] candidate_document_required → creates candidate_document
- [ ] input_rejected → marks action_required

### 10.2 集成测试场景

- [ ] Full flow: upload .md → extract → normalize → stable_document
- [ ] Full flow: upload .txt → extract → upgrade to markdown → stable_document
- [ ] Full flow: upload .pdf → extract → stable_document
- [ ] Full flow: upload image → OCR → candidate (if OCR enabled)
- [ ] Full flow: upload binary → decode_error → rejected
- [ ] Full flow: upload empty file → extraction_empty_text → rejected

### 10.3 Edge Cases

- [ ] Markdown with UTF-8 BOM
- [ ] Markdown with mixed CRLF/LF
- [ ] Markdown with only whitespace
- [ ] Markdown with very long lines (>10K chars)
- [ ] Markdown with deeply nested lists (10+ levels)
- [ ] Markdown with nested code blocks in quotes
- [ ] Markdown with LaTeX equations (inline + block)
- [ ] Markdown with footnotes and references
- [ ] Markdown with raw HTML mixed with Markdown
- [ ] PDF with mixed text/image pages
- [ ] PDF with no extractable text (scanned)
- [ ] Octet-stream with .md extension
- [ ] content_type with charset suffix ("text/plain; charset=utf-8")

---

**文档版本**: v1.0  
**创建时间**: 2026-08-17  
**追踪范围**: Claread API Service - File Upload → Parse Chain  
**关键依赖**: markdown-it-py, pypdf, oss2, spaCy (optional)
