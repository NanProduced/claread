"""Bounded Resend adapter for email-auth verification codes."""

from typing import Literal

import httpx

from app.config.settings import Settings, get_settings
from app.services.auth.email_address import normalize_email_address

SendEmailResult = Literal["sent", "rejected", "uncertain"]

_RESEND_EMAILS_URL = "https://api.resend.com/emails"
_REQUEST_TIMEOUT_SECONDS = 5.0


async def send_verification_email(
    *,
    recipient: str,
    purpose: Literal["register", "password_reset"],
    code: str,
    challenge_id: str,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SendEmailResult:
    """Send one verification email and return only a stable delivery state."""
    if purpose not in ("register", "password_reset"):
        raise ValueError("Unsupported email purpose")
    if not (
        isinstance(code, str) and len(code) == 6 and code.isascii() and code.isdigit()
    ):
        raise ValueError("Invalid verification code")
    if not (
        isinstance(challenge_id, str)
        and len(challenge_id) == 32
        and challenge_id.isascii()
        and all(character.isalnum() or character in "-_" for character in challenge_id)
    ):
        raise ValueError("Invalid challenge ID")
    normalized_recipient = normalize_email_address(recipient)
    resolved_settings = settings if settings is not None else get_settings()
    api_key = resolved_settings.resend_api_key.get_secret_value().strip()
    if not api_key:
        raise RuntimeError("Resend API key is not configured")

    subject = "Your Claread verification code"
    text = (
        f"Your Claread verification code is: {code}\n\n"
        "Do not share this code with anyone.\n"
        "If you did not request this code, you can ignore this email.\n"
        "This is an automated message. Please do not reply."
    )
    html = (
        "<p>Your Claread verification code is:</p>"
        f"<p><strong>{code}</strong></p>"
        "<p>Do not share this code with anyone.</p>"
        "<p>If you did not request this code, you can ignore this email.</p>"
        "<p>This is an automated message. Please do not reply.</p>"
    )
    payload: dict[str, object] = {
        "from": resolved_settings.resend_from,
        "to": [normalized_recipient],
        "subject": subject,
        "text": text,
        "html": html,
    }
    if resolved_settings.resend_reply_to:
        payload["reply_to"] = resolved_settings.resend_reply_to
    request_transport = (
        transport if transport is not None else httpx.AsyncHTTPTransport(retries=0)
    )
    try:
        async with httpx.AsyncClient(
            transport=request_transport,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            response = await client.post(
                _RESEND_EMAILS_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "User-Agent": "Claread",
                    "Idempotency-Key": f"email-auth/{purpose}/{challenge_id}",
                },
                json=payload,
            )
    except httpx.RequestError:
        return "uncertain"

    if 400 <= response.status_code < 500 and response.status_code != 409:
        return "rejected"
    if response.status_code != 200:
        return "uncertain"
    try:
        provider_id = response.json().get("id")
    except (AttributeError, ValueError):
        return "uncertain"
    return "sent" if isinstance(provider_id, str) and provider_id.strip() else "uncertain"
