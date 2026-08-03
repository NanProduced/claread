"""
Credit Service.

Handles quota checking, credit deduction, fixed-cost reservation/refund,
daily reset, and ledger entries.
Integrated with task submission (pre-check), task execution (post-deduct),
and synchronous AI capabilities that need upfront reservation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from app.database import connection as db_connection

logger = logging.getLogger(__name__)

# Default daily free points for new accounts
DEFAULT_DAILY_FREE_POINTS = 1000
LEDGER_ENTRY_TYPE_AI_CAPABILITY_DEDUCT = "ai_capability_deduct"
LEDGER_ENTRY_TYPE_REFUND = "refund"


class InsufficientCredits(Exception):
    """Raised when user has insufficient credits to submit a task."""

    def __init__(self, remaining: int, required: int = 1) -> None:
        self.remaining = remaining
        self.required = required
        super().__init__(
            f"Insufficient credits: remaining={remaining}, required>={required}"
        )


@dataclass(slots=True)
class CreditReservation:
    total_points: int
    deducted_from_daily: int
    deducted_from_bonus: int


@dataclass(slots=True)
class LedgerAttribution:
    subject_type: str | None = None
    subject_id: str | None = None
    reading_record_id: UUID | None = None
    reader_run_id: UUID | None = None
    reader_job_id: UUID | None = None
    title_snapshot: str | None = None


async def _insert_ledger_entry(
    conn,
    *,
    user_id: UUID,
    entry_type: str,
    points: int,
    bucket_type: str,
    balance_after: int,
    metadata: dict[str, Any] | None,
    created_at: datetime,
    attribution: LedgerAttribution | None,
) -> None:
    details = attribution or LedgerAttribution()
    await conn.execute(
        """
        INSERT INTO user_credit_ledger (
            user_id,
            subject_type,
            subject_id,
            reading_record_id,
            reader_run_id,
            reader_job_id,
            entry_type,
            points,
            bucket_type,
            balance_after,
            title_snapshot,
            metadata_json,
            created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
        """,
        user_id,
        details.subject_type,
        details.subject_id,
        details.reading_record_id,
        details.reader_run_id,
        details.reader_job_id,
        entry_type,
        points,
        bucket_type,
        balance_after,
        details.title_snapshot,
        metadata or {},
        created_at,
    )


async def ensure_credit_account(user_id: UUID) -> None:
    """
    Ensure the user has a credit account row.
    Creates one with defaults if missing (idempotent via ON CONFLICT).
    """
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_credit_accounts (user_id)
            VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id,
        )


async def check_quota(user_id: UUID) -> int:
    """
    Check if user has remaining quota. Handles daily reset transparently.

    Returns:
        Remaining points (daily_free - daily_used + bonus).
        If <= 0, means quota exhausted.

    Side effect:
        If last_reset_on is before today, resets daily_used_points to 0.
    """
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    today = date.today()

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT daily_free_points, daily_used_points, bonus_points, last_reset_on
                FROM user_credit_accounts
                WHERE user_id = $1
                FOR UPDATE
                """,
                user_id,
            )

            if row is None:
                # Account doesn't exist yet — create it
                await conn.execute(
                    """
                    INSERT INTO user_credit_accounts (user_id)
                    VALUES ($1)
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    user_id,
                )
                return DEFAULT_DAILY_FREE_POINTS

            daily_free = row["daily_free_points"]
            daily_used = row["daily_used_points"]
            bonus = row["bonus_points"]
            last_reset = row["last_reset_on"]

            # Daily reset if needed
            if last_reset < today:
                await conn.execute(
                    """
                    UPDATE user_credit_accounts
                    SET daily_used_points = 0, last_reset_on = $2, updated_at = $3
                    WHERE user_id = $1
                    """,
                    user_id,
                    today,
                    datetime.now(UTC),
                )
                daily_used = 0

            return (daily_free - daily_used) + bonus



async def reserve_points(
    user_id: UUID,
    cost_points: int,
    *,
    entry_type: str = LEDGER_ENTRY_TYPE_AI_CAPABILITY_DEDUCT,
    metadata: dict[str, Any] | None = None,
    attribution: LedgerAttribution | None = None,
) -> CreditReservation | None:
    """
    Reserve a fixed amount of credits upfront.

    Unlike a plain deduction, this only succeeds when the full amount is available.
    It is intended for short synchronous capabilities that should charge a fixed
    price or not run at all.
    """
    if cost_points <= 0:
        return CreditReservation(total_points=0, deducted_from_daily=0, deducted_from_bonus=0)

    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = datetime.now(UTC)
    today = date.today()

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT daily_free_points, daily_used_points, bonus_points, last_reset_on
                FROM user_credit_accounts
                WHERE user_id = $1
                FOR UPDATE
                """,
                user_id,
            )

            if row is None:
                logger.error("Cannot reserve credits: no account for user %s", user_id)
                return None

            daily_free = row["daily_free_points"]
            daily_used = row["daily_used_points"]
            bonus = row["bonus_points"]
            last_reset = row["last_reset_on"]

            if last_reset < today:
                daily_used = 0

            daily_remaining = max(daily_free - daily_used, 0)
            available_bonus = max(bonus, 0)
            available_total = daily_remaining + available_bonus
            if available_total < cost_points:
                return None

            deduct_from_daily = min(cost_points, daily_remaining)
            deduct_from_bonus = cost_points - deduct_from_daily

            new_daily_used = daily_used + deduct_from_daily
            new_bonus = bonus - deduct_from_bonus

            await conn.execute(
                """
                UPDATE user_credit_accounts
                SET daily_used_points = $2,
                    bonus_points = $3,
                    last_reset_on = $4,
                    updated_at = $5
                WHERE user_id = $1
                """,
                user_id,
                new_daily_used,
                new_bonus,
                today,
                now,
            )

            balance_after = (daily_free - new_daily_used) + new_bonus

            if deduct_from_daily > 0:
                await _insert_ledger_entry(
                    conn,
                    user_id=user_id,
                    entry_type=entry_type,
                    points=-deduct_from_daily,
                    bucket_type="daily_free",
                    balance_after=balance_after,
                    metadata=metadata,
                    created_at=now,
                    attribution=attribution,
                )

            if deduct_from_bonus > 0:
                await _insert_ledger_entry(
                    conn,
                    user_id=user_id,
                    entry_type=entry_type,
                    points=-deduct_from_bonus,
                    bucket_type="bonus",
                    balance_after=balance_after,
                    metadata=metadata,
                    created_at=now,
                    attribution=attribution,
                )

            logger.info(
                "Reserved %d points from user %s (daily=%d, bonus=%d, remaining=%d)",
                cost_points,
                user_id,
                deduct_from_daily,
                deduct_from_bonus,
                balance_after,
            )

            return CreditReservation(
                total_points=cost_points,
                deducted_from_daily=deduct_from_daily,
                deducted_from_bonus=deduct_from_bonus,
            )


