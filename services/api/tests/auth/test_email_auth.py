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
from app.services.auth.email_challenges import (
    ChallengeCreated,
    ChallengeState,
    EmailAuthStateError,
    TicketIssued,
)
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


async def test_start_always_issues_register_challenge_without_account_lookup() -> None:
    challenges = _challenge_service()
    created = ChallengeCreated(
        challenge_id="A" * 32, code="123456", expires_in=600, resend_after=73
    )
    challenges.create_challenge.return_value = created
    lookup = AsyncMock()
    sender = AsyncMock(return_value="sent")

    with (
        patch("app.services.auth.email_auth.lookup_email_account", lookup),
        patch("app.services.auth.email_auth.send_verification_email", sender),
    ):
        result = await EmailAuthService(challenges).start_email_auth(
            email=EMAIL,
            client_ip="203.0.113.10",
        )

    lookup.assert_not_awaited()
    assert not hasattr(result, "mode")
    assert result.challenge_id == created.challenge_id
    assert result.expires_in == created.expires_in
    assert result.resend_after == 73
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
    created = ChallengeCreated(
        challenge_id="B" * 32, code="654321", expires_in=600, resend_after=73
    )
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

    assert not hasattr(result, "mode")
    assert result.challenge_id == created.challenge_id
    challenges.discard_challenge.assert_not_awaited()


async def test_non_rejected_sender_failure_keeps_challenge() -> None:
    challenges = _challenge_service()
    created = ChallengeCreated(
        challenge_id="F" * 32, code="654321", expires_in=600, resend_after=73
    )
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
    created = ChallengeCreated(
        challenge_id="C" * 32, code="654321", expires_in=600, resend_after=73
    )
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


async def test_verified_otp_reads_challenge_and_forwards_resolved_purpose() -> None:
    challenges = _challenge_service()
    challenges.read_challenge.return_value = ChallengeState(
        purpose="password_reset", email=NORMALIZED_EMAIL
    )
    issued = TicketIssued(ticket="D" * 43, purpose="password_reset")
    challenges.verify_challenge.return_value = issued

    result = await EmailAuthService(challenges).verify_email_otp(
        challenge_id="C" * 32,
        code="123456",
    )

    assert result is issued
    assert result.purpose == "password_reset"
    challenges.read_challenge.assert_awaited_once_with("C" * 32)
    challenges.verify_challenge.assert_awaited_once_with(
        challenge_id="C" * 32,
        code="123456",
        ticket_purpose="password_reset",
    )


async def test_register_otp_verify_converts_existing_password_account_to_reset() -> None:
    challenges = _challenge_service()
    challenges.read_challenge.return_value = ChallengeState(
        purpose="register", email=NORMALIZED_EMAIL
    )
    lookup = AsyncMock(return_value=EmailCredentialLookup(user_id=USER_ID, has_password=True))
    challenges.verify_challenge.return_value = TicketIssued(
        ticket="D" * 43, purpose="password_reset"
    )

    with patch("app.services.auth.email_auth.lookup_email_account", lookup):
        result = await EmailAuthService(challenges).verify_email_otp(
            challenge_id="C" * 32,
            code="123456",
        )

    assert result.purpose == "password_reset"
    lookup.assert_awaited_once_with(NORMALIZED_EMAIL)
    challenges.verify_challenge.assert_awaited_once_with(
        challenge_id="C" * 32,
        code="123456",
        ticket_purpose="password_reset",
    )


async def test_register_otp_verify_keeps_register_purpose_for_unknown_email() -> None:
    challenges = _challenge_service()
    challenges.read_challenge.return_value = ChallengeState(
        purpose="register", email=NORMALIZED_EMAIL
    )
    lookup = AsyncMock(return_value=None)
    challenges.verify_challenge.return_value = TicketIssued(
        ticket="D" * 43, purpose="register"
    )

    with patch("app.services.auth.email_auth.lookup_email_account", lookup):
        result = await EmailAuthService(challenges).verify_email_otp(
            challenge_id="C" * 32,
            code="123456",
        )

    assert result.purpose == "register"
    lookup.assert_awaited_once_with(NORMALIZED_EMAIL)
    challenges.verify_challenge.assert_awaited_once_with(
        challenge_id="C" * 32,
        code="123456",
        ticket_purpose="register",
    )


