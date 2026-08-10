"""Reader-owned reconciliation from published job outcomes to usage events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from app.database.json_compat import ensure_json_object, jsonb_param
from app.services.model_execution_journal.models import MaterializationSummary
from app.services.model_execution_journal.service import ModelExecutionJournalService

_LAYER_TYPES_BY_INVOCATION_KIND: dict[str, frozenset[str]] = {
    "reader.grammar_unit": frozenset({"grammar_note", "sentence_analysis"}),
    "reader.grammar_window": frozenset({"grammar_note", "sentence_analysis"}),
    "reader.semantic_outline": frozenset({"semantic_outline"}),
    "reader.translation_batch": frozenset({"translation"}),
    "reader.translation_unit": frozenset({"translation"}),
    "reader.vocabulary_batch": frozenset({"vocabulary"}),
    "reader.vocabulary_unit": frozenset({"vocabulary"}),
}


@dataclass(frozen=True, slots=True)
class ReaderUsageAttributionSummary:
    materialization: MaterializationSummary
    scanned: int
    reconciled: int


class ReaderUsageAttributionService:
    """Apply Reader publisher-owned output references to materialized usage."""

    def __init__(
        self,
        *,
        journal_service: ModelExecutionJournalService,
    ) -> None:
        self._journal_service = journal_service

    async def materialize_and_reconcile(
        self,
        *,
        limit: int = 100,
        max_attempts: int = 3,
        invocation_key: str | None = None,
    ) -> ReaderUsageAttributionSummary:
        materialization = await self._journal_service.materialize_pending(
            limit=limit,
            max_attempts=max_attempts,
            invocation_key=invocation_key,
        )
        scanned, reconciled = await self._reconcile_published_outcomes(
            limit=limit,
            invocation_key=invocation_key,
        )
        return ReaderUsageAttributionSummary(
            materialization=materialization,
            scanned=scanned,
            reconciled=reconciled,
        )

    async def _reconcile_published_outcomes(
        self,
        *,
        limit: int,
        invocation_key: str | None,
    ) -> tuple[int, int]:
        scanned = 0
        reconciled = 0
        async with self._journal_service.get_pool().acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT journal.invocation_kind, journal.invocation_key,
                           journal.ai_usage_event_id, job.id AS reader_job_id,
                           job.reading_record_id, job.output_ref_json
                    FROM ai_model_execution_journal journal
                    JOIN ai_usage_events usage
                      ON usage.id = journal.ai_usage_event_id
                    JOIN reader_jobs job
                      ON job.id = journal.reader_job_id
                    WHERE journal.capture_state = 'captured'
                      AND journal.usage_delivery_state = 'reconciled'
                      AND journal.invocation_kind = ANY($1::text[])
                      AND job.status = 'succeeded'
                      AND ($3::text IS NULL OR journal.invocation_key = $3)
                      AND COALESCE(
                          usage.metadata_json
                              ->> 'publication_attribution_state',
                          ''
                      ) NOT IN (
                          'published_layer_reconciled',
                          'no_published_layer'
                      )
                    ORDER BY journal.created_at ASC, journal.id ASC
                    LIMIT $2
                    FOR UPDATE OF usage SKIP LOCKED
                    """,
                    list(_LAYER_TYPES_BY_INVOCATION_KIND),
                    limit,
                    invocation_key,
                )
                for row in rows:
                    scanned += 1
                    attribution = await self._published_layer_attribution(
                        conn,
                        row=row,
                    )
                    if attribution is None:
                        continue
                    primary_layer_id, metadata_patch = attribution
                    await conn.execute(
                        """
                        UPDATE ai_usage_events
                        SET enhancement_layer_id = $2,
                            metadata_json =
                                COALESCE(metadata_json, '{}'::jsonb) || $3::jsonb
                        WHERE id = $1
                        """,
                        row["ai_usage_event_id"],
                        primary_layer_id,
                        jsonb_param(metadata_patch),
                    )
                    reconciled += 1
        return scanned, reconciled

    async def _published_layer_attribution(
        self,
        conn: asyncpg.Connection,
        *,
        row: asyncpg.Record,
    ) -> tuple[UUID | None, dict[str, Any]] | None:
        invocation_kind = str(row["invocation_kind"])
        output_ref = ensure_json_object(row["output_ref_json"])
        ordered_layer_ids = _layer_ids_from_output_ref(
            invocation_kind=invocation_kind,
            output_ref=output_ref,
        )
        if ordered_layer_ids is None or len(set(ordered_layer_ids)) != len(
            ordered_layer_ids
        ):
            return None
        if not ordered_layer_ids:
            return _no_published_layer_attribution(
                invocation_kind=invocation_kind,
                output_ref=output_ref,
            )

        layer_rows = await conn.fetch(
            """
            SELECT id, layer_type
            FROM enhancement_layers
            WHERE id = ANY($1::uuid[])
              AND reading_record_id = $2
              AND source_job_id = $3
              AND status = 'published'
            """,
            ordered_layer_ids,
            row["reading_record_id"],
            row["reader_job_id"],
        )
        layer_types_by_id = {
            layer_row["id"]: str(layer_row["layer_type"])
            for layer_row in layer_rows
        }
        allowed_layer_types = _LAYER_TYPES_BY_INVOCATION_KIND[invocation_kind]
        if any(
            layer_types_by_id.get(layer_id) not in allowed_layer_types
            for layer_id in ordered_layer_ids
        ):
            return None

        metadata_patch: dict[str, Any] = {
            "publication_attribution_state": "published_layer_reconciled",
            "published_layer_ids": [str(layer_id) for layer_id in ordered_layer_ids],
        }
        if invocation_kind == "reader.grammar_unit":
            metadata_patch.update(
                {
                    "published_layer_types": [
                        layer_types_by_id[layer_id]
                        for layer_id in ordered_layer_ids
                    ],
                    "no_op": False,
                }
            )
        elif invocation_kind == "reader.grammar_window":
            grammar_ids = _uuid_list(output_ref.get("grammar_note_layer_ids"))
            sentence_ids = _uuid_list(
                output_ref.get("sentence_analysis_layer_ids")
            )
            accepted_count = output_ref.get("accepted_count")
            if (
                grammar_ids is None
                or sentence_ids is None
                or not isinstance(accepted_count, int)
            ):
                return None
            metadata_patch.update(
                {
                    "accepted_count": accepted_count,
                    "no_op": False,
                    "layer_ids": [str(layer_id) for layer_id in ordered_layer_ids],
                    "grammar_note_layer_ids": [
                        str(layer_id) for layer_id in grammar_ids
                    ],
                    "sentence_analysis_layer_ids": [
                        str(layer_id) for layer_id in sentence_ids
                    ],
                }
            )
        return ordered_layer_ids[0], metadata_patch


