from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.contracts.annotation import (
    compute_text_range_hash,
    slice_by_utf16_offsets,
    utf16_code_unit_length,
)
from app.services import nlp_model_registry

from .stable_annotation_analysis import (
    AcceptedStableBlockAnnotation,
    StableAnnotationAnalysis,
    StableBlockAnnotation,
    StableUnitRange,
    analyze_stable_annotations,
)

logger = logging.getLogger(__name__)

LOW_IMPACT_CANONICALIZER_VERSION = "reader_base_low_impact_v1"
DETERMINISTIC_READING_BASE_BUILDER_VERSION = "reading_base_builder_d3_p2_v1"
# R7-1: AUTO POLICY selector, deliberately distinct from every concrete
# segmenter identity. Production callers request this policy; the
# builder then resolves the actual English sentence provider at build
# time:
#   1. parser-backed spaCy ``en_core_web_sm`` (English text + model
#      available) -> persisted as SPACY_EN_SENTENCE_SEGMENTER_VERSION,
#   2. a named, initialism-aware regex fallback -> persisted as
#      REGEX_V2_SEGMENTER_VERSION (never silently impersonating spaCy).
# The resolved identity is stamped into ``StableReadingBase.segmenter_version``
# and per-unit into ``reading_units.metadata_json`` (sentence_provider),
# so persisted metadata distinguishes the segmenter that actually ran.
# The concrete algorithm labels below ONLY ever mean their actual
# algorithm/provider. AUTO is the only selection policy; explicit v1/v2/spaCy
# labels run that provider, while unknown labels fail closed.
AUTO_SEGMENTER_POLICY = "auto_sentence_provider_v1"
DETERMINISTIC_SEGMENTER_VERSION = "regex_sentence_clause_window_v1"
SPACY_EN_SENTENCE_SEGMENTER_VERSION = "spacy_en_core_web_sm_parser_v1"
REGEX_V2_SEGMENTER_VERSION = "regex_sentence_clause_window_v2"
MIXED_SEGMENTER_VERSION_SUFFIX = "+regex_v2_block_fallback"

# Per-unit sentence provider tags persisted into reading_units.metadata_json.
SENTENCE_PROVIDER_SPACY = "spacy_en_core_web_sm"
SENTENCE_PROVIDER_REGEX_V2 = "regex_v2"
SENTENCE_PROVIDER_REGEX_V1 = "regex_v1"

# Pipes disabled for the reader sentence pipeline. The dependency parser
# MUST remain enabled: parser-backed doc.sents is what correctly handles
# sentence-final initialisms such as "U.K. It ...". NER, tagger,
# attribute ruler and lemmatizer are not needed for sentence boundaries
# and are disabled to keep segmentation fast.
_SPACY_SENTENCE_PIPELINE_DISABLE = ("ner", "tagger", "attribute_ruler", "lemmatizer")

FALLBACK_WINDOW_WORD_COUNT = 24
# Canonicalizer version label used when the caller supplies an EXACT
# canonical text (already canonicalized by the stable document freeze
# plan) and the base builder must NOT recanonicalize it. D6 block
# offsets are bound to the exact canonical text.
EXACT_CANONICAL_TEXT_VERSION = "exact_canonical_text_v1"

_INVISIBLE_CHAR_PATTERN = re.compile(
    r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f"
    r"\u200b\u200c\u200d\u00ad\ufeff"
    r"\u200e\u200f\u202a-\u202e"
    r"\u2060-\u2064\u2066-\u2069"
    r"\ufffc]"
)
_UNICODE_SPACE_MAP = str.maketrans({
    "\u00a0": " ",
    "\u2000": " ",
    "\u2001": " ",
    "\u2002": " ",
    "\u2003": " ",
    "\u2004": " ",
    "\u2005": " ",
    "\u2006": " ",
    "\u2007": " ",
    "\u2008": " ",
    "\u2009": " ",
    "\u200a": " ",
    "\u202f": " ",
    "\u205f": " ",
    "\u3000": " ",
})
_BLANK_LINE_RUN_PATTERN = re.compile(r"\n(?:[ \t]*\n){2,}")
_WORD_PATTERN = re.compile(r"\S+")
_LIST_LINE_PATTERN = re.compile(r"^(?:[-*+]\s+|\d+[.)]\s+)")
_CLAUSE_BOUNDARY_PATTERN = re.compile(
    r";|:|—|–|--|,\s+(?=(?:and|but|or|so|yet|for|because|which|that|while|although|however)\b)",
    re.IGNORECASE,
)
_ABBREVIATION_SUFFIXES = {
    "u.s.",
    "u.k.",
    "ph.d.",
    "e.g.",
    "i.e.",
    "dr.",
    "mr.",
    "mrs.",
    "ms.",
    "prof.",
    "sr.",
    "jr.",
    "dept.",
    "govt.",
    "est.",
    "inc.",
    "ltd.",
    "corp.",
    "vs.",
    "approx.",
    "etc.",
}
_CLOSING_PUNCTUATION = "\"'”’)]}"
_SENTENCE_STARTERS = "\"'“‘([{"
# R7-1: initialism tokens (U.K., U.S., e.g., i.e., Ph.D., a.m., ...):
# adjacent letter groups each terminated by a period, with no whitespace
# between them. Sentence boundaries must NEVER be created inside these
# tokens. Applied as defense-in-depth to BOTH the spaCy main path (span
# repair) and the regex v2 fallback (boundary rejection).
_INITIALISM_PATTERN = re.compile(r"\b(?:[A-Za-z]{1,3}\.){2,}")
# Abbreviation-suffix entries that are initialisms. For these, the v2
# boundary check does NOT treat "tail ends with the abbreviation" as a
# hard veto: the initialism guard already protects every INTERNAL
# period, while the FINAL period may legitimately terminate a sentence
# ("... in the U.K. It led ..."). Whether the final period IS a
# boundary is decided by the conservative pronoun-class rule below
# (see _INITIALISM_SENTENCE_STARTERS), NOT by a broad "next char is
# uppercase" heuristic, which would mis-split title-case continuations
# like "the U.K. Prime Minister" / "a Ph.D. Student". Non-initialism
# abbreviations (Dr., Mr., Inc., ...) keep the v1 hard veto, because
# "Dr. Smith" has an uppercase next char that any next-word heuristic
# would mis-split.
_INLINE_INITIALISM_CONNECTORS = frozenset({"e.g.", "i.e."})
# R7-1 rework: closed function-word surface forms that — when they
# follow a sentence-final initialism — reliably open a NEW sentence.
# Personal/demonstrative pronouns are never parts of proper-noun titles
# or title-case noun phrases ("the U.K. Prime Minister", "the U.S.
# President", "the U.S. Secretary of State", "Ph.D. Students"), so a
# capitalized pronoun after "X.Y. " can only be sentence-initial. The
# comparison is SURFACE-SENSITIVE on purpose: all-caps "IT"/"WE" etc.
# are initialisms/acronyms in title position ("the U.K. IT sector") and
# must NOT trigger a split. This mirrors the parser's own decisions and
# is the ONLY condition under which the spaCy main path restores a
# boundary the parser missed, or the regex v2 fallback accepts a
# boundary after an initialism's final period. It is a fixed function-
# word class, not an expandable list of content words.
_INITIALISM_SENTENCE_STARTERS = frozenset({
    "I", "He", "She", "It", "We", "They", "You",
    "This", "That", "These", "Those", "There", "Here",
})
# R7-1: bare URLs. A period inside a URL is never a sentence terminator.
_URL_PATTERN = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
# R7-1 rework: trailing syntactic punctuation that \S+ greedily glues
# onto a URL match but that belongs to the surrounding sentence, e.g.
# the final period in "Visit https://example.com. Next sentence.".
# These characters are stripped from the URL protection range so a
# sentence terminator following a URL can still act as a boundary,
# while periods INSIDE the URL remain protected.
_URL_TRAILING_PUNCTUATION = ".,;:!?)]}\"'”’"


