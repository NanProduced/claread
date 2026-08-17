"""Manual recovery route for failed reader enhancement work."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.schemas.reader_recovery import ReaderRecoveryResponse
from app.services.auth.dependencies import AuthUserDep
from app.services.reader_orchestration.job_bootstrap import (
    RECOVERY_TRIGGER_MANUAL,
    EnhancementJobBootstrapService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reader", tags=["reader"])


@router.post(
    "/records/{record_id}/recovery",
    response_model=ReaderRecoveryResponse,
)
async def recover_reader_record_enhancements(
    record_id: UUID,
    current_user: AuthUserDep,
) -> ReaderRecoveryResponse:
    """Trigger same-generation recovery for a failed reading record.

    Identity comes only from the authenticated session; the trigger is
    fixed to manual and all eligibility/idempotency handling stays in
    the recovery core's transaction.
    """
    try:
        service = EnhancementJobBootstrapService()
        summary = await service.recover_failed_enhancement_jobs(
            record_id=record_id,
            user_id=UUID(current_user.user_id),
            trigger=RECOVERY_TRIGGER_MANUAL,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404, detail="reader_record_not_found"
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail="reader_recovery_not_available"
        ) from exc
    except Exception:
        # Stable event only: raw exception content must not reach logs.
        logger.error(
            "reader_manual_recovery_unexpected_failure record_id=%s",
            record_id,
        )
        raise HTTPException(
            status_code=503,
            detail="reader_recovery_temporarily_unavailable",
        ) from None
    return ReaderRecoveryResponse(
        record_id=str(summary.record_id),
        outcome=(
            "recovery_started" if summary.recovered else "nothing_to_recover"
        ),
        previous_product_state=summary.previous_product_state,
        next_product_state=summary.next_product_state,
        record_generation=summary.expected_generation,
        successor_job_count=len(summary.successor_job_ids),
    )