async def test_password_reset_otp_verify_keeps_reset_purpose_without_account_lookup() -> None:
    challenges = _challenge_service()
    challenges.read_challenge.return_value = ChallengeState(
        purpose="password_reset", email=NORMALIZED_EMAIL
    )
    lookup = AsyncMock()
    challenges.verify_challenge.return_value = TicketIssued(
        ticket="D" * 43, purpose="password_reset"
    )

    with patch("app.services.auth.email_auth.lookup_email_account", lookup):
        result = await EmailAuthService(challenges).verify_email_otp(
            challenge_id="C" * 32,
            code="123456",
        )

    assert result.purpose == "password_reset"
    lookup.assert_not_awaited()
    challenges.verify_challenge.assert_awaited_once_with(
        challenge_id="C" * 32,
        code="123456",
        ticket_purpose="password_reset",
    )


async def test_verify_otp_missing_challenge_fails_closed_without_lookup() -> None:
    challenges = _challenge_service()
    challenges.read_challenge.return_value = None
    lookup = AsyncMock()

    with patch("app.services.auth.email_auth.lookup_email_account", lookup):
        with pytest.raises(EmailAuthStateError) as caught:
            await EmailAuthService(challenges).verify_email_otp(
                challenge_id="C" * 32,
                code="123456",
            )

    assert caught.value.code == "invalid_or_expired_code"
    lookup.assert_not_awaited()
    challenges.verify_challenge.assert_not_awaited()


@pytest.mark.parametrize("safety_result", ["ok", "hibp_unavailable"])
async def test_register_checks_password_safety_before_consuming_ticket(safety_result: str) -> None:
    challenges = _challenge_service()
    events: list[str] = []

    async def safety_check(raw_password: str) -> str:
        assert raw_password == RAW_PASSWORD
        events.append("safety")
        return safety_result

    async def check_attempt(**kwargs: object) -> None:
        assert kwargs == {
            "subject": "T" * 43,
            "subject_kind": "ticket",
            "flow": "register",
            "client_ip": "198.51.100.7",
        }
        events.append("limit")

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
    challenges.check_auth_attempt.side_effect = check_attempt

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
            client_ip="198.51.100.7",
        )

    assert result == ("session-token", expires_at)
    assert events == ["limit", "safety", "consume", "identity", "password"]
    create_session.assert_awaited_once_with(
        USER_ID,
        provider="email",
        provider_user_id=NORMALIZED_EMAIL,
        client_platform="web",
        ip_address="198.51.100.7",
    )


async def test_password_login_limits_before_verification_and_clears_email_bucket() -> None:
    challenges = _challenge_service()
    events: list[str] = []

    async def check_attempt(**kwargs: object) -> None:
        assert kwargs == {
            "subject": NORMALIZED_EMAIL,
            "subject_kind": "email",
            "flow": "login",
            "client_ip": "198.51.100.7",
        }
        events.append("limit")

    async def lookup(email: str) -> EmailCredentialLookup:
        assert email == NORMALIZED_EMAIL
        events.append("lookup")
        return EmailCredentialLookup(user_id=USER_ID, has_password=True)

    async def verify(user_id: UUID, raw_password: str) -> PasswordVerification:
        assert user_id == USER_ID
        assert raw_password == RAW_PASSWORD
        events.append("verify")
        return PasswordVerification(valid=True, needs_rehash=False)

    async def clear_attempts(email: str) -> None:
        assert email == NORMALIZED_EMAIL
        events.append("clear")

    expires_at = datetime(2030, 1, 1, tzinfo=UTC)

    async def create_web_session(*args: object, **kwargs: object) -> tuple[str, datetime]:
        assert args == (USER_ID,)
        assert kwargs["ip_address"] == "198.51.100.7"
        events.append("session")
        return "session-token", expires_at

    challenges.check_auth_attempt.side_effect = check_attempt
    challenges.clear_login_email_attempts.side_effect = clear_attempts
    with (
        patch("app.services.auth.email_auth.lookup_email_account", lookup),
        patch("app.services.auth.email_auth.verify_email_password", verify),
        patch("app.services.auth.email_auth.create_session", create_web_session),
    ):
        result = await EmailAuthService(challenges).login_with_password(
            email=EMAIL,
            raw_password=RAW_PASSWORD,
            client_ip="198.51.100.7",
        )

    assert result == ("session-token", expires_at)
    assert events == ["limit", "lookup", "verify", "clear", "session"]


