"""
Credit Service.

Handles quota checking, credit deduction, daily reset, and ledger entries.
Integrated with task submission (pre-check) and task execution (post-deduct).
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from app.database import connection as db_connection

logger = logging.getLogger(__name__)

# Default daily free points for new accounts
DEFAULT_DAILY_FREE_POINTS = 1000


class InsufficientCredits(Exception):
    """Raised when user has insufficient credits to submit a task."""

    def __init__(self, remaining: int, required: int = 1) -> None:
        self.remaining = remaining
        self.required = required
        super().__init__(
            f"Insufficient credits: remaining={remaining}, required>={required}"
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
                    datetime.now(timezone.utc),
                )
                daily_used = 0

            return (daily_free - daily_used) + bonus


async def deduct_credits(
    user_id: UUID,
    task_id: UUID,
    cost_points: int,
    metadata: dict[str, Any] | None = None,
) -> int:
    """
    Deduct credits from user account after successful task completion.

    Deduction order: daily_free first, then bonus.
    If total available < cost_points, deducts only what's available (clamp to 0).
    Writes ledger entries only for actual amounts deducted.

    Args:
        user_id: User to deduct from
        task_id: Associated task ID
        cost_points: Points to deduct (must be > 0)
        metadata: Extra metadata (token counts, multipliers, etc.)

    Returns:
        Actual total points deducted (may be less than cost_points if balance insufficient).
    """
    if cost_points <= 0:
        return 0

    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    now = datetime.now(timezone.utc)
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
                logger.error(
                    "Cannot deduct credits: no account for user %s", user_id
                )
                return 0

            daily_free = row["daily_free_points"]
            daily_used = row["daily_used_points"]
            bonus = row["bonus_points"]
            last_reset = row["last_reset_on"]

            # Auto-reset if needed
            if last_reset < today:
                daily_used = 0

            # Compute actual deductions, clamped to available balances
            daily_remaining = max(daily_free - daily_used, 0)
            deduct_from_daily = min(cost_points, daily_remaining)

            remaining_cost = cost_points - deduct_from_daily
            available_bonus = max(bonus, 0)
            deduct_from_bonus = min(remaining_cost, available_bonus)

            actual_total = deduct_from_daily + deduct_from_bonus

            new_daily_used = daily_used + deduct_from_daily
            new_bonus = bonus - deduct_from_bonus

            # Update account
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

            # Write ledger entries — only for actual amounts deducted
            if deduct_from_daily > 0:
                await conn.execute(
                    """
                    INSERT INTO user_credit_ledger
                        (user_id, task_id, entry_type, points, bucket_type, balance_after, metadata_json, created_at)
                    VALUES ($1, $2, 'analysis_deduct', $3, 'daily_free', $4, $5, $6)
                    """,
                    user_id,
                    task_id,
                    -deduct_from_daily,
                    balance_after,
                    json.dumps(metadata or {}),
                    now,
                )

            if deduct_from_bonus > 0:
                await conn.execute(
                    """
                    INSERT INTO user_credit_ledger
                        (user_id, task_id, entry_type, points, bucket_type, balance_after, metadata_json, created_at)
                    VALUES ($1, $2, 'analysis_deduct', $3, 'bonus', $4, $5, $6)
                    """,
                    user_id,
                    task_id,
                    -deduct_from_bonus,
                    balance_after,
                    json.dumps(metadata or {}),
                    now,
                )

            logger.info(
                "Deducted %d/%d points from user %s (daily=%d, bonus=%d, remaining=%d)",
                actual_total, cost_points, user_id,
                deduct_from_daily, deduct_from_bonus, balance_after,
            )

            return actual_total


async def grant_bonus_credits(
    user_id: UUID,
    points: int,
    entry_type: str = "feedback_reward",
    metadata: dict[str, Any] | None = None,
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

    now = datetime.now(timezone.utc)

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

            await conn.execute(
                """
                INSERT INTO user_credit_ledger
                    (user_id, task_id, entry_type, points, bucket_type, balance_after, metadata_json, created_at)
                VALUES ($1, NULL, $2, $3, 'bonus', $4, $5, $6)
                """,
                user_id,
                entry_type,
                points,
                balance_after,
                json.dumps(metadata or {}),
                now,
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
