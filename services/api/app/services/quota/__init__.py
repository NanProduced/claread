"""Anonymous user quota service.

Handles trial-based quota for guest/unauthenticated users.
Resets daily at midnight UTC.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.database import connection as db_connection

ANONYMOUS_DAILY_TRIAL_LIMIT = 3


async def get_anonymous_quota_info(anonymous_id: str) -> dict:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    today = date.today()
    reset_at = datetime.combine(today, datetime.min.time()).astimezone(timezone.utc).isoformat()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT trial_count, last_trial_at FROM anonymous_quotas
            WHERE anonymous_id = $1
            """,
            anonymous_id,
        )

        if row is None:
            return {
                "remaining_trials": ANONYMOUS_DAILY_TRIAL_LIMIT,
                "max_trials_per_day": ANONYMOUS_DAILY_TRIAL_LIMIT,
                "reset_at": reset_at,
            }

        trial_count = row["trial_count"]
        last_trial = row["last_trial_at"]

        if last_trial < today:
            return {
                "remaining_trials": ANONYMOUS_DAILY_TRIAL_LIMIT,
                "max_trials_per_day": ANONYMOUS_DAILY_TRIAL_LIMIT,
                "reset_at": reset_at,
            }

        remaining = max(ANONYMOUS_DAILY_TRIAL_LIMIT - trial_count, 0)
        return {
            "remaining_trials": remaining,
            "max_trials_per_day": ANONYMOUS_DAILY_TRIAL_LIMIT,
            "reset_at": reset_at,
        }


async def check_and_consume_anonymous_trial(anonymous_id: str) -> tuple[int, str]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    today = date.today()
    reset_at = datetime.combine(today, datetime.min.time()).astimezone(timezone.utc).isoformat()

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT trial_count, last_trial_at FROM anonymous_quotas
                WHERE anonymous_id = $1
                FOR UPDATE
                """,
                anonymous_id,
            )

            if row is None:
                await conn.execute(
                    """
                    INSERT INTO anonymous_quotas (anonymous_id, trial_count, last_trial_at)
                    VALUES ($1, 1, $2)
                    """,
                    anonymous_id,
                    today,
                )
                return ANONYMOUS_DAILY_TRIAL_LIMIT - 1, reset_at

            trial_count = row["trial_count"]
            last_trial = row["last_trial_at"]

            if last_trial < today:
                await conn.execute(
                    """
                    UPDATE anonymous_quotas
                    SET trial_count = 1, last_trial_at = $2, updated_at = NOW()
                    WHERE anonymous_id = $1
                    """,
                    anonymous_id,
                    today,
                )
                return ANONYMOUS_DAILY_TRIAL_LIMIT - 1, reset_at

            if trial_count >= ANONYMOUS_DAILY_TRIAL_LIMIT:
                return 0, reset_at

            await conn.execute(
                """
                UPDATE anonymous_quotas
                SET trial_count = trial_count + 1, updated_at = NOW()
                WHERE anonymous_id = $1
                """,
                anonymous_id,
            )

            remaining = ANONYMOUS_DAILY_TRIAL_LIMIT - trial_count - 1
            return remaining, reset_at
