"""Candidate Document Confirm Transaction Service.

Reads a ``candidate_reading_documents`` row, rebuilds the
``StableDocumentFreezePlan`` from its ``blocks_json``, and delegates to
:func:`persist_stable_document_freeze_plan` to commit the stable
document + canonical text layer + candidate confirmation in a single
caller-managed transaction.

Transaction model:
    The caller opens the transaction (``async with conn.transaction():``)
    and passes the connection. This service validates
    ``conn.is_in_transaction()`` at entry and fails closed if the caller
    forgot to open a transaction.

    The candidate row is locked with ``SELECT ... FOR UPDATE`` to
    prevent concurrent confirmation attempts.

In-transaction steps (in order):
    1. Validate ``conn.is_in_transaction()``.
    2. SELECT the candidate row with FOR UPDATE, guarded by
       ``(id, reading_record_id, user_id)``.
    3. Validate candidate ``status='ready'``. Any other status
       (confirmed / rejected / superseded) fails closed.
    4. Parse ``blocks_json`` into a list of ``StableDocumentBlock``
       via ``model_validate``. Non-list / empty / invalid blocks fail
       closed.
    5. Construct ``source_profile_json`` from the candidate's
       ``source_refs_json`` and ``quality_json`` so source refs and
       quality metadata are preserved in the stable document. Both
       fields fail closed on non-object values (``None``, ``list``,
       numbers, invalid JSON, JSON arrays) — they are NEVER silently
       downgraded to ``{}``. JSON object strings are accepted for
       driver compatibility.
    6. Build the ``StableDocumentFreezePlan`` via
       :func:`build_stable_document_freeze_plan`.
       ``document_version = record_generation`` to satisfy
       ``uq_stable_reading_documents_record_version``.
    7. Persist the plan via :func:`persist_stable_document_freeze_plan`,
       passing ``candidate_document_id`` and ``user_id`` so the
       persistence layer can confirm the candidate with state-machine
       safety.
    8. Return a :class:`CandidateDocumentConfirmResult` mirroring the
       persistence result.

Out of scope:
    * API route / BFF / Web integration.
    * Reader event publication.
    * Snapshot reload.
    * Modifying ``reading_records.readiness_state`` / ``product_state``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import ValidationError

from app.schemas.reader_documents import (
    CandidateReadingDocument,
    StableDocumentBlock,
)
from app.services.reader_orchestration.confirmed_source_repository import (
    ConfirmedSourceError,
    freeze_confirmed_source,
    lock_confirmed_source_for_update,
)
from app.services.reader_orchestration.document_freeze_plan import (
    StableDocumentFreezePlanError,
    build_stable_document_freeze_plan,
)
from app.services.reader_orchestration.document_freeze_persistence import (
    StableDocumentFreezePersistenceError,
    StableDocumentFreezePersistenceResult,
    persist_stable_document_freeze_plan,
)


class CandidateDocumentConfirmError(ValueError):
    """Raised when candidate document confirmation cannot proceed.

    Concrete reasons: connection not in a transaction, candidate not
    found, candidate not in 'ready' status, blocks_json invalid/empty,
    plan build failure, or persistence failure.
    """


class StaleCandidateRevisionError(CandidateDocumentConfirmError):
    """L2 插入点 A — candidate 引用的 source revision/hash ≠ 当前
    confirmed_source_documents 行（fail closed，可恢复）。

    客户端重取 ``GET /records/{id}/confirmed-source`` 获得基于最新
    source revision 的 candidate 后重试。映射为
    ``409 {code:"stale_candidate_revision", resolution:"reload"}``。
    """

    def __init__(
        self,
        message: str,
        *,
        candidate_document_id: UUID,
        current_revision: int,
        current_content_sha256: str,
    ) -> None:
        super().__init__(message)
        self.candidate_document_id = candidate_document_id
        self.current_revision = current_revision
        self.current_content_sha256 = current_content_sha256


class CandidateDocumentStatusError(CandidateDocumentConfirmError):
    """Raised when the candidate row exists but ``status != 'ready'``.

    Carries structured fields so callers (e.g. the application
    service) can inspect the status and branch — for example, recovering
    a 'confirmed' candidate instead of re-freezing it.

    Attributes:
        candidate_document_id: The candidate document id.
        reading_record_id: The reading record id.
        user_id: The user id.
        record_generation: The candidate row's ``record_generation``.
        status: The actual candidate status (e.g. 'confirmed',
            'rejected', 'superseded').
    """

    def __init__(
        self,
        message: str,
        *,
        candidate_document_id: UUID,
        reading_record_id: UUID,
        user_id: UUID,
        record_generation: int,
        status: str,
    ) -> None:
        super().__init__(message)
        self.candidate_document_id = candidate_document_id
        self.reading_record_id = reading_record_id
        self.user_id = user_id
        self.record_generation = record_generation
        self.status = status


@dataclass(frozen=True, slots=True)
class CandidateDocumentConfirmResult:
    """Summary of the candidate document confirmation transaction.

    Mirrors :class:`StableDocumentFreezePersistenceResult` since this
    service is a thin wrapper around
    :func:`persist_stable_document_freeze_plan`.
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


