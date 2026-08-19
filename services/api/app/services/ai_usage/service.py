from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg

from app.database import connection as db_connection
from app.database.json_compat import jsonb_param
from app.services.ai_usage.execution_diagnostics import (
    META_USAGE_INVOCATION_OBSERVATION,
    STAGE_EVENT_DTO,
    STAGE_EVENT_PERSIST,
    STAGE_NORMALIZE,
    USAGE_COMPLETENESS_UNAVAILABLE,
    USAGE_EVENT_PERSIST_FAILED,
    USAGE_EVENT_PERSISTED,
    USAGE_EVENT_PERSISTED_ZERO,
    USAGE_INVOCATION_CONFLICT,
    USAGE_INVOCATION_OBSERVATION_SCHEMA_VERSION,
    USAGE_ZERO_AFTER_NORMALIZATION,
    UsageRecordOutcome,
    classify_usage_presence,
    current_execution,
    log_usage_diagnostic,
    merge_correlation_metadata,
    set_last_usage_outcome,
    valid_agent_run_usage_snapshot,
)

logger = logging.getLogger(__name__)


async def insert_ai_usage_event_by_invocation_key_in_transaction(
    conn: asyncpg.Connection,
    *,
    invocation_key: str,
    event: AIUsageEventCreate,
) -> UUID:
    """Insert-or-get one usage event inside the caller's transaction."""
    usage_totals = _extract_usage_totals(event.usage_data)
    metadata_json = dict(event.metadata_json or {})
    if event.usage_data is not None:
        metadata_json.setdefault("usage_snapshot", event.usage_data)
    inserted_id = await conn.fetchval(
        """
        INSERT INTO ai_usage_events (
            usage_scope, capability_code, billing_mode, status,
            user_id, reading_record_id,
            reader_run_id, reader_job_id, enhancement_layer_id,
            daily_reader_article_id, client_platform, request_id,
            invocation_key,
            workflow_name, workflow_version, schema_version, prompt_version,
            model_route, model_profile_id, model_profile, model_provider,
            model_name, planner_kind, policy_version, cache_hit, cache_status,
            cache_class, input_tokens, output_tokens, total_tokens,
            cache_read_tokens, cache_write_tokens, cached_input_tokens,
            cache_miss_input_tokens, cache_creation_input_tokens,
            token_budget_before, token_budget_after, latency_ms, billed_points,
            billing_policy_version, operation_fingerprint, error_code,
            error_message, metadata_json, created_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
            $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23,
            $24, $25, $26, $27, $28, $29, $30, $31, $32, $33, $34,
            $35, $36, $37, $38, $39, $40, $41, $42, $43, $44::jsonb, $45
        )
        ON CONFLICT (invocation_key) WHERE invocation_key IS NOT NULL
        DO NOTHING
        RETURNING id
        """,
        event.usage_scope,
        event.capability_code,
        event.billing_mode,
        event.status,
        event.user_id,
        event.reading_record_id,
        event.reader_run_id,
        event.reader_job_id,
        event.enhancement_layer_id,
        event.daily_reader_article_id,
        event.client_platform,
        event.request_id,
        invocation_key,
        event.workflow_name,
        event.workflow_version,
        event.schema_version,
        event.prompt_version,
        event.model_route,
        event.model_profile_id,
        event.model_profile,
        event.model_provider,
        event.model_name,
        event.planner_kind,
        event.policy_version,
        event.cache_hit,
        event.cache_status,
        event.cache_class,
        usage_totals["input_tokens"],
        usage_totals["output_tokens"],
        usage_totals["total_tokens"],
        usage_totals["cache_read_tokens"],
        usage_totals["cache_write_tokens"],
        event.cached_input_tokens,
        event.cache_miss_input_tokens,
        event.cache_creation_input_tokens,
        event.token_budget_before,
        event.token_budget_after,
        event.latency_ms,
        event.billed_points,
        event.billing_policy_version,
        event.operation_fingerprint,
        event.error_code,
        (event.error_message or "")[:1000] or None,
        jsonb_param(metadata_json),
        datetime.now(UTC),
    )
    event_id = inserted_id or await conn.fetchval(
        "SELECT id FROM ai_usage_events WHERE invocation_key = $1",
        invocation_key,
    )
    if not isinstance(event_id, UUID):
        raise RuntimeError("ai_usage_event_insert_not_confirmed")
    return event_id


