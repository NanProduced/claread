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


class NormalizedInputDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: InputAdapterSourceType
    title: str | None = None
    blocks: list[StableDocumentBlock] = Field(min_length=1)
    suitability: InputSuitabilityResult
    source_loss_flags: list[SourceLossFlag] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # Document-level parser identity triple (parser_name / parser_version /
    # profile) when the structured-source markdown parser produced the
    # blocks; ``None`` for the plain text path. Downstream freeze
    # persistence reads this to populate ``source_profile_json`` per
    # plan §4 G0 Clause 1 (parser identity written into document
    # metadata) — document-level metadata must not rely on block-level
    # inference.
    parser_identity: dict[str, str] | None = None