def _coerce_json_object_field(
    value: Any,
    *,
    field_name: str,
    candidate_document_id: UUID,
) -> dict[str, Any]:
    """Coerce a JSON-like candidate row field into a plain ``dict``.

    Accepts:
        * ``dict`` / ``Mapping`` -> plain ``dict`` (a genuinely empty
          ``{}`` is preserved, NOT treated as missing).
        * ``str`` -> ``json.loads``; the parsed value must be a JSON
          object (``dict``).

    Rejects (fail-closed with :class:`CandidateDocumentConfirmError`):
        * ``None``
        * ``list`` (and JSON array strings)
        * ``int`` / ``float`` / ``bool``
        * invalid JSON string
        * JSON string that parses to a non-object (array, string,
          number, bool, null)

    This function NEVER silently downgrades missing/invalid data to
    ``{}`` — doing so would lose candidate source refs / quality
    metadata. The error message includes ``field_name`` and
    ``candidate_document_id`` for diagnosis.
    """
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError) as exc:
            raise CandidateDocumentConfirmError(
                f"Candidate document {candidate_document_id} has "
                f"{field_name} that is not valid JSON: {value!r}."
            ) from exc
        if not isinstance(parsed, Mapping):
            raise CandidateDocumentConfirmError(
                f"Candidate document {candidate_document_id} has "
                f"{field_name} that parses to a non-object JSON value "
                f"(got {type(parsed).__name__}). Expected a JSON "
                "object."
            )
        return dict(parsed)
    if isinstance(value, Mapping):
        return dict(value)
    # None, list, int, float, bool, or any other type fails closed.
    raise CandidateDocumentConfirmError(
        f"Candidate document {candidate_document_id} has "
        f"{field_name} with invalid type "
        f"{type(value).__name__}. Expected a JSON object (dict) or a "
        "JSON object string."
    )