@dataclass(frozen=True, slots=True)
class LowImpactReadingBaseBuildInput:
    reading_record_id: str
    base_id: str
    source_text: str
    title: str | None = None
    language: str | None = None
    canonicalizer_version: str = LOW_IMPACT_CANONICALIZER_VERSION
    builder_version: str = DETERMINISTIC_READING_BASE_BUILDER_VERSION
    segmenter_version: str = AUTO_SEGMENTER_POLICY


@dataclass(frozen=True, slots=True)
class StableReadingBase:
    reading_record_id: str
    base_id: str
    text: str
    content_sha256: str
    content_utf16_length: int
    canonicalizer_version: str
    builder_version: str
    segmenter_version: str
    language: str | None = None
    title_snapshot: str | None = None


@dataclass(frozen=True, slots=True)
class BuiltReadingUnit:
    reading_record_id: str
    base_id: str
    unit_id: str
    order_index: int
    unit_type: str
    boundary_quality: str
    base_start_utf16: int
    base_end_utf16: int
    text_hash: str
    text: str
    label: str | None = None
    # R7-1: names the sentence provider that produced this unit's
    # SENTENCE-stage anchor segments (SENTENCE_PROVIDER_*), or None
    # when the unit's segments came from the clause / fallback-window
    # stage. Persisted into reading_units.metadata_json.
    sentence_provider: str | None = None
    # A5: stable block metadata. Populated when a ``StableBlockAnnotation``
    # matched this unit's UTF-16 range; ``None`` / empty for legacy units
    # (no annotations supplied or no annotation matched). The snapshot
    # builder only emits the corresponding payload fields when
    # ``stable_block_type`` is not ``None``, so legacy snapshots stay
    # byte-for-byte stable.
    stable_block_type: str | None = None
    stable_block_id: str | None = None
    heading_level: int | None = None
    inline_marks: tuple[dict[str, Any], ...] = ()
    table_role: str | None = None
    parent_stable_block_id: str | None = None
    # L1: code / table metadata projected from the matched stable block's
    # payload_json so the snapshot DTO can render language badges and
    # table header/alignment without re-parsing. ``None`` when the
    # matched block type does not carry the field.
    code_language: str | None = None
    table_is_header: bool | None = None
    table_alignment: str | None = None
    table_alignments: tuple[str, ...] | None = None
    table_header_rows: int | None = None
    # Semantic automatic-layer policy (versioned projection of stable
    # ``payload_json.semantic``). Legacy units (no contract_version) leave
    # these None. ``content_role`` may be None even with a contract marker
    # (heading/code/table). Policy is materialised into metadata_json.
    semantic_contract_version: str | None = None
    content_role: str | None = None
    automatic_layer_policy: dict[str, bool] | None = None
    automatic_layer_policy_resolver_version: str | None = None


@dataclass(frozen=True, slots=True)
class BuiltAnchorSegment:
    reading_record_id: str
    base_id: str
    unit_id: str
    anchor_segment_id: str
    sentence_id: str
    paragraph_id: str
    order_index: int
    unit_order_index: int
    segment_type: str
    boundary_quality: str
    base_start_utf16: int
    base_end_utf16: int
    unit_start_utf16: int
    unit_end_utf16: int
    text_hash: str
    text: str


@dataclass(frozen=True, slots=True)
class NavigationUnitFact:
    unit_id: str
    order_index: int
    unit_type: str
    boundary_quality: str
    label: str | None
    base_start_utf16: int
    base_end_utf16: int
    # A5: stable block metadata projected from the matched
    # ``StableBlockAnnotation``. ``None`` for legacy units so the
    # snapshot navigation projection omits the fields entirely.
    stable_block_type: str | None = None
    heading_level: int | None = None


@dataclass(frozen=True, slots=True)
class ReadingBaseBuildResult:
    base: StableReadingBase
    units: tuple[BuiltReadingUnit, ...]
    anchor_segments: tuple[BuiltAnchorSegment, ...]
    navigation_units: tuple[NavigationUnitFact, ...]
    # Stable Document rows are the structure truth.  The reading units and
    # anchors above remain location/scheduling carriers; they must not be
    # used to reconstruct list/table/callout structure on reload.
    stable_document_blocks: tuple[Any, ...] = ()
    # Analyzer output for the supplied stable annotations (None on the
    # legacy / low-impact path): accepted annotations, deterministic
    # diagnostics, and per-unit structural policy override records.
    annotation_analysis: StableAnnotationAnalysis | None = None


@dataclass(frozen=True, slots=True)
class _SegmentSpan:
    start_char: int
    end_char: int
    segment_type: str
    boundary_quality: str


def canonicalize_low_impact_text(source_text: str) -> str:
    text = source_text.replace("\r\n", "\n").replace("\r", "\n")
    text = _INVISIBLE_CHAR_PATTERN.sub("", text)
    text = text.translate(_UNICODE_SPACE_MAP)
    text = _BLANK_LINE_RUN_PATTERN.sub("\n\n", text)
    return text.strip()


def build_low_impact_reading_base(
    build_input: LowImpactReadingBaseBuildInput,
) -> ReadingBaseBuildResult:
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


def build_reading_base_from_canonical_text(
    *,
    reading_record_id: str,
    base_id: str,
    canonical_text: str,
    title: str | None = None,
    language: str | None = None,
    builder_version: str = DETERMINISTIC_READING_BASE_BUILDER_VERSION,
    segmenter_version: str = AUTO_SEGMENTER_POLICY,
    canonicalizer_version: str = EXACT_CANONICAL_TEXT_VERSION,
    stable_block_annotations: Sequence[StableBlockAnnotation] | None = None,
) -> ReadingBaseBuildResult:
    """Build a reading base from an EXACT canonical text.

    Unlike :func:`build_low_impact_reading_base`, this does NOT
    recanonicalize the text. The ``canonical_text`` is used as-is for
    unit/anchor segmentation and for ``content_sha256`` /
    ``content_utf16_length`` computation. This is required for D6-I2C
    where the stable document's block offsets are already bound to the
    exact canonical text produced by the freeze plan; recanonicalizing
    would invalidate those offsets.

    The private split/segment/hash helpers are reused so segmentation
    behavior is identical to the low-impact builder.

    A5: When ``stable_block_annotations`` is supplied, a built unit
    whose ``(base_start_utf16, base_end_utf16)`` exactly matches an
    annotation's ``(start_utf16, end_utf16)`` derives its ``unit_type``
    from the annotation's ``block_type`` (instead of the legacy text
    heuristic) and carries the annotation's payload (heading level /
    inline marks / table role / parent block id) onto the unit and
    into the snapshot ``reader_source_block`` payload. Annotations
    that match no unit are silently ignored (fail-safe). When the
    parameter is ``None`` or empty, behavior is identical to the
    legacy path — no stable block fields are emitted.

    Args:
        reading_record_id: The reading record id.
        base_id: The base id (UUID string).
        canonical_text: The EXACT canonical text from the stable
            document freeze plan. Must not be empty.
        title: Optional title snapshot.
        language: Optional language code.
        builder_version: Builder version label.
        segmenter_version: Segmenter version label.
        canonicalizer_version: Canonicalizer version label; defaults
            to :data:`EXACT_CANONICAL_TEXT_VERSION` to mark that the
            text was supplied exactly (not recanonicalized).
        stable_block_annotations: Optional sequence of
            :class:`StableBlockAnnotation` intervals. When supplied,
            units whose UTF-16 range exactly matches an annotation
            derive their ``unit_type`` from the annotation's
            ``block_type`` and carry the stable block payload.

    Returns:
        A validated :class:`ReadingBaseBuildResult`.

    Raises:
        ValueError: If ``canonical_text`` is empty or the
            segmentation/validation fails.
    """
    if not canonical_text:
        raise ValueError("canonical_text must not be empty")
    return _build_reading_base_core(
        reading_record_id=reading_record_id,
        base_id=base_id,
        text=canonical_text,
        title=title,
        language=language,
        canonicalizer_version=canonicalizer_version,
        builder_version=builder_version,
        segmenter_version=segmenter_version,
        stable_block_annotations=stable_block_annotations,
    )


