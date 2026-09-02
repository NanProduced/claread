"""Email authentication use-case orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.services.auth.email_address import InvalidEmailAddressError, normalize_email_address
from app.services.auth.email_challenges import (
    ChallengeCreated,
    ChallengeState,
    EmailAuthChallengeService,
    EmailAuthStateError,
    TicketIssued,
)
from app.services.auth.email_credentials import (
    get_or_create_user_by_verified_email,
    lookup_email_account,
    reset_email_password_and_revoke_sessions,
    set_email_password,
    verify_email_password,
)
from app.services.auth.password_safety import evaluate_password_safety
from app.services.auth.resend_email import send_verification_email
from app.services.auth.session import create_session

EmailPurpose = Literal["register", "password_reset"]


class EmailAuthError(RuntimeError):
    """Stable, non-sensitive failure from the email auth use-case layer."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EmailEntryResult:
    challenge_id: str = field(repr=False)
    expires_in: int
    resend_after: int


@dataclass(frozen=True, slots=True)
class EmailResetRequestResult:
    status: Literal["accepted"]
    challenge_id: str = field(repr=False)
    expires_in: int
    resend_after: int


class EmailAuthService:
    def __init__(self, challenge_service: EmailAuthChallengeService) -> None:
        self._challenges = challenge_service

    async def start_email_auth(self, *, email: str, client_ip: str) -> EmailEntryResult:
        """Explicit register entry: always issue a register challenge.

        The account state is never consulted here, so the response cannot
        reveal whether the email already exists.
        """
        normalized_email = normalize_email_address(email)
        challenge = await self._challenges.create_challenge(
            email=normalized_email,
            purpose="register",
            client_ip=client_ip,
        )
        await self._deliver_challenge(challenge, purpose="register", email=normalized_email)
        return EmailEntryResult(
            challenge_id=challenge.challenge_id,
            expires_in=challenge.expires_in,
            resend_after=challenge.resend_after,
        )

    async def _deliver_challenge(
        self,
        challenge: ChallengeCreated,
        *,
        purpose: EmailPurpose,
        email: str,
    ) -> None:
        delivery = await send_verification_email(
            recipient=email,
            purpose=purpose,
            code=challenge.code,
            challenge_id=challenge.challenge_id,
        )
        if delivery == "rejected":
            await self._challenges.discard_challenge(challenge.challenge_id)
            raise EmailAuthError("email_delivery_rejected")

    async def verify_email_otp(self, *, challenge_id: str, code: str) -> TicketIssued:
        """Verify the code and bind the ticket to the resolved purpose.

        A verified register code for an email that already owns a password is
        converted to a password_reset ticket. The account lookup happens only
        after the code has been presented, so the browser contract stays
        uniform before verification.
        """
        state = await self._challenges.read_challenge(challenge_id)
        if state is None:
            raise EmailAuthStateError("invalid_or_expired_code")
        ticket_purpose = await self._resolved_ticket_purpose(state)
        return await self._challenges.verify_challenge(
            challenge_id=challenge_id,
            code=code,
            ticket_purpose=ticket_purpose,
        )

    async def _resolved_ticket_purpose(self, state: ChallengeState) -> EmailPurpose:
        if state.purpose != "register":
            return state.purpose
        account = await lookup_email_account(state.email)
        if account is not None and account.has_password:
            return "password_reset"
        return "register"

    async def register_with_ticket(
        self,
        *,
        ticket: str,
        raw_password: str,
        client_ip: str | None = None,
    ) -> tuple[str, datetime]:
        await self._challenges.check_auth_attempt(
            subject=ticket,
            subject_kind="ticket",
            flow="register",
            client_ip=client_ip or "",
        )
        safety = await evaluate_password_safety(raw_password)
        if safety in ("common", "compromised"):
            raise EmailAuthError(safety)

        email = await self._challenges.consume_ticket(
            ticket,
            expected_purpose="register",
        )
        normalized_email = normalize_email_address(email)
        identity = await get_or_create_user_by_verified_email(normalized_email)
        await set_email_password(identity.user_id, raw_password)
        return await create_session(
            identity.user_id,
            provider="email",
            provider_user_id=normalized_email,
            client_platform="web",
            ip_address=client_ip,
        )

    async def login_with_password(
        self,
        *,
        email: str,
        raw_password: str,
        client_ip: str | None = None,
    ) -> tuple[str, datetime]:
        try:
            normalized_email = normalize_email_address(email)
        except InvalidEmailAddressError:
            raise EmailAuthError("invalid_credentials") from None

        await self._challenges.check_auth_attempt(
            subject=normalized_email,
            subject_kind="email",
            flow="login",
            client_ip=client_ip or "",
        )
        account = await lookup_email_account(normalized_email)
        if account is None or not account.has_password:
            raise EmailAuthError("invalid_credentials")

        verification = await verify_email_password(account.user_id, raw_password)
        if not verification.valid:
            raise EmailAuthError("invalid_credentials")
        await self._challenges.clear_login_email_attempts(normalized_email)
        return await create_session(
            account.user_id,
            provider="email",
            provider_user_id=normalized_email,
            client_platform="web",
            ip_address=client_ip,
        )

    async def request_password_reset(
        self,
        *,
        email: str,
        client_ip: str,
    ) -> EmailResetRequestResult:
        normalized_email = normalize_email_address(email)
        challenge = await self._challenges.create_challenge(
            email=normalized_email,
            purpose="password_reset",
            client_ip=client_ip,
        )
        try:
            await self._deliver_challenge(
                challenge,
                purpose="password_reset",
                email=normalized_email,
            )
        except EmailAuthError as error:
            if error.code != "email_delivery_rejected":
                raise
        return EmailResetRequestResult(
            status="accepted",
            challenge_id=challenge.challenge_id,
            expires_in=challenge.expires_in,
            resend_after=challenge.resend_after,
        )

    async def reset_with_ticket(
        self,
        *,
        ticket: str,
        raw_password: str,
        client_ip: str | None = None,
    ) -> tuple[str, datetime]:
        await self._challenges.check_auth_attempt(
            subject=ticket,
            subject_kind="ticket",
            flow="password_reset",
            client_ip=client_ip or "",
        )
        safety = await evaluate_password_safety(raw_password)
        if safety in ("common", "compromised"):
            raise EmailAuthError(safety)

        email = await self._challenges.consume_ticket(
            ticket,
            expected_purpose="password_reset",
        )
        normalized_email = normalize_email_address(email)
        account = await lookup_email_account(normalized_email)
        if account is None:
            raise EmailAuthError("invalid_credentials")

        await reset_email_password_and_revoke_sessions(account.user_id, raw_password)
        return await create_session(
            account.user_id,
            provider="email",
            provider_user_id=normalized_email,
            client_platform="web",
            ip_address=client_ip,
        )