def _layer_ids_from_output_ref(
    *,
    invocation_kind: str,
    output_ref: dict[str, Any],
) -> list[UUID] | None:
    if invocation_kind in {
        "reader.semantic_outline",
        "reader.translation_unit",
        "reader.vocabulary_unit",
    }:
        return _uuid_list(output_ref.get("layer_id"))
    if invocation_kind in {
        "reader.translation_batch",
        "reader.vocabulary_batch",
    }:
        return _uuid_list(output_ref.get("layer_ids"))
    if invocation_kind == "reader.grammar_unit":
        return _uuid_list(
            [
                value
                for value in (
                    output_ref.get("grammar_note_layer_id"),
                    output_ref.get("sentence_analysis_layer_id"),
                )
                if value is not None
            ]
        )
    if invocation_kind == "reader.grammar_window":
        grammar_ids = _uuid_list(output_ref.get("grammar_note_layer_ids"))
        sentence_ids = _uuid_list(output_ref.get("sentence_analysis_layer_ids"))
        if grammar_ids is None or sentence_ids is None:
            return None
        return grammar_ids + sentence_ids
    return None


def _no_published_layer_attribution(
    *,
    invocation_kind: str,
    output_ref: dict[str, Any],
) -> tuple[None, dict[str, Any]] | None:
    if output_ref.get("no_op") is not True:
        return None

    metadata_patch: dict[str, Any] = {
        "publication_attribution_state": "no_published_layer",
        "published_layer_ids": [],
        "no_op": True,
    }
    if invocation_kind == "reader.grammar_unit":
        if (
            output_ref.get("grammar_note_count") != 0
            or output_ref.get("sentence_analysis_count") != 0
        ):
            return None
        metadata_patch["published_layer_types"] = []
        return None, metadata_patch

    if invocation_kind == "reader.grammar_window":
        grammar_ids = _uuid_list(output_ref.get("grammar_note_layer_ids"))
        sentence_ids = _uuid_list(
            output_ref.get("sentence_analysis_layer_ids")
        )
        accepted_count = output_ref.get("accepted_count")
        if grammar_ids != [] or sentence_ids != [] or accepted_count != 0:
            return None
        metadata_patch.update(
            {
                "accepted_count": 0,
                "layer_ids": [],
                "grammar_note_layer_ids": [],
                "sentence_analysis_layer_ids": [],
            }
        )
        return None, metadata_patch

    return None


def _uuid_list(value: Any) -> list[UUID] | None:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    try:
        return [UUID(str(item)) for item in values]
    except (TypeError, ValueError, AttributeError):
        return None