def _build_reading_base_core(
    *,
    reading_record_id: str,
    base_id: str,
    text: str,
    title: str | None,
    language: str | None,
    canonicalizer_version: str,
    builder_version: str,
    segmenter_version: str,
    stable_block_annotations: Sequence[StableBlockAnnotation] | None = None,
) -> ReadingBaseBuildResult:
    """Core builder shared by :func:`build_low_impact_reading_base` and
    :func:`build_reading_base_from_canonical_text`.

    The caller is responsible for ensuring ``text`` is the exact
    canonical text (already canonicalized if needed). This helper does
    NOT recanonicalize; it only segments the supplied text into units
    and anchor segments and validates the result.

    R7-1: ``segmenter_version == AUTO_SEGMENTER_POLICY`` selects the
    AUTO POLICY. The English sentence provider is resolved here
    (parser-backed spaCy ``en_core_web_sm`` when the text is English
    and the model is available, otherwise the explicitly named regex v2
    fallback) and the RESOLVED identity is stamped into
    ``StableReadingBase.segmenter_version`` plus per-unit into
    ``BuiltReadingUnit.sentence_provider``. Explicit provider identities run
    that provider; unsupported labels fail closed.

    A5: When ``stable_block_annotations`` is supplied, a built unit whose
    ``(base_start_utf16, base_end_utf16)`` exactly matches an
    annotation's ``(start_utf16, end_utf16)`` derives its ``unit_type``
    from the annotation's ``block_type`` (instead of the legacy text
    heuristic) and carries the annotation's payload (heading level /
    inline marks / table role / parent block id) onto the unit and
    into the snapshot ``reader_source_block`` payload. Annotations
    that match no unit are silently ignored (fail-safe). When the
    parameter is ``None`` or empty, behavior is identical to the
    legacy path — no stable block fields are emitted.
    """
    utf16_prefix = _build_utf16_prefix(text)
    block_spans = _split_structure_blocks(text)
    if not block_spans:
        raise ValueError("non-empty canonical text must produce at least one structure block")

    sentence_policy, spacy_pipeline = _resolve_sentence_policy(
        requested_segmenter_version=segmenter_version,
        language=language,
    )

    # A5: every supplied annotation passes through the single analyzer
    # module BEFORE any filtering. It owns range validity, duplicate
    # judgement, inline mark validation, diagnostics, and per-unit
    # structural policy override attribution. Only its accepted
    # annotations feed the exact-match lookup below.
    annotation_analysis: StableAnnotationAnalysis | None = None
    accepted_by_range: dict[tuple[int, int], AcceptedStableBlockAnnotation] = {}
    if stable_block_annotations is not None:
        unit_ranges = [
            StableUnitRange(
                unit_id=f"u{index}",
                start_utf16=utf16_prefix[char_start],
                end_utf16=utf16_prefix[char_end],
            )
            for index, (char_start, char_end) in enumerate(block_spans, start=1)
        ]
        annotation_analysis = analyze_stable_annotations(
            raw_annotations=stable_block_annotations,
            base_utf16_length=utf16_prefix[-1],
            unit_ranges=unit_ranges,
        )
        accepted_by_range = {
            (
                accepted.annotation.start_utf16,
                accepted.annotation.end_utf16,
            ): accepted
            for accepted in annotation_analysis.accepted_annotations
        }

    units: list[BuiltReadingUnit] = []
    anchor_segments: list[BuiltAnchorSegment] = []
    navigation_units: list[NavigationUnitFact] = []

    for block_index, (char_start, char_end) in enumerate(block_spans, start=1):
        unit_id = f"u{len(units) + 1}"
        paragraph_id = f"p{block_index}"
        block_text = text[char_start:char_end]
        segment_spans, sentence_provider = _build_segment_spans(
            block_text,
            sentence_policy=sentence_policy,
            spacy_pipeline=spacy_pipeline,
        )
        if not segment_spans:
            raise ValueError(f"structure block {paragraph_id} did not produce anchor segments")

        base_start_utf16 = utf16_prefix[char_start]
        base_end_utf16 = utf16_prefix[char_end]
        unit_type = _classify_unit_type(block_text)
        label = _build_unit_label(block_text, unit_type)

        built_segments: list[BuiltAnchorSegment] = []
        unit_boundary_quality = "normal"
        for unit_order_index, span in enumerate(segment_spans, start=1):
            anchor_segment_id = f"s{len(anchor_segments) + 1}"
            segment_text = block_text[span.start_char:span.end_char]
            segment_base_start_utf16 = utf16_prefix[char_start + span.start_char]
            segment_base_end_utf16 = utf16_prefix[char_start + span.end_char]
            built_segment = BuiltAnchorSegment(
                reading_record_id=reading_record_id,
                base_id=base_id,
                unit_id=unit_id,
                anchor_segment_id=anchor_segment_id,
                sentence_id=anchor_segment_id,
                paragraph_id=paragraph_id,
                order_index=len(anchor_segments) + 1,
                unit_order_index=unit_order_index,
                segment_type=span.segment_type,
                boundary_quality=span.boundary_quality,
                base_start_utf16=segment_base_start_utf16,
                base_end_utf16=segment_base_end_utf16,
                unit_start_utf16=segment_base_start_utf16 - base_start_utf16,
                unit_end_utf16=segment_base_end_utf16 - base_start_utf16,
                text_hash=compute_text_range_hash(segment_text),
                text=segment_text,
            )
            built_segments.append(built_segment)
            anchor_segments.append(built_segment)
            if span.boundary_quality == "low":
                unit_boundary_quality = "low"

        if unit_type == "body" and all(
            segment.segment_type == "fallback_window" for segment in built_segments
        ):
            unit_type = "fallback"

        # A5: when a stable block annotation exactly matches this
        # unit's UTF-16 range, project the stable block_type and
        # payload onto the unit. There is no ``stable_block_type`` DB
        # column — the field lives on the in-memory ``BuiltReadingUnit``
        # and is re-joined on snapshot reload via exact UTF-16 range.
        # ``unit_type`` is only overridden for ``heading`` — the legacy
        # CHECK constraint on ``reading_units.unit_type`` (migration 0001)
        # allows only ``body`` / ``heading`` / ``list`` / ``quote`` /
        # ``unknown`` / ``fallback``, so new block types like
        # ``paragraph`` / ``list_item`` / ``blockquote`` / ``table*`` /
        # ``code_block`` MUST NOT be written to ``unit_type``. ``heading``
        # is the one exception because (a) it is in the legacy allowed
        # set and (b) downstream consumers (A6 semantic-outline skip
        # decision in ``job_bootstrap.py``, feature extractor, B4
        # outline projector) key off ``unit_type == "heading"`` to
        # detect Markdown headings. For all other stable block types
        # the heuristic ``unit_type`` is kept — the authoritative
        # block type is in ``stable_block_type``.
        matched_accepted = accepted_by_range.get(
            (base_start_utf16, base_end_utf16)
        )
        matched_annotation = (
            matched_accepted.annotation if matched_accepted is not None else None
        )
        stable_block_type: str | None = None
        stable_block_id: str | None = None
        heading_level: int | None = None
        inline_marks: tuple[dict[str, Any], ...] = ()
        table_role: str | None = None
        parent_stable_block_id: str | None = None
        code_language: str | None = None
        table_is_header: bool | None = None
        table_alignment: str | None = None
        table_alignments: tuple[str, ...] | None = None
        table_header_rows: int | None = None
        semantic_contract_version: str | None = None
        content_role: str | None = None
        automatic_layer_policy: dict[str, bool] | None = None
        automatic_layer_policy_resolver_version: str | None = None
        if matched_annotation is not None:
            stable_block_type = matched_annotation.block_type
            stable_block_id = matched_annotation.block_id
            parent_stable_block_id = matched_annotation.parent_block_id
            # Only ``heading`` overrides unit_type — see comment above.
            if matched_annotation.block_type == "heading":
                unit_type = "heading"
            payload = matched_annotation.payload_json or {}
            heading_level = _extract_heading_level(payload)
            inline_marks = matched_accepted.inline_marks if matched_accepted else ()
            table_role = _derive_table_role(matched_annotation.block_type)
            # L1: code language + table header/alignment metadata.
            if matched_annotation.block_type == "code_block":
                code_language = _extract_code_language(payload)
            elif matched_annotation.block_type == "table_cell":
                table_is_header = _extract_is_header(payload)
                table_alignment = _extract_alignment_value(payload)
            elif matched_annotation.block_type == "table_row":
                table_is_header = _extract_is_header(payload)
            elif matched_annotation.block_type == "table":
                table_alignments = _extract_alignments(payload)
                table_header_rows = _extract_header_rows(payload)
            # Semantic contract → versioned automatic layer policy (materialised
            # into reading_units.metadata_json). Uses the recorded contract on
            # the block; never invents all-false on missing version.
            from .automatic_layer_policy import resolve_policy_for_stable_block

            resolved_policy = resolve_policy_for_stable_block(
                block_type=matched_annotation.block_type,
                payload_json=payload,
            )
            if not resolved_policy.is_legacy:
                semantic_contract_version = resolved_policy.contract_version
                content_role = resolved_policy.content_role
                automatic_layer_policy = resolved_policy.policy.as_dict()
                automatic_layer_policy_resolver_version = (
                    resolved_policy.resolver_version
                )
            # Re-derive the label so a stable heading still produces a
            # heading label even when the heuristic would have
            # classified it as body.
            label = _build_unit_label(block_text, unit_type)

        unit_text_hash = compute_text_range_hash(block_text)
        built_unit = BuiltReadingUnit(
            reading_record_id=reading_record_id,
            base_id=base_id,
            unit_id=unit_id,
            order_index=len(units) + 1,
            unit_type=unit_type,
            boundary_quality=unit_boundary_quality,
            base_start_utf16=base_start_utf16,
            base_end_utf16=base_end_utf16,
            text_hash=unit_text_hash,
            text=block_text,
            label=label,
            sentence_provider=sentence_provider,
            stable_block_type=stable_block_type,
            stable_block_id=stable_block_id,
            heading_level=heading_level,
            inline_marks=inline_marks,
            table_role=table_role,
            parent_stable_block_id=parent_stable_block_id,
            code_language=code_language,
            table_is_header=table_is_header,
            table_alignment=table_alignment,
            table_alignments=table_alignments,
            table_header_rows=table_header_rows,
            semantic_contract_version=semantic_contract_version,
            content_role=content_role,
            automatic_layer_policy=automatic_layer_policy,
            automatic_layer_policy_resolver_version=(
                automatic_layer_policy_resolver_version
            ),
        )
        units.append(built_unit)
        navigation_units.append(
            NavigationUnitFact(
                unit_id=unit_id,
                order_index=built_unit.order_index,
                unit_type=unit_type,
                boundary_quality=unit_boundary_quality,
                label=label,
                base_start_utf16=base_start_utf16,
                base_end_utf16=base_end_utf16,
                stable_block_type=stable_block_type,
                heading_level=heading_level,
            )
        )

    resolved_segmenter_version = _resolve_persisted_segmenter_version(
        requested_segmenter_version=segmenter_version,
        sentence_policy=sentence_policy,
        per_unit_providers=[unit.sentence_provider for unit in units],
    )

    base = StableReadingBase(
        reading_record_id=reading_record_id,
        base_id=base_id,
        text=text,
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        content_utf16_length=utf16_prefix[-1],
        canonicalizer_version=canonicalizer_version,
        builder_version=builder_version,
        segmenter_version=resolved_segmenter_version,
        language=language,
        title_snapshot=title,
    )

    result = ReadingBaseBuildResult(
        base=base,
        units=tuple(units),
        anchor_segments=tuple(anchor_segments),
        navigation_units=tuple(navigation_units),
        annotation_analysis=annotation_analysis,
    )
    validate_reading_base_build_result(result)
    return result


