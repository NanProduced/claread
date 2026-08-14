"""``reader_parse_eval_artifact.v1`` — strict typed artifact contract.

This is the **portable, serializable contract** for the Reader
parse-eval first vertical slice (- of the Reader Agentic
Orchestration initiative).

 changes from the previous monolithic ``parse_eval_artifact.py``:

1. **Published layers carry reviewable evidence**, not just counts.
   Each non-empty layer carries either a typed
   :class:`NormalizedLayerOutput` (a closed-shape projection of the
   Reader published-layer output) or a content-addressed
   ``sidecar_ref`` + ``sidecar_sha256`` pair. Count-only summaries
   are no longer sufficient.

2. **Strict typed provenance** is split into
   :class:`ArtifactSourceProvenance`,
   :class:`ArtifactCanonicalizerProvenance`,
   :class:`ArtifactSegmenterProvenance`,
   :class:`ArtifactRunnerProvenance`,
   :class:`ArtifactModelProfileProvenance`, and
   :class:`ArtifactPromptRevisionProvenance`. Fake executors are
   explicitly marked ``is_fake=True`` and must NOT carry real model
   fields. Real executors must populate every model field — no
   arbitrary ``dict`` is allowed.

3. ``artifact_id`` derivation now includes
   ``canonical_text_sha256`` and the schema/producer semantic
   version, so a different canonical text or a producer bump
   automatically invalidates old fixture hashes. It no longer
   depends only on ``sample_id`` + ``clock_token``.

4. The artifact is still **closed-schema** (every model uses
   ``ConfigDict(extra="forbid")``) and strictly typed
   (``StrictStr`` / ``StrictInt`` / ``Literal``). No free-form
   ``dict[str, Any]`` payload is allowed at any level except the
   explicitly-typed per-layer normalized output, whose shape is
   constrained by a discriminated union on ``layer_type``.

5. The artifact never embeds ``render_scene_json``, Plate value,
   legacy ``task`` / ``record`` objects, free-form prompts, raw LLM
   responses, or traces. The gate scans the serialized JSON **keys**
   (not free-form text values) for forbidden markers and fails
   closed.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from .constants import (
    ARTIFACT_SCHEMA_VERSION,
    FNV1A32_LOWERCASE_HEX_RE,
    PRODUCER_SEMANTIC_VERSION,
    PRODUCER_VERSION,
    SHA256_LOWERCASE_HEX_RE,
)

# ---------------------------------------------------------------------------
# Re-used Reader Orchestration literals (mirrored locally, not imported)
# ---------------------------------------------------------------------------
# These mirror the production Literal types in
# ``services/api/app/schemas/reader_orchestration.py`` so a typo'd value
# is rejected at the artifact boundary. They are duplicated LOCALLY (not
# reverse-imported from ``app``) to keep the artifact self-contained —
# evals must be able to consume the JSON without an ``app`` runtime.
# The two copies MUST stay semantically identical.
# ---------------------------------------------------------------------------

ReadingGoalLiteral = Literal["daily_reading", "exam"]
ReadingVariantLiteral = Literal[
    "beginner_reading",
    "intermediate_reading",
    "intensive_reading",
    "gaokao",
    "cet",
    "kaoyan",
    "tem",
    "ielts_toefl",
]
ReaderLayerTypeLiteral = Literal[
    "translation",
    "vocabulary",
    "grammar_note",
    "sentence_analysis",
    "semantic_outline",
]
ReaderLayerTargetScopeLiteral = Literal[
    "unit",
    "anchor_segment",
    "unit_range",
    "record",
]
AnchorSegmentTypeLiteral = Literal["sentence", "clause", "fallback_window"]
ReaderBoundaryQualityLiteral = Literal["normal", "low"]
ReaderUnitTypeLiteral = Literal[
    "body",
    "heading",
    "list",
    "quote",
    "unknown",
    "fallback",
]
HashAlgorithmLiteral = Literal["fnv1a32-utf16"]
Sha256AlgorithmLiteral = Literal["sha256"]
CanonicalizerVersionLiteral = Literal[
    "exact_canonical_text_v1",
    "reader_base_low_impact_v1",
]
ExecutorModeLiteral = Literal["fake", "real"]
CompletionStatusLiteral = Literal["complete", "incomplete"]
ArtifactSourceKindLiteral = Literal[
    "golden_sample",
    "reader_record",
    "synthetic",
]
LayerOutputKindLiteral = Literal[
    "normalized_output",
    "sidecar_ref",
    "empty",
]


# ---------------------------------------------------------------------------
# Sample / document identity
# ---------------------------------------------------------------------------


class SampleIdentity(BaseModel):
    """Closed-shape projection of a :class:`GoldenSample` identity.

    Only the fields needed to identify the fixture and reproduce the
    run are carried. The full article text lives in
    :class:`DocumentIdentity` (hash + length only — the raw text is
    NOT embedded in the artifact).
    """

    model_config = ConfigDict(extra="forbid")

    sample_id: StrictStr
    shape: StrictStr
    source_attribution: StrictStr
    reading_goal: ReadingGoalLiteral
    reading_variant: ReadingVariantLiteral
    expected_char_band: tuple[StrictInt, StrictInt]
    expected_word_band: tuple[StrictInt, StrictInt]
    notes: StrictStr

    @model_validator(mode="after")
    def _validate_bands(self) -> SampleIdentity:
        for band_name, band in (
            ("expected_char_band", self.expected_char_band),
            ("expected_word_band", self.expected_word_band),
        ):
            if len(band) != 2:
                raise ValueError(f"{band_name} must be a (lo, hi) pair")
            lo, hi = band
            if lo < 0 or hi < 0 or hi < lo:
                raise ValueError(
                    f"{band_name} pair invalid: ({lo}, {hi})"
                )
        return self


class DocumentIdentity(BaseModel):
    """Canonical-text identity facts.

    The raw canonical text is intentionally NOT embedded. Only its
    SHA-256 hash, UTF-16 length, plain char length and word count are
    carried, plus a short preview used for display-only. The preview
    is NOT a truth field — the gate ignores it.
    """

    model_config = ConfigDict(extra="forbid")

    canonical_text_sha256: StrictStr
    canonical_text_length_utf16: StrictInt = Field(ge=1)
    canonical_text_length_chars: StrictInt = Field(ge=1)
    word_count: StrictInt = Field(ge=0)
    canonical_text_preview: StrictStr = Field(max_length=200)
    hash_algorithm: Sha256AlgorithmLiteral = "sha256"

    @field_validator("canonical_text_sha256")
    @classmethod
    def _validate_sha256(cls, v: str) -> str:
        if not SHA256_LOWERCASE_HEX_RE.match(v):
            raise ValueError(
                "canonical_text_sha256 must be 64 lowercase hex chars"
            )
        return v


# ---------------------------------------------------------------------------
# Anchor map (navigation units + anchor segments)
# ---------------------------------------------------------------------------


class NavigationUnitFact(BaseModel):
    """Closed-shape projection of one navigation unit.

    Mirrors the field subset of
    :class:`ReaderSnapshotNavigationUnit` that is part of the durable
    Reader contract. Plate-only fields (e.g. ``label`` rendering
    hints) are not carried here.
    """

    model_config = ConfigDict(extra="forbid")

    unit_id: StrictStr
    order_index: StrictInt = Field(ge=1)
    unit_type: ReaderUnitTypeLiteral
    boundary_quality: ReaderBoundaryQualityLiteral = "normal"
    base_start_utf16: StrictInt = Field(ge=0)
    base_end_utf16: StrictInt = Field(gt=0)
    text_hash: StrictStr
    hash_algorithm: HashAlgorithmLiteral = "fnv1a32-utf16"

    @field_validator("text_hash")
    @classmethod
    def _validate_text_hash(cls, v: str) -> str:
        if not FNV1A32_LOWERCASE_HEX_RE.match(v):
            raise ValueError(
                "navigation unit text_hash must be 8 lowercase hex chars"
            )
        return v

    @model_validator(mode="after")
    def _validate_offsets(self) -> NavigationUnitFact:
        if self.base_end_utf16 <= self.base_start_utf16:
            raise ValueError(
                f"unit {self.unit_id!r}: base_end_utf16 must be > base_start_utf16"
            )
        return self


class AnchorSegmentFact(BaseModel):
    """Closed-shape projection of one anchor segment.

    Mirrors the field subset of
    :class:`ReaderSnapshotAnchorSegment` that is part of the durable
    Reader contract.
    """

    model_config = ConfigDict(extra="forbid")

    anchor_segment_id: StrictStr
    sentence_id: StrictStr
    paragraph_id: StrictStr
    unit_id: StrictStr
    order_index: StrictInt = Field(ge=1)
    unit_order_index: StrictInt = Field(ge=1)
    segment_type: AnchorSegmentTypeLiteral
    boundary_quality: ReaderBoundaryQualityLiteral = "normal"
    base_start_utf16: StrictInt = Field(ge=0)
    base_end_utf16: StrictInt = Field(gt=0)
    unit_start_utf16: StrictInt = Field(ge=0)
    unit_end_utf16: StrictInt = Field(gt=0)
    text_hash: StrictStr
    hash_algorithm: HashAlgorithmLiteral = "fnv1a32-utf16"

    @field_validator("text_hash")
    @classmethod
    def _validate_text_hash(cls, v: str) -> str:
        if not FNV1A32_LOWERCASE_HEX_RE.match(v):
            raise ValueError(
                "anchor segment text_hash must be 8 lowercase hex chars"
            )
        return v

    @model_validator(mode="after")
    def _validate_offsets(self) -> AnchorSegmentFact:
        if self.base_end_utf16 <= self.base_start_utf16:
            raise ValueError(
                f"anchor segment {self.anchor_segment_id!r}: "
                "base_end_utf16 must be > base_start_utf16"
            )
        if self.unit_end_utf16 <= self.unit_start_utf16:
            raise ValueError(
                f"anchor segment {self.anchor_segment_id!r}: "
                "unit_end_utf16 must be > unit_start_utf16"
            )
        if not (
            self.unit_start_utf16 <= self.base_start_utf16
            and self.base_end_utf16 <= self.unit_end_utf16
        ):
            raise ValueError(
                f"anchor segment {self.anchor_segment_id!r}: "
                "base range must lie within unit range"
            )
        return self


class AnchorMap(BaseModel):
    """Closed-shape anchor map.

    Ordering is fixed: navigation units are sorted by ``order_index``
    ascending; anchor segments are sorted by
    ``(unit_order_index, order_index)`` ascending. The producer
    normalises the input to this order before validation so two runs
    over the same input produce identical bytes.
    """

    model_config = ConfigDict(extra="forbid")

    navigation_units: list[NavigationUnitFact] = Field(default_factory=list)
    anchor_segments: list[AnchorSegmentFact] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Published enhancement layer — typed normalized output
# ---------------------------------------------------------------------------
#
# Each non-empty published layer carries EITHER a typed
# ``NormalizedLayerOutput`` (a closed-shape projection of the Reader
# published-layer output) OR a content-addressed ``sidecar_ref`` +
# ``sidecar_sha256`` pair. Count-only summaries are NOT sufficient
# for parse-quality eval.
#
# The discriminated union is keyed on ``layer_type``. V1 covers
# ``translation`` and ``vocabulary`` (the two layer types with real
# Reader schema fixtures). Other layer types may use ``sidecar_ref``
# until their typed normalized output is added in a follow-up.
# ---------------------------------------------------------------------------


class TranslationGroupFact(BaseModel):
    """One translation group — reviewable normalized content.

    This is a closed-shape projection of
    :class:`TranslationGroup` from the Reader schema. The
    ``translated_text`` is the reviewable Simplified-Chinese output.
    No Plate value, render scene, or raw LLM response is carried.
    """

    model_config = ConfigDict(extra="forbid")

    group_id: StrictStr
    anchor_segment_ids: tuple[StrictStr, ...] = Field(min_length=1)
    source_text_hash: StrictStr
    translated_text: StrictStr = Field(min_length=1)

    @field_validator("source_text_hash")
    @classmethod
    def _validate_source_text_hash(cls, v: str) -> str:
        if not FNV1A32_LOWERCASE_HEX_RE.match(v):
            raise ValueError(
                "translation group source_text_hash must be 8 lowercase hex chars"
            )
        return v


class TranslationNormalizedOutput(BaseModel):
    """Normalized translation layer output (reviewable)."""

    model_config = ConfigDict(extra="forbid")

    layer_type: Literal["translation"] = "translation"
    groups: list[TranslationGroupFact] = Field(min_length=1)


class VocabularyItemFact(BaseModel):
    """One vocabulary highlight item — reviewable normalized content.

    This is a closed-shape projection of
    :class:`VocabularyHighlightItem` from the Reader schema. The
    anchor is projected to stable references (unit_id +
    anchor_segment_id + selected_text_hash) so the gate can verify
    anchor consistency without embedding the full anchor object.
    """

    model_config = ConfigDict(extra="forbid")

    headword: StrictStr
    brief_explanation: StrictStr
    reason: StrictStr
    anchor_unit_id: StrictStr
    anchor_segment_id: StrictStr
    selected_text_hash: StrictStr

    @field_validator("selected_text_hash")
    @classmethod
    def _validate_selected_text_hash(cls, v: str) -> str:
        if not FNV1A32_LOWERCASE_HEX_RE.match(v):
            raise ValueError(
                "vocabulary item selected_text_hash must be 8 lowercase hex chars"
            )
        return v


class VocabularyNormalizedOutput(BaseModel):
    """Normalized vocabulary layer output (reviewable)."""

    model_config = ConfigDict(extra="forbid")

    layer_type: Literal["vocabulary"] = "vocabulary"
    items: list[VocabularyItemFact] = Field(min_length=1)


# Discriminated union over ``layer_type``. V1 covers translation +
# vocabulary. Adding a new typed normalized output requires extending
# this union and bumping ``PRODUCER_SEMANTIC_VERSION``.
NormalizedLayerOutput = Annotated[
    TranslationNormalizedOutput | VocabularyNormalizedOutput,
    Field(discriminator="layer_type"),
]


class PublishedLayerFact(BaseModel):
    """Per-layer summary with reviewable evidence.

    Each non-empty layer MUST carry either a typed
    ``normalized_output`` + ``normalized_output_sha256`` OR a
    content-addressed ``sidecar_ref`` + ``sidecar_sha256`` pair.
    Empty layers (``item_count == 0``) use ``output_kind="empty"``.

    The ``normalized_output_sha256`` is the SHA-256 over the canonical
    JSON serialization of ``normalized_output`` (sorted keys,
    ensure_ascii=False). The producer computes it; the gate
    recomputes it and verifies equality.
    """

    model_config = ConfigDict(extra="forbid")

    layer_id: StrictStr
    layer_type: ReaderLayerTypeLiteral
    target_scope: ReaderLayerTargetScopeLiteral
    target_key: StrictStr
    schema_version: StrictInt = Field(ge=1)
    item_count: StrictInt = Field(ge=0)

    output_kind: LayerOutputKindLiteral
    normalized_output: NormalizedLayerOutput | None = None
    normalized_output_sha256: StrictStr | None = None
    sidecar_ref: StrictStr | None = None
    sidecar_sha256: StrictStr | None = None

    @field_validator("normalized_output_sha256", "sidecar_sha256")
    @classmethod
    def _validate_optional_sha256(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not SHA256_LOWERCASE_HEX_RE.match(v):
            raise ValueError(
                "sha256 fields must be 64 lowercase hex chars or null"
            )
        return v

    @model_validator(mode="after")
    def _validate_target_shape(self) -> PublishedLayerFact:
        if self.target_scope == "record" and self.target_key != "record":
            raise ValueError(
                f"layer {self.layer_id!r}: target_scope='record' requires "
                f"target_key='record', got {self.target_key!r}"
            )
        return self

    @model_validator(mode="after")
    def _validate_output_consistency(self) -> PublishedLayerFact:
        if self.output_kind == "normalized_output":
            if self.normalized_output is None:
                raise ValueError(
                    f"layer {self.layer_id!r}: output_kind='normalized_output' "
                    "requires normalized_output field"
                )
            if self.normalized_output_sha256 is None:
                raise ValueError(
                    f"layer {self.layer_id!r}: output_kind='normalized_output' "
                    "requires normalized_output_sha256"
                )
            if self.sidecar_ref is not None or self.sidecar_sha256 is not None:
                raise ValueError(
                    f"layer {self.layer_id!r}: output_kind='normalized_output' "
                    "must not carry sidecar fields"
                )
            if self.item_count <= 0:
                raise ValueError(
                    f"layer {self.layer_id!r}: output_kind='normalized_output' "
                    "requires item_count >= 1"
                )
        elif self.output_kind == "sidecar_ref":
            if self.sidecar_ref is None or self.sidecar_sha256 is None:
                raise ValueError(
                    f"layer {self.layer_id!r}: output_kind='sidecar_ref' "
                    "requires sidecar_ref and sidecar_sha256"
                )
            if self.normalized_output is not None:
                raise ValueError(
                    f"layer {self.layer_id!r}: output_kind='sidecar_ref' "
                    "must not carry normalized_output"
                )
            if self.item_count <= 0:
                raise ValueError(
                    f"layer {self.layer_id!r}: output_kind='sidecar_ref' "
                    "requires item_count >= 1"
                )
        elif self.output_kind == "empty":
            if self.normalized_output is not None:
                raise ValueError(
                    f"layer {self.layer_id!r}: output_kind='empty' "
                    "must not carry normalized_output"
                )
            if self.sidecar_ref is not None or self.sidecar_sha256 is not None:
                raise ValueError(
                    f"layer {self.layer_id!r}: output_kind='empty' "
                    "must not carry sidecar fields"
                )
            if self.item_count != 0:
                raise ValueError(
                    f"layer {self.layer_id!r}: output_kind='empty' "
                    "requires item_count == 0"
                )
        return self


class PublishedLayerSummary(BaseModel):
    """Aggregate layer summary."""

    model_config = ConfigDict(extra="forbid")

    layer_counts: dict[ReaderLayerTypeLiteral, StrictInt] = Field(
        default_factory=dict
    )
    layers: list[PublishedLayerFact] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Strict typed provenance
# ---------------------------------------------------------------------------


class ArtifactSourceProvenance(BaseModel):
    """Typed source identity of the artifact input.

    Records where the canonical text came from (golden sample,
    reader record, or synthetic). This replaces the previous
    implicit ``sample_id``-only identity.
    """

    model_config = ConfigDict(extra="forbid")

    source_kind: ArtifactSourceKindLiteral
    source_id: StrictStr
    source_shape: StrictStr | None = None
    source_attribution: StrictStr | None = None


class ArtifactCanonicalizerProvenance(BaseModel):
    """Typed canonicalizer pipeline identity."""

    model_config = ConfigDict(extra="forbid")

    canonicalizer_version: CanonicalizerVersionLiteral
    hash_algorithm: Sha256AlgorithmLiteral = "sha256"


class ArtifactSegmenterProvenance(BaseModel):
    """Typed segmenter / builder pipeline identity."""

    model_config = ConfigDict(extra="forbid")

    segmenter_version: StrictStr = Field(min_length=1)
    builder_version: StrictStr = Field(min_length=1)
    hash_algorithm: HashAlgorithmLiteral = "fnv1a32-utf16"


class ArtifactRunnerProvenance(BaseModel):
    """Typed pipeline runner identity + completion status.

    For fixture-grade artifacts (no smoke harness), ``record_id`` /
    ``base_id`` are deterministic markers derived from the sample id,
    ``generation`` is fixed to 1, and ``completion_status`` is
    ``incomplete`` with an explicit reason list. The gate verifies
    field presence + shape, not semantic completion.
    """

    model_config = ConfigDict(extra="forbid")

    executor_mode: ExecutorModeLiteral
    executor_note: StrictStr | None = None
    runner_version: StrictStr = Field(min_length=1)
    record_id: StrictStr
    base_id: StrictStr
    generation: StrictInt = Field(ge=1)
    last_event_sequence: StrictInt = Field(ge=0)
    total_ticks: StrictInt = Field(ge=0)
    total_jobs: StrictInt = Field(ge=0)
    stopped_reason: StrictStr
    completion_status: CompletionStatusLiteral
    completion_reasons: tuple[StrictStr, ...] = ()


class ArtifactModelProfileProvenance(BaseModel):
    """Typed model profile. Fake executors are explicitly marked.

    ``is_fake=True`` means the layer was produced by a deterministic
    fake executor (e.g. ``DevFakeTranslationExecutor``). Real model
    fields MUST be empty when ``is_fake=True``.

    ``is_fake=False`` means a real LLM produced the layer. All real
    model fields MUST be populated — no arbitrary ``dict`` is allowed.
    """

    model_config = ConfigDict(extra="forbid")

    is_fake: Literal[True, False]
    model_provider: StrictStr | None = None
    model_name: StrictStr | None = None
    model_profile: StrictStr | None = None

    @model_validator(mode="after")
    def _validate_fake_consistency(self) -> ArtifactModelProfileProvenance:
        if self.is_fake:
            if (
                self.model_provider is not None
                or self.model_name is not None
                or self.model_profile is not None
            ):
                raise ValueError(
                    "fake executor (is_fake=True) must not carry real model fields"
                )
        else:
            if not (self.model_provider and self.model_name and self.model_profile):
                raise ValueError(
                    "real executor (is_fake=False) requires model_provider, "
                    "model_name, and model_profile to be non-empty"
                )
        return self


class ArtifactPromptRevisionProvenance(BaseModel):
    """Typed prompt revision. Fake executors carry no prompt revision.

    ``is_fake=True`` means no real prompt was used (deterministic fake
    executor). ``prompt_revision`` MUST be ``None``.

    ``is_fake=False`` means a real prompt revision was used.
    ``prompt_revision`` MUST be a non-empty string.
    """

    model_config = ConfigDict(extra="forbid")

    is_fake: Literal[True, False]
    prompt_revision: StrictStr | None = None

    @model_validator(mode="after")
    def _validate_fake_consistency(self) -> ArtifactPromptRevisionProvenance:
        if self.is_fake:
            if self.prompt_revision is not None:
                raise ValueError(
                    "fake executor (is_fake=True) must not carry prompt_revision"
                )
        else:
            if not self.prompt_revision:
                raise ValueError(
                    "real executor (is_fake=False) requires non-empty prompt_revision"
                )
        return self


class ArtifactIdSemanticInputs(BaseModel):
    """The semantic inputs that derived ``artifact_id``.

    Recorded in :class:`ArtifactProvenance` so the gate can verify
    that ``artifact_id`` depends on ``canonical_text_sha256`` and
    ``schema_version`` + ``producer_semantic_version``, not just
    ``sample_id`` + ``clock_token``.
    """

    model_config = ConfigDict(extra="forbid")

    canonical_text_sha256: StrictStr
    schema_version: StrictStr
    producer_semantic_version: StrictStr
    source_id: StrictStr
    deterministic_clock_token: StrictStr

    @field_validator("canonical_text_sha256")
    @classmethod
    def _validate_sha256(cls, v: str) -> str:
        if not SHA256_LOWERCASE_HEX_RE.match(v):
            raise ValueError(
                "artifact_id_semantic_inputs.canonical_text_sha256 must be 64 lowercase hex chars"
            )
        return v


class ArtifactProvenance(BaseModel):
    """Producer identity + deterministic clock marker.

    The ``artifact_id_semantic_inputs`` field records the canonical
    inputs that derived ``artifact_id`` so the gate can verify the
    derivation. This makes the artifact self-describing: a reviewer
    can see that ``artifact_id`` depends on
    ``canonical_text_sha256`` + ``schema_version`` +
    ``producer_semantic_version``, not just ``sample_id`` + clock.
    """

    model_config = ConfigDict(extra="forbid")

    producer_module: StrictStr = Field(min_length=1)
    producer_version: StrictStr = Field(min_length=1)
    producer_semantic_version: StrictStr = Field(min_length=1)
    deterministic_clock_token: StrictStr = Field(min_length=1)
    produced_at_iso_utc: StrictStr | None = None
    forbidden_fields_present: Literal[False] = False
    artifact_id_semantic_inputs: ArtifactIdSemanticInputs


# ---------------------------------------------------------------------------
# Top-level artifact
# ---------------------------------------------------------------------------


class ParseEvalArtifactV1(BaseModel):
    """Top-level ``reader_parse_eval_artifact.v1`` contract.

    All nested models use ``extra="forbid"``. The top-level model
    also forbids extra fields so the gate can detect schema drift
    early.

    The ``artifact_id`` is derived from
    ``canonical_text_sha256 | schema_version | producer_semantic_version |
    source_id | deterministic_clock_token`` (see
    :class:`ArtifactIdSemanticInputs`). This ensures a different
    canonical text or a producer bump automatically invalidates old
    fixture hashes.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["reader_parse_eval_artifact.v1"] = (
        ARTIFACT_SCHEMA_VERSION
    )
    artifact_id: StrictStr
    sample: SampleIdentity
    document: DocumentIdentity
    source_provenance: ArtifactSourceProvenance
    canonicalizer_provenance: ArtifactCanonicalizerProvenance
    segmenter_provenance: ArtifactSegmenterProvenance
    anchor_map: AnchorMap
    published_layers: PublishedLayerSummary
    runner_provenance: ArtifactRunnerProvenance
    model_profile_provenance: ArtifactModelProfileProvenance
    prompt_revision_provenance: ArtifactPromptRevisionProvenance
    artifact_provenance: ArtifactProvenance

    @field_validator("artifact_id")
    @classmethod
    def _validate_artifact_id(cls, v: str) -> str:
        if not SHA256_LOWERCASE_HEX_RE.match(v):
            raise ValueError(
                "artifact_id must be 64 lowercase hex chars (SHA-256)"
            )
        return v

    @model_validator(mode="after")
    def _validate_artifact_id_semantic_inputs(self) -> ParseEvalArtifactV1:
        """Verify the recorded semantic inputs match the actual artifact fields.

        This is a self-describing check: the artifact records what
        went into ``artifact_id`` so the gate (and reviewers) can
        verify the derivation depends on
        ``canonical_text_sha256`` + ``schema_version`` +
        ``producer_semantic_version``, not just ``sample_id`` + clock.
        """
        sem = self.artifact_provenance.artifact_id_semantic_inputs
        if sem.canonical_text_sha256 != self.document.canonical_text_sha256:
            raise ValueError(
                "artifact_id_semantic_inputs.canonical_text_sha256 must equal "
                "document.canonical_text_sha256"
            )
        if sem.schema_version != self.schema_version:
            raise ValueError(
                "artifact_id_semantic_inputs.schema_version must equal "
                "top-level schema_version"
            )
        if sem.producer_semantic_version != (
            self.artifact_provenance.producer_semantic_version
        ):
            raise ValueError(
                "artifact_id_semantic_inputs.producer_semantic_version must equal "
                "artifact_provenance.producer_semantic_version"
            )
        if sem.source_id != self.source_provenance.source_id:
            raise ValueError(
                "artifact_id_semantic_inputs.source_id must equal "
                "source_provenance.source_id"
            )
        if sem.deterministic_clock_token != (
            self.artifact_provenance.deterministic_clock_token
        ):
            raise ValueError(
                "artifact_id_semantic_inputs.deterministic_clock_token must equal "
                "artifact_provenance.deterministic_clock_token"
            )
        return self


