"""Provider identity helpers for multi-client auth."""

from __future__ import annotations

import hashlib
import json
import random
import string
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from app.database import connection as db_connection

IdentityBindResult = Literal["created", "already_bound"]


class IdentityConflictError(Exception):
    """Raised when a provider identity belongs to another Claread user."""

    def __init__(self, provider: str, provider_user_id: str, existing_user_id: UUID) -> None:
        self.provider = provider
        self.provider_user_id = provider_user_id
        self.existing_user_id = existing_user_id
        super().__init__(
            f"Identity {provider}:{provider_user_id} already belongs to {existing_user_id}"
        )


@dataclass(frozen=True)
class IdentityLookupResult:
    user_id: UUID
    created: bool


def _generate_default_display_name() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"Claread_{suffix}"


def _stable_lock_key(value: str) -> int:
    digest = hashlib.sha256(value.encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big") & 0x7FFFFFFFFFFFFFFF


async def _find_identity_user_id(conn: Any, *, provider: str, provider_user_id: str) -> UUID | None:
    row = await conn.fetchrow(
        """
        SELECT user_id FROM user_identities
        WHERE provider = $1 AND provider_user_id = $2
        """,
        provider,
        provider_user_id,
    )
    return row["user_id"] if row is not None else None


async def _find_unionid_user_id(conn: Any, unionid: str | None) -> UUID | None:
    if not unionid:
        return None

    row = await conn.fetchrow(
        """
        SELECT user_id FROM user_identities
        WHERE unionid = $1
        ORDER BY created_at ASC
        LIMIT 1
        """,
        unionid,
    )
    return row["user_id"] if row is not None else None


async def _ensure_unionid_available_for_user(
    conn: Any,
    *,
    unionid: str | None,
    user_id: UUID,
    provider: str,
    provider_user_id: str,
) -> None:
    unionid_user_id = await _find_unionid_user_id(conn, unionid)
    if unionid_user_id is not None and unionid_user_id != user_id:
        raise IdentityConflictError(provider, provider_user_id, unionid_user_id)


async def _insert_identity(
    conn: Any,
    *,
    user_id: UUID,
    provider: str,
    provider_user_id: str,
    unionid: str | None,
    app_id: str | None,
    payload_json: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO user_identities
            (user_id, provider, provider_user_id, unionid, app_id, auth_payload_json)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        user_id,
        provider,
        provider_user_id,
        unionid,
        app_id,
        payload_json,
    )


async def get_or_create_user_by_identity(
    *,
    provider: str,
    provider_user_id: str,
    unionid: str | None = None,
    app_id: str | None = None,
    auth_payload: dict[str, Any] | None = None,
) -> IdentityLookupResult:
    """Find or create a Claread user by provider identity."""
    if db_connection.DB_POOL is None:
        raise RuntimeError("Database pool not initialized")

    identity_lock_key = _stable_lock_key(f"identity:{provider}:{provider_user_id}")
    payload_json = json.dumps(auth_payload or {})

    async with db_connection.DB_POOL.acquire() as conn:
        if unionid:
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1)",
                _stable_lock_key(f"unionid:{unionid}"),
            )
        await conn.execute("SELECT pg_advisory_xact_lock($1)", identity_lock_key)

        identity_user_id = await _find_identity_user_id(
            conn,
            provider=provider,
            provider_user_id=provider_user_id,
        )
        if identity_user_id is not None:
            await _ensure_unionid_available_for_user(
                conn,
                unionid=unionid,
                user_id=identity_user_id,
                provider=provider,
                provider_user_id=provider_user_id,
            )
            await conn.execute(
                """
                UPDATE user_identities
                SET unionid = COALESCE($3, unionid),
                    app_id = COALESCE($4, app_id),
                    auth_payload_json = $5
                WHERE provider = $1 AND provider_user_id = $2
                """,
                provider,
                provider_user_id,
                unionid,
                app_id,
                payload_json,
            )
            return IdentityLookupResult(user_id=identity_user_id, created=False)

        unionid_user_id = await _find_unionid_user_id(conn, unionid)
        if unionid_user_id is not None:
            await _insert_identity(
                conn,
                user_id=unionid_user_id,
                provider=provider,
                provider_user_id=provider_user_id,
                unionid=unionid,
                app_id=app_id,
                payload_json=payload_json,
            )
            return IdentityLookupResult(user_id=unionid_user_id, created=False)

        user_id: UUID = await conn.fetchval(
            """
            INSERT INTO users (display_name, metadata_json)
            VALUES ($1, '{}'::jsonb)
            RETURNING id
            """,
            _generate_default_display_name(),
        )

        await _insert_identity(
            conn,
            user_id=user_id,
            provider=provider,
            provider_user_id=provider_user_id,
            unionid=unionid,
            app_id=app_id,
            payload_json=payload_json,
        )

        return IdentityLookupResult(user_id=user_id, created=True)


async def bind_identity_to_user(
    *,
    user_id: UUID,
    provider: str,
    provider_user_id: str,
    unionid: str | None = None,
    app_id: str | None = None,
    auth_payload: dict[str, Any] | None = None,
) -> IdentityBindResult:
    """Bind a provider identity to an existing Claread user.

    Binding is idempotent for the same user and fails explicitly when the
    identity belongs to another user. Asset merging is intentionally not done
    here.
    """
    if db_connection.DB_POOL is None:
        raise RuntimeError("Database pool not initialized")

    identity_lock_key = _stable_lock_key(f"identity:{provider}:{provider_user_id}")
    payload_json = json.dumps(auth_payload or {})

    async with db_connection.DB_POOL.acquire() as conn:
        if unionid:
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1)",
                _stable_lock_key(f"unionid:{unionid}"),
            )
        await conn.execute("SELECT pg_advisory_xact_lock($1)", identity_lock_key)

        existing_user_id = await _find_identity_user_id(
            conn,
            provider=provider,
            provider_user_id=provider_user_id,
        )
        if existing_user_id is not None:
            if existing_user_id == user_id:
                await _ensure_unionid_available_for_user(
                    conn,
                    unionid=unionid,
                    user_id=user_id,
                    provider=provider,
                    provider_user_id=provider_user_id,
                )
                await conn.execute(
                    """
                    UPDATE user_identities
                    SET unionid = COALESCE($3, unionid),
                        app_id = COALESCE($4, app_id),
                        auth_payload_json = $5
                    WHERE provider = $1 AND provider_user_id = $2
                    """,
                    provider,
                    provider_user_id,
                    unionid,
                    app_id,
                    payload_json,
                )
                return "already_bound"

            raise IdentityConflictError(provider, provider_user_id, existing_user_id)

        await _ensure_unionid_available_for_user(
            conn,
            unionid=unionid,
            user_id=user_id,
            provider=provider,
            provider_user_id=provider_user_id,
        )

        await _insert_identity(
            conn,
            user_id=user_id,
            provider=provider,
            provider_user_id=provider_user_id,
            unionid=unionid,
            app_id=app_id,
            payload_json=payload_json,
        )

        return "created"