async def confirm_candidate_document(
    conn: asyncpg.Connection,
    *,
    candidate_document_id: UUID,
    reading_record_id: UUID,
    user_id: UUID,
    canonicalizer_version: str,
    builder_version: str,
    segmenter_version: str,
    language: str | None = None,
    now: datetime | None = None,
) -> CandidateDocumentConfirmResult:
    """Confirm a candidate reading document by freezing it into a stable
    document within a caller-managed transaction.

    The caller is responsible for ``async with conn.transaction():``.
    This function does NOT open its own transaction; it only executes
    SQL on the supplied connection. Any exception propagates and the
    caller's transaction context manager rolls back.

    Args:
        conn: An asyncpg connection that is already inside a
            transaction.
        candidate_document_id: The candidate document id to confirm.
        reading_record_id: The reading record id (must match the
            candidate row).
        user_id: The user id (must match the candidate row).
        canonicalizer_version: Version label for the canonicalizer.
        builder_version: Version label for the reading base builder.
        segmenter_version: Version label for the segmenter.
        language: Optional language code for the reading base.
        now: Optional timestamp; defaults to ``datetime.now(UTC)``.

    Returns:
        A :class:`CandidateDocumentConfirmResult` summarizing the
        persisted rows.

    Raises:
        CandidateDocumentConfirmError: On any input/state/plan/
            persistence error. The original exception is preserved as
            ``__cause__``.
    """
    # (0) Fail closed if the caller forgot to open a transaction.
    if not conn.is_in_transaction():
        raise CandidateDocumentConfirmError(
            "confirm_candidate_document must be called within an active "
            "transaction. Refusing to execute outside a transaction to "
            "prevent half-confirmed candidate documents."
        )

    frozen_at = now or datetime.now(UTC)

    # (1) Read and lock the candidate row with FOR UPDATE.
    row = await conn.fetchrow(
        """
        SELECT id, reading_record_id, user_id, record_generation, title,
               blocks_json, source_refs_json, quality_json, status
        FROM candidate_reading_documents
        WHERE id = $1
          AND reading_record_id = $2
          AND user_id = $3
        FOR UPDATE
        """,
        candidate_document_id,
        reading_record_id,
        user_id,
    )

    if row is None:
        raise CandidateDocumentConfirmError(
            f"Candidate document not found: id={candidate_document_id} "
            f"reading_record_id={reading_record_id} user_id={user_id}. "
            "The candidate may have been deleted, or the "
            "(id, reading_record_id, user_id) tuple does not match."
        )

    # (2) Validate candidate status — only 'ready' can be confirmed.
    # A typed CandidateDocumentStatusError is raised (subclass of
    # CandidateDocumentConfirmError) so callers can branch on the
    # status — e.g. the application service recovers a
    # 'confirmed' candidate instead of re-freezing.
    candidate_status = str(row["status"])
    if candidate_status != "ready":
        raise CandidateDocumentStatusError(
            f"Candidate document {candidate_document_id} has status="
            f"{candidate_status!r} (expected 'ready'). Only candidates "
            "in 'ready' status can be confirmed. If the candidate is "
            "already 'confirmed', the application service should recover "
            "the committed state instead of re-confirming.",
            candidate_document_id=candidate_document_id,
            reading_record_id=reading_record_id,
            user_id=user_id,
            record_generation=int(row["record_generation"]),
            status=candidate_status,
        )

    # (3) Parse blocks_json into StableDocumentBlock list.
    raw_blocks = row["blocks_json"]
    if isinstance(raw_blocks, str):
        try:
            raw_blocks = json.loads(raw_blocks)
        except (json.JSONDecodeError, TypeError) as exc:
            raise CandidateDocumentConfirmError(
                f"Candidate document {candidate_document_id} has "
                f"blocks_json that is not valid JSON: {raw_blocks!r}."
            ) from exc

    if not isinstance(raw_blocks, list):
        raise CandidateDocumentConfirmError(
            f"Candidate document {candidate_document_id} has "
            f"blocks_json that is not a list (got "
            f"{type(raw_blocks).__name__}). Cannot build stable "
            "document from non-array blocks."
        )
    if len(raw_blocks) == 0:
        raise CandidateDocumentConfirmError(
            f"Candidate document {candidate_document_id} has empty "
            "blocks_json. Cannot build stable document from zero "
            "blocks."
        )

    try:
        blocks = [
            StableDocumentBlock.model_validate(block)
            for block in raw_blocks
        ]
    except ValidationError as exc:
        raise CandidateDocumentConfirmError(
            f"Candidate document {candidate_document_id} has "
            f"blocks_json with invalid block(s): {exc}"
        ) from exc

    # (4) Construct CandidateReadingDocument to validate the row's
    # structure and preserve source_refs_json / quality_json.
    # Fail closed on non-dict / non-JSON-object values — silently
    # downgrading to {} would lose candidate source refs / quality
    # metadata. JSON object strings are accepted for compatibility
    # with drivers that return JSONB columns as strings.
    record_generation = int(row["record_generation"])
    source_refs_json = _coerce_json_object_field(
        row["source_refs_json"],
        field_name="source_refs_json",
        candidate_document_id=candidate_document_id,
    )
    quality_json = _coerce_json_object_field(
        row["quality_json"],
        field_name="quality_json",
        candidate_document_id=candidate_document_id,
    )

    try:
        candidate = CandidateReadingDocument(
            reading_record_id=str(row["reading_record_id"]),
            user_id=str(row["user_id"]),
            record_generation=record_generation,
            title=row["title"],
            blocks_json=raw_blocks,
            source_refs_json=source_refs_json,
            quality_json=quality_json,
            status="ready",
        )
    except ValidationError as exc:
        raise CandidateDocumentConfirmError(
            f"Candidate document {candidate_document_id} failed "
            f"CandidateReadingDocument validation: {exc}"
        ) from exc

    # (5) Build source_profile_json preserving source_refs and quality.
    source_profile_json: dict[str, Any] = {
        "source_refs": candidate.source_refs_json,
        "quality": candidate.quality_json,
    }

    # ------------------------------------------------------------------
    # L2 插入点 A（校验）：confirmed_source_documents 行
    # SELECT ... FOR UPDATE（(reading_record_id, record_generation)）。
    # - 行不存在 → legacy candidate（无 source 引用），走旧逻辑；
    # - 行存在但 candidate 缺少 source 引用三 key，或引用的
    #   revision/hash ≠ 当前 source 行 → fail closed
    #   stale_candidate_revision（可恢复：重取 confirmed-source）；
    # - source 已 frozen 且引用一致 → 幂等分支（下方 B 跳过冻结）。
    # ------------------------------------------------------------------
    confirmed_source = await lock_confirmed_source_for_update(
        conn,
        record_id=reading_record_id,
        user_id=user_id,
        generation=record_generation,
    )
    if confirmed_source is not None:
        referenced_revision = candidate.source_refs_json.get("source_revision")
        referenced_hash = candidate.source_refs_json.get(
            "source_content_sha256"
        )
        if (
            referenced_revision != confirmed_source.revision
            or referenced_hash != confirmed_source.content_sha256
        ):
            raise StaleCandidateRevisionError(
                f"Candidate document {candidate_document_id} references "
                f"confirmed source revision={referenced_revision!r} "
                f"hash={referenced_hash!r}, but the current confirmed "
                f"source row is revision={confirmed_source.revision} "
                f"hash={confirmed_source.content_sha256}. The candidate "
                "is stale; reload the confirmed source to obtain a "
                "candidate built from the latest revision.",
                candidate_document_id=candidate_document_id,
                current_revision=confirmed_source.revision,
                current_content_sha256=confirmed_source.content_sha256,
            )

    # document_version = record_generation to satisfy
    # uq_stable_reading_documents_record_version
    # (UNIQUE (reading_record_id, document_version)) across multiple
    # generations. Each generation gets its own document_version, so
    # superseded prior-generation docs don't cause unique violations.
    document_version = record_generation

    # (6) Build the stable document freeze plan.
    try:
        plan = build_stable_document_freeze_plan(
            reading_record_id=str(reading_record_id),
            record_generation=record_generation,
            document_version=document_version,
            title=candidate.title,
            blocks=blocks,
            source_profile_json=source_profile_json,
        )
    except StableDocumentFreezePlanError as exc:
        raise CandidateDocumentConfirmError(
            f"Failed to build stable document freeze plan for "
            f"candidate {candidate_document_id}: {exc}"
        ) from exc

    # (7) Persist the plan + confirm the candidate.
    try:
        persist_result: StableDocumentFreezePersistenceResult = (
            await persist_stable_document_freeze_plan(
                conn,
                plan=plan,
                canonicalizer_version=canonicalizer_version,
                builder_version=builder_version,
                segmenter_version=segmenter_version,
                language=language,
                candidate_document_id=candidate_document_id,
                user_id=user_id,
                now=frozen_at,
            )
        )
    except StableDocumentFreezePersistenceError as exc:
        raise CandidateDocumentConfirmError(
            f"Failed to persist stable document freeze for candidate "
            f"{candidate_document_id}: {exc}"
        ) from exc

    # ------------------------------------------------------------------
    # L2 插入点 B（冻结）：persist 成功（含 candidate ready→confirmed
    # UPDATE）之后、返回前，同事务冻结 source（期望 UPDATE 1）——
    # source 冻结与 Stable Document 原子提交。source 已 frozen 时
    # （插入点 A 的幂等分支）跳过，不重走 freeze。
    # ------------------------------------------------------------------
    if confirmed_source is not None and confirmed_source.status == "draft":
        try:
            await freeze_confirmed_source(
                conn,
                source_document_id=UUID(confirmed_source.id),
                now=frozen_at,
            )
        except ConfirmedSourceError as exc:
            raise CandidateDocumentConfirmError(
                f"Failed to freeze confirmed source "
                f"{confirmed_source.id} for candidate "
                f"{candidate_document_id}: {exc}"
            ) from exc

    # (8) Return the result.
    return CandidateDocumentConfirmResult(
        stable_document_id=persist_result.stable_document_id,
        base_id=persist_result.base_id,
        reading_record_id=persist_result.reading_record_id,
        record_generation=persist_result.record_generation,
        document_version=persist_result.document_version,
        content_sha256=persist_result.content_sha256,
        canonical_text_sha256=persist_result.canonical_text_sha256,
        block_count=persist_result.block_count,
        candidate_confirmed=persist_result.candidate_confirmed,
        idempotent_noop=persist_result.idempotent_noop,
    )