@dataclass(slots=True)
class AIUsageEventCreate:
    usage_scope: str
    capability_code: str
    billing_mode: str
    status: str
    user_id: UUID | None = None
    reading_record_id: UUID | None = None
    reader_run_id: UUID | None = None
    reader_job_id: UUID | None = None
    enhancement_layer_id: UUID | None = None
    daily_reader_article_id: str | None = None
    client_platform: str | None = None
    request_id: str | None = None
    workflow_name: str | None = None
    workflow_version: str | None = None
    schema_version: str | None = None
    prompt_version: str | None = None
    model_route: str | None = None
    model_profile_id: str | None = None
    model_profile: str | None = None
    model_provider: str | None = None
    model_name: str | None = None
    planner_kind: str | None = None
    policy_version: str | None = None
    cache_hit: bool | None = None
    cache_status: str | None = None
    cache_class: str | None = None
    usage_data: dict[str, Any] | None = None
    cached_input_tokens: int | None = None
    cache_miss_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    token_budget_before: int | None = None
    token_budget_after: int | None = None
    latency_ms: int | None = None
    billed_points: int | None = None
    billing_policy_version: str | None = None
    operation_fingerprint: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)


def _sum_from_per_agent(per_agent: object, field: str) -> int:
    if not isinstance(per_agent, Mapping):
        return 0
    total = 0
    for usage in per_agent.values():
        if isinstance(usage, Mapping):
            total += int(usage.get(field) or 0)
    return total


def _extract_usage_totals(usage_data: dict[str, Any] | None) -> dict[str, int]:
    if not usage_data:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }

    aggregate: Mapping[str, Any]
    if isinstance(usage_data.get("aggregate"), Mapping):
        aggregate = usage_data["aggregate"]
    else:
        aggregate = usage_data

    input_tokens = int(aggregate.get("input_tokens") or 0)
    output_tokens = int(aggregate.get("output_tokens") or 0)
    total_tokens = int(aggregate.get("total_tokens") or 0) or (input_tokens + output_tokens)

    cache_read_tokens = int(aggregate.get("cache_read_tokens") or 0)
    cache_write_tokens = int(aggregate.get("cache_write_tokens") or 0)

    if cache_read_tokens == 0:
        cache_read_tokens = _sum_from_per_agent(usage_data.get("per_agent"), "cache_read_tokens")
    if cache_write_tokens == 0:
        cache_write_tokens = _sum_from_per_agent(usage_data.get("per_agent"), "cache_write_tokens")

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
    }