def _build_utf16_prefix(text: str) -> list[int]:
    prefix = [0]
    for char in text:
        prefix.append(prefix[-1] + (2 if ord(char) > 0xFFFF else 1))
    return prefix


def _split_structure_blocks(text: str) -> list[tuple[int, int]]:
    block_spans: list[tuple[int, int]] = []
    block_start: int | None = None
    block_end: int | None = None
    line_start = 0

    for line in text.split("\n"):
        line_end = line_start + len(line)
        stripped = line.strip()
        if stripped:
            first_visible = len(line) - len(line.lstrip())
            last_visible = len(line.rstrip())
            visible_start = line_start + first_visible
            visible_end = line_start + last_visible
            if block_start is None:
                block_start = visible_start
            block_end = visible_end
        elif block_start is not None and block_end is not None:
            block_spans.append((block_start, block_end))
            block_start = None
            block_end = None
        line_start = line_end + 1

    if block_start is not None and block_end is not None:
        block_spans.append((block_start, block_end))

    return block_spans


def _build_segment_spans(
    block_text: str,
    *,
    sentence_policy: str,
    spacy_pipeline: object | None,
) -> tuple[list[_SegmentSpan], str | None]:
    """Segment one structure block into anchor spans (R7-1).

    ``sentence_policy`` is one of ``"spacy"`` / ``"regex_v2"`` /
    ``"regex_v1"``:

    - ``"spacy"``: parser-backed ``en_core_web_sm`` sentence boundaries
      are attempted first. When spaCy fails at runtime or produces
      spans that violate the coverage invariants, this block falls
      through to the explicitly named regex v2 segmenter (never
      silently impersonating spaCy results).
    - ``"regex_v2"``: the named initialism-aware regex fallback.
    - ``"regex_v1"``: the frozen legacy regex, used only when the
      caller pinned a non-default ``segmenter_version`` label.

    Returns ``(spans, sentence_provider)`` where ``sentence_provider``
    names the provider that produced the SENTENCE stage for this block
    (:data:`SENTENCE_PROVIDER_SPACY` / :data:`SENTENCE_PROVIDER_REGEX_V2`
    / :data:`SENTENCE_PROVIDER_REGEX_V1`), or ``None`` when the block's
    spans came from the clause / fallback-window stage.
    """
    if sentence_policy == "spacy" and spacy_pipeline is not None:
        spacy_result = _segment_sentence_spans_spacy(spacy_pipeline, block_text)
        if spacy_result is not None:
            spacy_spans, spacy_boundary_count = spacy_result
            if (
                spacy_spans
                and spacy_boundary_count > 0
                and _spans_cover_visible_text(block_text, spacy_spans)
            ):
                return (
                    _to_sentence_spans(block_text, spacy_spans),
                    SENTENCE_PROVIDER_SPACY,
                )
            if spacy_boundary_count > 0:
                # The parser claimed sentence boundaries but its spans
                # violate the coverage invariants: log loudly and fall
                # back to the named regex v2 segmenter for this block.
                logger.warning(
                    "base_builder: spaCy sentence spans unusable for a "
                    "structure block (coverage invariants failed); falling "
                    "back to %s for this block",
                    REGEX_V2_SEGMENTER_VERSION,
                )
            # spacy_boundary_count == 0: text has no sentence-terminated
            # span (same as the regex path) - fall through silently to
            # the clause / fallback-window stage.
        # spacy_result is None when spaCy raised at runtime (already
        # logged); fall through to the named regex v2 segmenter.
    if sentence_policy in ("spacy", "regex_v2"):
        sentence_spans, sentence_boundary_count = _segment_sentence_spans_v2(block_text)
        sentence_provider = SENTENCE_PROVIDER_REGEX_V2
    else:
        sentence_spans, sentence_boundary_count = _segment_sentence_spans(block_text)
        sentence_provider = SENTENCE_PROVIDER_REGEX_V1

    if (
        sentence_spans
        and sentence_boundary_count > 0
        and _spans_cover_visible_text(block_text, sentence_spans)
    ):
        return _to_sentence_spans(block_text, sentence_spans), sentence_provider

    clause_spans = _segment_clause_spans(block_text)
    if clause_spans and _spans_cover_visible_text(block_text, clause_spans):
        return (
            [
                _SegmentSpan(
                    start_char=start,
                    end_char=end,
                    segment_type="clause",
                    boundary_quality="normal",
                )
                for start, end in clause_spans
            ],
            None,
        )

    fallback_spans = _segment_fallback_windows(block_text)
    return (
        [
            _SegmentSpan(
                start_char=start,
                end_char=end,
                segment_type="fallback_window",
                boundary_quality="low",
            )
            for start, end in fallback_spans
        ],
        None,
    )


