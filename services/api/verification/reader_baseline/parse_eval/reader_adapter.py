"""Official API-side adapter: ReaderPlateSnapshot → parse-eval artifact.

R1 seam: this is the **official producer** that maps a
``ReaderPlateSnapshot`` + ``ReaderPipelineRunSummary`` into a
portable :class:`~.schema.ParseEvalArtifactV1`. It is the seam
between the Reader runtime and the eval contract.

Design boundaries:

1. The adapter imports Reader schemas at **type-check time only**
   (``TYPE_CHECKING``). At runtime it accepts duck-typed objects
   whose attributes match the Reader schema shape, so the artifact
   stays portable (evals can consume the JSON without an ``app``
   runtime).

2. The adapter **projects** the snapshot's published layers into
   typed :class:`~.schema.NormalizedLayerOutput` for ``translation``
   and ``vocabulary`` layers. Other layer types (``grammar_note``,
   ``sentence_analysis``, ``semantic_outline``) use
   ``output_kind="sidecar_ref"`` with a content-addressed SHA-256
   over the canonical JSON of the raw ``output`` blob.

3. The adapter **never** embeds Plate value, render scene, raw LLM
   response, or traces. The typed normalized output carries only
   the reviewable layer content (translated text, vocabulary
   headword / explanation / reason).

4. The adapter reuses the hermetic hash helpers from
   :mod:`.fixture_builder` so the artifact and the gate use the
   same FNV-1a32 / SHA-256 / UTF-16 length algorithms.

5. ``artifact_id`` is derived from
   ``canonical_text_sha256 | schema_version |
   producer_semantic_version | source_id |
   deterministic_clock_token`` — same as the fixture builder, so
   a fixture-grade artifact and an API-side artifact for the same
   canonical text + source share the same id derivation rule.

The adapter does NOT call the LLM, does not touch the DB, and does
not require spaCy. It is a pure projection function.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal, cast

from .constants import (
    ARTIFACT_SCHEMA_VERSION,
    DEFAULT_DETERMINISTIC_CLOCK_TOKEN,
    PRODUCER_SEMANTIC_VERSION,
    PRODUCER_VERSION,
)
from .fixture_builder import (
    derive_artifact_id,
    sha256_hex,
    utf16_code_unit_length,
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
    DocumentIdentity,
    NavigationUnitFact,
    ParseEvalArtifactV1,
    PublishedLayerFact,
    PublishedLayerSummary,
    SampleIdentity,
    TranslationGroupFact,
    TranslationNormalizedOutput,
    VocabularyItemFact,
    VocabularyNormalizedOutput,
)

if TYPE_CHECKING:
    # Type-check-only imports so the adapter has proper type hints
    # without creating a runtime dependency on the ``app`` package.
    # At runtime the adapter accepts any duck-typed object whose
    # attributes match the Reader schema shape.
    from app.schemas.reader_orchestration import ReaderPlateSnapshot
    from app.services.reader_orchestration.pipeline_runner import (
        ReaderPipelineRunSummary,
    )


#: Official API-side producer module identity. Recorded in
#: :class:`~.schema.ArtifactProvenance` so the gate can tell an
#: API-side artifact apart from a fixture-grade artifact.
ADAPTER_PRODUCER_MODULE: str = (
    "services/api/verification/reader_baseline/parse_eval/reader_adapter.py"
)


# ---------------------------------------------------------------------------
# Fail-closed exception for snapshot/text base mismatch (P1-2)
# ---------------------------------------------------------------------------
#
# The adapter MUST refuse to produce an artifact when the source-of-truth
# ``snapshot.base`` disagrees with the canonical text supplied to the
# producer. Without this check the adapter would happily emit an artifact
# whose ``document.canonical_text_sha256`` matches the passed text but
# whose underlying snapshot actually points at a different base — letting
# a "passing" artifact describe the wrong source.
#
# The exception message is a FIXED string and never embeds the actual
# hash / length values, so it is safe to surface in logs without leaking
# payload content.
# ---------------------------------------------------------------------------


class SnapshotBaseMismatch(ValueError):
    """Raised when ``snapshot.base`` does not match the passed canonical text.

    R2 (P1-2) correction: the adapter must fail-closed when the
    source-of-truth snapshot base disagrees with the canonical text
    supplied to the producer. The check covers both
    ``content_sha256`` and ``text_length_utf16`` so a swapped or
    stale snapshot cannot masquerade as the source of the artifact.

    The exception message is a fixed string; it never embeds the
    actual hash or length values, so it is safe to surface in logs
    without leaking payload content.
    """

    def __init__(self, field: str) -> None:
        super().__init__(
            f"snapshot.base.{field} does not match the corresponding "
            f"value recomputed from the passed canonical_text; the "
            f"adapter refuses to produce an artifact whose source "
            f"snapshot disagrees with its declared input text"
        )
        self.field = field


def _validate_snapshot_base_matches_canonical_text(
    snapshot: Any,
    *,
    canonical_text_sha256: str,
    canonical_text_length_utf16: int,
) -> None:
    """Fail-closed if ``snapshot.base`` does not match the canonical text.

    Reads ``snapshot.base.content_sha256`` and
    ``snapshot.base.text_length_utf16`` and verifies they equal the
    values recomputed from the passed canonical text. Raises
    :class:`SnapshotBaseMismatch` on any mismatch.

    Raises :class:`ValueError` (not ``SnapshotBaseMismatch``) when
    ``snapshot.base`` itself is missing or malformed — that is a
    caller bug, not a content drift.
    """
    base = getattr(snapshot, "base", None)
    if base is None:
        raise ValueError(
            "snapshot.base must be present when building an artifact "
            "from a snapshot; the adapter validates base.text content "
            "hash and length against the passed canonical_text"
        )

    base_content_sha256 = getattr(base, "content_sha256", None)
    base_text_length_utf16 = getattr(base, "text_length_utf16", None)

    if not isinstance(base_content_sha256, str) or not base_content_sha256:
        raise ValueError(
            "snapshot.base.content_sha256 must be a non-empty string"
        )
    if not isinstance(base_text_length_utf16, int):
        raise ValueError(
            "snapshot.base.text_length_utf16 must be an integer"
        )

    if base_content_sha256 != canonical_text_sha256:
        raise SnapshotBaseMismatch("content_sha256")
    if base_text_length_utf16 != canonical_text_length_utf16:
        raise SnapshotBaseMismatch("text_length_utf16")


# ---------------------------------------------------------------------------
# Canonical-text projection (duck-typed)
# ---------------------------------------------------------------------------


def _project_canonical_text_from_snapshot(snapshot: Any) -> str:
    """Project the canonical text from a snapshot's base identity.

    The snapshot does NOT carry the raw canonical text; it carries
    the ``content_sha256`` and ``text_length_utf16`` of the base.
    The adapter therefore expects the caller to pass the canonical
    text alongside the snapshot (see
    :func:`build_artifact_from_snapshot`).

    This helper exists so the adapter can fail closed with a clear
    message if the caller forgot to pass the canonical text.
    """
    raise RuntimeError(
        "reader_adapter does not extract canonical text from the snapshot; "
        "pass canonical_text explicitly to build_artifact_from_snapshot"
    )


# ---------------------------------------------------------------------------
# Anchor map projection
# ---------------------------------------------------------------------------


def _project_anchor_map(snapshot: Any) -> AnchorMap:
    """Project navigation units + anchor segments from a snapshot.

    Duck-typed: reads ``snapshot.navigation.units`` (list of objects
    with the Reader snapshot navigation-unit shape) and
    ``snapshot.anchor_segments`` (list of objects with the Reader
    snapshot anchor-segment shape).
    """
    navigation_units: list[NavigationUnitFact] = []
    for unit in getattr(snapshot.navigation, "units", []) or []:
        navigation_units.append(
            NavigationUnitFact(
                unit_id=unit.unit_id,
                order_index=unit.order_index,
                unit_type=unit.unit_type,
                boundary_quality=unit.boundary_quality,
                base_start_utf16=unit.base_start_utf16,
                base_end_utf16=unit.base_end_utf16,
                text_hash=unit.text_hash,
                hash_algorithm="fnv1a32-utf16",
            )
        )

    anchor_segments: list[AnchorSegmentFact] = []
    for seg in getattr(snapshot, "anchor_segments", []) or []:
        anchor_segments.append(
            AnchorSegmentFact(
                anchor_segment_id=seg.anchor_segment_id,
                sentence_id=seg.sentence_id,
                paragraph_id=seg.paragraph_id,
                unit_id=seg.unit_id,
                order_index=seg.order_index,
                unit_order_index=seg.unit_order_index,
                segment_type=seg.segment_type,
                boundary_quality=seg.boundary_quality,
                base_start_utf16=seg.base_start_utf16,
                base_end_utf16=seg.base_end_utf16,
                unit_start_utf16=seg.unit_start_utf16,
                unit_end_utf16=seg.unit_end_utf16,
                text_hash=seg.text_hash,
                hash_algorithm="fnv1a32-utf16",
            )
        )

    # Sort for determinism: navigation units by order_index, anchor
    # segments by (unit_order_index, order_index).
    navigation_units.sort(key=lambda u: u.order_index)
    anchor_segments.sort(key=lambda s: (s.unit_order_index, s.order_index))
    return AnchorMap(
        navigation_units=navigation_units,
        anchor_segments=anchor_segments,
    )


# ---------------------------------------------------------------------------
# Published layer projection
# ---------------------------------------------------------------------------


def _normalize_translation_output(
    raw_output: Any,
) -> tuple[TranslationNormalizedOutput, str]:
    """Project a ``TranslationLayerOutput`` into a typed normalized output.

    Returns the typed output and its canonical-JSON SHA-256.
    """
    groups: list[TranslationGroupFact] = []
    raw_groups = raw_output.get("groups") if isinstance(raw_output, dict) else None
    if raw_groups is None:
        raw_groups = getattr(raw_output, "groups", None)
    if raw_groups is None:
        raise ValueError(
            "translation layer output missing 'groups' field"
        )

    for group in raw_groups:
        if isinstance(group, dict):
            group_id = group["group_id"]
            anchor_segment_ids = tuple(group["anchor_segment_ids"])
            source_text_hash = group["source_text_hash"]
            translated_text = group["translated_text"]
        else:
            group_id = group.group_id
            anchor_segment_ids = tuple(group.anchor_segment_ids)
            source_text_hash = group.source_text_hash
            translated_text = group.translated_text
        groups.append(
            TranslationGroupFact(
                group_id=group_id,
                anchor_segment_ids=anchor_segment_ids,
                source_text_hash=source_text_hash,
                translated_text=translated_text,
            )
        )

    normalized = TranslationNormalizedOutput(layer_type="translation", groups=groups)
    canonical_json = json.dumps(
        normalized.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return normalized, sha256_hex(canonical_json)


def _normalize_vocabulary_output(
    raw_output: Any,
) -> tuple[VocabularyNormalizedOutput, str]:
    """Project a ``VocabularyLayerOutput`` into a typed normalized output.

    V1 projects only ``vocab_highlight`` items. ``phrase_gloss`` and
    ``context_gloss`` items are skipped (their typed normalized
    projection is a follow-up). If the layer has zero
    ``vocab_highlight`` items, the projection returns an empty
    normalized output and the layer falls back to ``sidecar_ref``.
    """
    items: list[VocabularyItemFact] = []
    raw_items = raw_output.get("items") if isinstance(raw_output, dict) else None
    if raw_items is None:
        raw_items = getattr(raw_output, "items", None)
    if raw_items is None:
        raise ValueError(
            "vocabulary layer output missing 'items' field"
        )

    for item in raw_items:
        if isinstance(item, dict):
            item_type = item.get("item_type")
            if item_type != "vocab_highlight":
                continue
            anchor = item["anchor"]
            headword = item["headword"]
            brief_explanation = item.get("brief_explanation") or ""
            reason = item.get("reason") or ""
            anchor_unit_id = anchor["unit_id"]
            anchor_segment_id = anchor["anchor_segment_id"]
            selected_text_hash = anchor["text_hash"]
        else:
            item_type = getattr(item, "item_type", None)
            if item_type != "vocab_highlight":
                continue
            anchor = item.anchor
            headword = item.headword
            brief_explanation = getattr(item, "brief_explanation", None) or ""
            reason = getattr(item, "reason", None) or ""
            anchor_unit_id = anchor.unit_id
            anchor_segment_id = anchor.anchor_segment_id
            selected_text_hash = anchor.text_hash
        items.append(
            VocabularyItemFact(
                headword=headword,
                brief_explanation=brief_explanation,
                reason=reason,
                anchor_unit_id=anchor_unit_id,
                anchor_segment_id=anchor_segment_id,
                selected_text_hash=selected_text_hash,
            )
        )

    if not items:
        raise ValueError(
            "vocabulary layer has no vocab_highlight items; "
            "fall back to sidecar_ref"
        )

    normalized = VocabularyNormalizedOutput(layer_type="vocabulary", items=items)
    canonical_json = json.dumps(
        normalized.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return normalized, sha256_hex(canonical_json)


def _sidecar_canonical_json_for_output(raw_output: Any) -> str:
    """Canonical JSON string of a raw sidecar output.

    R2 (P1-4): extracted from ``_sidecar_hash_for_output`` so the
    adapter can return the actual payload string alongside the hash.
    The gate resolves ``sidecar_ref`` → this canonical JSON string via
    :func:`collect_sidecar_payloads`, then recomputes the SHA-256 to
    verify ``sidecar_sha256``. Without this, ``sidecar_ref`` was just
    an opaque layer-id string with no verifiable content.
    """
    if isinstance(raw_output, dict | list):
        return json.dumps(
            raw_output,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    # Pydantic model or dataclass: dump via model_dump or __dict__.
    if hasattr(raw_output, "model_dump"):
        return json.dumps(
            raw_output.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return json.dumps(
        getattr(raw_output, "__dict__", {}),
        sort_keys=True,
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )


def _sidecar_hash_for_output(raw_output: Any) -> str:
    """Content-addressed SHA-256 over the canonical JSON of a raw output."""
    return sha256_hex(_sidecar_canonical_json_for_output(raw_output))


def _project_published_layers(snapshot: Any) -> PublishedLayerSummary:
    """Project ``snapshot.enhancement_layers`` into a typed summary.

    Each published layer (``status == "published"``) becomes a
    :class:`PublishedLayerFact`. Non-empty translation / vocabulary
    layers carry a typed :class:`NormalizedLayerOutput`. Other
    non-empty layer types carry a content-addressed ``sidecar_ref``.
    Empty layers (``item_count == 0``) use ``output_kind="empty"``.
    """
    layer_counts: dict[str, int] = {}
    layers: list[PublishedLayerFact] = []

    raw_layers = getattr(snapshot, "enhancement_layers", []) or []
    for layer in raw_layers:
        status = getattr(layer, "status", None)
        if status != "published":
            continue

        layer_type = getattr(layer, "layer_type", None)
        target_scope = getattr(layer, "target_scope", None)
        target_key = getattr(layer, "target_key", None)
        schema_version = getattr(layer, "schema_version", 1)
        layer_id = getattr(layer, "layer_id", None)
        raw_output = getattr(layer, "output", None)

        layer_type_key = layer_type if isinstance(layer_type, str) else ""
        layer_counts[layer_type_key] = layer_counts.get(layer_type_key, 0) + 1

        if raw_output is None:
            layers.append(
                PublishedLayerFact(
                    layer_id=layer_id,
                    layer_type=layer_type,
                    target_scope=target_scope,
                    target_key=target_key,
                    schema_version=schema_version,
                    item_count=0,
                    output_kind="empty",
                )
            )
            continue

        if layer_type == "translation":
            try:
                normalized, normalized_sha = _normalize_translation_output(
                    raw_output
                )
                groups_count = len(normalized.groups)
                layers.append(
                    PublishedLayerFact(
                        layer_id=layer_id,
                        layer_type=layer_type,
                        target_scope=target_scope,
                        target_key=target_key,
                        schema_version=schema_version,
                        item_count=groups_count,
                        output_kind="normalized_output",
                        normalized_output=normalized,
                        normalized_output_sha256=normalized_sha,
                    )
                )
                continue
            except (ValueError, KeyError, AttributeError, TypeError):
                # Fall through to sidecar_ref if normalization fails.
                pass

        if layer_type == "vocabulary":
            try:
                vocab_normalized, vocab_normalized_sha = _normalize_vocabulary_output(
                    raw_output
                )
                items_count = len(vocab_normalized.items)
                layers.append(
                    PublishedLayerFact(
                        layer_id=layer_id,
                        layer_type=layer_type,
                        target_scope=target_scope,
                        target_key=target_key,
                        schema_version=schema_version,
                        item_count=items_count,
                        output_kind="normalized_output",
                        normalized_output=vocab_normalized,
                        normalized_output_sha256=vocab_normalized_sha,
                    )
                )
                continue
            except (ValueError, KeyError, AttributeError, TypeError):
                # Fall through to sidecar_ref if normalization fails
                # (e.g. only phrase_gloss / context_gloss items).
                pass

        # Fallback: content-addressed sidecar ref for non-translation /
        # non-vocabulary layers, or for layers whose typed projection
        # failed.
        sidecar_sha = _sidecar_hash_for_output(raw_output)
        # item_count heuristic: list length if the output is a list, or
        # length of an 'items' / 'groups' field if present.
        item_count = _estimate_item_count(raw_output)
        layers.append(
            PublishedLayerFact(
                layer_id=layer_id,
                layer_type=layer_type,
                target_scope=target_scope,
                target_key=target_key,
                schema_version=schema_version,
                item_count=item_count,
                output_kind="sidecar_ref",
                sidecar_ref=f"reader_snapshot_layer:{layer_id}",
                sidecar_sha256=sidecar_sha,
            )
        )

    return PublishedLayerSummary(layer_counts=layer_counts, layers=layers)


def _estimate_item_count(raw_output: Any) -> int:
    """Best-effort item count for a raw layer output (sidecar path)."""
    if isinstance(raw_output, list):
        return len(raw_output)
    if isinstance(raw_output, dict):
        items = raw_output.get("items")
        if isinstance(items, list):
            return len(items)
        groups = raw_output.get("groups")
        if isinstance(groups, list):
            return len(groups)
        return 1 if raw_output else 0
    items = getattr(raw_output, "items", None)
    if isinstance(items, list):
        return len(items)
    groups = getattr(raw_output, "groups", None)
    if isinstance(groups, list):
        return len(groups)
    return 1


# ---------------------------------------------------------------------------
# R2 (P1-4): Sidecar payload resolver seam
# ---------------------------------------------------------------------------


def collect_sidecar_payloads(snapshot: Any) -> dict[str, str]:
    """Build the ``sidecar_ref → canonical JSON`` mapping for a snapshot.

    R2 (P1-4): the gate needs the actual sidecar content to verify
    ``sidecar_sha256``. Without this mapping, ``sidecar_ref`` was just
    an opaque ``reader_snapshot_layer:<layer_id>`` string with no
    resolvable content.

    This function mirrors the layer-projection logic in
    :func:`_project_published_layers`: for each published layer that
    would fall through to the ``sidecar_ref`` path (i.e. not
    translation / vocabulary with a successful typed normalization),
    it records the canonical JSON string of the raw output under the
    ``sidecar_ref`` key the adapter would assign.

    Callers pass the returned mapping to
    :class:`.gate.CanonicalTextEvidence.sidecar_payloads` so the gate
    can resolve each ``sidecar_ref`` layer, recompute the SHA-256, and
    verify it equals the layer's ``sidecar_sha256``.
    """
    payloads: dict[str, str] = {}
    raw_layers = getattr(snapshot, "enhancement_layers", []) or []
    for layer in raw_layers:
        status = getattr(layer, "status", None)
        if status != "published":
            continue

        raw_output = getattr(layer, "output", None)
        if raw_output is None:
            continue

        layer_type = getattr(layer, "layer_type", None)
        layer_id = getattr(layer, "layer_id", None)

        # If translation / vocabulary normalization succeeds, the
        # layer becomes normalized_output — no sidecar entry.
        if layer_type == "translation":
            try:
                _normalize_translation_output(raw_output)
                continue
            except (ValueError, KeyError, AttributeError, TypeError):
                pass
        if layer_type == "vocabulary":
            try:
                _normalize_vocabulary_output(raw_output)
                continue
            except (ValueError, KeyError, AttributeError, TypeError):
                pass

        sidecar_ref = f"reader_snapshot_layer:{layer_id}"
        payloads[sidecar_ref] = _sidecar_canonical_json_for_output(
            raw_output
        )
    return payloads


# ---------------------------------------------------------------------------
# Runner / model / prompt provenance projection
# ---------------------------------------------------------------------------


def _project_runner_provenance(
    snapshot: Any,
    pipeline_summary: Any | None,
    *,
    executor_mode: Literal["fake", "real"],
    executor_note: str | None,
    runner_version: str,
) -> ArtifactRunnerProvenance:
    """Project runner provenance from a snapshot + pipeline summary."""
    record = getattr(snapshot, "record", None)
    base = getattr(snapshot, "base", None)

    # ``record_id`` lives on ``ReaderPlateSnapshot`` itself in the real
    # schema (``app.schemas.reader_orchestration.ReaderPlateSnapshot``).
    # The legacy duck-typed ``FixturePlateSnapshot`` instead stashes
    # ``record_id`` on its ``FixtureSnapshotRecord`` subset. Read from
    # the snapshot first and fall back to the record so both shapes
    # project the same value.
    record_id = str(getattr(snapshot, "record_id", "")) or (
        str(getattr(record, "record_id", "")) if record else ""
    )
    base_id = str(getattr(base, "base_id", "")) if base else ""
    generation = int(getattr(record, "generation", 1)) if record else 1
    last_event_sequence = int(getattr(snapshot, "last_event_sequence", 0))

    if pipeline_summary is not None:
        total_ticks = int(getattr(pipeline_summary, "total_ticks", 0))
        total_jobs = int(getattr(pipeline_summary, "total_jobs", 0))
        stopped_reason = str(
            getattr(pipeline_summary, "stopped_reason", "unknown")
        )
    else:
        total_ticks = 0
        total_jobs = 0
        stopped_reason = "no_pipeline_summary"

    completion_status: Literal["complete", "incomplete"] = "incomplete"
    completion_reasons: tuple[str, ...] = ()
    if (
        pipeline_summary is not None
        and getattr(pipeline_summary, "stopped_reason", None)
        == "all_workers_no_job"
    ):
        completion_status = "complete"
        completion_reasons = ()
    else:
        completion_reasons = (
            "pipeline summary missing or stopped before all_workers_no_job",
        )

    return ArtifactRunnerProvenance(
        executor_mode=executor_mode,
        executor_note=executor_note,
        runner_version=runner_version,
        record_id=record_id,
        base_id=base_id,
        generation=generation,
        last_event_sequence=last_event_sequence,
        total_ticks=total_ticks,
        total_jobs=total_jobs,
        stopped_reason=stopped_reason,
        completion_status=completion_status,
        completion_reasons=completion_reasons,
    )


def _project_model_profile_provenance(
    *,
    is_fake: bool,
    model_provider: str | None = None,
    model_name: str | None = None,
    model_profile: str | None = None,
) -> ArtifactModelProfileProvenance:
    return ArtifactModelProfileProvenance(
        is_fake=is_fake,
        model_provider=model_provider,
        model_name=model_name,
        model_profile=model_profile,
    )


def _project_prompt_revision_provenance(
    *,
    is_fake: bool,
    prompt_revision: str | None = None,
) -> ArtifactPromptRevisionProvenance:
    return ArtifactPromptRevisionProvenance(
        is_fake=is_fake,
        prompt_revision=prompt_revision,
    )


# ---------------------------------------------------------------------------
# Canonicalizer / segmenter provenance projection
# ---------------------------------------------------------------------------


def _project_canonicalizer_provenance(snapshot: Any) -> ArtifactCanonicalizerProvenance:
    base = getattr(snapshot, "base", None)
    canonicalizer_version = (
        getattr(base, "canonicalizer_version", "exact_canonical_text_v1")
        if base
        else "exact_canonical_text_v1"
    )
    return ArtifactCanonicalizerProvenance(
        canonicalizer_version=cast(
            "Literal['exact_canonical_text_v1', 'reader_base_low_impact_v1']",
            canonicalizer_version,
        ),
        hash_algorithm="sha256",
    )


def _project_segmenter_provenance(snapshot: Any) -> ArtifactSegmenterProvenance:
    base = getattr(snapshot, "base", None)
    segmenter_version = (
        getattr(base, "segmenter_version", "unknown")
        if base
        else "unknown"
    )
    builder_version = (
        getattr(base, "builder_version", "unknown")
        if base
        else "unknown"
    )
    return ArtifactSegmenterProvenance(
        segmenter_version=str(segmenter_version),
        builder_version=str(builder_version),
        hash_algorithm="fnv1a32-utf16",
    )


# ---------------------------------------------------------------------------
# Sample identity (synthetic for snapshot path)
# ---------------------------------------------------------------------------


def _project_sample_identity(
    snapshot: Any,
    *,
    source_id: str,
    source_shape: str,
    source_attribution: str,
    reading_goal: str,
    reading_variant: str,
) -> SampleIdentity:
    """Build a :class:`SampleIdentity` from snapshot metadata.

    For the API-side path, the "sample" identity is synthesized
    from the record's reading goal / variant and the caller-supplied
    source metadata. ``expected_char_band`` / ``expected_word_band``
    are wide (0, 10**9) because the API path does not enforce band
    constraints — those are fixture-only.
    """
    return SampleIdentity(
        sample_id=source_id,
        shape=source_shape,
        source_attribution=source_attribution,
        reading_goal=cast(Any, reading_goal),
        reading_variant=cast(Any, reading_variant),
        expected_char_band=(0, 10**9),
        expected_word_band=(0, 10**9),
        notes="api-side adapter projection from ReaderPlateSnapshot",
    )


# ---------------------------------------------------------------------------
# Top-level adapter
# ---------------------------------------------------------------------------


def build_artifact_from_snapshot(
    snapshot: ReaderPlateSnapshot,
    *,
    canonical_text: str,
    source_id: str,
    source_shape: str,
    source_attribution: str,
    pipeline_summary: ReaderPipelineRunSummary | None = None,
    executor_mode: Literal["fake", "real"] = "fake",
    executor_note: str | None = None,
    runner_version: str = "reader_pipeline_runner_v1",
    model_provider: str | None = None,
    model_name: str | None = None,
    model_profile: str | None = None,
    prompt_revision: str | None = None,
    deterministic_clock_token: str = DEFAULT_DETERMINISTIC_CLOCK_TOKEN,
) -> ParseEvalArtifactV1:
    """Build an official API-side ``ParseEvalArtifactV1`` from a snapshot.

    This is the official seam between the Reader runtime and the
    portable eval contract. It projects the snapshot's anchor map,
    published layers, and runner provenance into a typed artifact
    that evals can consume without an ``app`` runtime.

    The caller MUST pass ``canonical_text`` explicitly — the
    snapshot does not carry the raw canonical text, only its hash
    and length. The adapter recomputes the SHA-256 / UTF-16 length
    / word count from the passed text and **fail-closed validates**
    that ``snapshot.base.content_sha256`` and
    ``snapshot.base.text_length_utf16`` match the recomputed values
    (R2 / P1-2). This prevents producing an artifact whose
    underlying snapshot points at a different base than the passed
    text. The gate still separately re-checks the canonical text
    via :class:`~.gate.CanonicalTextEvidence`.

    Args:
        snapshot: A ``ReaderPlateSnapshot`` (duck-typed at runtime).
        canonical_text: The canonical text of the reading base.
            The adapter does NOT extract this from the snapshot.
        source_id: Stable source identifier (e.g. record_id string).
        source_shape: Source shape label (e.g. ``medium_news``).
        source_attribution: Source attribution label.
        pipeline_summary: Optional ``ReaderPipelineRunSummary``.
            When ``None``, runner provenance records
            ``stopped_reason="no_pipeline_summary"`` and
            ``completion_status="incomplete"``. **Required** when
            ``executor_mode="real"`` — a real execution artifact
            must carry actual pipeline run evidence.
        executor_mode: ``"fake"`` (default) or ``"real"``. Drives
            :class:`ArtifactModelProfileProvenance.is_fake` and
            :class:`ArtifactPromptRevisionProvenance.is_fake`. The
            default is ``"fake"`` so callers must explicitly opt into
            ``"real"`` — the adapter never silently produces a
            real-execution artifact.
        executor_note: Free-text note recorded in runner provenance.
        runner_version: Pipeline runner version string.
        model_provider: Required when ``executor_mode="real"``.
        model_name: Required when ``executor_mode="real"``.
        model_profile: Required when ``executor_mode="real"``.
        prompt_revision: Required when ``executor_mode="real"``.
        deterministic_clock_token: Stable token for reproducibility.

    Returns:
        A validated :class:`.schema.ParseEvalArtifactV1`.
    """
    if not canonical_text:
        raise ValueError("canonical_text must be a non-empty string")

    # R3 (P3): executor_mode="real" requires a non-None pipeline_summary.
    # A real-execution artifact must carry actual pipeline run evidence —
    # producing a "real" artifact with stopped_reason="no_pipeline_summary"
    # would be a provenance lie. The adapter fail-closes rather than
    # silently emitting an incomplete real artifact.
    if executor_mode == "real" and pipeline_summary is None:
        raise ValueError(
            "executor_mode='real' requires a non-None pipeline_summary; "
            "a real execution artifact must carry actual pipeline run evidence"
        )

    canonical_text_sha256 = sha256_hex(canonical_text)
    canonical_text_length_utf16 = utf16_code_unit_length(canonical_text)
    canonical_text_length_chars = len(canonical_text)
    word_count = len(canonical_text.split())
    canonical_text_preview = canonical_text[:200]

    # R2 (P1-2): fail-closed if the snapshot's stored base hash / length
    # do not match the recomputed values from the passed canonical text.
    # This prevents producing an artifact whose snapshot points at a
    # different base than the actual input text.
    _validate_snapshot_base_matches_canonical_text(
        snapshot,
        canonical_text_sha256=canonical_text_sha256,
        canonical_text_length_utf16=canonical_text_length_utf16,
    )

    artifact_id = derive_artifact_id(
        canonical_text_sha256=canonical_text_sha256,
        schema_version=ARTIFACT_SCHEMA_VERSION,
        producer_semantic_version=PRODUCER_SEMANTIC_VERSION,
        source_id=source_id,
        deterministic_clock_token=deterministic_clock_token,
    )

    record = getattr(snapshot, "record", None)
    reading_goal = (
        getattr(record, "reading_goal", "daily_reading") if record else "daily_reading"
    )
    reading_variant = (
        getattr(record, "reading_variant", "intermediate_reading")
        if record
        else "intermediate_reading"
    )

    sample_identity = _project_sample_identity(
        snapshot,
        source_id=source_id,
        source_shape=source_shape,
        source_attribution=source_attribution,
        reading_goal=str(reading_goal),
        reading_variant=str(reading_variant),
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
        source_kind="reader_record",
        source_id=source_id,
        source_shape=source_shape,
        source_attribution=source_attribution,
    )
    canonicalizer_provenance = _project_canonicalizer_provenance(snapshot)
    segmenter_provenance = _project_segmenter_provenance(snapshot)
    anchor_map = _project_anchor_map(snapshot)
    published_layers = _project_published_layers(snapshot)
    runner_provenance = _project_runner_provenance(
        snapshot,
        pipeline_summary,
        executor_mode=executor_mode,
        executor_note=executor_note,
        runner_version=runner_version,
    )

    is_fake = executor_mode == "fake"
    model_profile_provenance = _project_model_profile_provenance(
        is_fake=is_fake,
        model_provider=model_provider if not is_fake else None,
        model_name=model_name if not is_fake else None,
        model_profile=model_profile if not is_fake else None,
    )
    prompt_revision_provenance = _project_prompt_revision_provenance(
        is_fake=is_fake,
        prompt_revision=prompt_revision if not is_fake else None,
    )

    artifact_id_semantic_inputs = ArtifactIdSemanticInputs(
        canonical_text_sha256=canonical_text_sha256,
        schema_version=ARTIFACT_SCHEMA_VERSION,
        producer_semantic_version=PRODUCER_SEMANTIC_VERSION,
        source_id=source_id,
        deterministic_clock_token=deterministic_clock_token,
    )
    artifact_provenance = ArtifactProvenance(
        producer_module=ADAPTER_PRODUCER_MODULE,
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
        published_layers=published_layers,
        runner_provenance=runner_provenance,
        model_profile_provenance=model_profile_provenance,
        prompt_revision_provenance=prompt_revision_provenance,
        artifact_provenance=artifact_provenance,
    )


__all__ = [
    "ADAPTER_PRODUCER_MODULE",
    "SnapshotBaseMismatch",
    "build_artifact_from_snapshot",
    "collect_sidecar_payloads",
]