async def record_ai_usage_event(event: AIUsageEventCreate) -> UUID | None:
    """
    Persist an AI usage audit event without interrupting the main business flow.

    When a Reader ``ExecutionCorrelation`` is active, merges
    correlation into metadata_json and emits usage-presence diagnostics.
    Without Reader scope, behaviour matches pre-O2 (persist only, no Reader
    diagnostics / private correlation metadata). Never logs prompt/article/
    raw response/secrets.
    """
    pool = db_connection.DB_POOL
    correlation = current_execution()
    reader_scope = correlation is not None

    presence_dto = classify_usage_presence(
        event.usage_data,
        stage=STAGE_EVENT_DTO,
        capability_code=event.capability_code,
        provider=event.model_provider,
        model=event.model_name,
    )
    usage_totals = _extract_usage_totals(event.usage_data)
    presence_norm = classify_usage_presence(
        event.usage_data if event.usage_data is not None else None,
        stage=STAGE_NORMALIZE,
        capability_code=event.capability_code,
        provider=event.model_provider,
        model=event.model_name,
    )

    diagnostic_codes: list[str] = []
    if reader_scope:
        log_usage_diagnostic(
            diagnostic_code=presence_dto.diagnostic_code,
            stage=STAGE_EVENT_DTO,
            correlation=correlation,
            usage_key_list=presence_dto.usage_key_list,
            normalized_totals=presence_dto.normalized_totals,
            provider=event.model_provider,
            model=event.model_name,
            capability_code=event.capability_code,
            status=event.status,
            extra={
                "usage_is_none": presence_dto.usage_is_none,
                "usage_is_empty_mapping": presence_dto.usage_is_empty_mapping,
            },
        )
        diagnostic_codes = [presence_dto.diagnostic_code]
        if presence_norm.diagnostic_code == USAGE_ZERO_AFTER_NORMALIZATION:
            diagnostic_codes.append(USAGE_ZERO_AFTER_NORMALIZATION)
            log_usage_diagnostic(
                diagnostic_code=USAGE_ZERO_AFTER_NORMALIZATION,
                stage=STAGE_NORMALIZE,
                correlation=correlation,
                usage_key_list=presence_norm.usage_key_list,
                normalized_totals=usage_totals,
                provider=event.model_provider,
                model=event.model_name,
                capability_code=event.capability_code,
                status=event.status,
            )
        metadata_json = merge_correlation_metadata(
            event.metadata_json,
            correlation,
            diagnostic_codes=diagnostic_codes,
            presence=presence_dto,
        )
    else:
        metadata_json = dict(event.metadata_json or {})

    if event.usage_data is not None:
        metadata_json.setdefault("usage_snapshot", event.usage_data)

    if pool is None:
        logger.warning("Skipping ai_usage_event because database pool is not initialized")
        if reader_scope:
            log_usage_diagnostic(
                diagnostic_code=USAGE_EVENT_PERSIST_FAILED,
                stage=STAGE_EVENT_PERSIST,
                correlation=correlation,
                usage_key_list=presence_dto.usage_key_list,
                normalized_totals=usage_totals,
                provider=event.model_provider,
                model=event.model_name,
                capability_code=event.capability_code,
                status=event.status,
                extra={"persist_failed": True},
            )
            set_last_usage_outcome(
                UsageRecordOutcome(
                    event_id=None,
                    recorded_totals=usage_totals,
                    diagnostic_codes=tuple(
                        [*diagnostic_codes, USAGE_EVENT_PERSIST_FAILED]
                    ),
                    usage_presence=presence_dto,
                )
            )
        return None

    try:
        async with pool.acquire() as conn:
            inserted_id = await conn.fetchval(
                """
                INSERT INTO ai_usage_events (
                    usage_scope, capability_code, billing_mode, status,
                    user_id, reading_record_id,
                    reader_run_id, reader_job_id, enhancement_layer_id,
                    daily_reader_article_id, client_platform, request_id,
                    workflow_name, workflow_version, schema_version, prompt_version,
                    model_route, model_profile_id, model_profile, model_provider, model_name,
                    planner_kind, policy_version, cache_hit, cache_status, cache_class,
                    input_tokens, output_tokens, total_tokens,
                    cache_read_tokens, cache_write_tokens,
                    cached_input_tokens, cache_miss_input_tokens, cache_creation_input_tokens,
                    token_budget_before, token_budget_after,
                    latency_ms, billed_points, billing_policy_version,
                    operation_fingerprint, error_code, error_message, metadata_json, created_at
                )
                VALUES (
                    $1, $2, $3, $4,
                    $5, $6,
                    $7, $8, $9,
                    $10, $11, $12,
                    $13, $14, $15, $16,
                    $17, $18, $19, $20, $21,
                    $22, $23, $24, $25, $26,
                    $27, $28, $29,
                    $30, $31,
                    $32, $33, $34,
                    $35, $36,
                    $37, $38, $39,
                    $40, $41, $42, $43::jsonb, $44
                )
                RETURNING id
                """,
                event.usage_scope,
                event.capability_code,
                event.billing_mode,
                event.status,
                event.user_id,
                event.reading_record_id,
                event.reader_run_id,
                event.reader_job_id,
                event.enhancement_layer_id,
                event.daily_reader_article_id,
                event.client_platform,
                event.request_id,
                event.workflow_name,
                event.workflow_version,
                event.schema_version,
                event.prompt_version,
                event.model_route,
                event.model_profile_id,
                event.model_profile,
                event.model_provider,
                event.model_name,
                event.planner_kind,
                event.policy_version,
                event.cache_hit,
                event.cache_status,
                event.cache_class,
                usage_totals["input_tokens"],
                usage_totals["output_tokens"],
                usage_totals["total_tokens"],
                usage_totals["cache_read_tokens"],
                usage_totals["cache_write_tokens"],
                event.cached_input_tokens,
                event.cache_miss_input_tokens,
                event.cache_creation_input_tokens,
                event.token_budget_before,
                event.token_budget_after,
                event.latency_ms,
                event.billed_points,
                event.billing_policy_version,
                event.operation_fingerprint,
                event.error_code,
                (event.error_message or "")[:1000] or None,
                jsonb_param(metadata_json),
                datetime.now(UTC),
            )
        event_uuid = inserted_id if isinstance(inserted_id, UUID) else None
        if reader_scope:
            persist_codes = list(diagnostic_codes)
            if (
                usage_totals["input_tokens"] == 0
                and usage_totals["output_tokens"] == 0
                and usage_totals["total_tokens"] == 0
            ):
                persist_codes.append(USAGE_EVENT_PERSISTED_ZERO)
                log_usage_diagnostic(
                    diagnostic_code=USAGE_EVENT_PERSISTED_ZERO,
                    stage=STAGE_EVENT_PERSIST,
                    correlation=correlation,
                    usage_key_list=presence_dto.usage_key_list,
                    normalized_totals=usage_totals,
                    provider=event.model_provider,
                    model=event.model_name,
                    capability_code=event.capability_code,
                    usage_event_id=event_uuid,
                    status=event.status,
                )
            else:
                persist_codes.append(USAGE_EVENT_PERSISTED)
                log_usage_diagnostic(
                    diagnostic_code=USAGE_EVENT_PERSISTED,
                    stage=STAGE_EVENT_PERSIST,
                    correlation=correlation,
                    usage_key_list=presence_dto.usage_key_list,
                    normalized_totals=usage_totals,
                    provider=event.model_provider,
                    model=event.model_name,
                    capability_code=event.capability_code,
                    usage_event_id=event_uuid,
                    status=event.status,
                )
            set_last_usage_outcome(
                UsageRecordOutcome(
                    event_id=event_uuid,
                    recorded_totals=usage_totals,
                    diagnostic_codes=tuple(persist_codes),
                    usage_presence=presence_dto,
                )
            )
        return event_uuid
    except Exception:
        logger.exception(
            "Failed to record ai_usage_event(scope=%s, capability=%s, status=%s)",
            event.usage_scope,
            event.capability_code,
            event.status,
        )
        if reader_scope:
            log_usage_diagnostic(
                diagnostic_code=USAGE_EVENT_PERSIST_FAILED,
                stage=STAGE_EVENT_PERSIST,
                correlation=correlation,
                usage_key_list=presence_dto.usage_key_list,
                normalized_totals=usage_totals,
                provider=event.model_provider,
                model=event.model_name,
                capability_code=event.capability_code,
                status=event.status,
                extra={"persist_failed": True},
            )
            set_last_usage_outcome(
                UsageRecordOutcome(
                    event_id=None,
                    recorded_totals=usage_totals,
                    diagnostic_codes=tuple(
                        [*diagnostic_codes, USAGE_EVENT_PERSIST_FAILED]
                    ),
                    usage_presence=presence_dto,
                )
            )
        return None