def _to_sentence_spans(
    block_text: str,
    spans: list[tuple[int, int]],
) -> list[_SegmentSpan]:
    return [
        _SegmentSpan(
            start_char=start,
            end_char=end,
            segment_type="sentence",
            boundary_quality=_sentence_boundary_quality(block_text[start:end]),
        )
        for start, end in spans
    ]


def _resolve_sentence_policy(
    *,
    requested_segmenter_version: str,
    language: str | None,
) -> tuple[str, object | None]:
    """Resolve the requested sentence policy to an executable provider.

    AUTO chooses parser-backed spaCy for English when available and otherwise
    uses regex v2. Explicit provider identities run that provider. Unknown
    labels fail closed so persisted metadata cannot claim a provider that did
    not run.
    """
    if requested_segmenter_version == DETERMINISTIC_SEGMENTER_VERSION:
        return "regex_v1", None
    if requested_segmenter_version == REGEX_V2_SEGMENTER_VERSION:
        return "regex_v2", None
    if requested_segmenter_version == SPACY_EN_SENTENCE_SEGMENTER_VERSION:
        if not _is_english_language(language):
            raise ValueError(
                "spacy_en_core_web_sm_parser_v1 requires an English language label"
            )
        pipeline = _load_spacy_sentence_pipeline()
        if pipeline is None:
            raise ValueError(
                "spacy_en_core_web_sm_parser_v1 was requested but the model is unavailable"
            )
        return "spacy", pipeline
    if requested_segmenter_version != AUTO_SEGMENTER_POLICY:
        raise ValueError(
            f"unsupported segmenter_version: {requested_segmenter_version!r}"
        )
    if _is_english_language(language):
        pipeline = _load_spacy_sentence_pipeline()
        if pipeline is not None:
            return "spacy", pipeline
        logger.warning(
            "base_builder: spaCy en_core_web_sm unavailable; using %s "
            "for sentence segmentation",
            REGEX_V2_SEGMENTER_VERSION,
        )
    return "regex_v2", None


def _resolve_persisted_segmenter_version(
    *,
    requested_segmenter_version: str,
    sentence_policy: str,
    per_unit_providers: list[str | None],
) -> str:
    """Return the identity of the provider(s) that actually produced spans.

    Explicit v1/v2 requests are deterministic and persist verbatim. AUTO and
    explicit spaCy requests inspect per-unit providers so runtime fallback is
    recorded as regex v2 or as a mixed identity instead of impersonating
    spaCy.
    """
    if requested_segmenter_version == DETERMINISTIC_SEGMENTER_VERSION:
        return DETERMINISTIC_SEGMENTER_VERSION
    if requested_segmenter_version == REGEX_V2_SEGMENTER_VERSION:
        return REGEX_V2_SEGMENTER_VERSION
    if requested_segmenter_version not in (
        AUTO_SEGMENTER_POLICY,
        SPACY_EN_SENTENCE_SEGMENTER_VERSION,
    ):
        raise ValueError(
            f"unsupported segmenter_version: {requested_segmenter_version!r}"
        )
    if sentence_policy != "spacy":
        return REGEX_V2_SEGMENTER_VERSION

    sentence_stage_providers = {
        provider
        for provider in per_unit_providers
        if provider in (SENTENCE_PROVIDER_SPACY, SENTENCE_PROVIDER_REGEX_V2)
    }
    if (
        SENTENCE_PROVIDER_SPACY in sentence_stage_providers
        and SENTENCE_PROVIDER_REGEX_V2 in sentence_stage_providers
    ):
        return SPACY_EN_SENTENCE_SEGMENTER_VERSION + MIXED_SEGMENTER_VERSION_SUFFIX
    if SENTENCE_PROVIDER_REGEX_V2 in sentence_stage_providers:
        return REGEX_V2_SEGMENTER_VERSION
    return SPACY_EN_SENTENCE_SEGMENTER_VERSION


def _is_english_language(language: str | None) -> bool:
    if not language:
        return False
    return language.strip().lower().startswith("en")


def _load_spacy_sentence_pipeline() -> object | None:
    """Load the parser-backed reader sentence pipeline via the shared
    NLP model registry (R7-1). Test seam: monkeypatch this function to
    simulate model unavailability."""
    return nlp_model_registry.get_english_pipeline(
        disable=_SPACY_SENTENCE_PIPELINE_DISABLE
    )


