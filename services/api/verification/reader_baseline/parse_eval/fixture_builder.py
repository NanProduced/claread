"""Hermetic **fixture-only** artifact producer.

R1 split: this module is NOT the official producer. It is a
hermetic, fixture-grade helper used by offline gate tests to build
an artifact from a :class:`GoldenSample` without touching the DB,
the LLM, spaCy, or the ``app`` runtime. The official API-side
producer lives in :mod:`.reader_adapter`.

The fixture builder is intentionally explicit about its
fixture-grade nature:

- Every artifact it produces carries ``executor_mode="fake"`` in
  :class:`~.schema.ArtifactRunnerProvenance`,
  ``is_fake=True`` in
  :class:`~.schema.ArtifactModelProfileProvenance` and
  :class:`~.schema.ArtifactPromptRevisionProvenance`, and a
  non-empty ``executor_note`` saying "hermetic fixture producer".
- ``artifact_id`` is derived from
  ``canonical_text_sha256 | schema_version |
  producer_semantic_version | source_id |
  deterministic_clock_token`` via SHA-256. Two runs on the same
  fixed sample produce the same id.
- The hermetic anchor map is a conservative paragraph-split
  projection; it does NOT mirror the real sentence segmenter. The
  real anchor map (from a ``ReaderPlateSnapshot`` or from a
  ``ReaderSmokeHarnessResult``) is wired via
  :mod:`.reader_adapter` / Task 5B.

Determinism contract: two consecutive calls on the same
``sample`` (with the same ``deterministic_clock_token``) MUST
produce byte-identical canonical JSON. The gate tests assert this.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .constants import (
    ARTIFACT_SCHEMA_VERSION,
    DEFAULT_DETERMINISTIC_CLOCK_TOKEN,
    FIXTURE_PIPELINE_RUNNER_VERSION,
    PRODUCER_SEMANTIC_VERSION,
    PRODUCER_VERSION,
)
from .schema import (
    AnchorMap,
    AnchorSegmentFact,
    ArtifactCanonicalizerProvenance,
    ArtifactIdSemanticInputs,
    ArtifactModelProfileProvenance,
    ArtifactPromptRevisionProvenance,
    ArtifactProvenance,
    ArtifactRunnerProvenance,
    ArtifactSegmenterProvenance,
    ArtifactSourceProvenance,
    CanonicalizerVersionLiteral,
    DocumentIdentity,
    LegacyBaselineFreeze,
    NavigationUnitFact,
    ParseEvalArtifactV1,
    PublishedLayerSummary,
    SampleIdentity,
)

# ---------------------------------------------------------------------------
# Hermetic version markers (fixture-only)
# ---------------------------------------------------------------------------

#: Canonicalizer version used by the hermetic fixture producer. The
#: hermetic producer does NOT call the real
#: :func:`canonicalize_low_impact_text`; it only strips leading /
#: trailing whitespace and normalises CRLF to LF. This minimal
#: canonicalization is sufficient for the V1 gate (which tests
#: contract / determinism, not semantic canonicalization parity)
#: and avoids any dependency on spaCy / base_builder runtime.
HERMETIC_CANONICALIZER_VERSION: CanonicalizerVersionLiteral = (
    "exact_canonical_text_v1"
)
HERMETIC_BUILDER_VERSION: str = "hermetic_fixture_builder_v1"
HERMETIC_SEGMENTER_VERSION: str = "hermetic_paragraph_splitter_v1"

#: Fixture-only producer module identity. The official producer is
#: ``reader_adapter``; this marker is here so the gate can tell a
#: fixture-grade artifact apart from an API-side artifact.
FIXTURE_PRODUCER_MODULE: str = (
    "services/api/verification/reader_baseline/parse_eval/fixture_builder.py"
)


# ---------------------------------------------------------------------------
# Hermetic hash functions (no ``app`` runtime import)
# ---------------------------------------------------------------------------


def utf16_code_unit_length(text: str) -> int:
    """Return the UTF-16 code-unit length of ``text``.

    Mirrors :func:`app.contracts.annotation.utf16_code_unit_length`
    without importing it, so the producer stays hermetic (no ``app``
    runtime dependency for the V1 fixture path). The two
    implementations MUST stay numerically identical.
    """
    return len(text.encode("utf-16-le")) // 2


def fnv1a32_utf16(text: str) -> str:
    """FNV-1a 32-bit hash over the UTF-16-LE code units of ``text``.

    Mirrors :func:`app.contracts.annotation.compute_text_range_hash`
    without importing it, so the producer stays hermetic. The two
    implementations MUST stay numerically identical.
    """
    encoded = text.encode("utf-16-le")
    hash_value = 0x811C9DC5
    for byte in encoded:
        hash_value ^= byte
        hash_value = (hash_value * 0x01000193) & 0xFFFFFFFF
    return f"{hash_value:08x}"


def sha256_hex(payload: str | bytes) -> str:
    """SHA-256 hex digest of ``payload`` (str is UTF-8 encoded)."""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def canonicalize_hermetic(plain_text: str) -> str:
    """Minimal hermetic canonicalization.

    Strips leading/trailing whitespace and normalises CRLF / CR to LF.
    Does NOT strip invisible Unicode characters or normalise Unicode
    spaces — those concerns belong to the real canonicalizer and are
    out of scope for the V1 fixture path.
    """
    text = plain_text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


#: Regex that splits canonical text into paragraphs on blank-line
#: boundaries. Matches the ``\n(?:[ \t]*\n){1,}`` rule used by the
#: real ``_BLANK_LINE_RUN_PATTERN`` in base_builder for paragraph
#: detection, simplified for hermetic use.
_PARAGRAPH_SPLIT_RE: re.Pattern[str] = re.compile(r"\n[ \t]*\n+")


# ---------------------------------------------------------------------------
# Hermetic anchor map builder
# ---------------------------------------------------------------------------


def build_hermetic_anchor_map(canonical_text: str) -> AnchorMap:
    """Build a fixture-grade anchor map from canonical text.

    Strategy: split on blank-line runs into paragraphs. Each
    non-empty paragraph becomes one ``body`` navigation unit and one
    ``sentence`` anchor segment spanning the entire unit. This is
    deliberately conservative — it does NOT try to mirror the real
    sentence segmenter, only to produce a structurally valid anchor
    map that the gate can verify end-to-end (offsets / hash / text
    consistency).

    The real anchor map (from
    :func:`build_reading_base_from_canonical_text` or from a
    ``ReaderPlateSnapshot``) is wired via :mod:`.reader_adapter` /
    Task 5B.
    """
    if not canonical_text:
        return AnchorMap(navigation_units=[], anchor_segments=[])

    # Split into paragraphs, preserving UTF-16 offsets into the
    # canonical text. We rebuild the paragraph list by walking the
    # regex matches so we can compute exact offsets.
    paragraphs: list[tuple[int, int, str]] = []
    last_end = 0
    for match in _PARAGRAPH_SPLIT_RE.finditer(canonical_text):
        para_text = canonical_text[last_end : match.start()]
        if para_text:
            paragraphs.append((last_end, match.start(), para_text))
        last_end = match.end()
    # Trailing paragraph (no blank-line terminator)
    if last_end < len(canonical_text):
        para_text = canonical_text[last_end:]
        if para_text:
            paragraphs.append((last_end, len(canonical_text), para_text))

    navigation_units: list[NavigationUnitFact] = []
    anchor_segments: list[AnchorSegmentFact] = []
    for order_index, (para_start_char, para_end_char, para_text) in enumerate(
        paragraphs, start=1
    ):
        unit_id = f"unit-{order_index:04d}"
        anchor_segment_id = f"anchor-{order_index:04d}"
        # UTF-16 offsets over the FULL canonical text
        base_start_utf16 = utf16_code_unit_length(
            canonical_text[:para_start_char]
        )
        base_end_utf16 = utf16_code_unit_length(
            canonical_text[:para_end_char]
        )
        text_hash = fnv1a32_utf16(para_text)
        navigation_units.append(
            NavigationUnitFact(
                unit_id=unit_id,
                order_index=order_index,
                unit_type="body",
                boundary_quality="normal",
                base_start_utf16=base_start_utf16,
                base_end_utf16=base_end_utf16,
                text_hash=text_hash,
                hash_algorithm="fnv1a32-utf16",
            )
        )
        anchor_segments.append(
            AnchorSegmentFact(
                anchor_segment_id=anchor_segment_id,
                sentence_id=anchor_segment_id,
                paragraph_id=f"para-{order_index:04d}",
                unit_id=unit_id,
                order_index=1,
                unit_order_index=order_index,
                segment_type="sentence",
                boundary_quality="normal",
                base_start_utf16=base_start_utf16,
                base_end_utf16=base_end_utf16,
                unit_start_utf16=base_start_utf16,
                unit_end_utf16=base_end_utf16,
                text_hash=text_hash,
                hash_algorithm="fnv1a32-utf16",
            )
        )
    return AnchorMap(
        navigation_units=navigation_units,
        anchor_segments=anchor_segments,
    )


# ---------------------------------------------------------------------------
# Fixture-grade legacy baseline (unavailable)
# ---------------------------------------------------------------------------


def build_legacy_baseline_unavailable(sample_id: str) -> LegacyBaselineFreeze:
    """Build the structured ``unavailable`` legacy baseline freeze.

    Per the task spec: the legacy chain requires
    ``READER_BASELINE_REAL_LLM=1`` and a configured model profile to
    actually run. Task 5A is offline-only and never calls the real
    LLM. No qualifying already-existing frozen output is currently
    checked into the repository, so the V1 freeze status is
    ``unavailable`` with a structured reason.

    Task 5B (or a follow-up) may wire a real frozen baseline by
    recording an already-produced render_scene JSON under
    ``verification/reader_baseline/legacy_frozen/`` and pointing
    ``source_location`` at it.
    """
    return LegacyBaselineFreeze(
        status="unavailable",
        unavailable_reason=(
            f"no qualifying already-existing legacy scene-render output "
            f"is checked into the repository for sample {sample_id!r}; "
            f"Task 5A is offline-only and refuses to call the real LLM "
            f"(env flag READER_BASELINE_REAL_LLM=1 not set, and the "
            f"legacy chain has no deterministic fake executor). Use "
            f"Task 5B to record a real frozen baseline after a "
            f"reviewer-approved real-LLM run."
        ),
        visible_limitations=[
            "legacy chain always calls a real LLM (no deterministic fake executor)",
            "legacy chain writes a scene-render payload, not enhancement_layers / reader_events",
            "legacy chain does not persist reading_records.reading_goal / reading_variant",
            "real-LLM runs require READER_BASELINE_REAL_LLM=1 and a configured model profile",
        ],
    )


# ---------------------------------------------------------------------------
# Artifact id derivation (R1: includes canonical_text_sha256)
# ---------------------------------------------------------------------------


def derive_artifact_id(
    *,
    canonical_text_sha256: str,
    schema_version: str,
    producer_semantic_version: str,
    source_id: str,
    deterministic_clock_token: str,
) -> str:
    """Derive ``artifact_id`` from canonical semantic inputs.

    R1 change: ``artifact_id`` now depends on
    ``canonical_text_sha256 | schema_version |
    producer_semantic_version | source_id |
    deterministic_clock_token``. A different canonical text or a
    producer bump automatically invalidates old fixture hashes.
    """
    return sha256_hex(
        "|".join(
            [
                canonical_text_sha256,
                schema_version,
                producer_semantic_version,
                source_id,
                deterministic_clock_token,
            ]
        )
    )


# ---------------------------------------------------------------------------
# Fixture-grade artifact builder
# ---------------------------------------------------------------------------


def build_fixture_artifact_from_sample(
    sample: Any,
    *,
    deterministic_clock_token: str = DEFAULT_DETERMINISTIC_CLOCK_TOKEN,
) -> ParseEvalArtifactV1:
    """Build a **fixture-grade** ``ParseEvalArtifactV1`` from a golden sample.

    Hermetic: no DB, no LLM, no spaCy, no ``app`` runtime imports.
    Two consecutive calls on the same ``sample`` (with the same
    ``deterministic_clock_token``) produce byte-identical normalised
    JSON (verified by the gate tests).

    This is NOT the official producer. It is a fixture helper used
    by offline gate tests. The official API-side producer is
    :func:`.reader_adapter.build_artifact_from_snapshot`.

    Args:
        sample: A :class:`GoldenSample` instance (duck-typed — only
            attribute access is used, so any compatible dataclass
            works).
        deterministic_clock_token: Stable token recorded in
            :class:`ArtifactProvenance`. Replaces wall-clock
            ``datetime.now()`` so the artifact is reproducible.

    Returns:
        A validated :class:`ParseEvalArtifactV1` with
        ``executor_mode="fake"``, ``is_fake=True`` model / prompt
        provenance, and an ``artifact_id`` derived from
        ``canonical_text_sha256`` + schema/producer versions.
    """
    canonical_text = canonicalize_hermetic(sample.plain_text)
    if not canonical_text:
        raise ValueError(
            f"sample {sample.sample_id!r}: canonical text is empty after hermetic canonicalization"
        )

    canonical_text_sha256 = sha256_hex(canonical_text)
    canonical_text_length_utf16 = utf16_code_unit_length(canonical_text)
    canonical_text_length_chars = len(canonical_text)
    word_count = len(canonical_text.split())
    canonical_text_preview = canonical_text[:200]

    anchor_map = build_hermetic_anchor_map(canonical_text)

    # R1: artifact_id now depends on canonical_text_sha256 + schema
    # version + producer semantic version + source_id + clock token.
    artifact_id = derive_artifact_id(
        canonical_text_sha256=canonical_text_sha256,
        schema_version=ARTIFACT_SCHEMA_VERSION,
        producer_semantic_version=PRODUCER_SEMANTIC_VERSION,
        source_id=sample.sample_id,
        deterministic_clock_token=deterministic_clock_token,
    )

    # Deterministic record_id / base_id derived from sample_id so the
    # runner provenance is stable across runs. These are NOT real
    # database UUIDs — they are fixture-grade markers.
    record_id = sha256_hex(f"record|{sample.sample_id}").replace("-", "")[:32]
    base_id = sha256_hex(f"base|{sample.sample_id}").replace("-", "")[:32]

    sample_identity = SampleIdentity(
        sample_id=sample.sample_id,
        shape=sample.shape,
        source_attribution=sample.source_attribution,
        reading_goal=sample.reading_goal,
        reading_variant=sample.reading_variant,
        expected_char_band=sample.expected_char_band,
        expected_word_band=sample.expected_word_band,
        notes=sample.notes,
    )
    document = DocumentIdentity(
        canonical_text_sha256=canonical_text_sha256,
        canonical_text_length_utf16=canonical_text_length_utf16,
        canonical_text_length_chars=canonical_text_length_chars,
        word_count=word_count,
        canonical_text_preview=canonical_text_preview,
        hash_algorithm="sha256",
    )

    source_provenance = ArtifactSourceProvenance(
        source_kind="golden_sample",
        source_id=sample.sample_id,
        source_shape=sample.shape,
        source_attribution=sample.source_attribution,
    )
    canonicalizer_provenance = ArtifactCanonicalizerProvenance(
        canonicalizer_version=HERMETIC_CANONICALIZER_VERSION,
        hash_algorithm="sha256",
    )
    segmenter_provenance = ArtifactSegmenterProvenance(
        segmenter_version=HERMETIC_SEGMENTER_VERSION,
        builder_version=HERMETIC_BUILDER_VERSION,
        hash_algorithm="fnv1a32-utf16",
    )

    # Fixture-grade runner provenance: the hermetic producer did not
    # run the real pipeline, so completion_status is ``incomplete``
    # with an explicit reason. The gate verifies field presence +
    # shape, not semantic completion.
    runner_provenance = ArtifactRunnerProvenance(
        executor_mode="fake",
        executor_note=(
            "hermetic fixture producer; no smoke harness run; "
            "no DB / no LLM / no spaCy"
        ),
        runner_version=FIXTURE_PIPELINE_RUNNER_VERSION,
        record_id=record_id,
        base_id=base_id,
        generation=1,
        last_event_sequence=0,
        total_ticks=0,
        total_jobs=0,
        stopped_reason="fixture_no_pipeline_run",
        completion_status="incomplete",
        completion_reasons=(
            "hermetic fixture producer did not run the smoke harness",
        ),
    )

    # Fake executor: explicitly marked. Real model fields MUST be empty.
    model_profile_provenance = ArtifactModelProfileProvenance(
        is_fake=True,
        model_provider=None,
        model_name=None,
        model_profile=None,
    )
    prompt_revision_provenance = ArtifactPromptRevisionProvenance(
        is_fake=True,
        prompt_revision=None,
    )

    legacy_baseline = build_legacy_baseline_unavailable(sample.sample_id)

    artifact_id_semantic_inputs = ArtifactIdSemanticInputs(
        canonical_text_sha256=canonical_text_sha256,
        schema_version=ARTIFACT_SCHEMA_VERSION,
        producer_semantic_version=PRODUCER_SEMANTIC_VERSION,
        source_id=sample.sample_id,
        deterministic_clock_token=deterministic_clock_token,
    )
    artifact_provenance = ArtifactProvenance(
        producer_module=FIXTURE_PRODUCER_MODULE,
        producer_version=PRODUCER_VERSION,
        producer_semantic_version=PRODUCER_SEMANTIC_VERSION,
        deterministic_clock_token=deterministic_clock_token,
        produced_at_iso_utc=None,
        forbidden_fields_present=False,
        artifact_id_semantic_inputs=artifact_id_semantic_inputs,
    )

    return ParseEvalArtifactV1(
        schema_version=ARTIFACT_SCHEMA_VERSION,
        artifact_id=artifact_id,
        sample=sample_identity,
        document=document,
        source_provenance=source_provenance,
        canonicalizer_provenance=canonicalizer_provenance,
        segmenter_provenance=segmenter_provenance,
        anchor_map=anchor_map,
        published_layers=PublishedLayerSummary(
            layer_counts={},
            layers=[],
        ),
        runner_provenance=runner_provenance,
        model_profile_provenance=model_profile_provenance,
        prompt_revision_provenance=prompt_revision_provenance,
        legacy_baseline=legacy_baseline,
        artifact_provenance=artifact_provenance,
    )


__all__ = [
    "HERMETIC_CANONICALIZER_VERSION",
    "HERMETIC_BUILDER_VERSION",
    "HERMETIC_SEGMENTER_VERSION",
    "FIXTURE_PRODUCER_MODULE",
    "utf16_code_unit_length",
    "fnv1a32_utf16",
    "sha256_hex",
    "canonicalize_hermetic",
    "build_hermetic_anchor_map",
    "build_legacy_baseline_unavailable",
    "derive_artifact_id",
    "build_fixture_artifact_from_sample",
]