# ---------------------------------------------------------------------------
# Failure-path usage persistence (OBS-01A)
# ---------------------------------------------------------------------------

# Stable alias: pydantic-ai's exception class name is not a safe error_code
# semantic; normalise to a fixed lowercase label without claiming retry
# exhaustion (which requires worker-owned typed evidence).
_ERROR_CODE_ALIASES: dict[str, str] = {
    "UnexpectedModelBehavior": "unexpected_model_behavior",
}

# Disposition: inserted | replayed | conflict | recorded_plain | persist_failed
ReaderFailedUsageDisposition = str


async def record_invocation_keyed_usage_event(
    event: AIUsageEventCreate,
    *,
    invocation_key: str,
    observation_hash: str,
    pool: asyncpg.Pool | None = None,
) -> tuple[UUID | None, str]:
    """Persist exactly one invocation-keyed usage event — status-agnostic.

    Generic primitive extracted from the OBS-01A failed-usage recorder.
    The caller owns every domain-specific concern (status semantics, error
    normalization, correlation / snapshot gates, observation-hash payload);
    this helper only:

    - merges the schema-versioned ``usage_invocation_observation`` fragment
      (``{"schema_version": 1, "sha256": observation_hash}``) into a copy of
      the event's metadata — the caller must NOT write that field itself,
      and neither the caller's event nor its metadata dict is mutated;
    - resolves the pool: explicit ``pool`` first, then ``DB_POOL``; neither
      available -> ``(None, "persist_failed")`` without raising;
    - owns its own transaction (the caller must not supply one):
      ``pg_advisory_xact_lock(hashtext(key))`` -> ``SELECT ... FOR UPDATE``
      -> compare the stored observation sha256 -> ``replayed`` (same hash) /
      ``conflict`` (different, missing or invalid — the existing row is
      never touched) / insert via
      ``insert_ai_usage_event_by_invocation_key_in_transaction`` ->
      ``inserted``;
    - never raises to the business caller (DB failures collapse to
      ``(None, "persist_failed")``).

    Does not read ``current_execution()`` and does not restrict ``status``.
    Logs carry only invocation_key / capability_code / disposition-category
    — never event metadata, usage payloads or exception text.
    """
    metadata_json = dict(event.metadata_json or {})
    metadata_json[META_USAGE_INVOCATION_OBSERVATION] = {
        "schema_version": USAGE_INVOCATION_OBSERVATION_SCHEMA_VERSION,
        "sha256": observation_hash,
    }
    event = replace(event, metadata_json=metadata_json)

    target_pool = pool or db_connection.DB_POOL
    if target_pool is None:
        logger.warning(
            "Skipping invocation-keyed usage event because database "
            "pool is not initialized (capability=%s)",
            event.capability_code,
        )
        return None, "persist_failed"

    try:
        async with target_pool.acquire() as conn:
            async with conn.transaction():
                # Serialize concurrent first-writers for this key:
                # SELECT ... FOR UPDATE locks nothing when the row does not
                # exist, so take a transaction-scoped advisory lock on the
                # hashed key before the existence check.
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    invocation_key,
                )
                existing = await conn.fetchrow(
                    """
                    SELECT id,
                           metadata_json->'usage_invocation_observation'
                               AS observation
                    FROM ai_usage_events
                    WHERE invocation_key = $1
                    FOR UPDATE
                    """,
                    invocation_key,
                )
                if existing is not None:
                    stored = existing["observation"]
                    stored_hash = (
                        stored.get("sha256")
                        if isinstance(stored, dict)
                        else None
                    )
                    if stored_hash == observation_hash:
                        return existing["id"], "replayed"
                    logger.warning(
                        "%s invocation_key=%s capability=%s",
                        USAGE_INVOCATION_CONFLICT,
                        invocation_key,
                        event.capability_code,
                    )
                    return existing["id"], "conflict"
                event_id = (
                    await insert_ai_usage_event_by_invocation_key_in_transaction(
                        conn,
                        invocation_key=invocation_key,
                        event=event,
                    )
                )
                return event_id, "inserted"
    except Exception as exc:
        # Fixed structured log only: never logger.exception / exc_info —
        # the exception payload (message, traceback, args) may carry
        # sensitive content (DB URI, keys). Only the exception class
        # name is a safe error category.
        logger.error(
            "usage_invocation_persist_failed "
            "invocation_key=%s capability=%s error_category=%s",
            invocation_key,
            event.capability_code,
            type(exc).__name__,
        )
        return None, "persist_failed"


