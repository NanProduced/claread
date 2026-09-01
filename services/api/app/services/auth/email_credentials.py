"""Persistent email identity and password credentials."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.database import connection as db_connection
from app.services.auth.email_address import normalize_email_address
from app.services.auth.identity import IdentityLookupResult, get_or_create_user_by_identity
from app.services.auth.passwords import PasswordVerification, hash_password, verify_password


@dataclass(frozen=True, slots=True)
class EmailCredentialLookup:
    user_id: UUID
    has_password: bool


async def get_or_create_user_by_verified_email(email: str) -> IdentityLookupResult:
    normalized_email = normalize_email_address(email)
    return await get_or_create_user_by_identity(
        provider="email",
        provider_user_id=normalized_email,
    )


async def lookup_email_account(email: str) -> EmailCredentialLookup | None:
    normalized_email = normalize_email_address(email)
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        row: Any = await conn.fetchrow(
            """
            SELECT identity.user_id,
                   (credential.user_id IS NOT NULL) AS has_password
            FROM user_identities AS identity
            LEFT JOIN user_password_credentials AS credential
                ON credential.user_id = identity.user_id
            WHERE identity.provider = $1
              AND identity.provider_user_id = $2
            """,
            "email",
            normalized_email,
        )

    if row is None:
        return None
    return EmailCredentialLookup(
        user_id=row["user_id"],
        has_password=bool(row["has_password"]),
    )


async def _upsert_password_credential(conn: Any, user_id: UUID, password_hash: str) -> None:
    await conn.execute(
        """
        INSERT INTO user_password_credentials
            (user_id, password_hash, password_changed_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (user_id) DO UPDATE
        SET password_hash = EXCLUDED.password_hash,
            password_changed_at = NOW()
        """,
        user_id,
        password_hash,
    )


async def set_email_password(user_id: UUID, raw_password: str) -> None:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    password_hash = hash_password(raw_password)
    async with pool.acquire() as conn:
        await _upsert_password_credential(conn, user_id, password_hash)


async def reset_email_password_and_revoke_sessions(user_id: UUID, raw_password: str) -> None:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    password_hash = hash_password(raw_password)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _upsert_password_credential(conn, user_id, password_hash)
            await conn.fetch(
                """
                UPDATE user_sessions
                SET status = 'revoked', revoked_at = NOW()
                WHERE user_id = $1 AND status = 'active'
                RETURNING id
                """,
                user_id,
            )


async def verify_email_password(user_id: UUID, raw_password: str) -> PasswordVerification:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        row: Any = await conn.fetchrow(
            """
            SELECT password_hash
            FROM user_password_credentials
            WHERE user_id = $1
            """,
            user_id,
        )

    if row is None:
        return PasswordVerification(valid=False, needs_rehash=False)
    try:
        password_hash = row["password_hash"]
    except (KeyError, TypeError):
        return PasswordVerification(valid=False, needs_rehash=False)

    verification = verify_password(raw_password, password_hash)
    if verification.valid and verification.needs_rehash:
        upgraded_hash = hash_password(raw_password)
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE user_password_credentials
                SET password_hash = $2
                WHERE user_id = $1
                  AND password_hash = $3
                """,
                user_id,
                upgraded_hash,
                password_hash,
            )
    return verification
