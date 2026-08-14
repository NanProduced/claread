"""Pure read-path hydrate of optional snapshot.semantic_outline.

Only returns a trusted published ready|partial projection. Fail-closed → None.
Does not synthesize unavailable/pending/failed/stale envelopes.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import ValidationError

from app.schemas.reader_orchestration import (
    ReaderSemanticOutlineDiagnostics,
    ReaderSemanticOutlineProjection,
    ReaderSemanticOutlineProvenance,
    ReaderSemanticOutlinePublication,
    ReaderSemanticOutlineSourceIdentity,
    ReaderSnapshotLayer,
)

from .base_builder import ReadingBaseBuildResult
from .semantic_outline import (
    RawSemanticOutlineNode,
    SemanticOutlineAnchor,
    SemanticOutlineSourceIdentity,
    SemanticOutlineUnit,
    SemanticOutlineValidationContext,
    SemanticOutlineValidationInput,
    validate_semantic_outline_projection,
)

_OUTLINE_LAYER_TYPE = "semantic_outline"
_OUTLINE_TARGET_SCOPE = "record"
_OUTLINE_TARGET_KEY = "document"
_TRUSTED_STATUSES = frozenset({"ready", "partial"})


def project_semantic_outline_for_snapshot(
    *,
    build_result: ReadingBaseBuildResult,
    record_generation: int,
    enhancement_layers: Sequence[ReaderSnapshotLayer],
) -> ReaderSemanticOutlineProjection | None:
    """Select and revalidate published outline for the current snapshot fence."""
    base_id = build_result.base.base_id
    fence = SemanticOutlineSourceIdentity(
        base_id=base_id,
        generation=int(record_generation),
    )
    candidates = [
        layer
        for layer in enhancement_layers
        if layer.layer_type == _OUTLINE_LAYER_TYPE
        and layer.target_scope == _OUTLINE_TARGET_SCOPE
        and layer.target_key == _OUTLINE_TARGET_KEY
        and layer.base_id == base_id
        and layer.status == "published"
    ]
    if not candidates:
        return None

    # Defensive: newest published wins if multiple (publisher guarantees ≤1).
    candidates = sorted(
        candidates,
        key=lambda layer: (layer.published_at, layer.layer_id),
        reverse=True,
    )
    layer = candidates[0]
    return _hydrate_trusted_projection(
        layer=layer,
        fence=fence,
        build_result=build_result,
    )


def _hydrate_trusted_projection(
    *,
    layer: ReaderSnapshotLayer,
    fence: SemanticOutlineSourceIdentity,
    build_result: ReadingBaseBuildResult,
) -> ReaderSemanticOutlineProjection | None:
    raw = layer.output
    if not isinstance(raw, dict):
        return None

    try:
        envelope = ReaderSemanticOutlineProjection.model_validate(raw)
    except ValidationError:
        return None

    if envelope.schema_kind != "reader_semantic_outline":
        return None
    if envelope.schema_version != 1:
        return None
    # Envelope status fence BEFORE node revalidation. Layer row status='published'
    # must not upgrade failed/pending/unavailable/stale envelopes to ready|partial.
    if envelope.status not in _TRUSTED_STATUSES:
        return None

    env_identity = SemanticOutlineSourceIdentity(
        base_id=envelope.source_identity.base_id,
        generation=int(envelope.source_identity.generation),
    )
    if env_identity != fence:
        return None

    context = _validation_context_from_build_result(build_result, fence)
    attempted = tuple(
        RawSemanticOutlineNode(
            node_id=node.node_id,
            parent_node_id=node.parent_node_id,
            depth=node.depth,
            title=node.title,
            start_unit_id=node.start_unit_id,
            end_unit_id=node.end_unit_id,
            start_anchor_segment_id=node.start_anchor_segment_id,
            end_anchor_segment_id=node.end_anchor_segment_id,
        )
        for node in envelope.nodes
    )
    if not attempted:
        return None

    validation = validate_semantic_outline_projection(
        context,
        SemanticOutlineValidationInput(
            field_present=True,
            requested=True,
            in_flight=False,
            worker_failure=False,
            projection_source_identity=fence,
            attempted_nodes=attempted,
        ),
    )
    if validation.status not in _TRUSTED_STATUSES:
        return None
    if not validation.nodes:
        return None

    return ReaderSemanticOutlineProjection(
        status=validation.status,
        source_identity=ReaderSemanticOutlineSourceIdentity(
            base_id=fence.base_id,
            generation=fence.generation,
        ),
        publication=ReaderSemanticOutlinePublication(
            outline_revision=envelope.publication.outline_revision,
            layer_id=layer.layer_id,
            # Durable row clock is authoritative (same as layer_id).
            published_at=layer.published_at,
        ),
        provenance=ReaderSemanticOutlineProvenance(
            kind=envelope.provenance.kind,
            builder=envelope.provenance.builder,
            model=envelope.provenance.model,
        ),
        nodes=list(validation.nodes),
        diagnostics=ReaderSemanticOutlineDiagnostics(
            drops=list(validation.diagnostics.drops),
            skipped_node_count=validation.diagnostics.skipped_node_count,
        ),
    )


def _validation_context_from_build_result(
    build_result: ReadingBaseBuildResult,
    fence: SemanticOutlineSourceIdentity,
) -> SemanticOutlineValidationContext:
    units = tuple(
        SemanticOutlineUnit(unit_id=unit.unit_id, order_index=unit.order_index)
        for unit in build_result.units
    )
    anchors = tuple(
        SemanticOutlineAnchor(
            anchor_segment_id=segment.anchor_segment_id,
            unit_id=segment.unit_id,
        )
        for segment in build_result.anchor_segments
    )
    return SemanticOutlineValidationContext(
        source_identity=fence,
        units=units,
        anchors=anchors,
    )


__all__ = ["project_semantic_outline_for_snapshot"]