async def test_login_attempt_limit_precedes_argon2_verification() -> None:
    challenges = _challenge_service()
    limit_error = EmailAuthStateError("auth_attempt_limit", retry_after=19)
    challenges.check_auth_attempt.side_effect = limit_error
    verifier = AsyncMock()

    with (
        patch(
            "app.services.auth.email_auth.lookup_email_account",
            AsyncMock(return_value=EmailCredentialLookup(user_id=USER_ID, has_password=True)),
        ),
        patch("app.services.auth.email_auth.verify_email_password", verifier),
    ):
        with pytest.raises(EmailAuthStateError) as caught:
            await EmailAuthService(challenges).login_with_password(
                email=EMAIL,
                raw_password=RAW_PASSWORD,
                client_ip="198.51.100.7",
            )

    assert caught.value.code == "auth_attempt_limit"
    assert caught.value.retry_after == 19
    verifier.assert_not_awaited()


@pytest.mark.parametrize("method_name", ["register_with_ticket", "reset_with_ticket"])
async def test_ticket_attempt_limit_precedes_hibp_for_fake_ticket(method_name: str) -> None:
    challenges = _challenge_service()
    limit_error = EmailAuthStateError("auth_attempt_limit", retry_after=23)
    challenges.check_auth_attempt.side_effect = limit_error
    safety_check = AsyncMock()

    with patch("app.services.auth.email_auth.evaluate_password_safety", safety_check):
        with pytest.raises(EmailAuthStateError) as caught:
            await getattr(EmailAuthService(challenges), method_name)(
                ticket="T" * 43,
                raw_password=RAW_PASSWORD,
                client_ip="198.51.100.7",
            )

    assert caught.value.code == "auth_attempt_limit"
    assert caught.value.retry_after == 23
    safety_check.assert_not_awaited()
    challenges.consume_ticket.assert_not_awaited()


@pytest.mark.parametrize("method_name", ["register_with_ticket", "reset_with_ticket"])
async def test_repeated_fake_ticket_stops_before_a_third_hibp_check(method_name: str) -> None:
    challenges = _challenge_service()
    limit_error = EmailAuthStateError("auth_attempt_limit", retry_after=23)
    challenges.check_auth_attempt.side_effect = [None, None, limit_error]
    challenges.consume_ticket.side_effect = EmailAuthStateError("ticket_invalid_or_expired")
    safety_check = AsyncMock(return_value="hibp_unavailable")

    with patch("app.services.auth.email_auth.evaluate_password_safety", safety_check):
        for _ in range(2):
            with pytest.raises(EmailAuthStateError) as invalid_ticket:
                await getattr(EmailAuthService(challenges), method_name)(
                    ticket="T" * 43,
                    raw_password=RAW_PASSWORD,
                    client_ip="198.51.100.7",
                )
            assert invalid_ticket.value.code == "ticket_invalid_or_expired"
        with pytest.raises(EmailAuthStateError) as limited:
            await getattr(EmailAuthService(challenges), method_name)(
                ticket="T" * 43,
                raw_password=RAW_PASSWORD,
                client_ip="198.51.100.7",
            )

    assert limited.value.code == "auth_attempt_limit"
    assert safety_check.await_count == 2


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
            client_ip="198.51.100.7",
        )

    assert result == ("session-token", expires_at)
    lookup.assert_awaited_once_with(NORMALIZED_EMAIL)
    verifier.assert_awaited_once_with(USER_ID, RAW_PASSWORD)
    create_session.assert_awaited_once_with(
        USER_ID,
        provider="email",
        provider_user_id=NORMALIZED_EMAIL,
        client_platform="web",
        ip_address="198.51.100.7",
    )


@pytest.mark.parametrize("delivery", ["sent", "uncertain", "rejected"])
async def test_password_reset_request_hides_lookup_and_delivery_state(
    delivery: str,
) -> None:
    challenges = _challenge_service()
    created = ChallengeCreated(
        challenge_id="E" * 32, code="123456", expires_in=600, resend_after=73
    )
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
        resend_after=73,
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
            "ip_address": "198.51.100.7",
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
            client_ip="198.51.100.7",
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
                client_ip="198.51.100.7",
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
