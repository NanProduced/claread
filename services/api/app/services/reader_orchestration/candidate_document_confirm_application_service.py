"""D6-I2D-B Candidate Document Confirm Application Service.

Wraps D6-I2D-A's :func:`confirm_candidate_document` in a full
application service that transitions a candidate document into a
readable Reader state within a single caller-independent transaction.

Normal transaction flow (strict order):
    1. ``frozen_at = now or datetime.now(UTC)``
    2. ``async with pool.acquire() as conn``
    3. ``async with conn.transaction()``
    4. Call :func:`confirm_candidate_document` (D6-I2D-A) — freezes the
       candidate into a stable document + canonical text layer +
       confirms the candidate row.
    5. Fail closed if ``freeze_result.base_id is None``.
    6. Call ``repository.set_active_base_and_mark_article_ready`` to
       set ``active_base_id``, ``readiness_state='article_ready'``,
       ``product_state='readable_enhancing'`` on ``reading_records``,
       guarded by ``expected_generation``.
    7. Call ``event_runtime.publish_event_in_transaction`` with
       ``event_type="article_ready"`` and a payload capturing the
       candidate/stable/base/hash/idempotent fields. The DB does not
       allow new ``event_type`` values, so ``"article_ready"`` is
       reused with a ``source`` discriminator in the payload.
    8. After the transaction commits, call
       ``snapshot_service.load_snapshot`` to reload the
       :class:`ReaderPlateSnapshot`.
    9. Return a :class:`CandidateDocumentConfirmApplicationResult`
       mapping freeze result + event envelope + snapshot.

Confirmed-candidate recovery path (D6-I2D-B-H):
    When :func:`confirm_candidate_document` raises
    :class:`CandidateDocumentStatusError` with ``status='confirmed'``
    (i.e. a prior attempt committed the freeze + state + event but
    failed before returning — e.g. snapshot reload crashed), the
    service enters a recovery path instead of re-freezing:

    1. ``async with pool.acquire() as conn``
    2. ``async with conn.transaction()``
    3. ``confirm_candidate_document`` raises
       :class:`CandidateDocumentStatusError(status='confirmed')`.
    4. ``_recover_confirmed_candidate`` reads and validates the
       committed state (5 read-only queries: stable_reading_documents,
       reading_records, reading_bases, stable_document_blocks count,
       reader_events). Any missing/inconsistent row fails closed.
    5. NO ``set_active_base_and_mark_article_ready``.
    6. NO ``publish_event_in_transaction``.
    7. After commit, ``snapshot_service.load_snapshot``.
    8. Return result with ``candidate_confirmed=True``,
       ``freeze_idempotent_noop=True``.

    ``rejected`` / ``superseded`` / unknown statuses still fail
    closed — they do NOT enter recovery.

Error strategy:
    * :class:`CandidateDocumentConfirmError` from D6-I2D-A is wrapped
      as :class:`CandidateDocumentConfirmApplicationError` with
      ``raise ... from exc``.
    * :class:`CandidateDocumentStatusError` with ``status='confirmed'``
      triggers recovery; other statuses are wrapped as
      :class:`CandidateDocumentConfirmApplicationError`.
    * ``ValueError`` / ``LookupError`` / ``RuntimeError`` (and
      ``TypeError`` for the event runtime) from repository / event /
      snapshot are similarly wrapped with ``__cause__`` preserved.
    * Any error inside the transaction triggers a rollback — no
      snapshot reload is attempted.
    * A snapshot load error after commit is wrapped and re-raised;
      the transaction has already committed and cannot be rolled back.

Out of scope:
    * API route / BFF / Web integration.
    * New ``event_type`` values (DB constraint prevents this).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg

from app.schemas.reader_orchestration import ReaderPlateSnapshot
from app.services.reader_orchestration.article_ready_service import (
    ArticleReadyPersistenceService,
)
from app.services.reader_orchestration.candidate_document_confirm_service import (
    CandidateDocumentConfirmError,
    CandidateDocumentConfirmResult,
    CandidateDocumentStatusError,
    confirm_candidate_document,
)
from app.services.reader_orchestration.event_runtime import (
    ReaderEventEnvelope,
    ReaderEventRuntime,
)
from app.services.reader_orchestration.repository import (
    ReaderOrchestrationRepository,
)


class CandidateDocumentConfirmApplicationError(ValueError):
    """Raised when the application service cannot complete the
    candidate document confirmation flow.

    Wraps :class:`CandidateDocumentConfirmError` (from D6-I2D-A) and
    ``ValueError`` / ``LookupError`` / ``RuntimeError`` /
    ``TypeError`` from the repository / event runtime / snapshot
    service. The original exception is preserved as ``__cause__``.
    """


@dataclass(frozen=True, slots=True)
class CandidateDocumentConfirmApplicationResult:
    """Application-layer result mapping the D6-I2D-A freeze result,
    the ``article_ready`` event envelope, and the reloaded snapshot.
    """

    reading_record_id: UUID
    candidate_document_id: UUID
    stable_document_id: UUID
    base_id: UUID
    record_generation: int
    document_version: int
    content_sha256: str
    canonical_text_sha256: str
    block_count: int
    candidate_confirmed: bool
    freeze_idempotent_noop: bool
    article_ready_event_id: UUID
    article_ready_sequence: int
    snapshot: ReaderPlateSnapshot


@dataclass(frozen=True, slots=True)
class _RecoveryState:
    """Committed-state snapshot read by the confirmed-candidate recovery
    path. All fields are validated to be present and consistent before
    the recovery returns — any missing/inconsistent row fails closed.
    """

    stable_document_id: UUID
    base_id: UUID
    record_generation: int
    document_version: int
    content_sha256: str
    canonical_text_sha256: str
    block_count: int
    article_ready_event_id: UUID
    article_ready_sequence: int


class CandidateDocumentConfirmApplicationService:
    """Application service that confirms a candidate document and
    transitions the reading record into a readable state.

    The service acquires its own pool connection, opens its own
    transaction, and delegates to D6-I2D-A
    :func:`confirm_candidate_document` for the freeze logic. After
    commit, it reloads the snapshot.
    """

    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        repository: ReaderOrchestrationRepository | None = None,
        event_runtime: ReaderEventRuntime | None = None,
        snapshot_service: ArticleReadyPersistenceService | None = None,
    ) -> None:
        self._pool = pool
        self._repository = repository or ReaderOrchestrationRepository(pool=pool)
        self._event_runtime = event_runtime or ReaderEventRuntime(pool=pool)
        self._snapshot_service = snapshot_service or ArticleReadyPersistenceService(
            pool=pool, repository=self._repository
        )

    def _get_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        return self._repository.get_pool()

    async def confirm_candidate_document_and_load_snapshot(
        self,
        *,
        candidate_document_id: UUID,
        reading_record_id: UUID,
        user_id: UUID,
        canonicalizer_version: str,
        builder_version: str,
        segmenter_version: str,
        language: str | None = None,
        now: datetime | None = None,
    ) -> CandidateDocumentConfirmApplicationResult:
        """Confirm a candidate document, mark the reading record
        article_ready, publish an event, and reload the snapshot.

        If the candidate is already ``confirmed`` (e.g. a prior
        attempt committed the freeze + state + event but failed before
        returning), the service enters a **recovery path**: it reads
        and validates the committed state inside a fresh transaction,
        then reloads the snapshot — without re-freezing, re-marking
        state, or re-publishing the event.

        Args:
            candidate_document_id: The candidate document to confirm.
            reading_record_id: The reading record id.
            user_id: The user id (must match the candidate row).
            canonicalizer_version: Version label for the canonicalizer.
            builder_version: Version label for the reading base builder.
            segmenter_version: Version label for the segmenter.
            language: Optional language code.
            now: Optional timestamp; defaults to ``datetime.now(UTC)``.

        Returns:
            A :class:`CandidateDocumentConfirmApplicationResult`.

        Raises:
            CandidateDocumentConfirmApplicationError: On any failure
                in the confirm/state/event/snapshot flow or in the
                recovery validation. The original exception is
                preserved as ``__cause__``.
        """
        frozen_at = now or datetime.now(UTC)
        pool = self._get_pool()

        recovery_state: _RecoveryState | None = None
        freeze_result: CandidateDocumentConfirmResult | None = None
        envelope: ReaderEventEnvelope | None = None

        # ------------------------------------------------------------------
        # Transaction boundary: confirm + state update + event.
        # ------------------------------------------------------------------
        async with pool.acquire() as conn:
            async with conn.transaction():
                # (1) Confirm candidate document (D6-I2D-A).
                try:
                    freeze_result = await confirm_candidate_document(
                        conn,
                        candidate_document_id=candidate_document_id,
                        reading_record_id=reading_record_id,
                        user_id=user_id,
                        canonicalizer_version=canonicalizer_version,
                        builder_version=builder_version,
                        segmenter_version=segmenter_version,
                        language=language,
                        now=frozen_at,
                    )
                except CandidateDocumentStatusError as exc:
                    if exc.status == "confirmed":
                        # Recovery path: candidate was already confirmed
                        # in a prior committed attempt. Read and validate
                        # the committed state instead of re-freezing.
                        # Does NOT call set_active_base_and_mark_article_ready
                        # or publish_event_in_transaction.
                        recovery_state = await self._recover_confirmed_candidate(
                            conn,
                            candidate_document_id=candidate_document_id,
                            reading_record_id=reading_record_id,
                            user_id=user_id,
                            status_error=exc,
                        )
                    else:
                        # rejected / superseded / unknown — fail closed.
                        raise CandidateDocumentConfirmApplicationError(
                            f"Candidate document {candidate_document_id} has "
                            f"status={exc.status!r} (expected 'ready'). Only "
                            "'ready' candidates can be confirmed; 'confirmed' "
                            "candidates are recovered. Other statuses fail "
                            "closed."
                        ) from exc
                except CandidateDocumentConfirmError as exc:
                    raise CandidateDocumentConfirmApplicationError(
                        f"Candidate document confirmation failed for "
                        f"candidate {candidate_document_id}: {exc}"
                    ) from exc

                if recovery_state is None:
                    # Normal path: freeze succeeded, mark state + publish
                    # event. The recovery path skips all of this.
                    assert freeze_result is not None

                    # (2) Fail closed if base_id is None — cannot mark the
                    # reading record article_ready without an active base.
                    if freeze_result.base_id is None:
                        raise CandidateDocumentConfirmApplicationError(
                            f"Candidate document confirmation for "
                            f"candidate {candidate_document_id} returned "
                            f"base_id=None. Cannot mark reading record "
                            f"article_ready without an active base."
                        )

                    # (3) Mark reading record as article_ready +
                    # readable_enhancing, guarded by expected_generation.
                    try:
                        await self._repository.set_active_base_and_mark_article_ready(
                            conn,
                            record_id=reading_record_id,
                            base_id=freeze_result.base_id,
                            expected_generation=freeze_result.record_generation,
                            updated_at=frozen_at,
                        )
                    except (ValueError, LookupError, RuntimeError) as exc:
                        raise CandidateDocumentConfirmApplicationError(
                            f"Failed to mark reading record "
                            f"{reading_record_id} as article_ready: {exc}"
                        ) from exc

                    # (4) Publish article_ready event. The DB does not
                    # allow new event_type values, so "article_ready" is
                    # reused with a "source" discriminator in the payload.
                    payload_json: dict[str, Any] = {
                        "record_id": str(reading_record_id),
                        "candidate_document_id": str(candidate_document_id),
                        "stable_document_id": str(freeze_result.stable_document_id),
                        "base_id": str(freeze_result.base_id),
                        "generation": freeze_result.record_generation,
                        "document_version": freeze_result.document_version,
                        "readiness_state": "article_ready",
                        "product_state": "readable_enhancing",
                        "content_sha256": freeze_result.content_sha256,
                        "canonical_text_sha256": freeze_result.canonical_text_sha256,
                        "block_count": freeze_result.block_count,
                        "candidate_confirmed": freeze_result.candidate_confirmed,
                        "freeze_idempotent_noop": freeze_result.idempotent_noop,
                        "source": "candidate_document_confirm",
                    }
                    try:
                        envelope = await self._event_runtime.publish_event_in_transaction(
                            conn,
                            record_id=reading_record_id,
                            event_type="article_ready",
                            payload_json=payload_json,
                            created_at=frozen_at,
                        )
                    except (ValueError, LookupError, RuntimeError, TypeError) as exc:
                        raise CandidateDocumentConfirmApplicationError(
                            f"Failed to publish article_ready event for "
                            f"reading record {reading_record_id}: {exc}"
                        ) from exc

        # ------------------------------------------------------------------
        # Post-commit: reload snapshot.
        # ------------------------------------------------------------------
        if recovery_state is not None:
            expected_base_id = recovery_state.base_id
            expected_generation = recovery_state.record_generation
        else:
            assert freeze_result is not None
            expected_base_id = freeze_result.base_id
            expected_generation = freeze_result.record_generation

        try:
            snapshot = await self._snapshot_service.load_snapshot(
                record_id=reading_record_id,
                user_id=user_id,
                expected_base_id=expected_base_id,
                expected_generation=expected_generation,
            )
        except (ValueError, LookupError, RuntimeError) as exc:
            raise CandidateDocumentConfirmApplicationError(
                f"Failed to reload snapshot after committing candidate "
                f"document confirmation for reading record "
                f"{reading_record_id}: {exc}"
            ) from exc

        # ------------------------------------------------------------------
        # Return result.
        # ------------------------------------------------------------------
        if recovery_state is not None:
            return CandidateDocumentConfirmApplicationResult(
                reading_record_id=reading_record_id,
                candidate_document_id=candidate_document_id,
                stable_document_id=recovery_state.stable_document_id,
                base_id=recovery_state.base_id,
                record_generation=recovery_state.record_generation,
                document_version=recovery_state.document_version,
                content_sha256=recovery_state.content_sha256,
                canonical_text_sha256=recovery_state.canonical_text_sha256,
                block_count=recovery_state.block_count,
                candidate_confirmed=True,
                freeze_idempotent_noop=True,
                article_ready_event_id=recovery_state.article_ready_event_id,
                article_ready_sequence=recovery_state.article_ready_sequence,
                snapshot=snapshot,
            )

        assert freeze_result is not None
        assert envelope is not None
        return CandidateDocumentConfirmApplicationResult(
            reading_record_id=reading_record_id,
            candidate_document_id=candidate_document_id,
            stable_document_id=freeze_result.stable_document_id,
            base_id=freeze_result.base_id,
            record_generation=freeze_result.record_generation,
            document_version=freeze_result.document_version,
            content_sha256=freeze_result.content_sha256,
            canonical_text_sha256=freeze_result.canonical_text_sha256,
            block_count=freeze_result.block_count,
            candidate_confirmed=freeze_result.candidate_confirmed,
            freeze_idempotent_noop=freeze_result.idempotent_noop,
            article_ready_event_id=envelope.event_id,
            article_ready_sequence=envelope.sequence,
            snapshot=snapshot,
        )

    async def _recover_confirmed_candidate(
        self,
        conn: asyncpg.Connection,
        *,
        candidate_document_id: UUID,
        reading_record_id: UUID,
        user_id: UUID,
        status_error: CandidateDocumentStatusError,
    ) -> _RecoveryState:
        """Read and validate the already-committed state for a
        ``confirmed`` candidate inside the current transaction.

        Issues five read-only queries in strict order and fails closed
        (raising :class:`CandidateDocumentConfirmApplicationError`) if
        any expected row is missing or inconsistent. Does NOT write
        state, publish events, or re-confirm the candidate.

        Query order:
            1. ``stable_reading_documents`` — active row for
               (reading_record_id, record_generation).
            2. ``reading_records`` — guarded by user_id /
               deleted_at IS NULL / lifecycle_status='active'.
               Validates active_base_id non-NULL, generation match,
               readiness_state='article_ready',
               product_state='readable_enhancing'.
            3. ``reading_bases`` — active row with matching id /
               record_id / generation.
            4. ``stable_document_blocks`` — count > 0.
            5. ``reader_events`` — latest ``article_ready`` event with
               ``source='candidate_document_confirm'`` and matching
               ``candidate_document_id``. payload_json is returned and
               fully validated: must be a JSON object with matching
               source / candidate_document_id / stable_document_id /
               base_id / generation / document_version fields.
        """
        record_generation = status_error.record_generation

        # (1) stable_reading_documents — must have an active row.
        stable_row = await conn.fetchrow(
            """
            SELECT id, document_version, content_sha256
            FROM stable_reading_documents
            WHERE reading_record_id = $1
              AND record_generation = $2
              AND status = 'active'
            """,
            reading_record_id,
            record_generation,
        )
        if stable_row is None:
            raise CandidateDocumentConfirmApplicationError(
                f"Recovery for confirmed candidate {candidate_document_id} "
                f"failed: no active stable_reading_documents row for "
                f"reading_record_id={reading_record_id} "
                f"record_generation={record_generation}."
            )
        stable_document_id = UUID(str(stable_row["id"]))
        document_version = int(stable_row["document_version"])
        content_sha256 = str(stable_row["content_sha256"])

        # (2) reading_records — guarded by user_id / deleted_at /
        # lifecycle_status. Validates active_base_id, generation,
        # readiness_state, product_state.
        record_row = await conn.fetchrow(
            """
            SELECT active_base_id, generation, product_state, readiness_state
            FROM reading_records
            WHERE id = $1
              AND user_id = $2
              AND deleted_at IS NULL
              AND lifecycle_status = 'active'
            """,
            reading_record_id,
            user_id,
        )
        if record_row is None:
            raise CandidateDocumentConfirmApplicationError(
                f"Recovery for confirmed candidate {candidate_document_id} "
                f"failed: reading_records row not found for "
                f"reading_record_id={reading_record_id} "
                f"user_id={user_id} (guarded by deleted_at IS NULL "
                f"and lifecycle_status='active')."
            )
        active_base_id_raw = record_row["active_base_id"]
        if active_base_id_raw is None:
            raise CandidateDocumentConfirmApplicationError(
                f"Recovery for confirmed candidate {candidate_document_id} "
                f"failed: reading_records.active_base_id is NULL for "
                f"reading_record_id={reading_record_id}."
            )
        active_base_id = UUID(str(active_base_id_raw))
        actual_generation = int(record_row["generation"])
        if actual_generation != record_generation:
            raise CandidateDocumentConfirmApplicationError(
                f"Recovery for confirmed candidate {candidate_document_id} "
                f"failed: reading_records.generation={actual_generation} "
                f"does not match record_generation={record_generation}."
            )
        actual_readiness_state = str(record_row["readiness_state"])
        if actual_readiness_state != "article_ready":
            raise CandidateDocumentConfirmApplicationError(
                f"Recovery for confirmed candidate {candidate_document_id} "
                f"failed: reading_records.readiness_state="
                f"{actual_readiness_state!r} (expected 'article_ready') "
                f"for reading_record_id={reading_record_id}."
            )
        actual_product_state = str(record_row["product_state"])
        if actual_product_state != "readable_enhancing":
            raise CandidateDocumentConfirmApplicationError(
                f"Recovery for confirmed candidate {candidate_document_id} "
                f"failed: reading_records.product_state="
                f"{actual_product_state!r} (expected 'readable_enhancing') "
                f"for reading_record_id={reading_record_id}."
            )

        # (3) reading_bases — active row must exist with matching id.
        base_row = await conn.fetchrow(
            """
            SELECT id, content_sha256
            FROM reading_bases
            WHERE id = $1
              AND reading_record_id = $2
              AND record_generation = $3
              AND status = 'active'
            """,
            active_base_id,
            reading_record_id,
            record_generation,
        )
        if base_row is None:
            raise CandidateDocumentConfirmApplicationError(
                f"Recovery for confirmed candidate {candidate_document_id} "
                f"failed: no active reading_bases row for "
                f"base_id={active_base_id} "
                f"reading_record_id={reading_record_id} "
                f"record_generation={record_generation}."
            )
        canonical_text_sha256 = str(base_row["content_sha256"])

        # (4) stable_document_blocks — count must be > 0.
        block_count_raw = await conn.fetchval(
            """
            SELECT COUNT(*) FROM stable_document_blocks
            WHERE stable_document_id = $1
            """,
            stable_document_id,
        )
        if block_count_raw is None or int(block_count_raw) == 0:
            raise CandidateDocumentConfirmApplicationError(
                f"Recovery for confirmed candidate {candidate_document_id} "
                f"failed: stable_document_blocks count is 0 for "
                f"stable_document_id={stable_document_id}."
            )
        block_count = int(block_count_raw)

        # (5) reader_events — latest article_ready event with
        # source='candidate_document_confirm' and matching candidate.
        # Returns payload_json for full field validation.
        event_row = await conn.fetchrow(
            """
            SELECT id, sequence, payload_json
            FROM reader_events
            WHERE reading_record_id = $1
              AND event_type = 'article_ready'
              AND payload_json->>'source' = 'candidate_document_confirm'
              AND payload_json->>'candidate_document_id' = $2
            ORDER BY sequence DESC
            LIMIT 1
            """,
            reading_record_id,
            str(candidate_document_id),
        )
        if event_row is None:
            raise CandidateDocumentConfirmApplicationError(
                f"Recovery for confirmed candidate {candidate_document_id} "
                f"failed: no article_ready event with "
                f"source='candidate_document_confirm' found for "
                f"reading_record_id={reading_record_id} "
                f"candidate_document_id={candidate_document_id}."
            )
        article_ready_event_id = UUID(str(event_row["id"]))
        article_ready_sequence = int(event_row["sequence"])

        # (5b) Validate event payload_json is a JSON object with
        # matching fields. JSONB may arrive as a dict (asyncpg default
        # for jsonb columns) or as a JSON string (some driver configs).
        # Non-object / invalid JSON fails closed.
        payload_raw = event_row["payload_json"]
        if isinstance(payload_raw, str):
            try:
                payload = json.loads(payload_raw)
            except (json.JSONDecodeError, TypeError) as exc:
                raise CandidateDocumentConfirmApplicationError(
                    f"Recovery for confirmed candidate {candidate_document_id} "
                    f"failed: reader_events payload_json is not valid JSON."
                ) from exc
        elif isinstance(payload_raw, Mapping):
            payload = dict(payload_raw)
        else:
            raise CandidateDocumentConfirmApplicationError(
                f"Recovery for confirmed candidate {candidate_document_id} "
                f"failed: reader_events payload_json is not a JSON object "
                f"(got {type(payload_raw).__name__})."
            )
        if not isinstance(payload, Mapping):
            raise CandidateDocumentConfirmApplicationError(
                f"Recovery for confirmed candidate {candidate_document_id} "
                f"failed: reader_events payload_json parsed to a "
                f"non-object value (got {type(payload).__name__})."
            )

        # Validate every payload field against the recovered committed
        # state. str() comparison handles both int and str JSON values.
        expected_fields: dict[str, Any] = {
            "source": "candidate_document_confirm",
            "candidate_document_id": str(candidate_document_id),
            "stable_document_id": str(stable_document_id),
            "base_id": str(active_base_id),
            "generation": record_generation,
            "document_version": document_version,
        }
        for field_name, expected_value in expected_fields.items():
            actual_value = payload.get(field_name)
            if str(actual_value) != str(expected_value):
                raise CandidateDocumentConfirmApplicationError(
                    f"Recovery for confirmed candidate {candidate_document_id} "
                    f"failed: reader_events payload_json field "
                    f"{field_name!r}={actual_value!r} does not match "
                    f"expected value {expected_value!r}."
                )

        return _RecoveryState(
            stable_document_id=stable_document_id,
            base_id=active_base_id,
            record_generation=record_generation,
            document_version=document_version,
            content_sha256=content_sha256,
            canonical_text_sha256=canonical_text_sha256,
            block_count=block_count,
            article_ready_event_id=article_ready_event_id,
            article_ready_sequence=article_ready_sequence,
        )