def _segment_sentence_spans_spacy(
    pipeline: object,
    block_text: str,
) -> tuple[list[tuple[int, int]], int] | None:
    """Parser-backed sentence spans over the canonical block text (R7-1).

    ``block_text`` (the exact canonical Unit text slice) is passed to
    spaCy AS-IS: no normalization, strip-and-rebuild, whitespace
    merging, or rewriting. spaCy ``Span.start_char`` / ``end_char``
    are Python character offsets into this same string, so they feed
    directly into the existing UTF-16 offset conversion chain in
    :func:`_build_reading_base_core`.

    Returns ``(spans, boundary_count)`` with whitespace-trimmed spans
    shaped exactly like the regex segmenter's (whitespace-only gaps),
    or ``None`` when spaCy fails at runtime; the caller then uses the
    named regex v2 fallback.
    """
    try:
        doc = pipeline(block_text)  # type: ignore[operator]
        raw_spans = [(sent.start_char, sent.end_char) for sent in doc.sents]
    except Exception as exc:  # noqa: BLE001 - runtime fallback is the contract
        logger.warning(
            "base_builder: spaCy sentence segmentation raised at runtime: "
            "%s; falling back to %s",
            exc,
            REGEX_V2_SEGMENTER_VERSION,
        )
        return None

    spans: list[tuple[int, int]] = []
    for raw_start, raw_end in raw_spans:
        start = raw_start + _next_visible_index(block_text[raw_start:raw_end], 0)
        end = _trim_trailing_whitespace(block_text, raw_end)
        if start < end:
            spans.append((start, end))
    # Defense-in-depth (R7-1): even though the parser handles
    # initialisms correctly today, never let a split inside an
    # initialism token (U.|K., U.|S., Ph.|D.) survive.
    spans = _repair_initialism_splits(block_text, spans)
    # The parser occasionally misses the boundary AFTER a
    # sentence-final initialism ("... U.K. It led ..." stays one
    # sentence). Restore those specific boundaries; every other
    # boundary remains exactly what the parser decided.
    spans = _refine_initialism_final_boundaries(block_text, spans)
    return spans, _count_terminated_spans(block_text, spans)


def _count_terminated_spans(
    block_text: str,
    spans: list[tuple[int, int]],
) -> int:
    """Count spans that end in sentence-terminator punctuation (R7-1).

    Mirrors the regex segmenter's boundary semantics: a block whose
    only span ends in ``.`` / ``!`` / ``?`` (optionally followed by
    closing punctuation) HAS a sentence boundary; a span ending in an
    unterminated word does not, and the block drops to the clause /
    fallback-window stage.
    """
    count = 0
    for _, end in spans:
        index = end - 1
        while index >= 0 and block_text[index] in _CLOSING_PUNCTUATION:
            index -= 1
        if index >= 0 and block_text[index] in ".!?":
            count += 1
    return count


