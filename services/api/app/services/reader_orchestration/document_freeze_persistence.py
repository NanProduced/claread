"""D6-I2B Stable Document Freeze Persistence Transaction.

Consumes a D6-I2A ``StableDocumentFreezePlan`` and commits the stable
document + canonical text layer + (optional) candidate confirmation in
a single caller-managed DB transaction.

Transaction model:
    The caller opens the transaction (``async with conn.transaction():``)
    and passes the connection. This service executes its SQL within that
    transaction. Any exception raised here propagates to the caller and
    triggers a rollback, leaving no half-frozen document.

    The service validates ``conn.is_in_transaction()`` at entry and
    fails closed if the caller forgot to open a transaction.

In-transaction steps (in order):
    1. Idempotency check: query for an existing
       ``stable_reading_documents`` row with the same
       ``(reading_record_id, record_generation)``.
         * Same ``content_sha256`` -> idempotent no-op for the stable
           document. Fails closed if ``reading_records.active_base_id``
           is NULL (interrupted prior freeze). If
           ``candidate_document_id`` is provided, still confirms the
           candidate via the state-machine-safe helper.
         * Different ``content_sha256`` -> fail closed.
    2. Supersede prior active ``stable_reading_documents`` for the same
       record (set ``status='superseded'``).
    3. Insert ``stable_reading_documents`` row from ``plan.stable_document``.
    4. Insert ``stable_document_blocks`` rows from ``plan.blocks``.
       Each block's ``interpretation_policy_json`` is materialized via
       ``block.interpretation_policy.model_dump(mode="json")`` — the DB
       DEFAULT ``'{}'::jsonb`` is never relied on (see migration comment).
       ``parent_block_id`` is the document-local block_id string, NOT
       the row UUID.
    5. Supersede prior active ``reading_bases`` for the same record
       (set ``status='superseded'``). This MUST happen before the new
       INSERT to avoid violating ``uq_reading_bases_active_record``
       (only one active base per reading_record_id).
    6. Insert ``reading_bases`` row as the V1 Canonical Text Layer
       carrier: ``text = plan.canonical_text``,
       ``content_sha256 = sha256(plan.canonical_text)`` (NOT
       ``plan.content_sha256``, which is the block-level hash).
    7. Set ``reading_records.active_base_id`` to the new base with a
       generation fence (``WHERE generation = $N``).
    8. If ``candidate_document_id`` is provided, confirm the candidate
       via ``_confirm_candidate_document`` with state-machine safety:
         * ``ready`` -> ``confirmed`` (UPDATE with ``AND status='ready'``).
         * ``confirmed`` -> idempotent success (no write).
         * ``rejected`` / ``superseded`` -> fail closed.
       Guarded by ``(reading_record_id, record_generation)`` and
       ``user_id`` (when provided).

Out of scope (D6-I2C/I2D follow-up):
    * Reading Units / Anchor Segments construction.
    * API route / BFF / Web integration.
    * Reader event publication (the caller may publish a
      ``stable_document_frozen`` event after commit if desired).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg

from app.database.json_compat import jsonb_param
from app.contracts.annotation import utf16_code_unit_length
from app.schemas.reader_documents import StableDocumentBlock
from app.services.reader_orchestration.document_freeze_plan import (
    StableDocumentFreezePlan,
)


class StableDocumentFreezePersistenceError(ValueError):
    """Raised when the freeze persistence cannot proceed.

    Concrete reasons: connection not in a transaction, same-generation
    content_sha256 mismatch (fail closed), generation fence violation
    on reading_records, interrupted prior freeze (NULL active_base_id
    in idempotent branch), candidate document not found, or candidate
    in a non-confirmable state.
    """


@dataclass(frozen=True, slots=True)
class StableDocumentFreezePersistenceResult:
    """Summary of the persisted freeze transaction.

    ``idempotent_noop=True`` means the service found an existing
    stable document with the same (record, generation, content_sha256)
    and performed NO writes to stable_reading_documents /
    stable_document_blocks / reading_bases. In that case
    ``stable_document_id`` is the existing row's id and ``base_id`` is
    the current ``reading_records.active_base_id`` (which must NOT be
    NULL — a NULL active_base_id in the idempotent branch is treated
    as an interrupted prior freeze and fails closed).
    """

    stable_document_id: UUID
    base_id: UUID | None
    reading_record_id: UUID
    record_generation: int
    document_version: int
    content_sha256: str
    canonical_text_sha256: str
    block_count: int
    candidate_confirmed: bool
    idempotent_noop: bool


def compute_canonical_text_sha256(canonical_text: str) -> str:
    """SHA-256 of the canonical text, for ``reading_bases.content_sha256``.

    This is distinct from ``plan.content_sha256`` (which is the
    block-level hash computed by
    ``compute_stable_document_content_sha256``). The reading_bases row
    hashes the canonical TEXT so that existing snapshot validation
    (which compares ``sha256(base.text)``) continues to work.
    """
    return hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()


def compute_canonical_text_utf16_length(canonical_text: str) -> int:
    """UTF-16 code unit length of the canonical text.

    Uses the same ``utf16_code_unit_length`` helper as the rest of the
    codebase so emoji / surrogate pairs are counted correctly.
    """
    return utf16_code_unit_length(canonical_text)


async def persist_stable_document_freeze_plan(
    conn: asyncpg.Connection,
    *,
    plan: StableDocumentFreezePlan,
    canonicalizer_version: str,
    builder_version: str,
    segmenter_version: str,
    language: str | None = None,
    candidate_document_id: UUID | None = None,
    user_id: UUID | None = None,
    now: datetime | None = None,
) -> StableDocumentFreezePersistenceResult:
    """Persist a ``StableDocumentFreezePlan`` within a caller-managed
    transaction.

    The caller is responsible for ``async with conn.transaction():``.
    This function does NOT open its own transaction; it only executes
    SQL on the supplied connection. Any exception propagates and the
    caller's transaction context manager rolls back.

    Args:
        conn: An asyncpg connection that is already inside a
            transaction.
        plan: The freeze plan produced by
            ``build_stable_document_freeze_plan``. Not mutated.
        canonicalizer_version: Version label for the canonicalizer
            that produced the canonical text (written to
            ``reading_bases.canonicalizer_version``).
        builder_version: Version label for the reading base builder.
        segmenter_version: Version label for the segmenter.
        language: Optional language code for the reading base.
        candidate_document_id: If provided, the candidate document
            with this id will be confirmed via the state-machine-safe
            helper ``_confirm_candidate_document``.
        user_id: If provided (together with ``candidate_document_id``),
            the candidate lookup/update is additionally guarded by
            ``user_id``.
        now: Optional timestamp; defaults to ``datetime.now(UTC)``.

    Returns:
        A ``StableDocumentFreezePersistenceResult`` summarizing the
        persisted rows.

    Raises:
        StableDocumentFreezePersistenceError: On connection not in
            transaction, same-generation hash mismatch, generation
            fence violation, interrupted prior freeze (NULL
            active_base_id in idempotent branch), candidate not found,
            or candidate in a non-confirmable state.
    """
    # (0) Fail closed if the caller forgot to open a transaction.
    if not conn.is_in_transaction():
        raise StableDocumentFreezePersistenceError(
            "persist_stable_document_freeze_plan must be called within "
            "an active transaction. Refusing to execute outside a "
            "transaction to prevent half-frozen documents."
        )

    frozen_at = now or datetime.now(UTC)
    stable_doc = plan.stable_document
    reading_record_id = UUID(stable_doc.reading_record_id)
    record_generation = stable_doc.record_generation
    document_version = stable_doc.document_version
    content_sha256 = plan.content_sha256
    canonical_text = plan.canonical_text
    canonical_text_sha256 = compute_canonical_text_sha256(canonical_text)
    canonical_text_utf16_length = compute_canonical_text_utf16_length(canonical_text)

    # ------------------------------------------------------------------
    # (1) Idempotency / fail-closed check on same-generation stable doc.
    # ------------------------------------------------------------------
    existing_row = await conn.fetchrow(
        """
        SELECT id, content_sha256, status, document_version
        FROM stable_reading_documents
        WHERE reading_record_id = $1
          AND record_generation = $2
        """,
        reading_record_id,
        record_generation,
    )

    if existing_row is not None:
        existing_sha = str(existing_row["content_sha256"])
        if existing_sha == content_sha256:
            # Idempotent no-op for the stable document. Fetch the
            # current active_base_id; if it is NULL, a prior freeze
            # was interrupted before setting the active base. Fail
            # closed rather than returning a partial result.
            record_row = await conn.fetchrow(
                """
                SELECT active_base_id
                FROM reading_records
                WHERE id = $1 AND generation = $2
                """,
                reading_record_id,
                record_generation,
            )
            if record_row is None or record_row["active_base_id"] is None:
                raise StableDocumentFreezePersistenceError(
                    f"Idempotent stable document found for "
                    f"reading_record_id={reading_record_id} "
                    f"record_generation={record_generation}, but "
                    "reading_records.active_base_id is NULL. The prior "
                    "freeze was interrupted before setting the active "
                    "base; refusing to return a partial result."
                )
            existing_base_id = UUID(str(record_row["active_base_id"]))

            # If candidate_document_id is provided, still confirm the
            # candidate even though the stable document is idempotent.
            # The candidate may not have been confirmed in the
            # interrupted prior freeze.
            candidate_confirmed = False
            if candidate_document_id is not None:
                candidate_confirmed = await _confirm_candidate_document(
                    conn,
                    candidate_document_id=candidate_document_id,
                    reading_record_id=reading_record_id,
                    record_generation=record_generation,
                    user_id=user_id,
                    frozen_at=frozen_at,
                )

            return StableDocumentFreezePersistenceResult(
                stable_document_id=UUID(str(existing_row["id"])),
                base_id=existing_base_id,
                reading_record_id=reading_record_id,
                record_generation=record_generation,
                document_version=int(existing_row["document_version"]),
                content_sha256=existing_sha,
                canonical_text_sha256=canonical_text_sha256,
                block_count=len(plan.blocks),
                candidate_confirmed=candidate_confirmed,
                idempotent_noop=True,
            )
        raise StableDocumentFreezePersistenceError(
            f"Cannot freeze stable document for reading_record_id="
            f"{reading_record_id} record_generation={record_generation}: "
            f"an existing stable document has content_sha256="
            f"{existing_sha!r} which differs from the plan's "
            f"content_sha256={content_sha256!r}. Same-generation "
            "re-freeze with different content is not allowed."
        )

    # ------------------------------------------------------------------
    # (2) Supersede prior active stable_reading_documents for this record.
    # ------------------------------------------------------------------
    await conn.execute(
        """
        UPDATE stable_reading_documents
        SET status = 'superseded'
        WHERE reading_record_id = $1
          AND status = 'active'
        """,
        reading_record_id,
    )

    # ------------------------------------------------------------------
    # (3) Insert stable_reading_documents row.
    # ------------------------------------------------------------------
    stable_document_id = uuid4()
    await conn.execute(
        """
        INSERT INTO stable_reading_documents (
            id,
            reading_record_id,
            record_generation,
            title,
            document_version,
            source_profile_json,
            content_sha256,
            status,
            frozen_at,
            created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, 'active', $8, $8)
        """,
        stable_document_id,
        reading_record_id,
        record_generation,
        stable_doc.title,
        document_version,
        jsonb_param(stable_doc.source_profile_json),
        content_sha256,
        frozen_at,
    )

    # ------------------------------------------------------------------
    # (4) Insert stable_document_blocks rows.
    #
    # interpretation_policy_json is ALWAYS materialized from the
    # Python model — the DB DEFAULT '{}'::jsonb is never relied on.
    # parent_block_id is the document-local block_id string, NOT
    # the row UUID (the FK is composite on (stable_document_id,
    # parent_block_id)).
    # ------------------------------------------------------------------
    for block in plan.blocks:
        await _insert_stable_document_block(
            conn,
            stable_document_id=stable_document_id,
            block=block,
        )

    # ------------------------------------------------------------------
    # (5) Supersede prior active reading_bases for this record.
    #
    # This MUST happen before the INSERT below to avoid violating the
    # ``uq_reading_bases_active_record`` unique partial index:
    #
    #     CREATE UNIQUE INDEX uq_reading_bases_active_record
    #       ON reading_bases(reading_record_id)
    #       WHERE status = 'active';
    #
    # Without this UPDATE, the INSERT would collide with the prior
    # active base and the transaction would abort.
    # ------------------------------------------------------------------
    await conn.execute(
        """
        UPDATE reading_bases
        SET status = 'superseded'
        WHERE reading_record_id = $1
          AND status = 'active'
        """,
        reading_record_id,
    )

    # ------------------------------------------------------------------
    # (6) Insert reading_bases row as the V1 Canonical Text Layer
    # carrier.
    #
    # reading_bases.content_sha256 hashes the canonical TEXT (not the
    # block-level hash), so existing snapshot validation that compares
    # sha256(base.text) continues to work.
    # base_version aligns with stable_document.document_version.
    # navigation_json is empty — Reading Units / Anchor Segments are
    # D6-I2C/I2D follow-up.
    # ------------------------------------------------------------------
    base_id = uuid4()
    await conn.execute(
        """
        INSERT INTO reading_bases (
            id,
            reading_record_id,
            base_version,
            record_generation,
            text,
            content_sha256,
            content_utf16_length,
            canonicalizer_version,
            builder_version,
            segmenter_version,
            language,
            title_snapshot,
            navigation_json,
            status,
            frozen_at,
            created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, 'active', $14, $14)
        """,
        base_id,
        reading_record_id,
        document_version,
        record_generation,
        canonical_text,
        canonical_text_sha256,
        canonical_text_utf16_length,
        canonicalizer_version,
        builder_version,
        segmenter_version,
        language,
        stable_doc.title,
        jsonb_param({"units": []}),
        frozen_at,
    )

    # ------------------------------------------------------------------
    # (7) Set reading_records.active_base_id with generation fence.
    # ------------------------------------------------------------------
    fence_result = await conn.execute(
        """
        UPDATE reading_records
        SET active_base_id = $2,
            updated_at = $3
        WHERE id = $1
          AND generation = $4
        """,
        reading_record_id,
        base_id,
        frozen_at,
        record_generation,
    )
    if fence_result != "UPDATE 1":
        raise StableDocumentFreezePersistenceError(
            f"Generation fence violation: expected to update exactly one "
            f"reading_records row for id={reading_record_id} "
            f"generation={record_generation}, but got {fence_result!r}. "
            "The reading record may not exist or its generation may have "
            "changed."
        )

    # ------------------------------------------------------------------
    # (8) Optionally confirm candidate_reading_documents with
    # state-machine safety.
    # ------------------------------------------------------------------
    candidate_confirmed = False
    if candidate_document_id is not None:
        candidate_confirmed = await _confirm_candidate_document(
            conn,
            candidate_document_id=candidate_document_id,
            reading_record_id=reading_record_id,
            record_generation=record_generation,
            user_id=user_id,
            frozen_at=frozen_at,
        )

    return StableDocumentFreezePersistenceResult(
        stable_document_id=stable_document_id,
        base_id=base_id,
        reading_record_id=reading_record_id,
        record_generation=record_generation,
        document_version=document_version,
        content_sha256=content_sha256,
        canonical_text_sha256=canonical_text_sha256,
        block_count=len(plan.blocks),
        candidate_confirmed=candidate_confirmed,
        idempotent_noop=False,
    )


async def _confirm_candidate_document(
    conn: asyncpg.Connection,
    *,
    candidate_document_id: UUID,
    reading_record_id: UUID,
    record_generation: int,
    user_id: UUID | None,
    frozen_at: datetime,
) -> bool:
    """Confirm a candidate document with state-machine safety.

    The candidate can only transition from ``status='ready'`` to
    ``status='confirmed'``. If it is already ``confirmed``, the call
    is an idempotent success (no write). If it is ``rejected`` or
    ``superseded``, the call fails closed.

    The lookup is guarded by ``(reading_record_id, record_generation)``
    and ``user_id`` (when provided) so a candidate belonging to a
    different record / generation / user is treated as "not found"
    and fails closed.

    Returns:
        ``True`` if the candidate is confirmed (newly or already).

    Raises:
        StableDocumentFreezePersistenceError: If the candidate is not
            found, is in a non-confirmable state, or the UPDATE
            affects zero rows.
    """
    # Fetch current status with record / generation / user guards.
    if user_id is not None:
        row = await conn.fetchrow(
            """
            SELECT status
            FROM candidate_reading_documents
            WHERE id = $1
              AND reading_record_id = $2
              AND record_generation = $3
              AND user_id = $4
            """,
            candidate_document_id,
            reading_record_id,
            record_generation,
            user_id,
        )
    else:
        row = await conn.fetchrow(
            """
            SELECT status
            FROM candidate_reading_documents
            WHERE id = $1
              AND reading_record_id = $2
              AND record_generation = $3
            """,
            candidate_document_id,
            reading_record_id,
            record_generation,
        )

    if row is None:
        raise StableDocumentFreezePersistenceError(
            f"Candidate document not found for id={candidate_document_id} "
            f"reading_record_id={reading_record_id} "
            f"record_generation={record_generation}"
            + (f" user_id={user_id}" if user_id is not None else "")
            + ". The candidate may not exist or may belong to a different "
            "record/generation/user."
        )

    current_status = str(row["status"])

    if current_status == "confirmed":
        # Idempotent success: candidate was already confirmed in a
        # prior freeze attempt.
        return True

    if current_status in ("rejected", "superseded"):
        raise StableDocumentFreezePersistenceError(
            f"Candidate document {candidate_document_id} is in status="
            f"{current_status!r}, cannot confirm. Only candidates in "
            "status='ready' can transition to 'confirmed'."
        )

    if current_status != "ready":
        raise StableDocumentFreezePersistenceError(
            f"Candidate document {candidate_document_id} is in unexpected "
            f"status={current_status!r}. Expected 'ready'."
        )

    # UPDATE from ready -> confirmed with state-machine guard.
    if user_id is not None:
        result = await conn.execute(
            """
            UPDATE candidate_reading_documents
            SET status = 'confirmed',
                confirmed_at = $3,
                updated_at = $3
            WHERE id = $1
              AND reading_record_id = $2
              AND record_generation = $4
              AND user_id = $5
              AND status = 'ready'
            """,
            candidate_document_id,
            reading_record_id,
            frozen_at,
            record_generation,
            user_id,
        )
    else:
        result = await conn.execute(
            """
            UPDATE candidate_reading_documents
            SET status = 'confirmed',
                confirmed_at = $3,
                updated_at = $3
            WHERE id = $1
              AND reading_record_id = $2
              AND record_generation = $4
              AND status = 'ready'
            """,
            candidate_document_id,
            reading_record_id,
            frozen_at,
            record_generation,
        )

    if result != "UPDATE 1":
        raise StableDocumentFreezePersistenceError(
            f"Candidate document confirmation failed: expected to "
            f"update exactly one candidate_reading_documents row for "
            f"id={candidate_document_id} "
            f"reading_record_id={reading_record_id} "
            f"record_generation={record_generation}"
            + (f" user_id={user_id}" if user_id is not None else "")
            + f", but got {result!r}."
        )

    return True


async def _insert_stable_document_block(
    conn: asyncpg.Connection,
    *,
    stable_document_id: UUID,
    block: StableDocumentBlock,
) -> None:
    """Insert one stable_document_blocks row.

    ``interpretation_policy_json`` is ALWAYS materialized from the
    Python model so the DB DEFAULT ``'{}'::jsonb`` is never used.
    ``parent_block_id`` is the document-local block_id string.
    """
    # Materialize the interpretation policy. This is the critical
    # step: an empty '{}' would silently route the block as
    # main_reading / main_reading_text and contradict the D6
    # projection rules (tables / images / footnotes / code blocks
    # would leak into the main grammar pass).
    policy_json = block.interpretation_policy.model_dump(mode="json")

    await conn.execute(
        """
        INSERT INTO stable_document_blocks (
            id,
            stable_document_id,
            block_id,
            parent_block_id,
            order_index,
            block_type,
            text_content,
            payload_json,
            source_refs_json,
            canonical_text_start_utf16,
            canonical_text_end_utf16,
            interpretation_policy_json,
            quality_json
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10, $11, $12::jsonb, $13::jsonb)
        """,
        uuid4(),
        stable_document_id,
        block.block_id,
        block.parent_block_id,
        block.order_index,
        block.block_type,
        block.text_content,
        jsonb_param(block.payload_json),
        jsonb_param(block.source_refs_json),
        block.canonical_text_start_utf16,
        block.canonical_text_end_utf16,
        jsonb_param(policy_json),
        jsonb_param(block.quality_json),
    )
