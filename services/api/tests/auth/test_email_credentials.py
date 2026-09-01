"""Offline contract tests for email identity and password credentials."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock, patch
from uuid import UUID

import pytest

from app.services.auth.email_credentials import (
    EmailCredentialLookup,
    get_or_create_user_by_verified_email,
    lookup_email_account,
    set_email_password,
    verify_email_password,
)
from app.services.auth.identity import IdentityLookupResult
from app.services.auth.passwords import PasswordVerification, hash_password

pytestmark = [
    pytest.mark.chain_auth,
    pytest.mark.seam_service_integration,
    pytest.mark.life_permanent_regression,
    pytest.mark.no_network_default,
]

EMAIL = "User@Example.COM"
NORMALIZED_EMAIL = "User@example.com"
USER_ID = UUID("11111111-1111-4111-8111-111111111111")
RAW_PASSWORD = "correct horse battery staple"
PASSWORD_HASH = "$argon2id$v=19$m=19456,t=2,p=1$hashed-value"


async def test_verified_email_get_or_create_normalizes_and_preserves_created() -> None:
    expected = IdentityLookupResult(user_id=USER_ID, created=True)
    identity = AsyncMock(return_value=expected)

    with patch(
        "app.services.auth.email_credentials.get_or_create_user_by_identity",
        identity,
    ):
        result = await get_or_create_user_by_verified_email(EMAIL)

    assert result == expected
    identity.assert_awaited_once_with(
        provider="email",
        provider_user_id=NORMALIZED_EMAIL,
    )


async def test_verified_email_get_or_create_preserves_existing_created_false() -> None:
    expected = IdentityLookupResult(user_id=USER_ID, created=False)
    identity = AsyncMock(return_value=expected)

    with patch(
        "app.services.auth.email_credentials.get_or_create_user_by_identity",
        identity,
    ):
        result = await get_or_create_user_by_verified_email(EMAIL)

    assert result == expected
    assert result.created is False


async def test_lookup_returns_only_typed_identity_and_password_presence() -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = {"user_id": USER_ID, "has_password": True}
    pool = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn

    with patch("app.services.auth.email_credentials.db_connection.DB_POOL", pool):
        result = await lookup_email_account(EMAIL)

    assert isinstance(result, EmailCredentialLookup)
    assert result.user_id == USER_ID
    assert result.has_password is True
    assert not hasattr(result, "password_hash")
    query, *args = conn.fetchrow.await_args.args
    assert NORMALIZED_EMAIL in args
    assert "password_hash" not in query.lower()


async def test_lookup_missing_email_returns_none() -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    pool = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn

    with patch("app.services.auth.email_credentials.db_connection.DB_POOL", pool):
        result = await lookup_email_account(EMAIL)

    assert result is None


async def test_lookup_reports_password_absence_without_hash() -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = {"user_id": USER_ID, "has_password": False}
    pool = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn

    with patch("app.services.auth.email_credentials.db_connection.DB_POOL", pool):
        result = await lookup_email_account(EMAIL)

    assert result == EmailCredentialLookup(user_id=USER_ID, has_password=False)


async def test_set_password_hashes_and_writes_one_upsert_without_plaintext() -> None:
    conn = AsyncMock()
    pool = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    hasher = Mock(return_value=PASSWORD_HASH)

    with (
        patch("app.services.auth.email_credentials.db_connection.DB_POOL", pool),
        patch("app.services.auth.email_credentials.hash_password", hasher),
    ):
        await set_email_password(USER_ID, RAW_PASSWORD)

    hasher.assert_called_once_with(RAW_PASSWORD)
    conn.execute.assert_awaited_once()
    query, *args = conn.execute.await_args.args
    assert "INSERT INTO user_password_credentials" in query
    assert "ON CONFLICT (user_id) DO UPDATE" in query
    assert "password_changed_at = NOW()" in query
    assert PASSWORD_HASH in args
    assert RAW_PASSWORD not in " ".join(map(str, (query, *args)))


async def test_verify_missing_credential_returns_invalid() -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    pool = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn

    with patch("app.services.auth.email_credentials.db_connection.DB_POOL", pool):
        result = await verify_email_password(USER_ID, RAW_PASSWORD)

    assert result == PasswordVerification(valid=False, needs_rehash=False)
    conn.execute.assert_not_awaited()


async def test_verify_wrong_password_returns_invalid_without_rehash() -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = {"password_hash": hash_password(RAW_PASSWORD)}
    pool = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn

    with patch("app.services.auth.email_credentials.db_connection.DB_POOL", pool):
        result = await verify_email_password(USER_ID, "wrong horse battery")

    assert result == PasswordVerification(valid=False, needs_rehash=False)
    conn.execute.assert_not_awaited()


@pytest.mark.parametrize("stored_hash", ["not-a-hash", "$argon2id$broken", None])
async def test_verify_malformed_hash_returns_invalid_without_rehash(stored_hash: object) -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = {"password_hash": stored_hash}
    pool = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn

    with patch("app.services.auth.email_credentials.db_connection.DB_POOL", pool):
        result = await verify_email_password(USER_ID, RAW_PASSWORD)

    assert result == PasswordVerification(valid=False, needs_rehash=False)
    conn.execute.assert_not_awaited()


async def test_verify_rehash_uses_old_hash_as_cas_condition() -> None:
    old_hash = "$argon2id$old"
    upgraded_hash = "$argon2id$upgraded"
    conn = AsyncMock()
    conn.fetchrow.return_value = {"password_hash": old_hash}
    pool = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    verifier = Mock(return_value=PasswordVerification(valid=True, needs_rehash=True))
    hasher = Mock(return_value=upgraded_hash)

    with (
        patch("app.services.auth.email_credentials.db_connection.DB_POOL", pool),
        patch("app.services.auth.email_credentials.verify_password", verifier),
        patch("app.services.auth.email_credentials.hash_password", hasher),
    ):
        result = await verify_email_password(USER_ID, RAW_PASSWORD)

    assert result == PasswordVerification(valid=True, needs_rehash=True)
    verifier.assert_called_once_with(RAW_PASSWORD, old_hash)
    hasher.assert_called_once_with(RAW_PASSWORD)
    conn.execute.assert_awaited_once()
    query, *args = conn.execute.await_args.args
    normalized_query = " ".join(query.split())
    assert "SET password_hash = $2" in normalized_query
    assert "password_changed_at" not in normalized_query
    assert "updated_at" not in normalized_query
    assert "AND password_hash = $3" in query
    assert args == [USER_ID, upgraded_hash, old_hash]


async def test_verify_rehash_does_not_overwrite_concurrent_password_reset() -> None:
    old_hash = "$argon2id$old"
    reset_hash = "$argon2id$reset"
    upgraded_hash = "$argon2id$upgraded"
    current_hash = reset_hash
    conn = AsyncMock()
    conn.fetchrow.return_value = {"password_hash": old_hash}

    async def execute_cas(query: str, user_id: UUID, new_hash: str, expected_hash: str) -> str:
        nonlocal current_hash
        if current_hash == expected_hash:
            current_hash = new_hash
            return "UPDATE 1"
        return "UPDATE 0"

    conn.execute.side_effect = execute_cas
    pool = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    verifier = Mock(return_value=PasswordVerification(valid=True, needs_rehash=True))

    def rehash_after_reset(_: str) -> str:
        nonlocal current_hash
        current_hash = reset_hash
        return upgraded_hash

    with (
        patch("app.services.auth.email_credentials.db_connection.DB_POOL", pool),
        patch("app.services.auth.email_credentials.verify_password", verifier),
        patch("app.services.auth.email_credentials.hash_password", side_effect=rehash_after_reset),
    ):
        result = await verify_email_password(USER_ID, RAW_PASSWORD)

    assert result.valid is True
    assert current_hash == reset_hash
    assert conn.execute.await_count == 1


async def test_all_database_operations_fail_closed_without_pool() -> None:
    with patch("app.services.auth.email_credentials.db_connection.DB_POOL", None):
        with pytest.raises(RuntimeError, match="Database pool not initialized"):
            await get_or_create_user_by_verified_email(EMAIL)
        with pytest.raises(RuntimeError, match="Database pool not initialized"):
            await lookup_email_account(EMAIL)
        with pytest.raises(RuntimeError, match="Database pool not initialized"):
            await set_email_password(USER_ID, RAW_PASSWORD)
        with pytest.raises(RuntimeError, match="Database pool not initialized"):
            await verify_email_password(USER_ID, RAW_PASSWORD)
