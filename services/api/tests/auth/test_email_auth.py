"""Offline contract tests for email authentication use-case orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from app.services.auth.email_auth import (
    EmailAuthError,
    EmailAuthService,
    EmailResetRequestResult,
)
from app.services.auth.email_challenges import ChallengeCreated, TicketIssued
from app.services.auth.email_credentials import EmailCredentialLookup
from app.services.auth.identity import IdentityLookupResult
from app.services.auth.passwords import PasswordVerification

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


def _challenge_service() -> AsyncMock:
    service = AsyncMock()
    return service


async def test_existing_password_starts_password_mode_without_challenge() -> None:
    challenges = _challenge_service()
    lookup = AsyncMock(return_value=EmailCredentialLookup(user_id=USER_ID, has_password=True))

    with patch("app.services.auth.email_auth.lookup_email_account", lookup):
        result = await EmailAuthService(challenges).start_email_auth(
            email=EMAIL,
            client_ip="203.0.113.10",
        )

    assert result.mode == "password"
    assert result.challenge_id is None
    lookup.assert_awaited_once_with("User@example.com")
    challenges.create_challenge.assert_not_awaited()


@pytest.mark.parametrize(
    "account",
    [None, EmailCredentialLookup(user_id=USER_ID, has_password=False)],
)
async def test_new_email_starts_register_challenge_and_sends_code(
    account: EmailCredentialLookup | None,
) -> None:
    challenges = _challenge_service()
    created = ChallengeCreated(challenge_id="A" * 32, code="123456", expires_in=600)
    challenges.create_challenge.return_value = created
    lookup = AsyncMock(return_value=account)
    sender = AsyncMock(return_value="sent")

    with (
        patch("app.services.auth.email_auth.lookup_email_account", lookup),
        patch("app.services.auth.email_auth.send_verification_email", sender),
    ):
        result = await EmailAuthService(challenges).start_email_auth(
            email=EMAIL,
            client_ip="203.0.113.10",
        )

    assert result.mode == "register"
    assert result.challenge_id == created.challenge_id
    assert result.expires_in == created.expires_in
    assert not hasattr(result, "code")
    assert created.challenge_id not in repr(result)
    challenges.create_challenge.assert_awaited_once_with(
        email="User@example.com",
        purpose="register",
        client_ip="203.0.113.10",
    )
    sender.assert_awaited_once_with(
        recipient="User@example.com",
        purpose="register",
        code=created.code,
        challenge_id=created.challenge_id,
    )


async def test_uncertain_delivery_keeps_challenge_and_returns_accepted_result() -> None:
    challenges = _challenge_service()
    created = ChallengeCreated(challenge_id="B" * 32, code="654321", expires_in=600)
    challenges.create_challenge.return_value = created
    sender = AsyncMock(return_value="uncertain")

    with (
        patch(
            "app.services.auth.email_auth.lookup_email_account",
            AsyncMock(return_value=None),
        ),
        patch("app.services.auth.email_auth.send_verification_email", sender),
    ):
        result = await EmailAuthService(challenges).start_email_auth(
            email=EMAIL,
            client_ip="203.0.113.10",
        )

    assert result.mode == "register"
    assert result.challenge_id == created.challenge_id
    challenges.discard_challenge.assert_not_awaited()


async def test_non_rejected_sender_failure_keeps_challenge() -> None:
    challenges = _challenge_service()
    created = ChallengeCreated(challenge_id="F" * 32, code="654321", expires_in=600)
    challenges.create_challenge.return_value = created
    sender = AsyncMock(side_effect=RuntimeError("transport failed"))

    with (
        patch(
            "app.services.auth.email_auth.lookup_email_account",
            AsyncMock(return_value=None),
        ),
        patch("app.services.auth.email_auth.send_verification_email", sender),
    ):
        with pytest.raises(RuntimeError, match="transport failed"):
            await EmailAuthService(challenges).start_email_auth(
                email=EMAIL,
                client_ip="203.0.113.10",
            )

    challenges.discard_challenge.assert_not_awaited()


async def test_rejected_delivery_discards_challenge_and_returns_stable_error() -> None:
    challenges = _challenge_service()
    created = ChallengeCreated(challenge_id="C" * 32, code="654321", expires_in=600)
    challenges.create_challenge.return_value = created
    sender = AsyncMock(return_value="rejected")

    with (
        patch(
            "app.services.auth.email_auth.lookup_email_account",
            AsyncMock(return_value=None),
        ),
        patch("app.services.auth.email_auth.send_verification_email", sender),
    ):
        with pytest.raises(EmailAuthError) as error:
            await EmailAuthService(challenges).start_email_auth(
                email=EMAIL,
                client_ip="203.0.113.10",
            )

    assert error.value.code == "email_delivery_rejected"
    challenges.discard_challenge.assert_awaited_once_with(created.challenge_id)


async def test_verified_otp_returns_existing_one_time_ticket() -> None:
    challenges = _challenge_service()
    issued = TicketIssued(ticket="D" * 43)
    challenges.verify_challenge.return_value = issued

    result = await EmailAuthService(challenges).verify_email_otp(
        challenge_id="C" * 32,
        code="123456",
    )

    assert result is issued
    challenges.verify_challenge.assert_awaited_once_with(
        challenge_id="C" * 32,
        code="123456",
    )


@pytest.mark.parametrize("safety_result", ["ok", "hibp_unavailable"])
async def test_register_checks_password_safety_before_consuming_ticket(safety_result: str) -> None:
    challenges = _challenge_service()
    events: list[str] = []

    async def safety_check(raw_password: str) -> str:
        assert raw_password == RAW_PASSWORD
        events.append("safety")
        return safety_result

    async def consume_ticket(ticket: str, *, expected_purpose: str) -> str:
        assert ticket == "T" * 43
        assert expected_purpose == "register"
        events.append("consume")
        return NORMALIZED_EMAIL

    async def create_identity(email: str) -> IdentityLookupResult:
        assert email == NORMALIZED_EMAIL
        events.append("identity")
        return IdentityLookupResult(user_id=USER_ID, created=True)

    async def set_password(user_id: UUID, raw_password: str) -> None:
        assert user_id == USER_ID
        assert raw_password == RAW_PASSWORD
        events.append("password")

    expires_at = datetime(2030, 1, 1, tzinfo=UTC)
    create_session = AsyncMock(return_value=("session-token", expires_at))

    with (
        patch("app.services.auth.email_auth.evaluate_password_safety", safety_check),
        patch.object(challenges, "consume_ticket", consume_ticket),
        patch("app.services.auth.email_auth.get_or_create_user_by_verified_email", create_identity),
        patch("app.services.auth.email_auth.set_email_password", set_password),
        patch("app.services.auth.email_auth.create_session", create_session),
    ):
        result = await EmailAuthService(challenges).register_with_ticket(
            ticket="T" * 43,
            raw_password=RAW_PASSWORD,
        )

    assert result == ("session-token", expires_at)
    assert events == ["safety", "consume", "identity", "password"]
    create_session.assert_awaited_once_with(
        USER_ID,
        provider="email",
        provider_user_id=NORMALIZED_EMAIL,
        client_platform="web",
    )


@pytest.mark.parametrize("safety_result", ["common", "compromised"])
async def test_unsafe_register_password_does_not_consume_ticket(safety_result: str) -> None:
    challenges = _challenge_service()
    safety_check = AsyncMock(return_value=safety_result)

    with patch(
        "app.services.auth.email_auth.evaluate_password_safety",
        safety_check,
    ):
        with pytest.raises(EmailAuthError) as error:
            await EmailAuthService(challenges).register_with_ticket(
                ticket="T" * 43,
                raw_password=RAW_PASSWORD,
            )

    assert error.value.code == safety_result
    challenges.consume_ticket.assert_not_awaited()


@pytest.mark.parametrize(
    ("account", "verification"),
    [
        (None, None),
        (EmailCredentialLookup(user_id=USER_ID, has_password=False), None),
        (
            EmailCredentialLookup(user_id=USER_ID, has_password=True),
            PasswordVerification(valid=False, needs_rehash=False),
        ),
    ],
)
async def test_password_login_uses_same_invalid_credentials_error(
    account: EmailCredentialLookup | None,
    verification: PasswordVerification | None,
) -> None:
    challenges = _challenge_service()
    lookup = AsyncMock(return_value=account)
    verifier = AsyncMock(return_value=verification)

    with (
        patch("app.services.auth.email_auth.lookup_email_account", lookup),
        patch("app.services.auth.email_auth.verify_email_password", verifier),
    ):
        with pytest.raises(EmailAuthError) as error:
            await EmailAuthService(challenges).login_with_password(
                email=EMAIL,
                raw_password=RAW_PASSWORD,
            )

    assert error.value.code == "invalid_credentials"
    if account is None or not account.has_password:
        verifier.assert_not_awaited()
    else:
        verifier.assert_awaited_once_with(USER_ID, RAW_PASSWORD)


async def test_password_login_invalid_email_also_uses_invalid_credentials() -> None:
    challenges = _challenge_service()
    lookup = AsyncMock()

    with patch("app.services.auth.email_auth.lookup_email_account", lookup):
        with pytest.raises(EmailAuthError) as error:
            await EmailAuthService(challenges).login_with_password(
                email="not-an-email",
                raw_password=RAW_PASSWORD,
            )

    assert error.value.code == "invalid_credentials"
    lookup.assert_not_awaited()


async def test_password_login_success_creates_web_session() -> None:
    challenges = _challenge_service()
    lookup = AsyncMock(
        return_value=EmailCredentialLookup(user_id=USER_ID, has_password=True),
    )
    verifier = AsyncMock(return_value=PasswordVerification(valid=True, needs_rehash=False))
    expires_at = datetime(2030, 1, 1, tzinfo=UTC)
    create_session = AsyncMock(return_value=("session-token", expires_at))

    with (
        patch("app.services.auth.email_auth.lookup_email_account", lookup),
        patch("app.services.auth.email_auth.verify_email_password", verifier),
        patch("app.services.auth.email_auth.create_session", create_session),
    ):
        result = await EmailAuthService(challenges).login_with_password(
            email=EMAIL,
            raw_password=RAW_PASSWORD,
        )

    assert result == ("session-token", expires_at)
    lookup.assert_awaited_once_with(NORMALIZED_EMAIL)
    verifier.assert_awaited_once_with(USER_ID, RAW_PASSWORD)
    create_session.assert_awaited_once_with(
        USER_ID,
        provider="email",
        provider_user_id=NORMALIZED_EMAIL,
        client_platform="web",
    )


@pytest.mark.parametrize("delivery", ["sent", "uncertain", "rejected"])
async def test_password_reset_request_hides_lookup_and_delivery_state(
    delivery: str,
) -> None:
    challenges = _challenge_service()
    created = ChallengeCreated(challenge_id="E" * 32, code="123456", expires_in=600)
    challenges.create_challenge.return_value = created
    lookup = AsyncMock()
    sender = AsyncMock(return_value=delivery)

    with (
        patch("app.services.auth.email_auth.lookup_email_account", lookup),
        patch("app.services.auth.email_auth.send_verification_email", sender),
    ):
        result = await EmailAuthService(challenges).request_password_reset(
            email=EMAIL,
            client_ip="203.0.113.10",
        )

    assert result == EmailResetRequestResult(
        status="accepted",
        challenge_id=created.challenge_id,
        expires_in=created.expires_in,
    )
    lookup.assert_not_awaited()
    challenges.create_challenge.assert_awaited_once_with(
        email=NORMALIZED_EMAIL,
        purpose="password_reset",
        client_ip="203.0.113.10",
    )
    sender.assert_awaited_once_with(
        recipient=NORMALIZED_EMAIL,
        purpose="password_reset",
        code=created.code,
        challenge_id=created.challenge_id,
    )
    if delivery == "rejected":
        challenges.discard_challenge.assert_awaited_once_with(created.challenge_id)
    else:
        challenges.discard_challenge.assert_not_awaited()


@pytest.mark.parametrize("safety_result", ["ok", "hibp_unavailable"])
async def test_reset_checks_safety_and_creates_session_after_atomic_update(
    safety_result: str,
) -> None:
    challenges = _challenge_service()
    events: list[str] = []

    async def safety_check(raw_password: str) -> str:
        assert raw_password == RAW_PASSWORD
        events.append("safety")
        return safety_result

    async def consume_ticket(ticket: str, *, expected_purpose: str) -> str:
        assert ticket == "R" * 43
        assert expected_purpose == "password_reset"
        events.append("consume")
        return NORMALIZED_EMAIL

    async def lookup(email: str) -> EmailCredentialLookup:
        assert email == NORMALIZED_EMAIL
        events.append("lookup")
        return EmailCredentialLookup(user_id=USER_ID, has_password=True)

    async def reset_password(user_id: UUID, raw_password: str) -> None:
        assert user_id == USER_ID
        assert raw_password == RAW_PASSWORD
        events.append("reset")

    expires_at = datetime(2030, 1, 1, tzinfo=UTC)

    async def create_web_session(*args: object, **kwargs: object) -> tuple[str, datetime]:
        assert args == (USER_ID,)
        assert kwargs == {
            "provider": "email",
            "provider_user_id": NORMALIZED_EMAIL,
            "client_platform": "web",
        }
        events.append("session")
        return "new-session-token", expires_at

    with (
        patch("app.services.auth.email_auth.evaluate_password_safety", safety_check),
        patch.object(challenges, "consume_ticket", consume_ticket),
        patch("app.services.auth.email_auth.lookup_email_account", lookup),
        patch(
            "app.services.auth.email_auth.reset_email_password_and_revoke_sessions",
            reset_password,
        ),
        patch("app.services.auth.email_auth.create_session", create_web_session),
    ):
        result = await EmailAuthService(challenges).reset_with_ticket(
            ticket="R" * 43,
            raw_password=RAW_PASSWORD,
        )

    assert result == ("new-session-token", expires_at)
    assert events == ["safety", "consume", "lookup", "reset", "session"]


@pytest.mark.parametrize("safety_result", ["common", "compromised"])
async def test_unsafe_reset_password_does_not_consume_ticket(safety_result: str) -> None:
    challenges = _challenge_service()
    safety_check = AsyncMock(return_value=safety_result)

    with patch("app.services.auth.email_auth.evaluate_password_safety", safety_check):
        with pytest.raises(EmailAuthError) as error:
            await EmailAuthService(challenges).reset_with_ticket(
                ticket="R" * 43,
                raw_password=RAW_PASSWORD,
            )

    assert error.value.code == safety_result
    challenges.consume_ticket.assert_not_awaited()


async def test_reset_transaction_failure_does_not_create_new_session() -> None:
    challenges = _challenge_service()
    reset_password = AsyncMock(side_effect=RuntimeError("database failure"))
    create_session = AsyncMock()

    with (
        patch(
            "app.services.auth.email_auth.evaluate_password_safety",
            AsyncMock(return_value="ok"),
        ),
        patch.object(
            challenges,
            "consume_ticket",
            AsyncMock(return_value=NORMALIZED_EMAIL),
        ),
        patch(
            "app.services.auth.email_auth.lookup_email_account",
            AsyncMock(
                return_value=EmailCredentialLookup(user_id=USER_ID, has_password=True),
            ),
        ),
        patch(
            "app.services.auth.email_auth.reset_email_password_and_revoke_sessions",
            reset_password,
        ),
        patch("app.services.auth.email_auth.create_session", create_session),
    ):
        with pytest.raises(RuntimeError, match="database failure"):
            await EmailAuthService(challenges).reset_with_ticket(
                ticket="R" * 43,
                raw_password=RAW_PASSWORD,
            )

    create_session.assert_not_awaited()