def compute_usage_invocation_observation_hash(
    *,
    invocation_key: str,
    event: AIUsageEventCreate,
    snapshot_fragment: dict[str, Any] | None,
    execution_id: str | None = None,
    agent_run_id: str | None = None,
    attempt_ordinal: int | None = None,
) -> str:
    """Deterministic sha256 over the stable observation of one invocation.

    Covers invocation identity, the stable correlation identity
    (execution_id / agent_run_id / attempt_ordinal), status/error code,
    capability and model identity, normalized usage totals and the usage
    snapshot fragment. Never includes prompts, outputs, exception text,
    duration or any other volatile field.
    """
    usage_totals = _extract_usage_totals(event.usage_data)
    payload = {
        "invocation_key": invocation_key,
        "execution_id": execution_id,
        "agent_run_id": agent_run_id,
        "attempt_ordinal": attempt_ordinal,
        "status": event.status,
        "error_code": event.error_code,
        "capability_code": event.capability_code,
        "model_route": event.model_route,
        "model_profile": event.model_profile,
        "model_provider": event.model_provider,
        "model_name": event.model_name,
        "user_id": str(event.user_id) if event.user_id else None,
        "reading_record_id": (
            str(event.reading_record_id) if event.reading_record_id else None
        ),
        "reader_run_id": str(event.reader_run_id) if event.reader_run_id else None,
        "reader_job_id": str(event.reader_job_id) if event.reader_job_id else None,
        "usage_totals": usage_totals,
        "snapshot": snapshot_fragment or None,
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def record_reader_failed_usage_event(
    event: AIUsageEventCreate,
    *,
    invocation_key: str | None = None,
    snapshot_fragment: dict[str, Any] | None = None,
    pool: asyncpg.Pool | None = None,
) -> tuple[UUID | None, ReaderFailedUsageDisposition]:
    """Persist exactly one FAILED usage event for a Reader model invocation.

    - Requires ``status == STATUS_FAILED``. BEFORE any branch: forces
      ``error_message=None`` (never persists exception text) and
      normalises pydantic-ai exception class names to the conservative
      ``unexpected_model_behavior`` label — also on the plain fallback.
    - With an active Reader execution correlation, derives the durable
      ``invocation_key`` (same identity family as the model-execution
      journal), merges the authoritative correlation fragment (execution /
      attempt / job / run / capability / fingerprint + duration provenance)
      over caller metadata, computes the observation hash, and delegates
      the idempotent keyed persistence (advisory lock, replay / conflict,
      old-row-never-overwritten) to the generic
      :func:`record_invocation_keyed_usage_event`.
    - Without a correlation, falls back to the pre-existing plain
      recorder (no invocation key) for backwards compatibility.
    - A valid snapshot (double identity gate) with confirmed responses
      replaces the event's usage_data so real observed tokens are billed;
      an ``unavailable`` snapshot keeps tokens at zero (never fabricated).
    """
    if event.status != "failed":
        raise ValueError(
            "record_reader_failed_usage_event requires status='failed'"
        )

    # Normalisation BEFORE the correlation branch — applies everywhere.
    normalized_code = _ERROR_CODE_ALIASES.get(event.error_code or "")
    if normalized_code is not None:
        event = replace(event, error_code=normalized_code)
    event = replace(event, error_message=None)

    correlation = current_execution()
    if correlation is None:
        event_id = await record_ai_usage_event(event)
        return event_id, "recorded_plain"

    snapshot = valid_agent_run_usage_snapshot(correlation)
    if snapshot_fragment is None and snapshot is not None:
        snapshot_fragment = snapshot.to_metadata()
        if snapshot.usage_completeness != USAGE_COMPLETENESS_UNAVAILABLE:
            if snapshot.usage_data:
                event = replace(event, usage_data=snapshot.usage_data)

    resolved_key = invocation_key or (
        f"reader:{correlation.capability_code}:"
        f"{correlation.reader_job_id}:{correlation.attempt_ordinal}:1"
    )

    # Authoritative correlation fragment (execution / agent-run / attempt /
    # job / run / capability / fingerprint + duration provenance) overrides
    # any caller-forged same-name metadata fields.
    metadata_json = merge_correlation_metadata(
        event.metadata_json,
        correlation,
    )
    if snapshot_fragment is not None:
        metadata_json.update(snapshot_fragment)
    observation_hash = compute_usage_invocation_observation_hash(
        invocation_key=resolved_key,
        event=event,
        snapshot_fragment=snapshot_fragment,
        execution_id=str(correlation.execution_id),
        agent_run_id=(
            str(correlation.agent_run_id)
            if correlation.agent_run_id is not None
            else None
        ),
        attempt_ordinal=int(correlation.attempt_ordinal),
    )
    event = replace(event, metadata_json=metadata_json)

    return await record_invocation_keyed_usage_event(
        event,
        invocation_key=resolved_key,
        observation_hash=observation_hash,
        pool=pool,
    )


async def update_ai_usage_event_outcome(
    event_id: UUID,
    *,
    status: str,
    metadata_patch: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> bool:
    """Update the publication outcome of an ALREADY-PERSISTED
    model-invocation usage event — the SAME row, never a second event.

    Sets the terminal ``status`` (e.g. ``layer_published`` /
    ``publication_failed`` / ``ownership_lost`` /
    ``publication_interrupted``), merges ``metadata_patch`` into
    ``metadata_json`` (``jsonb ||``) and fills the error fields when
    provided. Idempotent: re-running with the same arguments is a
    no-op. Returns True iff exactly one row was updated; False on
    missing pool / row or DB failure (callers may retry).
    """
    pool = db_connection.DB_POOL
    if pool is None:
        logger.warning(
            "Skipping ai_usage outcome update because database pool "
            "is not initialized"
        )
        return False
    try:
        async with pool.acquire() as conn:
            updated = await conn.execute(
                """
                UPDATE ai_usage_events
                SET status = $2,
                    metadata_json = metadata_json || $3::jsonb,
                    error_code = COALESCE($4, error_code),
                    error_message = COALESCE($5, error_message)
                WHERE id = $1
                """,
                event_id,
                status,
                jsonb_param(dict(metadata_patch or {})),
                error_code,
                (error_message or "")[:1000] or None,
            )
        return updated == "UPDATE 1"
    except Exception:
        logger.exception(
            "Failed to update ai_usage_event outcome for %s", event_id
        )
        return False


async def update_ai_usage_event_metadata(
    event_id: UUID,
    *,
    metadata_patch: dict[str, Any],
    pool: asyncpg.Pool | None = None,
) -> bool:
    """Merge a metadata patch into one persisted usage event — metadata ONLY.

    Minimal metadata-only seam for callers that must enrich an
    already-persisted event (e.g. a publish outcome) WITHOUT touching the
    provider-invocation semantics carried by the other columns. Executes
    exactly:

    ``UPDATE ai_usage_events SET metadata_json = metadata_json || $patch
    WHERE id = $event_id``

    Never updates status, error_code, error_message, token, billing or
    association columns; never modifies the caller's ``metadata_patch``
    dict. Idempotent: re-running with the same patch is a no-op merge.
    Pool resolution: explicit ``pool`` first, then ``DB_POOL``. Returns
    True iff exactly one row was updated; False on missing pool / row,
    DB failure (callers may retry), or when the patch attempts to
    overwrite the frozen observation identity
    (``usage_invocation_observation`` is a reserved top-level key — the
    patch is rejected whole, before any pool access or SQL). Never
    raises to the business caller.
    """
    if META_USAGE_INVOCATION_OBSERVATION in metadata_patch:
        # The observation identity (schema_version + sha256) is frozen at
        # insert time by the invocation-keyed recorder; a patch that tries
        # to overwrite it is rejected whole — no partial application of
        # the remaining keys. Fixed safe warning, no patch payload.
        logger.warning(
            "usage_invocation_metadata_patch_rejected_reserved_key "
            "event_id=%s",
            event_id,
        )
        return False

    target_pool = pool or db_connection.DB_POOL
    if target_pool is None:
        logger.warning(
            "Skipping ai_usage metadata patch because database pool "
            "is not initialized"
        )
        return False
    try:
        async with target_pool.acquire() as conn:
            updated = await conn.execute(
                """
                UPDATE ai_usage_events
                SET metadata_json = metadata_json || $2::jsonb
                WHERE id = $1
                """,
                event_id,
                jsonb_param(dict(metadata_patch or {})),
            )
        return bool(updated == "UPDATE 1")
    except Exception as exc:
        # Fixed structured log only: never logger.exception / exc_info —
        # the exception payload (message, traceback, args) may carry
        # sensitive content. Only the exception class name is a safe
        # error category.
        logger.error(
            "usage_invocation_metadata_patch_failed "
            "event_id=%s error_category=%s",
            event_id,
            type(exc).__name__,
        )
        return False
