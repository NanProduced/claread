"""ASK-RETRY-CONTRACT-R5 — durable submission claim/reconcile gateway.

Lifecycle (DB-authoritative):

  claimed → streaming → completed | failed | cancelled

Reading Record Ask **agentic and legacy** new sends MUST enter through
``ensure_submission_for_send`` before any model call. Duplicate
``client_submission_id`` values never create a second user/assistant
pair and never re-invoke the model.

Atomicity: claim + user/assistant pair + bind happen in **one** DB
transaction (see ``ReaderRecordAskRepository.ensure_submission_message_pair``).
Model invocation runs only after that transaction commits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException

from app.services.reader_record_ask.repository import (
    ReaderRecordAskRepository,
    SubmissionIdempotencyUnavailable,
)

RETRY_CONTRACT_VERSION = "ask_retry_contract_r5"
ORPHAN_CLAIM_LEASE_SECONDS = 60

SubmissionStatus = Literal[
    "claimed",
    "streaming",
    "completed",
    "failed",
    "cancelled",
    "not_found",
]


@dataclass(slots=True, frozen=True)
class SubmissionEnsureResult:
    """Result of durable ensure before model call."""

    client_submission_id: str
    thread_id: str
    status: SubmissionStatus
    claim_generation: int | None = None
    user_message_id: str | None = None
    assistant_message_id: str | None = None
    user_message: dict[str, Any] | None = None
    assistant_message: dict[str, Any] | None = None
    terminal_code: str | None = None
    # Fresh pair owned by this request — may invoke the model.
    may_create_model: bool = False
    # Existing submission — do not model; reconcile/hydrate instead.
    stop_model: bool = False


@dataclass(slots=True, frozen=True)
class SubmissionReconcileView:
    """Typed reconcile with optional public message projections."""

    client_submission_id: str
    thread_id: str
    status: SubmissionStatus
    user_message_id: str | None = None
    assistant_message_id: str | None = None
    terminal_code: str | None = None
    claim_generation: int | None = None
    user_message: dict[str, Any] | None = None
    assistant_message: dict[str, Any] | None = None
    # UI hint when lane cannot be retried safely.
    action_hint: Literal["resend", "retry", "reask", "wait", "none"] | None = None


def build_retry_snapshot(
    *,
    lane: Literal["agentic", "legacy"],
    model_option_key: str | None,
    web_search_mode: str,
    route_identity: str | None = None,
) -> dict[str, Any]:
    """Immutable retry snapshot persisted on user+assistant messages."""
    return {
        "retry_contract_version": RETRY_CONTRACT_VERSION,
        "retry_lane": lane,
        "execution_version": (
            "reader_record_ask_agentic_v2"
            if lane == "agentic"
            else "reader_record_ask_legacy"
        ),
        "model_option_key": model_option_key,
        "route_identity": route_identity,
        "web_search_mode": web_search_mode,
        "snapshotted_at": datetime.now(UTC).isoformat(),
    }


async def ensure_submission_for_send(
    *,
    repo: ReaderRecordAskRepository,
    thread_id: UUID,
    user_id: UUID,
    client_submission_id: UUID | None,
    content_md: str,
    retry_snapshot: dict[str, Any],
    user_extra_metadata: dict[str, Any] | None = None,
    assistant_extra_metadata: dict[str, Any] | None = None,
) -> SubmissionEnsureResult | None:
    """Ensure durable claim+pair for both agentic and legacy lanes.

    Returns ``None`` only when ``client_submission_id`` is absent (pre-R2
    clients). When present, fails closed if the submissions table is
    missing (typed 503).
    """
    if client_submission_id is None:
        return None

    user_meta = {
        **(user_extra_metadata or {}),
        "retry_snapshot": retry_snapshot,
        "retry_lane": retry_snapshot.get("retry_lane"),
        "retry_contract_version": retry_snapshot.get("retry_contract_version"),
        "execution_version": retry_snapshot.get("execution_version"),
        "web_search_mode": retry_snapshot.get("web_search_mode"),
        "model_option_key": retry_snapshot.get("model_option_key"),
    }
    assistant_meta = {
        **(assistant_extra_metadata or {}),
        "retry_snapshot": retry_snapshot,
        "retry_lane": retry_snapshot.get("retry_lane"),
        "retry_contract_version": retry_snapshot.get("retry_contract_version"),
        "execution_version": retry_snapshot.get("execution_version"),
        "model_option_key": retry_snapshot.get("model_option_key"),
    }

    try:
        raw = await repo.ensure_submission_message_pair(
            thread_id=thread_id,
            user_id=user_id,
            client_submission_id=client_submission_id,
            content_md=content_md,
            user_metadata=user_meta,
            assistant_metadata=assistant_meta,
            orphan_lease_seconds=ORPHAN_CLAIM_LEASE_SECONDS,
        )
    except SubmissionIdempotencyUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "submission_idempotency_unavailable",
                "message": (
                    "消息幂等保护暂不可用，请稍后重试。"
                    "（需要 Owner 单独 apply migration 0026）"
                ),
            },
        ) from exc

    status = str(raw.get("status") or "claimed")
    may = bool(raw.get("may_create_model"))
    return SubmissionEnsureResult(
        client_submission_id=str(client_submission_id),
        thread_id=str(thread_id),
        status=status,  # type: ignore[arg-type]
        claim_generation=raw.get("claim_generation"),
        user_message_id=raw.get("user_message_id"),
        assistant_message_id=raw.get("assistant_message_id"),
        user_message=raw.get("user_message"),
        assistant_message=raw.get("assistant_message"),
        terminal_code=raw.get("terminal_code"),
        may_create_model=may,
        stop_model=not may,
    )


async def mark_submission_terminal(
    *,
    repo: ReaderRecordAskRepository,
    status: Literal["completed", "failed", "cancelled"],
    thread_id: UUID | None = None,
    client_submission_id: UUID | None = None,
    assistant_message_id: UUID | None = None,
    claim_generation: int | None = None,
) -> None:
    await repo.mark_client_submission_terminal(
        status=status,
        thread_id=thread_id,
        client_submission_id=client_submission_id,
        assistant_message_id=assistant_message_id,
        claim_generation=claim_generation,
    )


@dataclass(slots=True)
class SubmissionTerminalHook:
    """R6/R7: internal typed hook so lanes sync submission status without
    parsing public SSE text. Exactly-once CAS via claim_generation /
    assistant_message_id; stale owners never overwrite a new generation.

    R7: separates **intended** model terminal from **synced** DB write.
    ``mark()`` returns success only when the DB write completes; failures
    leave ``_synced=False`` so finally can retry with the same intended
    status (never rewrite completed → cancelled on sync failure).
    """

    thread_id: UUID
    client_submission_id: UUID | None
    claim_generation: int | None = None
    assistant_message_id: UUID | None = None
    # Real model terminal — set as soon as the lane knows the outcome.
    intended_status: Literal["completed", "failed", "cancelled"] | None = None
    # True only after a successful terminal DB write (or no-op inactive).
    _synced: bool = False

    @property
    def active(self) -> bool:
        return self.client_submission_id is not None

    @property
    def synced(self) -> bool:
        return self._synced

    @property
    def fired(self) -> bool:
        """Backward-compatible alias for ``synced``."""
        return self._synced

    def remember(
        self,
        status: Literal["completed", "failed", "cancelled"],
    ) -> None:
        """Record real model terminal without writing (or overwriting)."""
        if self.intended_status is None:
            self.intended_status = status
        # Once a terminal is known, never demote completed → cancelled
        # on a later compensate path. Prefer stronger outcomes:
        # completed wins over failed/cancelled; failed wins over cancelled.
        elif self.intended_status == "completed":
            return
        elif self.intended_status == "failed" and status == "cancelled":
            return
        else:
            self.intended_status = status

    async def mark(
        self,
        status: Literal["completed", "failed", "cancelled"],
        *,
        repo: ReaderRecordAskRepository | None = None,
    ) -> bool:
        """Attempt terminal DB sync. Returns True only on successful write.

        Always records ``intended_status``. Never sets ``_synced`` on
        exception — callers may retry via ``ensure_synced``.
        """
        self.remember(status)
        if self.client_submission_id is None:
            self._synced = True
            return True
        if self._synced:
            return True
        active_repo = repo or ReaderRecordAskRepository()
        try:
            await mark_submission_terminal(
                repo=active_repo,
                status=status,
                thread_id=self.thread_id,
                client_submission_id=self.client_submission_id,
                assistant_message_id=self.assistant_message_id,
                claim_generation=self.claim_generation,
            )
        except SubmissionIdempotencyUnavailable:
            # Table missing mid-stream — send already passed preflight;
            # best-effort only. Leave unsynced so compensate can retry.
            return False
        except Exception:
            # Terminal sync must never tear down the SSE stream.
            # Do NOT mark synced — finally will retry intended status.
            return False
        self._synced = True
        return True

    async def ensure_synced(
        self,
        *,
        repo: ReaderRecordAskRepository | None = None,
        fallback: Literal["completed", "failed", "cancelled"] | None = None,
    ) -> bool:
        """Retry terminal write using intended (or fallback) status once.

        Preserves real model terminal: if intended is completed, never
        writes cancelled because a prior sync attempt failed.
        """
        if self._synced:
            return True
        if self.client_submission_id is None:
            self._synced = True
            return True
        status = self.intended_status or fallback
        if status is None:
            return False
        return await self.mark(status, repo=repo)

    async def mark_from_message_status(
        self,
        message_status: str | None,
        *,
        repo: ReaderRecordAskRepository | None = None,
        unknown_as_cancelled: bool = False,
    ) -> bool:
        """Map assistant message row status → submission terminal.

        R8: unknown / streaming / None does **not** default to cancelled
        unless ``unknown_as_cancelled=True`` (explicit disconnect paths).
        Read failures must leave the submission unsynced for safe recovery.
        """
        if message_status == "completed":
            return await self.mark("completed", repo=repo)
        if message_status in {"failed"}:
            return await self.mark("failed", repo=repo)
        if message_status in {"interrupted", "cancelled"}:
            return await self.mark("cancelled", repo=repo)
        if message_status in {"streaming", "pending", None}:
            if unknown_as_cancelled:
                return await self.mark("cancelled", repo=repo)
            return False
        if unknown_as_cancelled:
            return await self.mark("failed", repo=repo)
        return False


async def build_reconcile_view(
    *,
    repo: ReaderRecordAskRepository,
    thread_id: UUID,
    client_submission_id: UUID,
    project_public_message,
) -> SubmissionReconcileView:
    """Build reconcile view with safe public message projections."""
    row = await repo.get_client_submission(
        thread_id=thread_id,
        client_submission_id=client_submission_id,
    )
    if row is None:
        return SubmissionReconcileView(
            client_submission_id=str(client_submission_id),
            thread_id=str(thread_id),
            status="not_found",
            action_hint="resend",
            terminal_code="submission_not_found",
        )

    status = str(row["status"])
    user_mid = row.get("user_message_id")
    asst_mid = row.get("assistant_message_id")
    user_pub = None
    asst_pub = None
    if user_mid:
        user_pub = await project_public_message(UUID(str(user_mid)))
    if asst_mid:
        asst_pub = await project_public_message(UUID(str(asst_mid)))

    action: Literal["resend", "retry", "reask", "wait", "none"] = "none"
    if status == "streaming":
        action = "wait"
    elif status in {"failed", "cancelled"} and asst_mid:
        action = "retry"
    elif status in {"failed", "cancelled", "claimed"} and not asst_mid:
        action = "resend"
    elif status == "completed":
        action = "none"

    return SubmissionReconcileView(
        client_submission_id=row["client_submission_id"],
        thread_id=row["thread_id"],
        status=status,  # type: ignore[arg-type]
        user_message_id=user_mid,
        assistant_message_id=asst_mid,
        terminal_code=f"submission_{status}",
        claim_generation=row.get("claim_generation"),
        user_message=user_pub,
        assistant_message=asst_pub,
        action_hint=action,
    )


def lease_expires_at(
    *,
    now: datetime | None = None,
    seconds: int = ORPHAN_CLAIM_LEASE_SECONDS,
) -> datetime:
    base = now or datetime.now(UTC)
    return base + timedelta(seconds=seconds)


# --- backward-compatible aliases used by earlier call sites ---


async def begin_or_reconcile_submission(
    *,
    repo: ReaderRecordAskRepository,
    thread_id: UUID,
    user_id: UUID,
    client_submission_id: UUID | None,
) -> SubmissionEnsureResult | None:
    """Deprecated thin wrapper — prefer ensure_submission_for_send."""
    if client_submission_id is None:
        return None
    # Without content we can only look up.
    row = await repo.get_client_submission(
        thread_id=thread_id,
        client_submission_id=client_submission_id,
    )
    if row is None:
        return SubmissionEnsureResult(
            client_submission_id=str(client_submission_id),
            thread_id=str(thread_id),
            status="not_found",
            stop_model=True,
            may_create_model=False,
            terminal_code="submission_not_found",
        )
    status = str(row["status"])
    has_pair = bool(row.get("assistant_message_id"))
    return SubmissionEnsureResult(
        client_submission_id=str(client_submission_id),
        thread_id=str(thread_id),
        status=status,  # type: ignore[arg-type]
        claim_generation=row.get("claim_generation"),
        user_message_id=row.get("user_message_id"),
        assistant_message_id=row.get("assistant_message_id"),
        may_create_model=False,
        stop_model=True,
        terminal_code=f"submission_{status}" if has_pair else "submission_in_progress",
    )