__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "PRODUCER_VERSION",
    "PRODUCER_SEMANTIC_VERSION",
    "ReadingGoalLiteral",
    "ReadingVariantLiteral",
    "ReaderLayerTypeLiteral",
    "ReaderLayerTargetScopeLiteral",
    "AnchorSegmentTypeLiteral",
    "ReaderBoundaryQualityLiteral",
    "ReaderUnitTypeLiteral",
    "HashAlgorithmLiteral",
    "Sha256AlgorithmLiteral",
    "CanonicalizerVersionLiteral",
    "ExecutorModeLiteral",
    "CompletionStatusLiteral",
    "ArtifactSourceKindLiteral",
    "LayerOutputKindLiteral",
    "SampleIdentity",
    "DocumentIdentity",
    "NavigationUnitFact",
    "AnchorSegmentFact",
    "AnchorMap",
    "TranslationGroupFact",
    "TranslationNormalizedOutput",
    "VocabularyItemFact",
    "VocabularyNormalizedOutput",
    "NormalizedLayerOutput",
    "PublishedLayerFact",
    "PublishedLayerSummary",
    "ArtifactSourceProvenance",
    "ArtifactCanonicalizerProvenance",
    "ArtifactSegmenterProvenance",
    "ArtifactRunnerProvenance",
    "ArtifactModelProfileProvenance",
    "ArtifactPromptRevisionProvenance",
    "ArtifactIdSemanticInputs",
    "ArtifactProvenance",
    "ParseEvalArtifactV1",
]