def _refine_initialism_final_boundaries(
    block_text: str,
    spans: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Conservatively restore parser-missed boundaries after initialisms.

    Only a closed class of surface-form pronouns/demonstratives may trigger
    the repair. Inline explanatory connectors such as e.g. and i.e. never
    trigger it because their following phrase belongs to the same sentence.
    """
    if not spans:
        return spans

    refined: list[tuple[int, int]] = []
    for span_start, span_end in spans:
        pieces: list[tuple[int, int]] = []
        cursor = span_start
        for match in _INITIALISM_PATTERN.finditer(block_text, span_start, span_end):
            if match.group(0).lower() in _INLINE_INITIALISM_CONNECTORS:
                continue
            boundary_end = match.end()
            while (
                boundary_end < span_end
                and block_text[boundary_end] in _CLOSING_PUNCTUATION
            ):
                boundary_end += 1
            visible_next = _next_visible_index(block_text, boundary_end)
            if visible_next >= span_end:
                continue
            if (
                _next_word_after(block_text, boundary_end)
                not in _INITIALISM_SENTENCE_STARTERS
            ):
                continue
            piece_end = _trim_trailing_whitespace(block_text, boundary_end)
            if piece_end > cursor:
                pieces.append((cursor, piece_end))
            cursor = visible_next
        tail_end = _trim_trailing_whitespace(block_text, span_end)
        if cursor < tail_end:
            pieces.append((cursor, tail_end))
        refined.extend(pieces)
    return refined


def _next_word_after(block_text: str, index: int) -> str:
    """The alphabetic word starting at/after ``index`` (R7-1 rework).

    Skips whitespace and opening quotes/brackets, then reads the
    maximal alphabetic run and returns it with its ORIGINAL casing
    (surface-sensitive: "It" is a pronoun, "IT" is an acronym in title
    position). Returns "" when no alphabetic word follows.
    """
    position = index
    while position < len(block_text) and (
        block_text[position].isspace() or block_text[position] in _SENTENCE_STARTERS
    ):
        position += 1
    end = position
    while end < len(block_text) and block_text[end].isalpha():
        end += 1
    return block_text[position:end]


def _repair_initialism_splits(
    block_text: str,
    spans: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Merge adjacent spans whose boundary falls inside an initialism.

    A split point strictly inside an initialism match (e.g. between
    ``U.`` and ``K.`` of ``U.K.``) is illegal: the spans are merged.
    A split at the initialism's FINAL period is legal (``... U.K. It
    led ...``) and is preserved. Only spans separated by whitespace
    are merged, preserving the whitespace-gap invariant.
    """
    matches = list(_INITIALISM_PATTERN.finditer(block_text))
    if not matches or len(spans) < 2:
        return spans

    repaired = list(spans)
    changed = True
    while changed:
        changed = False
        merged: list[tuple[int, int]] = []
        for span in repaired:
            if merged:
                previous_start, previous_end = merged[-1]
                gap_is_whitespace = not block_text[previous_end:span[0]].strip()
                if gap_is_whitespace and any(
                    match.start() < previous_end < match.end() for match in matches
                ):
                    merged[-1] = (previous_start, span[1])
                    changed = True
                    continue
            merged.append(span)
        repaired = merged
    return repaired


def _protected_boundary_ranges(block_text: str) -> list[tuple[int, int]]:
    """Character ranges whose periods must not become sentence boundaries
    (R7-1 regex v2 guard).

    - Initialism tokens: every period except the token's FINAL period
      is protected (the final period may legitimately end a sentence:
      ``... in the U.K. It led ...`` must split after ``U.K.``).
    - URLs: every period inside the URL is protected.
    """
    ranges: list[tuple[int, int]] = []
    for match in _INITIALISM_PATTERN.finditer(block_text):
        ranges.append((match.start(), match.end() - 1))
    for match in _URL_PATTERN.finditer(block_text):
        # \S+ greedily glues trailing sentence punctuation onto the URL
        # ("Visit https://example.com." -> match includes the final
        # period). Strip trailing syntactic punctuation so the URL's
        # INTERNAL periods stay protected while a sentence terminator
        # AFTER the URL can still act as a boundary (R7-1 rework).
        url_end = match.end()
        while (
            url_end > match.start()
            and block_text[url_end - 1] in _URL_TRAILING_PUNCTUATION
        ):
            url_end -= 1
        if url_end > match.start():
            ranges.append((match.start(), url_end))
    return ranges


def _initialism_ending_at(
    block_text: str,
    *,
    start: int,
    end: int,
) -> str | None:
    """Return the normalized initialism whose final period is end - 1."""
    for match in _INITIALISM_PATTERN.finditer(block_text, start, end):
        if match.end() == end:
            return match.group(0).lower()
    return None


def _segment_sentence_spans_v2(
    block_text: str,
) -> tuple[list[tuple[int, int]], int]:
    """Regex v2 sentence segmentation: v1 algorithm plus initialism and
    URL boundary guards (R7-1 named fallback).

    Used when spaCy / ``en_core_web_sm`` is unavailable, when the text
    is not English, or when a spaCy run fails. Its identity is recorded
    as :data:`REGEX_V2_SEGMENTER_VERSION`; it never masquerades as the
    spaCy main path.
    """
    protected_ranges = _protected_boundary_ranges(block_text)
    spans: list[tuple[int, int]] = []
    start = _next_visible_index(block_text, 0)
    if start >= len(block_text):
        return [], 0

    boundary_count = 0
    index = start
    while index < len(block_text):
        char = block_text[index]
        if char in ".!?":
            if char == "." and index + 1 < len(block_text) and block_text[index + 1] == ".":
                index += 1
                continue

            boundary_end = index + 1
            while (
                boundary_end < len(block_text)
                and block_text[boundary_end] in _CLOSING_PUNCTUATION
            ):
                boundary_end += 1

            if _is_sentence_boundary_v2(block_text, start, boundary_end, protected_ranges):
                trimmed_end = _trim_trailing_whitespace(block_text, boundary_end)
                if trimmed_end > start:
                    spans.append((start, trimmed_end))
                    boundary_count += 1
                start = _next_visible_index(block_text, boundary_end)
                index = start
                continue
        index += 1

    final_end = _trim_trailing_whitespace(block_text, len(block_text))
    if start < final_end:
        spans.append((start, final_end))
    return spans, boundary_count


def _is_sentence_boundary_v2(
    block_text: str,
    start: int,
    boundary_end: int,
    protected_ranges: list[tuple[int, int]],
) -> bool:
    if boundary_end >= len(block_text):
        return True

    visible_next = _next_visible_index(block_text, boundary_end)
    if visible_next >= len(block_text):
        return True

    punct_index = boundary_end - 1
    while punct_index >= start and block_text[punct_index] in _CLOSING_PUNCTUATION:
        punct_index -= 1

    if any(
        range_start <= punct_index < range_end
        for range_start, range_end in protected_ranges
    ):
        return False

    if (
        punct_index > start
        and block_text[punct_index] == "."
        and block_text[punct_index - 1].isdigit()
        and block_text[visible_next].isdigit()
    ):
        return False

    initialism = None
    if punct_index >= start and block_text[punct_index] == ".":
        initialism = _initialism_ending_at(
            block_text,
            start=start,
            end=punct_index + 1,
        )
    if initialism is not None:
        if initialism in _INLINE_INITIALISM_CONNECTORS:
            return False
        return (
            _next_word_after(block_text, boundary_end)
            in _INITIALISM_SENTENCE_STARTERS
        )

    tail = block_text[max(start, boundary_end - 20):boundary_end].lower()
    if block_text[punct_index] == "." and any(
        tail.endswith(abbreviation) for abbreviation in _ABBREVIATION_SUFFIXES
    ):
        return False

    next_char = block_text[visible_next]
    return (
        next_char.isupper()
        or next_char.isdigit()
        or next_char in _SENTENCE_STARTERS
        or "一" <= next_char <= "鿿"
    )


def _segment_sentence_spans(block_text: str) -> tuple[list[tuple[int, int]], int]:
    spans: list[tuple[int, int]] = []
    start = _next_visible_index(block_text, 0)
    if start >= len(block_text):
        return [], 0

    boundary_count = 0
    index = start
    while index < len(block_text):
        char = block_text[index]
        if char in ".!?":
            if char == "." and index + 1 < len(block_text) and block_text[index + 1] == ".":
                index += 1
                continue

            boundary_end = index + 1
            while (
                boundary_end < len(block_text)
                and block_text[boundary_end] in _CLOSING_PUNCTUATION
            ):
                boundary_end += 1

            if _is_sentence_boundary(block_text, start, boundary_end):
                trimmed_end = _trim_trailing_whitespace(block_text, boundary_end)
                if trimmed_end > start:
                    spans.append((start, trimmed_end))
                    boundary_count += 1
                start = _next_visible_index(block_text, boundary_end)
                index = start
                continue
        index += 1

    final_end = _trim_trailing_whitespace(block_text, len(block_text))
    if start < final_end:
        spans.append((start, final_end))
    return spans, boundary_count


def _is_sentence_boundary(block_text: str, start: int, boundary_end: int) -> bool:
    if boundary_end >= len(block_text):
        return True

    visible_next = _next_visible_index(block_text, boundary_end)
    if visible_next >= len(block_text):
        return True

    # Locate the sentence-ending punctuation (. ! ?) that triggered this
    # check. ``boundary_end`` is advanced past the punctuation and any
    # trailing closing punctuation (quotes, brackets), so scan backwards
    # through closing punctuation to find the actual sentence terminator.
    punct_index = boundary_end - 1
    while punct_index >= start and block_text[punct_index] in _CLOSING_PUNCTUATION:
        punct_index -= 1

    # Decimal number guard: "digit . digit" is NOT a sentence boundary.
    # This prevents splitting "$2.13 per hour" at "$2." and "3.5 million"
    # at "3.". The dot is a decimal separator, not a sentence terminator.
    if (
        punct_index > start
        and block_text[punct_index] == "."
        and block_text[punct_index - 1].isdigit()
        and block_text[visible_next].isdigit()
    ):
        return False

    tail = block_text[max(start, boundary_end - 20):boundary_end].lower()
    if block_text[boundary_end - 1] == "." and any(
        tail.endswith(abbreviation) for abbreviation in _ABBREVIATION_SUFFIXES
    ):
        return False

    next_char = block_text[visible_next]
    return (
        next_char.isupper()
        or next_char.isdigit()
        or next_char in _SENTENCE_STARTERS
        or "\u4e00" <= next_char <= "\u9fff"
    )


def _segment_clause_spans(block_text: str) -> list[tuple[int, int]]:
    matches = list(_CLAUSE_BOUNDARY_PATTERN.finditer(block_text))
    if not matches:
        if _word_count(block_text) <= 20:
            start = _next_visible_index(block_text, 0)
            end = _trim_trailing_whitespace(block_text, len(block_text))
            return [(start, end)] if start < end else []
        return []

    spans: list[tuple[int, int]] = []
    start = _next_visible_index(block_text, 0)
    for match in matches:
        match_text = match.group(0)
        split_end = match.start() + 1 if match_text.startswith(",") else match.end()
        end = _trim_trailing_whitespace(block_text, split_end)
        if end > start:
            spans.append((start, end))
        start = _next_visible_index(block_text, split_end)

    final_end = _trim_trailing_whitespace(block_text, len(block_text))
    if start < final_end:
        spans.append((start, final_end))
    return spans


def _segment_fallback_windows(block_text: str) -> list[tuple[int, int]]:
    tokens = list(_WORD_PATTERN.finditer(block_text))
    if not tokens:
        return []

    spans: list[tuple[int, int]] = []
    for start_index in range(0, len(tokens), FALLBACK_WINDOW_WORD_COUNT):
        window = tokens[start_index:start_index + FALLBACK_WINDOW_WORD_COUNT]
        spans.append((window[0].start(), window[-1].end()))
    return spans


def _sentence_boundary_quality(segment_text: str) -> str:
    if len(segment_text) > 280:
        return "low"
    return "normal"


def _spans_cover_visible_text(text: str, spans: list[tuple[int, int]]) -> bool:
    if not spans:
        return False

    first_visible = _next_visible_index(text, 0)
    if first_visible >= len(text):
        return False
    if text[first_visible:spans[0][0]].strip():
        return False

    previous_end = spans[0][1]
    if spans[0][0] >= previous_end:
        return False
    for start, end in spans[1:]:
        if start >= end or start < previous_end:
            return False
        if text[previous_end:start].strip():
            return False
        previous_end = end

    final_end = _trim_trailing_whitespace(text, len(text))
    return not text[previous_end:final_end].strip()


def _classify_unit_type(block_text: str) -> str:
    lines = [line.strip() for line in block_text.splitlines() if line.strip()]
    if not lines:
        return "unknown"
    if all(_LIST_LINE_PATTERN.match(line) for line in lines):
        return "list"
    if all(line.startswith(">") for line in lines):
        return "quote"
    if len(lines) == 1:
        line = lines[0]
        if len(line) <= 80 and len(line.split()) <= 12 and not re.search(r"[.!?;:]", line):
            return "heading"
    return "body"


def _build_unit_label(block_text: str, unit_type: str) -> str | None:
    if unit_type != "heading":
        return None
    return " ".join(block_text.split())


def _extract_heading_level(payload: dict[str, Any]) -> int | None:
    """A5: extract a 1-based heading level from a stable block payload.

    The Markdown ecosystem refactor stores the heading level under
    ``payload_json.level`` (1-based, matching ATX ``#`` count). Returns
    ``None`` when the key is absent or the value is not a positive
    integer. A non-positive or non-integer value is silently ignored
    so a malformed payload can never corrupt the heading_level field.
    """
    raw = payload.get("level")
    if isinstance(raw, bool):
        # ``bool`` is a subclass of ``int`` — reject it explicitly so
        # ``True`` / ``False`` never become heading levels 1 / 0.
        return None
    if not isinstance(raw, int):
        return None
    if raw < 1:
        return None
    return raw


def _derive_table_role(block_type: str) -> str | None:
    """A5: map a stable block_type to a snapshot table_role.

    The snapshot ``reader_source_block`` payload carries a
    ``tableRole`` field so the Web reading surface can render table
    structure without re-parsing the canonical text. Only table-family
    block types get a non-None role; everything else returns ``None``
    so legacy snapshots (no table blocks) stay byte-for-byte stable.
    """
    if block_type == "table":
        return "table"
    if block_type == "table_row":
        return "row"
    if block_type == "table_cell":
        return "cell"
    return None


def _extract_code_language(payload: dict[str, Any]) -> str | None:
    """L1: extract the fenced code language from a code_block payload.

    The parser stores the fence info string under ``payload_json.language``
    (``""`` for indented / language-less code). Returns ``None`` when the
    key is absent, not a string, or empty — a language-less code block
    projects ``codeLanguage: null`` so the frontend renders no badge.
    """
    raw = payload.get("language")
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped or None


def _extract_is_header(payload: dict[str, Any]) -> bool | None:
    """L1: extract the header marker from a table_row / table_cell payload."""
    raw = payload.get("is_header")
    if isinstance(raw, bool):
        return raw
    return None


def _extract_alignment_value(payload: dict[str, Any]) -> str | None:
    """L1: extract a single cell alignment (left / center / right / default)."""
    raw = payload.get("alignment")
    if raw in {"left", "center", "right", "default"}:
        return str(raw)
    return None


def _extract_alignments(payload: dict[str, Any]) -> tuple[str, ...] | None:
    """L1: extract the column alignment array from a table wrapper payload."""
    raw = payload.get("alignments")
    if not isinstance(raw, list):
        return None
    values = tuple(str(v) for v in raw if isinstance(v, str))
    return values or None


def _extract_header_rows(payload: dict[str, Any]) -> int | None:
    """L1: extract the header row count from a table wrapper payload."""
    raw = payload.get("header_rows")
    if isinstance(raw, bool):
        return None
    if not isinstance(raw, int):
        return None
    if raw < 0:
        return None
    return raw


def _next_visible_index(text: str, start: int) -> int:
    index = start
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _trim_trailing_whitespace(text: str, end: int) -> int:
    index = end
    while index > 0 and text[index - 1].isspace():
        index -= 1
    return index


def _word_count(text: str) -> int:
    return len(_WORD_PATTERN.findall(text))


def validate_reading_base_build_result(result: ReadingBaseBuildResult) -> None:
    base_text = result.base.text
    units = list(result.units)
    segments = list(result.anchor_segments)

    if not units or not segments:
        raise ValueError("non-empty stable base must produce units and anchor segments")

    _validate_absolute_spans(base_text, units)
    _validate_unit_anchors(base_text, units, segments)


def _validate_absolute_spans(base_text: str, units: list[BuiltReadingUnit]) -> None:
    previous_end: int | None = None
    for unit in units:
        sliced = slice_by_utf16_offsets(base_text, unit.base_start_utf16, unit.base_end_utf16)
        if sliced != unit.text:
            raise ValueError(f"unit {unit.unit_id} does not round-trip to stable base text")
        if compute_text_range_hash(unit.text) != unit.text_hash:
            raise ValueError(f"unit {unit.unit_id} text hash mismatch")
        if previous_end is None:
            leading = slice_by_utf16_offsets(base_text, 0, unit.base_start_utf16)
            if leading and leading.strip():
                raise ValueError("leading gap before first unit must be whitespace only")
        else:
            if unit.base_start_utf16 < previous_end:
                raise ValueError("unit spans must not overlap")
            gap = slice_by_utf16_offsets(base_text, previous_end, unit.base_start_utf16)
            if gap and gap.strip():
                raise ValueError("unit gaps must be whitespace only")
        previous_end = unit.base_end_utf16

    if previous_end is None:
        return

    trailing = slice_by_utf16_offsets(base_text, previous_end, result_length_utf16(base_text))
    if trailing and trailing.strip():
        raise ValueError("trailing gap after last unit must be whitespace only")


def _validate_unit_anchors(
    base_text: str,
    units: list[BuiltReadingUnit],
    segments: list[BuiltAnchorSegment],
) -> None:
    units_by_id = {unit.unit_id: unit for unit in units}
    grouped: dict[str, list[BuiltAnchorSegment]] = {unit.unit_id: [] for unit in units}

    for segment in segments:
        unit = units_by_id.get(segment.unit_id)
        if unit is None:
            raise ValueError(f"anchor segment {segment.anchor_segment_id} references unknown unit")
        if (
            segment.base_start_utf16 < unit.base_start_utf16
            or segment.base_end_utf16 > unit.base_end_utf16
        ):
            raise ValueError(f"anchor segment {segment.anchor_segment_id} is outside its unit")
        absolute_text = slice_by_utf16_offsets(
            base_text,
            segment.base_start_utf16,
            segment.base_end_utf16,
        )
        local_text = slice_by_utf16_offsets(
            unit.text,
            segment.unit_start_utf16,
            segment.unit_end_utf16,
        )
        if absolute_text != segment.text or local_text != segment.text:
            raise ValueError(f"anchor segment {segment.anchor_segment_id} does not round-trip")
        if compute_text_range_hash(segment.text) != segment.text_hash:
            raise ValueError(f"anchor segment {segment.anchor_segment_id} text hash mismatch")
        if segment.segment_type == "fallback_window" and segment.boundary_quality != "low":
            raise ValueError("fallback_window segments must be marked low quality")
        grouped[segment.unit_id].append(segment)

    for unit in units:
        unit_segments = grouped[unit.unit_id]
        if not unit_segments:
            raise ValueError(f"unit {unit.unit_id} must have at least one anchor segment")
        unit_segments.sort(key=lambda segment: segment.unit_order_index)

        previous_end: int | None = None
        for segment in unit_segments:
            if previous_end is None:
                leading = slice_by_utf16_offsets(unit.text, 0, segment.unit_start_utf16)
                if leading and leading.strip():
                    raise ValueError(f"unit {unit.unit_id} has a non-whitespace leading anchor gap")
            else:
                if segment.unit_start_utf16 < previous_end:
                    raise ValueError(f"unit {unit.unit_id} anchor segments must not overlap")
                gap = slice_by_utf16_offsets(unit.text, previous_end, segment.unit_start_utf16)
                if gap and gap.strip():
                    raise ValueError(f"unit {unit.unit_id} anchor gaps must be whitespace only")
            previous_end = segment.unit_end_utf16

        if previous_end is None:
            continue
        trailing = slice_by_utf16_offsets(unit.text, previous_end, result_length_utf16(unit.text))
        if trailing and trailing.strip():
            raise ValueError(f"unit {unit.unit_id} has a non-whitespace trailing anchor gap")


def result_length_utf16(text: str) -> int:
    return utf16_code_unit_length(text)