async def refund_reserved_points(
    user_id: UUID,
    reservation: CreditReservation,
    *,
    metadata: dict[str, Any] | None = None,
    attribution: LedgerAttribution | None = None,
) -> int:
    """Refund a prior fixed reservation back to the appropriate buckets."""
    if reservation.total_points <= 0:
        return 0

    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = datetime.now(UTC)
    today = date.today()

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT daily_free_points, daily_used_points, bonus_points, last_reset_on
                FROM user_credit_accounts
                WHERE user_id = $1
                FOR UPDATE
                """,
                user_id,
            )

            if row is None:
                logger.error("Cannot refund credits: no account for user %s", user_id)
                return 0

            daily_free = row["daily_free_points"]
            daily_used = row["daily_used_points"]
            bonus = row["bonus_points"]
            last_reset = row["last_reset_on"]

            bonus_refund = reservation.deducted_from_bonus
            refund_to_daily = 0

            if last_reset >= today:
                refund_to_daily = min(reservation.deducted_from_daily, max(daily_used, 0))
                bonus_refund += reservation.deducted_from_daily - refund_to_daily
            else:
                bonus_refund += reservation.deducted_from_daily
                daily_used = 0

            new_daily_used = max(daily_used - refund_to_daily, 0)
            new_bonus = bonus + bonus_refund

            await conn.execute(
                """
                UPDATE user_credit_accounts
                SET daily_used_points = $2,
                    bonus_points = $3,
                    updated_at = $4
                WHERE user_id = $1
                """,
                user_id,
                new_daily_used,
                new_bonus,
                now,
            )

            balance_after = (daily_free - new_daily_used) + new_bonus

            if refund_to_daily > 0:
                await _insert_ledger_entry(
                    conn,
                    user_id=user_id,
                    task_id=task_id,
                    entry_type=LEDGER_ENTRY_TYPE_REFUND,
                    points=refund_to_daily,
                    bucket_type="daily_free",
                    balance_after=balance_after,
                    metadata=metadata,
                    created_at=now,
                    attribution=attribution,
                )

            if bonus_refund > 0:
                await _insert_ledger_entry(
                    conn,
                    user_id=user_id,
                    task_id=task_id,
                    entry_type=LEDGER_ENTRY_TYPE_REFUND,
                    points=bonus_refund,
                    bucket_type="bonus",
                    balance_after=balance_after,
                    metadata=metadata,
                    created_at=now,
                    attribution=attribution,
                )

            refunded_total = refund_to_daily + bonus_refund
            logger.info(
                "Refunded %d points to user %s (daily=%d, bonus=%d, remaining=%d)",
                refunded_total,
                user_id,
                refund_to_daily,
                bonus_refund,
                balance_after,
            )
            return refunded_total


async def grant_bonus_credits(
    user_id: UUID,
    points: int,
    entry_type: str = "feedback_reward",
    metadata: dict[str, Any] | None = None,
    attribution: LedgerAttribution | None = None,
) -> int:
    """
    Grant bonus points to a user account.

    Args:
        user_id: User to grant points to
        points: Points to grant (must be > 0)
        entry_type: Ledger entry type, default 'feedback_reward'
        metadata: Extra metadata

    Returns:
        Actual points granted (0 if account not found or points <= 0)
    """
    if points <= 0:
        return 0

    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = datetime.now(UTC)

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT daily_free_points, daily_used_points, bonus_points
                FROM user_credit_accounts
                WHERE user_id = $1
                FOR UPDATE
                """,
                user_id,
            )

            if row is None:
                logger.error("Cannot grant bonus: no account for user %s", user_id)
                return 0

            new_bonus = row["bonus_points"] + points
            balance_after = (row["daily_free_points"] - row["daily_used_points"]) + new_bonus

            await conn.execute(
                """
                UPDATE user_credit_accounts
                SET bonus_points = $2, updated_at = $3
                WHERE user_id = $1
                """,
                user_id,
                new_bonus,
                now,
            )

            await _insert_ledger_entry(
                conn,
                user_id=user_id,
                task_id=None,
                entry_type=entry_type,
                points=points,
                bucket_type="bonus",
                balance_after=balance_after,
                metadata=metadata,
                created_at=now,
                attribution=attribution,
            )

            logger.info(
                "Granted %d bonus points to user %s (entry_type=%s, new_bonus=%d, balance=%d)",
                points, user_id, entry_type, new_bonus, balance_after,
            )

            return points


async def get_quota_info(user_id: UUID) -> dict[str, Any]:
    """Get user's current quota information, performing daily reset if needed."""
    remaining = await check_quota(user_id)

    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT daily_free_points, daily_used_points, bonus_points, last_reset_on
            FROM user_credit_accounts
            WHERE user_id = $1
            """,
            user_id,
        )

    if row is None:
        return {
            "daily_free_points": DEFAULT_DAILY_FREE_POINTS,
            "daily_used_points": 0,
            "bonus_points": 0,
            "remaining_points": DEFAULT_DAILY_FREE_POINTS,
        }

    return {
        "daily_free_points": row["daily_free_points"],
        "daily_used_points": row["daily_used_points"],
        "bonus_points": row["bonus_points"],
        "remaining_points": remaining,
    }
