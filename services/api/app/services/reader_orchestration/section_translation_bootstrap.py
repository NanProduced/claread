"""T5.6b — explicit section translation bootstrap (server-side).

FOR UPDATE → reload facts → plan_explicit_section_request → insert
translate_article/unit_range with request_origin=section_v1.

Does **not** call wide ``_supersede_stale_fingerprint_jobs``.
Does **not** insert when translation budget is already exhausted.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import UUID, uuid4

import asyncpg

from app.database import connection as db_connection
from app.services.reader_orchestration.execution_budget import ExecutionBudget
from app.services.reader_orchestration.job_bootstrap import (
    DEFAULT_TRANSLATION_MAX_ATTEMPTS,
    DEFAULT_TRANSLATION_TARGET_LANGUAGE,
    TRANSLATION_BATCH_JOB_TYPE,
    TRANSLATION_BATCH_TARGET_SCOPE,
    TRANSLATION_RUN_TYPE,
    TRANSLATION_TRIGGER_KIND,
    _LockedActiveBaseState,
    _LAYER_NAME_BY_JOB_TYPE,
    _compose_operation_fingerprint,
    _insert_unit_range_job,
    _load_locked_active_base_state,
)
from app.services.reader_orchestration.section_candidates import (
    OutlineNodeInput,
    TrustedOutlineInput,
)
from app.services.reader_orchestration.section_identity import (
    SectionUnit,
    encode_section_target_key,
)
from app.services.reader_orchestration.section_lane import (
    SECTION_REQUEST_ORIGIN,
    TRANSLATION_SECTION_OPERATION_FINGERPRINT,
    TRANSLATION_SECTION_POLICY_VERSION,
)
from app.services.reader_orchestration.section_request_planner import (
    ExplicitSectionIntent,
    PlanOutcomeKind,
    REASON_TRANSLATION_BUDGET_EXHAUSTED,
    SectionPlanResult,
    SectionPlannerFacts,
    plan_explicit_section_request,
)

REASON_ALREADY_QUEUED = "section_job_already_queued"
# Client may omit layer_family (server fills translation) but must not
# forge a non-translation family to bypass translation overlap gates.
REASON_LAYER_FAMILY_NOT_TRANSLATION = "layer_family_not_translation"


class SectionBootstrapOutcome(str, Enum):
    ADMITTED = "admitted"
    NO_OP = "no_op"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class SectionTranslationBootstrapResult:
    outcome: SectionBootstrapOutcome
    reason: str | None = None
    job_id: UUID | None = None
    run_id: UUID | None = None
    plan: SectionPlanResult | None = None
    target_unit_ids: tuple[str, ...] = ()
    target_key: str | None = None


class SectionTranslationBootstrapService:
    """Public server entry for explicit section translation requests."""

    def __init__(self, *, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def request_section_translation(
        self,
        *,
        record_id: UUID,
        user_id: UUID,
        intent: ExplicitSectionIntent,
        authorized: bool = True,
    ) -> SectionTranslationBootstrapResult:
        """Bootstrap a section_v1 translate_article job when admitted.

        Budget already exhausted → no insert, reason translation_budget_exhausted.
        Non-translation ``layer_family`` is Reject (cannot bypass translation
        published/active overlap via vocabulary/grammar family forgery).
        """
        # Fail-closed family gate (before any DB write).
        if intent.layer_family is not None and intent.layer_family != "translation":
            return SectionTranslationBootstrapResult(
                outcome=SectionBootstrapOutcome.REJECT,
                reason=REASON_LAYER_FAMILY_NOT_TRANSLATION,
            )

        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                state = await _load_locked_active_base_state(
                    conn, record_id=record_id, user_id=user_id
                )
                # Pre-check durable translation budget before any insert.
                durable = await ExecutionBudget.load_durable(
                    conn,
                    record_id=state.record_id,
                    base_id=state.base_id,
                    expected_generation=state.expected_generation,
                )
                budget = ExecutionBudget()
                budget.load_from_durable(durable)
                if budget.is_exhausted("translation"):
                    return SectionTranslationBootstrapResult(
                        outcome=SectionBootstrapOutcome.NO_OP,
                        reason=REASON_TRANSLATION_BUDGET_EXHAUSTED,
                    )

                facts = await self._load_planner_facts(
                    conn, state=state, authorized=authorized
                )
                # Force server source fence + constant translation family.
                # Client cannot select vocabulary/grammar for this entry.
                server_intent = ExplicitSectionIntent(
                    trigger=intent.trigger,
                    layer_family="translation",
                    record_id=str(state.record_id),
                    base_id=str(state.base_id),
                    generation=state.expected_generation,
                    start_unit_id=intent.start_unit_id,
                    end_unit_id=intent.end_unit_id,
                    start_anchor_segment_id=intent.start_anchor_segment_id,
                    end_anchor_segment_id=intent.end_anchor_segment_id,
                    node_id=intent.node_id,
                    outline_revision=intent.outline_revision,
                )
                plan = plan_explicit_section_request(server_intent, facts)
                if plan.kind is PlanOutcomeKind.REJECT:
                    return SectionTranslationBootstrapResult(
                        outcome=SectionBootstrapOutcome.REJECT,
                        reason=plan.reason,
                        plan=plan,
                    )
                if plan.kind is PlanOutcomeKind.NO_OP:
                    return SectionTranslationBootstrapResult(
                        outcome=SectionBootstrapOutcome.NO_OP,
                        reason=plan.reason,
                        plan=plan,
                    )
                assert plan.identity is not None
                assert plan.target_unit_ids

                # Defense: re-check published/active overlap after plan.
                if await self._has_unit_overlap(
                    conn,
                    state=state,
                    target_unit_ids=plan.target_unit_ids,
                ):
                    return SectionTranslationBootstrapResult(
                        outcome=SectionBootstrapOutcome.NO_OP,
                        reason="section_already_covered_or_inflight",
                        plan=plan,
                    )

                target_key = encode_section_target_key(plan.identity)
                operation_fingerprint = _compose_operation_fingerprint(
                    TRANSLATION_SECTION_OPERATION_FINGERPRINT,
                    state.strategy,
                )

                existing = await conn.fetchrow(
                    """
                    SELECT id, run_id
                    FROM reader_jobs
                    WHERE reading_record_id = $1
                      AND base_id = $2
                      AND job_type = $3
                      AND target_type = $4
                      AND target_key = $5
                      AND expected_generation = $6
                      AND operation_fingerprint = $7
                      AND status IN (
                          'queued', 'claimed', 'retry_later', 'paused', 'succeeded'
                      )
                    LIMIT 1
                    """,
                    state.record_id,
                    state.base_id,
                    TRANSLATION_BATCH_JOB_TYPE,
                    TRANSLATION_BATCH_TARGET_SCOPE,
                    target_key,
                    state.expected_generation,
                    operation_fingerprint,
                )
                if existing is not None:
                    return SectionTranslationBootstrapResult(
                        outcome=SectionBootstrapOutcome.NO_OP,
                        reason=REASON_ALREADY_QUEUED,
                        job_id=existing["id"],
                        run_id=existing["run_id"],
                        plan=plan,
                        target_unit_ids=plan.target_unit_ids,
                        target_key=target_key,
                    )

                target_unit_ids = list(plan.target_unit_ids)
                section_identity_payload = {
                    "record_id": plan.identity.record_id,
                    "base_id": plan.identity.base_id,
                    "generation": plan.identity.generation,
                    "start_unit_id": plan.identity.start_unit_id,
                    "end_unit_id": plan.identity.end_unit_id,
                    "start_anchor_segment_id": plan.identity.start_anchor_segment_id,
                    "end_anchor_segment_id": plan.identity.end_anchor_segment_id,
                }
                trace_id = uuid4()
                run_id, job_id = await _insert_unit_range_job(
                    conn,
                    state=state,
                    run_type=TRANSLATION_RUN_TYPE,
                    job_type=TRANSLATION_BATCH_JOB_TYPE,
                    target_scope=TRANSLATION_BATCH_TARGET_SCOPE,
                    policy_version=TRANSLATION_SECTION_POLICY_VERSION,
                    trigger_kind=TRANSLATION_TRIGGER_KIND,
                    operation_fingerprint=operation_fingerprint,
                    max_attempts=DEFAULT_TRANSLATION_MAX_ATTEMPTS,
                    envelope_json={
                        "record_id": str(state.record_id),
                        "base_id": str(state.base_id),
                        "target_scope": TRANSLATION_BATCH_TARGET_SCOPE,
                        "target_unit_ids": target_unit_ids,
                        "target_language": DEFAULT_TRANSLATION_TARGET_LANGUAGE,
                        "trace_id": str(trace_id),
                        "request_origin": SECTION_REQUEST_ORIGIN,
                        "section_identity": section_identity_payload,
                    },
                    input_signature_suffix=(
                        f"{state.base_language}:{DEFAULT_TRANSLATION_TARGET_LANGUAGE}:"
                        f"section_v1:{target_key}"
                    ),
                    input_json={
                        "target_scope": TRANSLATION_BATCH_TARGET_SCOPE,
                        "target_unit_ids": target_unit_ids,
                        "base_language": state.base_language,
                        "target_language": DEFAULT_TRANSLATION_TARGET_LANGUAGE,
                        "request_origin": SECTION_REQUEST_ORIGIN,
                        "section_identity": section_identity_payload,
                        "client_node_id": (
                            plan.audit.client_node_id if plan.audit else None
                        ),
                        "client_outline_revision": (
                            plan.audit.client_outline_revision if plan.audit else None
                        ),
                    },
                    layer_name=_LAYER_NAME_BY_JOB_TYPE[TRANSLATION_BATCH_JOB_TYPE],
                    target_key_override=target_key,
                    idempotency_key_suffix=f"section:{target_key}",
                )
                return SectionTranslationBootstrapResult(
                    outcome=SectionBootstrapOutcome.ADMITTED,
                    job_id=job_id,
                    run_id=run_id,
                    plan=plan,
                    target_unit_ids=plan.target_unit_ids,
                    target_key=target_key,
                )

    async def _load_planner_facts(
        self,
        conn: asyncpg.Connection,
        *,
        state: _LockedActiveBaseState,
        authorized: bool,
    ) -> SectionPlannerFacts:
        unit_rows = await conn.fetch(
            """
            SELECT unit_id, order_index
            FROM reading_units
            WHERE reading_record_id = $1 AND base_id = $2
            ORDER BY order_index ASC
            """,
            state.record_id,
            state.base_id,
        )
        ordered_units = tuple(
            SectionUnit(unit_id=str(r["unit_id"]), order_index=int(r["order_index"]))
            for r in unit_rows
        )
        anchor_rows = await conn.fetch(
            """
            SELECT anchor_segment_id, unit_id
            FROM anchor_segments
            WHERE reading_record_id = $1 AND base_id = $2
            """,
            state.record_id,
            state.base_id,
        )
        anchor_to_unit = {
            str(r["anchor_segment_id"]): str(r["unit_id"]) for r in anchor_rows
        }

        published_rows = await conn.fetch(
            """
            SELECT target_key
            FROM enhancement_layers
            WHERE reading_record_id = $1
              AND base_id = $2
              AND generation = $3
              AND layer_type = 'translation'
              AND target_scope = 'unit'
              AND status = 'published'
            """,
            state.record_id,
            state.base_id,
            state.expected_generation,
        )
        published = frozenset(str(r["target_key"]) for r in published_rows)

        active_rows = await conn.fetch(
            """
            SELECT input_json
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND base_id = $2
              AND expected_generation = $3
              AND job_type = $4
              AND target_type = $5
              AND status IN ('queued', 'claimed', 'retry_later', 'paused')
            """,
            state.record_id,
            state.base_id,
            state.expected_generation,
            TRANSLATION_BATCH_JOB_TYPE,
            TRANSLATION_BATCH_TARGET_SCOPE,
        )
        active_units: set[str] = set()
        active_section_ranges: list[tuple[str, str]] = []
        for row in active_rows:
            raw = row["input_json"] or {}
            if not isinstance(raw, dict):
                continue
            for uid in raw.get("target_unit_ids") or []:
                active_units.add(str(uid))
            if raw.get("request_origin") == SECTION_REQUEST_ORIGIN:
                sid = raw.get("section_identity") or {}
                if isinstance(sid, dict) and sid.get("start_unit_id") and sid.get(
                    "end_unit_id"
                ):
                    active_section_ranges.append(
                        (str(sid["start_unit_id"]), str(sid["end_unit_id"]))
                    )

        # Also claim translate_unit claimed jobs as active units.
        claimed_unit_rows = await conn.fetch(
            """
            SELECT target_key
            FROM reader_jobs
            WHERE reading_record_id = $1
              AND base_id = $2
              AND expected_generation = $3
              AND job_type = 'translate_unit'
              AND target_type = 'unit'
              AND status = 'claimed'
            """,
            state.record_id,
            state.base_id,
            state.expected_generation,
        )
        for r in claimed_unit_rows:
            active_units.add(str(r["target_key"]))

        trusted_outline = await self._load_trusted_outline(
            conn, state=state, ordered_units=ordered_units
        )

        return SectionPlannerFacts(
            authorized=authorized,
            record_id=str(state.record_id),
            base_id=str(state.base_id),
            generation=state.expected_generation,
            ordered_units=ordered_units,
            anchor_to_unit=anchor_to_unit,
            trusted_outline=trusted_outline,
            published_units_by_family={"translation": published},
            active_target_units_by_family={"translation": frozenset(active_units)},
            active_section_ranges_by_family={
                "translation": tuple(active_section_ranges)
            },
        )

    async def _load_trusted_outline(
        self,
        conn: asyncpg.Connection,
        *,
        state: _LockedActiveBaseState,
        ordered_units: tuple[SectionUnit, ...],
    ) -> TrustedOutlineInput | None:
        """Fail-closed load of published semantic outline for the active base.

        Missing outline / missing source_identity / source mismatch / any
        malformed node → None. Never fills missing source fields from the
        current base, and never skips a bad node to keep remaining ones.
        """
        del ordered_units  # reserved for future geometric pre-checks
        row = await conn.fetchrow(
            """
            SELECT output_json
            FROM enhancement_layers
            WHERE reading_record_id = $1
              AND base_id = $2
              AND generation = $3
              AND layer_type = 'semantic_outline'
              AND target_scope = 'record'
              AND status = 'published'
            ORDER BY published_at DESC NULLS LAST, id DESC
            LIMIT 1
            """,
            state.record_id,
            state.base_id,
            state.expected_generation,
        )
        if row is None:
            return None
        return parse_trusted_outline_payload(
            row["output_json"],
            expected_base_id=str(state.base_id),
            expected_generation=int(state.expected_generation),
        )

    async def _has_unit_overlap(
        self,
        conn: asyncpg.Connection,
        *,
        state: _LockedActiveBaseState,
        target_unit_ids: tuple[str, ...],
    ) -> bool:
        if not target_unit_ids:
            return True
        published = await conn.fetchval(
            """
            SELECT 1
            FROM enhancement_layers
            WHERE reading_record_id = $1
              AND base_id = $2
              AND generation = $3
              AND layer_type = 'translation'
              AND target_scope = 'unit'
              AND status = 'published'
              AND target_key = ANY($4::text[])
            LIMIT 1
            """,
            state.record_id,
            state.base_id,
            state.expected_generation,
            list(target_unit_ids),
        )
        if published is not None:
            return True
        active = await conn.fetchval(
            """
            SELECT 1
            FROM reader_jobs job
            CROSS JOIN LATERAL
                 jsonb_array_elements_text(job.input_json->'target_unit_ids')
                 AS tgt(unit_id)
            WHERE job.reading_record_id = $1
              AND job.base_id = $2
              AND job.expected_generation = $3
              AND job.job_type = $4
              AND job.target_type = $5
              AND job.status IN ('queued', 'claimed', 'retry_later', 'paused')
              AND tgt.unit_id = ANY($6::text[])
            LIMIT 1
            """,
            state.record_id,
            state.base_id,
            state.expected_generation,
            TRANSLATION_BATCH_JOB_TYPE,
            TRANSLATION_BATCH_TARGET_SCOPE,
            list(target_unit_ids),
        )
        return active is not None


def parse_trusted_outline_payload(
    raw: object,
    *,
    expected_base_id: str,
    expected_generation: int,
) -> TrustedOutlineInput | None:
    """Pure fail-closed parser for semantic_outline ``output_json``.

    Exposed for unit tests. Any contract violation returns None.
    """
    if not isinstance(raw, dict):
        return None
    status = raw.get("status")
    if status not in {"ready", "partial"}:
        return None

    src = raw.get("source_identity")
    if not isinstance(src, dict):
        return None
    if "base_id" not in src or "generation" not in src:
        return None
    source_base_id = src["base_id"]
    source_generation = src["generation"]
    if not isinstance(source_base_id, str) or not source_base_id:
        return None
    if isinstance(source_generation, bool) or not isinstance(
        source_generation, int
    ):
        # Reject bool (subclass of int) and non-int; also reject stringly numbers.
        return None
    if source_generation < 1:
        return None
    if source_base_id != expected_base_id or source_generation != expected_generation:
        return None

    nodes_raw = raw.get("nodes")
    if not isinstance(nodes_raw, list):
        return None
    nodes: list[OutlineNodeInput] = []
    for n in nodes_raw:
        if not isinstance(n, dict):
            return None
        nid = n.get("node_id")
        su = n.get("start_unit_id")
        eu = n.get("end_unit_id")
        if not isinstance(nid, str) or not nid:
            return None
        if not isinstance(su, str) or not su:
            return None
        if not isinstance(eu, str) or not eu:
            return None
        order_raw = n.get("order_index", 0)
        if isinstance(order_raw, bool) or not isinstance(order_raw, int):
            return None
        title_raw = n.get("title", "")
        if title_raw is None:
            title = ""
        elif not isinstance(title_raw, str):
            return None
        else:
            title = title_raw
        sa = n.get("start_anchor_segment_id")
        ea = n.get("end_anchor_segment_id")
        if sa is not None and not isinstance(sa, str):
            return None
        if ea is not None and not isinstance(ea, str):
            return None
        nodes.append(
            OutlineNodeInput(
                node_id=nid,
                start_unit_id=su,
                end_unit_id=eu,
                title=title,
                order_index=order_raw,
                start_anchor_segment_id=sa or None,
                end_anchor_segment_id=ea or None,
            )
        )

    pub = raw.get("publication") or {}
    revision = None
    if isinstance(pub, dict):
        rev = pub.get("outline_revision")
        if rev is not None:
            if not isinstance(rev, str):
                return None
            revision = rev

    return TrustedOutlineInput(
        status=str(status),
        source_base_id=source_base_id,
        source_generation=source_generation,
        outline_revision=revision,
        nodes=tuple(nodes),
    )


__all__ = [
    "REASON_ALREADY_QUEUED",
    "REASON_LAYER_FAMILY_NOT_TRANSLATION",
    "REASON_TRANSLATION_BUDGET_EXHAUSTED",
    "SectionBootstrapOutcome",
    "SectionTranslationBootstrapResult",
    "SectionTranslationBootstrapService",
    "parse_trusted_outline_payload",
]
