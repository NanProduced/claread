from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.reader_documents import StableDocumentBlock

InputSuitabilityOutcome = Literal[
    "stable_document_ready",
    "candidate_document_required",
    "input_rejected_or_action_required",
]

InputAdapterSourceType = Literal[
    "pasted_text",
    "txt_file",
    "markdown_file",
    "ocr_text",
    "pdf_text",
    "url_text",
]

# L2 — 输入来源（source_type）与内容格式（detected_format）解耦。
#   * ``plain_text`` — 无 Markdown 块结构（parser 只产出 paragraph）。
#   * ``markdown``   — parser 检测到非 paragraph 块结构（heading / list /
#     blockquote / table / code_block / thematic_break），或来源显式声明
#     markdown（markdown_file）。
#   * ``rich_html``  — 保留值：富文本 HTML 输入（clipboard text/html），
#     当前后端尚无该来源，前端经 prepare-clipboard-html 归一后为 markdown。
# detected_format 由 MarkdownSourceParser 的块结构唯一决定，与
# source_type（pasted_text / markdown_file / artifact 来源）正交。
DetectedInputFormat = Literal[
    "plain_text",
    "markdown",
    "rich_html",
]

SourceLossFlag = Literal[
    "non_english_or_mixed_language",
    "too_short_for_learning",
    "too_long_requires_envelope",
    "layout_order_uncertain",
    "ocr_low_confidence",
    "table_structure_uncertain",
    "image_ocr_uncertain",
    "footnote_or_caption_merged",
    "document_block_degraded",
    "code_dominant",
    "link_list_dominant",
    "markdown_complex_structure",
]

SourceArtifactKind = Literal[
    "original_upload",
    "pdf_page_image",
    "ocr_result",
    "extracted_text",
    "webpage_snapshot",
    "derived_preview",
]

SourceArtifactStorageProvider = Literal["oss", "local"]

SourceArtifactStatus = Literal["pending", "available", "failed", "deleted"]

# L1 — Authoritative Normalization 三级分类（唯一的分类权威在服务端）。
#   * ``silent``            — 确定性、保义的规范化（用户无感）。
#   * ``adaptation_notice`` — 内容被清洗/安全降级但文档继续（非阻断提示）。
#   * ``content_check``     — 内容/边界/含义可能变化，必须 candidate 审查。
AdaptationClassification = Literal[
    "silent",
    "adaptation_notice",
    "content_check",
]


class AdaptationRecord(BaseModel):
    """One structured adaptation record from parser or gate.

    ``code`` reuses the parser warning code or the gate signal flag name;
    ``classification`` is the closed three-level set above.
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    message: str = ""
    classification: AdaptationClassification


class InputSuitabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: InputAdapterSourceType
    text: str
    filename: str | None = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class InputSuitabilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: InputSuitabilityOutcome
    source_type: InputAdapterSourceType
    word_count: int = Field(ge=0)
    english_word_ratio: float = Field(ge=0.0, le=1.0)
    natural_language_score: float = Field(ge=0.0, le=1.0)
    flags: list[SourceLossFlag] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    normalized_preview: str = ""
    # L1: structured three-level adaptation output (silent /
    # adaptation_notice / content_check). Parser warnings flow through
    # with their classification; gate-only signals (image / math / OCR /
    # code dominance / length / source-type defaults) are recorded as
    # content_check entries.
    adaptations: list[AdaptationRecord] = Field(default_factory=list)
    # L2: 内容格式检测结果（plain_text / markdown / rich_html），由
    # parser 块结构决定，与 source_type 正交。candidate / stable-ready
    # 两条路径的结构化构造统一以它为驱动。
    detected_format: DetectedInputFormat = "plain_text"


class NormalizedInputDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: InputAdapterSourceType
    title: str | None = None
    blocks: list[StableDocumentBlock] = Field(min_length=1)
    suitability: InputSuitabilityResult
    source_loss_flags: list[SourceLossFlag] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # L1: structured adaptation records mirrored from the suitability
    # result so the stable-ready freeze path can persist the three-level
    # classification into document metadata.
    adaptations: list[AdaptationRecord] = Field(default_factory=list)
    # Document-level parser identity triple (parser_name / parser_version /
    # profile) when the structured-source markdown parser produced the
    # blocks; ``None`` for the plain text path. Downstream freeze
    # persistence reads this to populate ``source_profile_json`` per
    # plan §4 G0 Clause 1 (parser identity written into document
    # metadata) — document-level metadata must not rely on block-level
    # inference.
    parser_identity: dict[str, str] | None = None
    # L2: 内容格式检测结果，镜像 gate 的判定（markdown 时 blocks 由
    # parser 产出；plain_text 时保持纯文本段落行为）。
    detected_format: DetectedInputFormat = "plain_text"
