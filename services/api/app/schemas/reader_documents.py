"""Stable Document Block domain contract.

This module defines the typed contracts for the "Stable Document Block
后端合同落地":

    - CandidateReadingDocument  (reviewable document in
      `needs_confirmation` product state)
    - StableReadingDocument     (immutable document truth per
      (reading_record_id, record_generation))
    - StableDocumentBlock       (ordered, addressable block under a stable
      document; tables / images / footnotes / code blocks stay as
      first-class blocks, never silently flattened)

Scope: schema/domain contract landing only. This module does NOT:
    - bind to a web framework,
    - bind to an API route,
    - implement the Candidate Document confirm flow,
    - implement input adapters / OCR / PDF / Markdown parsers,
    - implement block-scoped RAG indexing.

The Canonical Text Layer is intentionally NOT modeled as a separate table
here. V1 keeps `reading_bases.text` as the transitional carrier and uses
UTF-16 offsets to map into it; see
docs/architecture/reader-orchestration.md
section "Canonical Text Layer transition".
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CandidateReadingDocumentStatus = Literal[
    "ready",
    "confirmed",
    "rejected",
    "superseded",
]

StableReadingDocumentStatus = Literal["active", "superseded"]

# L2 — Confirmed Source 生命周期（migration 0025）。
# 每个 (reading_record_id, record_generation) 至多一行；markdown_text 是
# 该 generation 全库唯一一份完整正文（规范化后文本）。
ConfirmedSourceDocumentStatus = Literal["draft", "frozen"]

ConfirmedSourceEditSource = Literal[
    "initial",
    "extraction",
    "wysiwyg",
    "source_mode",
    "content_check",
]

# Mirrors the CHECK constraint on stable_document_blocks.block_type.
StableDocumentBlockType = Literal[
    "paragraph",
    "heading",
    "list",
    "list_item",
    "blockquote",
    "table",
    "table_row",
    "table_cell",
    "footnote",
    "image",
    "image_ocr",
    "caption",
    "code_block",
    "thematic_break",
    "unknown",
]

# Mirrors interpretation_policy_json.allowed_source_scope values used by
# docs/architecture/reader-rag.md and apps/web/docs/reader-ia.md.
StableDocumentSourceScope = Literal[
    "main_reading_text",
    "heading",
    "table_cell",
    "image_ocr",
    "footnote",
    "code_block",
    "published_layer",
]

# Mirrors interpretation_policy_json.default_route.
StableDocumentInterpretationRoute = Literal[
    "main_reading",
    "rag_ask_only",
    "metadata_only",
    "ignored",
]

# Block types whose structural truth lives in payload_json (tables /
# images / code). For these, text_content may be NULL even though they
# still participate in the document.
_STRUCTURAL_BLOCK_TYPES = frozenset(
    {"list", "table", "table_row", "table_cell", "image", "code_block", "thematic_break", "unknown"}
)


# Per-block-type interpretation policy defaults. Mirrors the
# projection rules in
# apps/web/docs/reader-ia.md
# and the RAG scope taxonomy in docs/architecture/reader-rag.md. The textual narrative
# blocks (paragraph / heading / list_item / blockquote / caption) flow
# into main grammar / sentence analysis on first freeze. Since the
# Markdown ecosystem policy treats code_block and the table hierarchy
# are also first-class main-reading content so the reading surface can
# render them. image / image_ocr / footnote / unknown still MUST NOT
# silently enter the main reading chain by default.
_DEFAULT_POLICY_BY_BLOCK_TYPE: dict[str, StableDocumentInterpretationPolicy] = {
    # Narrative blocks -> main reading, scope = main_reading_text.
    "paragraph": dict(
        allowed_source_scope=["main_reading_text"],
        default_route="main_reading",
        rag_eligible=True,
    ),
    "list": dict(
        # List wrapper block (text_content is null); its child list_item
        # blocks carry the narrative text. The wrapper itself routes
        # through main_reading so the list structure participates in the
        # document tree, but RAG indexing targets the list_item children.
        allowed_source_scope=["main_reading_text"],
        default_route="main_reading",
        rag_eligible=True,
    ),
    "list_item": dict(
        allowed_source_scope=["main_reading_text"],
        default_route="main_reading",
        rag_eligible=True,
    ),
    "blockquote": dict(
        allowed_source_scope=["main_reading_text"],
        default_route="main_reading",
        rag_eligible=True,
    ),
    "caption": dict(
        # Caption is textual and user-facing, but it annotates a
        # structural block (image / table). It still routes through
        # main reading because the caption often carries narrative
        # value the main grammar pass should consider.
        allowed_source_scope=["main_reading_text"],
        default_route="main_reading",
        rag_eligible=True,
    ),
    # Heading -> main reading, scope = heading so RAG / Plate can
    # distinguish "this came from a heading" from body text.
    "heading": dict(
        allowed_source_scope=["heading"],
        default_route="main_reading",
        rag_eligible=True,
    ),
    # Table structural blocks -> main reading by default (Markdown
    # ecosystem policy): tables are first-class reading content
    # and must flow into canonical text so the reading surface can
    # render them. The table / table_row wrapper blocks carry no
    # text_content (the narrative text lives in the table_cell
    # children), so the freeze plan skips them when deriving canonical
    # text — the same treatment as the ``list`` wrapper. rag_eligible
    # stays False for the wrappers: RAG targets the table_cell leaves.
    # A caller-supplied policy may still demote any table block back to
    # metadata_only / rag_ask_only explicitly.
    "table": dict(
        allowed_source_scope=["table_cell"],
        default_route="main_reading",
        rag_eligible=False,
    ),
    "table_row": dict(
        allowed_source_scope=["table_cell"],
        default_route="main_reading",
        rag_eligible=False,
    ),
    "table_cell": dict(
        allowed_source_scope=["table_cell"],
        default_route="main_reading",
        rag_eligible=True,
    ),
    "image": dict(
        allowed_source_scope=["image_ocr"],
        default_route="metadata_only",
        rag_eligible=False,
    ),
    "image_ocr": dict(
        allowed_source_scope=["image_ocr"],
        default_route="rag_ask_only",
        rag_eligible=True,
    ),
    "footnote": dict(
        allowed_source_scope=["footnote"],
        default_route="rag_ask_only",
        rag_eligible=True,
    ),
    "code_block": dict(
        # Code blocks are first-class reading content (Markdown
        # ecosystem policy): they route into main reading so the
        # reading surface can render them; rag_eligible stays True so
        # they remain retrievable via the main RAG path. A
        # caller-supplied policy may still demote a code block back to
        # rag_ask_only explicitly.
        allowed_source_scope=["code_block"],
        default_route="main_reading",
        rag_eligible=True,
    ),
    "thematic_break": dict(
        # Thematic break (hr) is a structural separator with no text
        # content; routes to metadata_only so it does not enter the
        # main grammar pass or RAG.
        allowed_source_scope=["published_layer"],
        default_route="metadata_only",
        rag_eligible=False,
    ),
    "unknown": dict(
        # Conservative default: do not feed the main chain, do not
        # feed RAG either. The Candidate Document editor must
        # explicitly promote an unknown block to a typed block +
        # policy before it becomes reachable from main reading /
        # Ask / RAG.
        allowed_source_scope=["published_layer"],
        default_route="metadata_only",
        rag_eligible=False,
    ),
}


def default_interpretation_policy_for(
    block_type: StableDocumentBlockType,
) -> StableDocumentInterpretationPolicy:
    """Return the per-block-type default StableDocumentInterpretationPolicy.

    Callers should NOT mutate the returned instance; if a caller needs
    a modified policy they should construct a new
    StableDocumentInterpretationPolicy explicitly. The defaults mirror
    the projection rules:

        * paragraph / list_item / blockquote / caption / heading ->
          main_reading (caption uses main_reading_text, heading uses
          the dedicated heading scope)
        * table / table_row / table_cell / code_block -> main_reading
          (Markdown ecosystem policy: code/table are first-class
          reading content; the table / table_row wrappers carry no
          text_content and are skipped during canonical text
          derivation, and stay rag_eligible=False — RAG targets the
          table_cell leaves)
        * image_ocr / footnote -> rag_ask_only
        * image / unknown -> metadata_only, with a conservative scope
          ("published_layer" for unknown since no closer match exists;
          "image_ocr" for image since the image's structural truth is
          best reached via its OCR child).

    StableDocumentBlock uses this helper when the caller does not pass
    an explicit interpretation_policy. An explicit policy passed by the
    caller (e.g. a Candidate Document confirm flow promoting an
    image_ocr into the main reading chain) is preserved verbatim.
    """
    try:
        policy_dict = _DEFAULT_POLICY_BY_BLOCK_TYPE[block_type]
    except KeyError as exc:  # pragma: no cover - Literal is closed
        raise ValueError(f"unknown block_type {block_type!r}") from exc
    return StableDocumentInterpretationPolicy.model_validate(policy_dict)


class StableDocumentInterpretationPolicy(BaseModel):
    """Per-block interpretation defaults consumed by Plate projection and
    by the (future) RAG indexing layer.

    Note on the DB column: `interpretation_policy_json` defaults to
    `'{}'::jsonb` in the migration as a storage-only placeholder. The
    Python model `StableDocumentBlock` ALWAYS materializes a
    per-block-type default policy via
    `default_interpretation_policy_for`, and the service that
    persists frozen Stable Reading Documents MUST write that
    Python-model policy into the column. The DB default is never
    relied on at runtime; an empty `{}` would silently route the block
    as main_reading/main_reading_text and contradict the
    projection rules.
    """

    model_config = ConfigDict(extra="forbid")

    allowed_source_scope: list[StableDocumentSourceScope] = Field(
        default_factory=lambda: ["main_reading_text"]
    )
    default_route: StableDocumentInterpretationRoute = "main_reading"
    rag_eligible: bool = True
    notes: list[str] = Field(default_factory=list)

    @field_validator("allowed_source_scope")
    @classmethod
    def _non_empty_scope(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("allowed_source_scope must contain at least one entry")
        return list(value)

    @model_validator(mode="after")
    def _route_matches_scope(self) -> StableDocumentInterpretationPolicy:
        if self.default_route == "ignored" and self.rag_eligible:
            # An ignored block cannot also be RAG-eligible; this would
            # silently re-introduce flattened-table / ignored-image
            # content into RAG.
            raise ValueError("default_route='ignored' requires rag_eligible=False")
        return self


class StableDocumentBlock(BaseModel):
    """One ordered block under a stable reading document.

    `canonical_text_*_utf16` are optional mappings into the Canonical Text
    Layer carrier (reading_bases.text). When both are present,
    canonical_text_end_utf16 > canonical_text_start_utf16.

    `interpretation_policy` defaults to the per-block-type policy from
    `default_interpretation_policy_for(block_type)` when the caller does
    not pass it explicitly. A caller-supplied policy is preserved
    verbatim, so the Candidate Document confirm flow can promote e.g. a
    user-chosen image_ocr into the main reading chain without being
    overridden by the block-type default.
    """

    model_config = ConfigDict(extra="forbid")

    block_id: str = Field(min_length=1)
    parent_block_id: str | None = None
    order_index: int = Field(ge=0)
    block_type: StableDocumentBlockType
    text_content: str | None = None
    payload_json: dict[str, Any] = Field(default_factory=dict)
    source_refs_json: dict[str, Any] = Field(default_factory=dict)
    canonical_text_start_utf16: int | None = Field(default=None, ge=0)
    canonical_text_end_utf16: int | None = Field(default=None, ge=0)
    # Typed Optional so the mode="before" validator can substitute the
    # per-block-type default; after that hook the value is always a
    # StableDocumentInterpretationPolicy.
    interpretation_policy: StableDocumentInterpretationPolicy | None = Field(default=None)
    quality_json: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _apply_block_type_default_policy(cls, values: Any) -> Any:
        """Substitute the per-block-type default policy when the caller
        omitted `interpretation_policy`. Dict input is the common path
        for the validator / Candidate confirm flow.

        Rules — the following inputs are all treated as "policy not
        provided" and replaced with `default_interpretation_policy_for(
        block_type)`:

            * the field is absent,
            * the field is explicitly `None`,
            * the field is the empty dict `{}` (the storage placeholder
              used in the migration column
              `interpretation_policy_json DEFAULT '{}'::jsonb`). An
              empty dict would otherwise parse to the model's defaults
              (main_reading / main_reading_text), which would silently
              re-introduce tables / images / footnotes / code blocks
              into the main grammar pass — the exact failure the
              per-block-type defaults exist to prevent.

        Inputs that ARE treated as an explicit caller policy and are
        preserved verbatim (so the Candidate Document confirm flow can
        promote e.g. an image_ocr into the main reading chain):

            * a non-empty dict (any key set, even if it overlaps the
              defaults),
            * a StableDocumentInterpretationPolicy instance.
        """
        if not isinstance(values, dict):
            return values
        if "interpretation_policy" not in values:
            # Field absent.
            block_type = values.get("block_type")
            if block_type is None or block_type not in _DEFAULT_POLICY_BY_BLOCK_TYPE:
                return values
            values["interpretation_policy"] = default_interpretation_policy_for(block_type)
            return values

        supplied = values["interpretation_policy"]
        if supplied is None:
            # Field explicitly None.
            block_type = values.get("block_type")
            if block_type is None or block_type not in _DEFAULT_POLICY_BY_BLOCK_TYPE:
                return values
            values["interpretation_policy"] = default_interpretation_policy_for(block_type)
            return values

        if isinstance(supplied, dict):
            if not supplied:
                # Empty dict -> storage placeholder. Treat as omitted.
                block_type = values.get("block_type")
                if block_type is None or block_type not in _DEFAULT_POLICY_BY_BLOCK_TYPE:
                    return values
                values["interpretation_policy"] = default_interpretation_policy_for(block_type)
                return values
            # Non-empty dict: explicit caller policy, leave untouched so
            # the Candidate confirm flow can override the per-block-type
            # default.
            return values

        # StableDocumentInterpretationPolicy instance or other typed
        # value: explicit caller policy.
        return values

    @model_validator(mode="after")
    def _canonical_text_offsets(self) -> StableDocumentBlock:
        start = self.canonical_text_start_utf16
        end = self.canonical_text_end_utf16
        if (start is None) != (end is None):
            raise ValueError(
                "canonical_text_start_utf16 and canonical_text_end_utf16 must be set together"
            )
        if start is not None and end is not None and end <= start:
            raise ValueError(
                "canonical_text_end_utf16 must be greater than canonical_text_start_utf16"
            )
        return self

    @model_validator(mode="after")
    def _text_for_textual_types(self) -> StableDocumentBlock:
        if self.block_type in _STRUCTURAL_BLOCK_TYPES:
            return self
        if not self.text_content:
            raise ValueError(f"text_content is required for block_type={self.block_type!r}")
        return self

    @field_validator("parent_block_id")
    @classmethod
    def _no_self_parent(cls, value: str | None) -> str | None:
        # Cannot verify against `block_id` here (field-order limitation),
        # but a non-empty string is enforced below via model_validator.
        if value is not None and not value:
            raise ValueError("parent_block_id must be a non-empty string when set")
        return value

    @model_validator(mode="after")
    def _parent_must_differ_from_block_id(self) -> StableDocumentBlock:
        if self.parent_block_id is not None and self.parent_block_id == self.block_id:
            raise ValueError("parent_block_id must differ from block_id")
        return self


class StableReadingDocument(BaseModel):
    """Immutable document truth for one (reading_record_id,
    record_generation) pair.

    Block ordering and content are carried by StableDocumentBlock rows;
    this model exposes only the document-level metadata.
    """

    model_config = ConfigDict(extra="forbid")

    reading_record_id: str = Field(min_length=1)
    record_generation: int = Field(ge=1)
    title: str | None = None
    document_version: int = Field(ge=1)
    source_profile_json: dict[str, Any] = Field(default_factory=dict)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: StableReadingDocumentStatus = "active"


class CandidateReadingDocument(BaseModel):
    """Reviewable document in `needs_confirmation` product state.

    `blocks_json` is intentionally a free-form array: the durable audit
    preserves the candidate payload verbatim (including the candidate edit
    history), while the confirmed facts are normalized into the stable
    document and its blocks (see StableReadingDocument /
    StableDocumentBlock). Raw Plate editor state is never persisted as
    truth here.
    """

    model_config = ConfigDict(extra="forbid")

    reading_record_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    record_generation: int = Field(ge=1)
    title: str | None = None
    blocks_json: list[Any] = Field(default_factory=list)
    canonical_text_preview: str = ""
    source_refs_json: dict[str, Any] = Field(default_factory=dict)
    quality_json: dict[str, Any] = Field(default_factory=dict)
    status: CandidateReadingDocumentStatus = "ready"

    @field_validator("blocks_json")
    @classmethod
    def _blocks_is_list(cls, value: list[Any]) -> list[Any]:
        # Pydantic already enforces `list`; this exists so that the
        # domain-level "must be an ordered list" invariant is documented
        # and re-validated after model mutations in callers.
        return list(value)


class ConfirmedSourceDocument(BaseModel):
    """L2 — 单一 Confirmed Source 生命周期实体（migration 0025）。

    每个 ``(reading_record_id, record_generation)`` 至多一行；
    ``markdown_text`` 是该 generation 全库唯一一份完整正文（规范化后
    文本，与 blocks / reparse 输入严格同源）。revision 乐观并发演进
    采用原地 UPDATE，不保留历史正文；``content_sha256`` 由 DB CHECK
    自校验（reading_bases 先例）。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    reading_record_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    record_generation: int = Field(ge=1)
    original_input_id: str | None = None
    markdown_text: str = Field(min_length=1)
    revision: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: ConfirmedSourceDocumentStatus = "draft"
    edit_source: ConfirmedSourceEditSource = "initial"


# ---------------------------------------------------------------------------
# R8 — Structured Review Item contract.
# Mirrors the frozen Review-Item / Evidence minimal capability contract in
# apps/web/docs/design/surface-read-intake-content-check.md §13.1:
# issue_id / tier / target_scope / source_anchor / anchor_hash /
# evidence{excerpt, proposed_patch} / source_media_coordinate.
# silent 与 adaptation_notice 分类行为不变；Routine / Attention 是
# content_check 内部的产品 tier，不是后端 classification 的替代枚举。
# ---------------------------------------------------------------------------

ReviewIssueTier = Literal["attention", "routine"]

ReviewIssueTargetScope = Literal["document", "range"]

# Mirrors AdaptationClassification in reader_input_adapter (closed
# three-level set). Kept here to avoid a circular import
# (reader_input_adapter imports StableDocumentBlock from this module);
# test_review_item_enrichment.py / the response DTO tests keep the sets
# in sync with the canonical literal.
ReviewItemClassification = Literal[
    "silent",
    "adaptation_notice",
    "content_check",
]


class ReviewIssueEvidence(BaseModel):
    """Per-issue evidence fields; both degrade to ``None`` when the backend
    cannot derive them exactly (no fuzzy guessing).

    ``excerpt_text`` is the official surface-spec field name (the narrow
    repair renamed the earlier ``excerpt``).
    """

    model_config = ConfigDict(extra="forbid")

    excerpt_text: str | None = None
    proposed_patch: str | None = None


class ReviewSourceAnchor(BaseModel):
    """Structured anchor for a local review item — EXACTLY ONE form:
    a non-empty ``block_id`` OR a complete UTF-16 range with
    ``end > start``. Never both, never empty (an item without a precise
    anchor simply carries ``source_anchor=None`` and degrades to document
    scope)."""

    model_config = ConfigDict(extra="forbid")

    block_id: str | None = None
    start_utf16: int | None = Field(default=None, ge=0)
    end_utf16: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _single_form(self) -> ReviewSourceAnchor:
        has_block = self.block_id is not None
        has_range = self.start_utf16 is not None or self.end_utf16 is not None
        if has_block == has_range:
            raise ValueError(
                "source_anchor must be exactly one form: a non-empty "
                "block_id OR a complete UTF-16 range"
            )
        if has_block:
            if not self.block_id:
                raise ValueError("block_id must be non-empty when provided")
            return self
        start = self.start_utf16
        end = self.end_utf16
        if (start is None) != (end is None):
            raise ValueError("start_utf16 and end_utf16 must be set together")
        if start is not None and end is not None and end <= start:
            raise ValueError("end_utf16 must be greater than start_utf16")
        return self


class ReviewMediaCoordinate(BaseModel):
    """Optional original-media coordinate (page / bbox) for an issue."""

    model_config = ConfigDict(extra="forbid")

    page_number: int | None = Field(default=None, ge=1)
    bbox: list[int] | None = None


class StructuredReviewItem(BaseModel):
    """One structured review item carried in confirmed-source responses and
    persisted inside ``quality_json.suitability.adaptations``.

    Public contract (narrow repair):

    - ``classification`` is EXACTLY ``content_check`` — silent /
      adaptation_notice records never surface in ``content_check``,
    - ``issue_id`` / ``tier`` / ``target_scope`` / ``evidence`` are
      REQUIRED (an item that cannot provide them is not emitted),
    - ``issue_id`` is exactly 16 lowercase hex chars,
    - ``source_anchor`` is nullable; when present it is exactly one form
      (see ``ReviewSourceAnchor``),
    - ``target_scope='range'`` REQUIRES a valid ``source_anchor`` —
      ranges are never fabricated; without a precise anchor the item
      degrades to ``target_scope='document'``.
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    message: str = ""
    classification: Literal["content_check"]
    issue_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    tier: ReviewIssueTier
    target_scope: ReviewIssueTargetScope
    source_anchor: ReviewSourceAnchor | None = None
    anchor_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    evidence: ReviewIssueEvidence
    source_media_coordinate: ReviewMediaCoordinate | None = None

    @model_validator(mode="after")
    def _range_requires_anchor(self) -> StructuredReviewItem:
        if self.target_scope == "range" and self.source_anchor is None:
            raise ValueError(
                "target_scope='range' requires a valid source_anchor (no fabricated ranges)"
            )
        return self
