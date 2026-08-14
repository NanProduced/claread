"""Record-level semantic outline publisher.

Hard pipeline:

    candidate nodes
      → allocate outline_revision
      → map candidate_ref / parent_candidate_ref → opaque node_id / parent_node_id
      → validate_semantic_outline_projection(FINAL ids)
      → V>0 only: atomic re-fence + supersede-or-idempotent + insert + layer_published

Does NOT reuse unit-scoped ``_assert_no_published_layer``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4

import asyncpg

from app.database import connection as db_connection
from app.database.json_compat import ensure_json_object, jsonb_param
from app.schemas.reader_orchestration import (
    ReaderSemanticOutlineDiagnostics,
    ReaderSemanticOutlineDrop,
    ReaderSemanticOutlineNode,
    ReaderSemanticOutlineProjection,
    ReaderSemanticOutlineProvenance,
    ReaderSemanticOutlinePublication,
    ReaderSemanticOutlineSourceIdentity,
)

from .event_runtime import ReaderEventEnvelope, ReaderEventRuntime
from .job_bootstrap import (
    SEMANTIC_OUTLINE_JOB_TYPE,
    SEMANTIC_OUTLINE_OPERATION_FINGERPRINT,
    SEMANTIC_OUTLINE_TARGET_SCOPE,
    _fingerprint_matches_base,
)
from .job_runtime import (
    FenceViolationError,
    LeaseExpiredError,
    LeaseTokenMismatchError,
    ReaderJobRuntime,
    _assert_lease_valid,
)
from .semantic_outline import (
    RawSemanticOutlineNode,
    SemanticOutlineAnchor,
    SemanticOutlineSourceIdentity,
    SemanticOutlineUnit,
    SemanticOutlineValidationContext,
    SemanticOutlineValidationInput,
    SemanticOutlineValidationResult,
    validate_semantic_outline_projection,
)

SEMANTIC_OUTLINE_LAYER_TYPE = "semantic_outline"
SEMANTIC_OUTLINE_TARGET_SCOPE = "record"
SEMANTIC_OUTLINE_TARGET_KEY = "document"
SEMANTIC_OUTLINE_SCHEMA_VERSION = 1
SEMANTIC_OUTLINE_BUILDER = "reader-semantic-outline-publisher-v1"


@dataclass(frozen=True, slots=True)
class SemanticOutlineCandidateNode:
    """Model/worker candidate — temporary refs only, not durable ids."""

    candidate_ref: str
    parent_candidate_ref: str | None
    depth: int
    title: str
    start_unit_id: str
    end_unit_id: str
    start_anchor_segment_id: str | None = None
    end_anchor_segment_id: str | None = None


@dataclass(frozen=True, slots=True)
class SemanticOutlineOpaqueMapResult:
    outline_revision: str
    attempted_nodes: tuple[RawSemanticOutlineNode, ...]
    opaque_by_candidate: dict[str, str]


@dataclass(frozen=True, slots=True)
class SemanticOutlinePublishResult:
    outcome: str
    layer_id: UUID | None
    outline_revision: str | None
    status: str | None
    validation: SemanticOutlineValidationResult | None
    event: ReaderEventEnvelope | None
    reused_existing: bool = False


def allocate_outline_revision() -> str:
    """Opaque revision id; not base_short+order."""
    return f"olrev_{uuid4().hex}"


def map_candidates_to_opaque_nodes(
    candidates: Sequence[SemanticOutlineCandidateNode],
    *,
    outline_revision: str | None = None,
) -> SemanticOutlineOpaqueMapResult:
    """Allocate revision + opaque ids, preserving parent edges.

    Mapping is bijective over unique non-empty candidate_refs in input order.
    Unknown parent refs become invalid_parent for the validator (parent_node_id
    points at a never-accepted id, or None is not used for broken parents —
    we keep the mapped parent only when parent ref is known).
    """
    revision = outline_revision or allocate_outline_revision()
    opaque_by_candidate: dict[str, str] = {}
    for candidate in candidates:
        ref = candidate.candidate_ref.strip() if candidate.candidate_ref else ""
        if not ref or ref in opaque_by_candidate:
            continue
        # Opaque, revision-scoped; deliberately not base_short+order.
        opaque_by_candidate[ref] = f"oln_{uuid4().hex}"

    attempted: list[RawSemanticOutlineNode] = []
    for candidate in candidates:
        ref = candidate.candidate_ref.strip() if candidate.candidate_ref else ""
        node_id = opaque_by_candidate.get(ref) if ref else None
        parent_ref = (
            candidate.parent_candidate_ref.strip()
            if candidate.parent_candidate_ref
            else None
        )
        if parent_ref == "":
            parent_ref = None
        parent_node_id: str | None
        if parent_ref is None:
            parent_node_id = None
        elif parent_ref in opaque_by_candidate:
            parent_node_id = opaque_by_candidate[parent_ref]
        else:
            # Unknown parent — emit a non-accepted parent id so validator drops
            # with invalid_parent rather than silently reparenting to root.
            parent_node_id = f"oln_missing_parent_{uuid4().hex}"
        attempted.append(
            RawSemanticOutlineNode(
                node_id=node_id,
                parent_node_id=parent_node_id,
                depth=candidate.depth,
                title=candidate.title,
                start_unit_id=candidate.start_unit_id,
                end_unit_id=candidate.end_unit_id,
                start_anchor_segment_id=candidate.start_anchor_segment_id,
                end_anchor_segment_id=candidate.end_anchor_segment_id,
            )
        )
    return SemanticOutlineOpaqueMapResult(
        outline_revision=revision,
        attempted_nodes=tuple(attempted),
        opaque_by_candidate=dict(opaque_by_candidate),
    )


def build_validation_context(
    *,
    base_id: str,
    generation: int,
    units: Sequence[SemanticOutlineUnit],
    anchors: Sequence[SemanticOutlineAnchor] = (),
) -> SemanticOutlineValidationContext:
    return SemanticOutlineValidationContext(
        source_identity=SemanticOutlineSourceIdentity(
            base_id=base_id,
            generation=generation,
        ),
        units=tuple(units),
        anchors=tuple(anchors),
    )


def validate_mapped_outline(
    *,
    context: SemanticOutlineValidationContext,
    mapped: SemanticOutlineOpaqueMapResult,
    worker_failure: bool = False,
) -> SemanticOutlineValidationResult:
    """Run validator on FINAL opaque ids (never on raw candidate refs)."""
    return validate_semantic_outline_projection(
        context,
        SemanticOutlineValidationInput(
            field_present=True,
            requested=True,
            in_flight=False,
            worker_failure=worker_failure,
            projection_source_identity=context.source_identity,
            attempted_nodes=mapped.attempted_nodes,
        ),
    )


def build_canonical_envelope(
    *,
    validation: SemanticOutlineValidationResult,
    source_identity: SemanticOutlineSourceIdentity,
    outline_revision: str,
    layer_id: str | None,
    published_at: datetime | None,
    provenance_kind: str = "llm",
    builder: str = SEMANTIC_OUTLINE_BUILDER,
    model: str | None = None,
) -> dict[str, Any]:
    """Build wire-shaped envelope for enhancement_layers.output_json."""
    diagnostics = ReaderSemanticOutlineDiagnostics(
        drops=[
            ReaderSemanticOutlineDrop(node_id=d.node_id, reason_code=d.reason_code)
            for d in validation.diagnostics.drops
        ],
        skipped_node_count=validation.diagnostics.skipped_node_count,
    )
    nodes = [
        ReaderSemanticOutlineNode(
            node_id=node.node_id,
            parent_node_id=node.parent_node_id,
            depth=node.depth,
            title=node.title,
            start_unit_id=node.start_unit_id,
            end_unit_id=node.end_unit_id,
            start_anchor_segment_id=node.start_anchor_segment_id,
            end_anchor_segment_id=node.end_anchor_segment_id,
            order_index=node.order_index,
        )
        for node in validation.nodes
    ]
    projection = ReaderSemanticOutlineProjection(
        status=validation.status,
        source_identity=ReaderSemanticOutlineSourceIdentity(
            base_id=source_identity.base_id,
            generation=source_identity.generation,
        ),
        publication=ReaderSemanticOutlinePublication(
            outline_revision=outline_revision,
            layer_id=layer_id,
            published_at=published_at,
        ),
        provenance=ReaderSemanticOutlineProvenance(
            kind=provenance_kind,  # type: ignore[arg-type]
            builder=builder,
            model=model,
        ),
        nodes=nodes,
        diagnostics=diagnostics,
    )
    return projection.model_dump(mode="json")


class SemanticOutlineLayerPublisher:
    """Record-level publish seam for semantic_outline enhancement layers."""

    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        event_runtime: ReaderEventRuntime | None = None,
        job_runtime: ReaderJobRuntime | None = None,
    ) -> None:
        self._pool = pool
        self._event_runtime = event_runtime or ReaderEventRuntime(pool=pool)
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def publish_from_candidates(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        reading_record_id: UUID,
        base_id: UUID,
        generation: int,
        operation_fingerprint: str,
        source_run_id: UUID | None,
        source_job_id: UUID | None,
        units: Sequence[SemanticOutlineUnit],
        anchors: Sequence[SemanticOutlineAnchor] = (),
        candidates: Sequence[SemanticOutlineCandidateNode],
        worker_failure: bool = False,
        provenance_kind: str = "llm",
        model: str | None = None,
        outline_revision: str | None = None,
    ) -> SemanticOutlinePublishResult:
        """Map → validate → maybe atomic replace under job lease fence.

        ``job_id`` + ``lease_token`` are required. Publish only proceeds when
        the job is still claimed under this lease and the full job-runtime
        fence passes. Failure paths never supersede an old published outline.
        """
        context = build_validation_context(
            base_id=str(base_id),
            generation=generation,
            units=units,
            anchors=anchors,
        )
        mapped = map_candidates_to_opaque_nodes(
            candidates,
            outline_revision=outline_revision,
        )
        validation = validate_mapped_outline(
            context=context,
            mapped=mapped,
            worker_failure=worker_failure,
        )
        valid_count = len(validation.nodes)
        if worker_failure or validation.status in {"failed", "stale", "unavailable", "pending"}:
            return SemanticOutlinePublishResult(
                outcome="not_published",
                layer_id=None,
                outline_revision=mapped.outline_revision,
                status=validation.status,
                validation=validation,
                event=None,
            )
        if valid_count <= 0:
            return SemanticOutlinePublishResult(
                outcome="not_published",
                layer_id=None,
                outline_revision=mapped.outline_revision,
                status=validation.status,
                validation=validation,
                event=None,
            )
        if validation.status not in {"ready", "partial"}:
            return SemanticOutlinePublishResult(
                outcome="not_published",
                layer_id=None,
                outline_revision=mapped.outline_revision,
                status=validation.status,
                validation=validation,
                event=None,
            )

        return await self._atomic_replace_published(
            job_id=job_id,
            lease_token=lease_token,
            reading_record_id=reading_record_id,
            base_id=base_id,
            generation=generation,
            operation_fingerprint=operation_fingerprint,
            source_run_id=source_run_id,
            source_job_id=source_job_id,
            mapped=mapped,
            validation=validation,
            provenance_kind=provenance_kind,
            model=model,
        )

    async def _atomic_replace_published(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        reading_record_id: UUID,
        base_id: UUID,
        generation: int,
        operation_fingerprint: str,
        source_run_id: UUID | None,
        source_job_id: UUID | None,
        mapped: SemanticOutlineOpaqueMapResult,
        validation: SemanticOutlineValidationResult,
        provenance_kind: str,
        model: str | None,
    ) -> SemanticOutlinePublishResult:
        now = datetime.now(UTC)
        pool = self.get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Job lease fence FIRST (grammar/translation publisher pattern).
                # Any failure raises before layer lock/supersede/insert/event.
                job_row = await conn.fetchrow(
                    "SELECT * FROM reader_jobs WHERE id = $1 FOR UPDATE",
                    job_id,
                )
                try:
                    job_row = _assert_claimed_outline_job_row(
                        job_row,
                        job_id=job_id,
                        lease_token=lease_token,
                        reading_record_id=reading_record_id,
                        base_id=base_id,
                        generation=generation,
                        operation_fingerprint=operation_fingerprint,
                    )
                    fence_error = await self._job_runtime._validate_fence(  # type: ignore[attr-defined]
                        conn, job_row
                    )
                    if fence_error is not None:
                        raise FenceViolationError(
                            f"semantic outline publish fence failed for job "
                            f"{job_id}: {fence_error}"
                        )
                    if source_job_id is not None and source_job_id != job_id:
                        raise ValueError(
                            "semantic outline publish source_job_id must match job_id"
                        )
                    if (
                        source_run_id is not None
                        and source_run_id != job_row["run_id"]
                    ):
                        raise ValueError(
                            "semantic outline publish source_run_id must match job run_id"
                        )
                except (LeaseTokenMismatchError, LeaseExpiredError) as exc:
                    raise FenceViolationError(
                        f"semantic outline publish lease failed for job "
                        f"{job_id}: {exc}"
                    ) from exc
                except ValueError as exc:
                    raise FenceViolationError(
                        f"semantic outline publish job fence failed for job "
                        f"{job_id}: {exc}"
                    ) from exc

                # Provenance is bound to the locked claim, never caller input.
                source_run_id = job_row["run_id"]
                source_job_id = job_id

                existing = await conn.fetchrow(
                    """
                    SELECT id, operation_fingerprint, output_json, published_at
                    FROM enhancement_layers
                    WHERE reading_record_id = $1
                      AND base_id = $2
                      AND generation = $3
                      AND layer_type = $4
                      AND target_scope = $5
                      AND target_key = $6
                      AND status = 'published'
                    FOR UPDATE
                    """,
                    reading_record_id,
                    base_id,
                    generation,
                    SEMANTIC_OUTLINE_LAYER_TYPE,
                    SEMANTIC_OUTLINE_TARGET_SCOPE,
                    SEMANTIC_OUTLINE_TARGET_KEY,
                )
                if existing is not None and str(existing["operation_fingerprint"]) == (
                    operation_fingerprint
                ):
                    return SemanticOutlinePublishResult(
                        outcome="idempotent_reuse",
                        layer_id=existing["id"],
                        outline_revision=_existing_revision(existing),
                        status=validation.status,
                        validation=validation,
                        event=None,
                        reused_existing=True,
                    )

                if existing is not None:
                    await conn.execute(
                        """
                        UPDATE enhancement_layers
                        SET status = 'superseded',
                            superseded_at = $2,
                            updated_at = $2
                        WHERE id = $1
                        """,
                        existing["id"],
                        now,
                    )

                layer_id = uuid4()
                envelope = build_canonical_envelope(
                    validation=validation,
                    source_identity=SemanticOutlineSourceIdentity(
                        base_id=str(base_id),
                        generation=generation,
                    ),
                    outline_revision=mapped.outline_revision,
                    layer_id=str(layer_id),
                    published_at=now,
                    provenance_kind=provenance_kind,
                    model=model,
                )
                coverage = {
                    "attempted_node_count": len(mapped.attempted_nodes),
                    "valid_node_count": len(validation.nodes),
                    "skipped_node_count": validation.diagnostics.skipped_node_count,
                    "status": validation.status,
                }
                await conn.execute(
                    """
                    INSERT INTO enhancement_layers (
                        id,
                        reading_record_id,
                        base_id,
                        layer_type,
                        layer_subtype,
                        target_scope,
                        target_key,
                        generation,
                        status,
                        operation_fingerprint,
                        schema_version,
                        output_json,
                        coverage_json,
                        quality_json,
                        source_run_id,
                        source_job_id,
                        published_at
                    )
                    VALUES (
                        $1, $2, $3, $4, NULL, $5, $6, $7, 'published',
                        $8, $9, $10::jsonb, $11::jsonb, '{}'::jsonb,
                        $12, $13, $14
                    )
                    """,
                    layer_id,
                    reading_record_id,
                    base_id,
                    SEMANTIC_OUTLINE_LAYER_TYPE,
                    SEMANTIC_OUTLINE_TARGET_SCOPE,
                    SEMANTIC_OUTLINE_TARGET_KEY,
                    generation,
                    operation_fingerprint,
                    SEMANTIC_OUTLINE_SCHEMA_VERSION,
                    jsonb_param(envelope),
                    jsonb_param(coverage),
                    source_run_id,
                    source_job_id,
                    now,
                )
                event_payload = {
                    "record_id": str(reading_record_id),
                    "base_id": str(base_id),
                    "layer_id": str(layer_id),
                    "layer_type": SEMANTIC_OUTLINE_LAYER_TYPE,
                    "target_scope": SEMANTIC_OUTLINE_TARGET_SCOPE,
                    "target_key": SEMANTIC_OUTLINE_TARGET_KEY,
                    "generation": generation,
                }
                event = await self._event_runtime.publish_event_in_transaction(
                    conn,
                    record_id=reading_record_id,
                    event_type="layer_published",
                    payload_json=event_payload,
                    source_run_id=source_run_id,
                    source_job_id=source_job_id,
                    source_layer_id=layer_id,
                    created_at=now,
                )
                return SemanticOutlinePublishResult(
                    outcome="published",
                    layer_id=layer_id,
                    outline_revision=mapped.outline_revision,
                    status=validation.status,
                    validation=validation,
                    event=event,
                    reused_existing=False,
                )


def _existing_revision(existing: asyncpg.Record) -> str | None:
    raw = existing["output_json"]
    data = ensure_json_object(raw) if raw is not None else {}
    if not isinstance(data, Mapping):
        return None
    publication = data.get("publication")
    if isinstance(publication, Mapping):
        rev = publication.get("outline_revision")
        if isinstance(rev, str) and rev:
            return rev
    return None


def _assert_claimed_outline_job_row(
    job_row: asyncpg.Record | None,
    *,
    job_id: UUID,
    lease_token: UUID,
    reading_record_id: UUID,
    base_id: UUID,
    generation: int,
    operation_fingerprint: str,
) -> asyncpg.Record:
    """Validate claimed outline job identity + lease before any layer write.

    Raises ``ValueError`` / ``LeaseTokenMismatchError`` / ``LeaseExpiredError``
    for the caller to map into ``FenceViolationError`` (zero layer/event).
    """
    if job_row is None:
        raise ValueError(f"reader job {job_id} not found")
    if str(job_row["status"]) != "claimed":
        raise ValueError(
            f"semantic outline publish requires claimed job; got {job_row['status']!r}"
        )
    if str(job_row["job_type"]) != SEMANTIC_OUTLINE_JOB_TYPE:
        raise ValueError(
            f"semantic outline publish requires job_type={SEMANTIC_OUTLINE_JOB_TYPE!r}"
        )
    if str(job_row["target_type"]) != SEMANTIC_OUTLINE_TARGET_SCOPE:
        raise ValueError(
            f"semantic outline publish requires target_type={SEMANTIC_OUTLINE_TARGET_SCOPE!r}"
        )
    if str(job_row["target_key"]) != str(reading_record_id):
        raise ValueError("semantic outline publish job target_key mismatch")
    if job_row["reading_record_id"] != reading_record_id:
        raise ValueError("semantic outline publish job reading_record_id mismatch")
    if job_row["base_id"] != base_id:
        raise ValueError("semantic outline publish job base_id mismatch")
    if int(job_row["expected_generation"]) != int(generation):
        raise ValueError("semantic outline publish job expected_generation mismatch")
    job_fp = str(job_row["operation_fingerprint"] or "")
    if job_fp != operation_fingerprint:
        raise ValueError("semantic outline publish job operation_fingerprint mismatch")
    if not _fingerprint_matches_base(job_fp, SEMANTIC_OUTLINE_OPERATION_FINGERPRINT):
        raise ValueError(
            "semantic outline publish job fingerprint is not an outline fingerprint"
        )
    _assert_lease_valid(job_row, job_id, lease_token)
    return job_row
