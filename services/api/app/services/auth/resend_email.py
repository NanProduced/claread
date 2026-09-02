"""Bounded Resend adapter for email-auth verification codes."""

import logging
from html import escape
from typing import Literal

import httpx

from app.config.settings import Settings, get_settings
from app.services.auth.email_address import normalize_email_address

SendEmailResult = Literal["sent", "rejected", "uncertain"]
_EmailPurpose = Literal["register", "password_reset"]
_EmailOutcomeReason = Literal[
    "http_rejected",
    "request_timeout",
    "request_error",
    "http_conflict",
    "http_server_error",
    "http_redirect",
    "http_unexpected",
    "malformed_success",
]

_RESEND_EMAILS_URL = "https://api.resend.com/emails"
_REQUEST_TIMEOUT_SECONDS = 5.0
_ONE_TIME_COPY = "验证码仅可使用一次，请尽快完成验证。"
_COMMON_SAFETY = "请勿将验证码告知任何人。"
_AUTOMATED_COPY = "此邮件由 Claread 自动发送，请勿回复。"
_TAGLINE = "透读英文文章"
_SANS_FONT = (
    "-apple-system,BlinkMacSystemFont,'PingFang SC','Hiragino Sans GB',"
    "'Microsoft YaHei',sans-serif"
)
_SERIF_FONT = "Georgia,'Songti SC','STSong','Noto Serif SC',serif"
_WORDMARK_FONT = "'Bodoni 72',Didot,'Bodoni MT','Times New Roman',Georgia,serif"
_WORDMARK_CJK_FONT = "'Songti SC',STSong,'Noto Serif SC','Source Han Serif SC',serif"
_CODE_FONT = "ui-monospace,'SFMono-Regular',Consolas,monospace"
logger = logging.getLogger(__name__)


def _log_provider_outcome(
    *,
    outcome: SendEmailResult,
    reason: _EmailOutcomeReason | None = None,
    status_code: int | None = None,
) -> None:
    fields = ["event=email_provider_outcome", f"outcome={outcome}"]
    extra: dict[str, object] = {"event": "email_provider_outcome", "outcome": outcome}
    if reason is not None:
        fields.append(f"reason={reason}")
        extra["reason"] = reason
    if status_code is not None:
        fields.append(f"status_code={status_code}")
        extra["status_code"] = status_code
    logger.log(
        logging.INFO if outcome == "sent" else logging.WARNING,
        " ".join(fields),
        extra=extra,
    )


def _purpose_copy(purpose: _EmailPurpose) -> tuple[str, str, str, str, str]:
    if purpose == "register":
        return (
            "Claread 注册验证码",
            "使用此验证码完成 Claread 账号创建。",
            "完成账号创建",
            "请在 Claread 输入以下验证码，完成账号创建。",
            "如果不是你本人操作，忽略本邮件即可。",
        )
    return (
        "Claread 密码重置验证码",
        "使用此验证码重置你的 Claread 密码。",
        "重置账号密码",
        "请在 Claread 输入以下验证码，继续重置账号密码。",
        "如果不是你本人操作，忽略本邮件即可，你的密码不会因此发生变化。",
    )


def _text_body(heading: str, intro: str, safety: str, code: str) -> str:
    return (
        "Claread 透读\n"
        f"{_TAGLINE}\n\n"
        f"{heading}\n"
        f"{intro}\n\n"
        f"一次性验证码：{code}\n"
        f"{_ONE_TIME_COPY}\n\n"
        "安全提醒\n"
        f"{_COMMON_SAFETY}\n"
        f"{safety}\n\n"
        f"{_AUTOMATED_COPY}\n"
    )


def _html_body(
    subject: str,
    preheader: str,
    heading: str,
    intro: str,
    safety: str,
    code: str,
) -> str:
    safe_subject = escape(subject)
    safe_preheader = escape(preheader)
    safe_heading = escape(heading)
    safe_intro = escape(intro)
    safe_safety = escape(safety)
    safe_code = escape(code)
    return (
        "<!DOCTYPE html>"
        '<html lang="zh-CN">'
        "<head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{safe_subject}</title>"
        "</head>"
        '<body style="margin:0;padding:0;background-color:#F6F3EC;">'
        '<div style="display:none;max-height:0;max-width:0;overflow:hidden;'
        "opacity:0;mso-hide:all;font-size:1px;line-height:1px;color:#F6F3EC;\">"
        f"{safe_preheader}</div>"
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'width="100%" style="background-color:#F6F3EC;">'
        '<tr><td align="center" style="padding:32px 16px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'width="560" style="width:100%;max-width:560px;background-color:#FFFFFF;'
        'border:1px solid #EAE7DF;border-radius:4px;">'
        '<tr><td style="padding:28px 32px 0 32px;color:#111111;">'
        '<p aria-label="Claread 透读" style="margin:0;line-height:30px;">'
        '<span style="font-size:28px;font-weight:400;color:#111111;'
        f'font-family:{_WORDMARK_FONT};letter-spacing:-0.02em;">Claread</span>'
        '<span style="margin-left:6px;font-size:18px;font-weight:400;color:#6B6E77;'
        f'font-family:{_WORDMARK_CJK_FONT};letter-spacing:0;">透读</span></p>'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'width="100%" style="margin-top:18px;">'
        '<tr><td width="40" height="2" style="width:40px;height:2px;'
        'font-size:0;line-height:0;background-color:#155CFF;">&nbsp;</td>'
        '<td height="2" style="height:2px;font-size:0;line-height:0;'
        'background-color:#EAE7DF;">&nbsp;</td></tr></table>'
        "</td></tr>"
        '<tr><td style="padding:30px 32px 32px 32px;color:#111111;'
        f'font-family:{_SANS_FONT};">'
        '<h1 style="margin:0 0 12px 0;font-size:28px;line-height:36px;'
        f'font-weight:700;font-family:{_SERIF_FONT};letter-spacing:-0.02em;'
        f'color:#111111;">{safe_heading}</h1>'
        f'<p style="margin:0 0 24px 0;font-size:15px;line-height:25px;'
        f'color:#4A4A4A;">{safe_intro}</p>'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'width="100%" style="background-color:#FAF9F6;border:1px solid #EAE7DF;'
        'border-radius:4px;">'
        '<tr><td style="padding:22px 24px 20px 24px;">'
        '<p style="margin:0 0 8px 0;font-size:12px;line-height:18px;'
        'font-weight:600;color:#6B6E77;">一次性验证码</p>'
        f'<p style="margin:0;font-size:34px;line-height:44px;font-weight:700;'
        f"letter-spacing:0.16em;font-family:{_CODE_FONT};color:#111111;\">"
        f"{safe_code}</p>"
        f'<p style="margin:10px 0 0 0;font-size:13px;line-height:21px;'
        f'color:#4A4A4A;">{escape(_ONE_TIME_COPY)}</p>'
        "</td></tr></table>"
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'width="100%" style="margin-top:28px;border-top:1px solid #EAE7DF;">'
        '<tr><td style="padding:22px 0 0 0;">'
        '<h2 style="margin:0 0 8px 0;font-size:14px;line-height:22px;'
        'font-weight:700;color:#111111;">安全提醒</h2>'
        f'<p style="margin:0;font-size:13px;line-height:22px;color:#4A4A4A;">'
        f"{escape(_COMMON_SAFETY)}<br>{safe_safety}</p>"
        "</td></tr></table>"
        "</td></tr>"
        '<tr><td style="padding:20px 32px 22px 32px;background-color:#FAF9F6;'
        'border-top:1px solid #EAE7DF;">'
        f'<p style="margin:0 0 5px 0;font-size:14px;line-height:21px;'
        f'font-family:{_SERIF_FONT};font-weight:700;color:#111111;">{_TAGLINE}</p>'
        f'<p style="margin:0;font-size:12px;line-height:20px;font-family:{_SANS_FONT};'
        f'color:#6B6E77;">{escape(_AUTOMATED_COPY)}</p>'
        "</td></tr>"
        "</table>"
        "</td></tr></table>"
        "</body></html>"
    )


async def send_verification_email(
    *,
    recipient: str,
    purpose: _EmailPurpose,
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

    subject, preheader, heading, intro, safety = _purpose_copy(purpose)
    payload: dict[str, object] = {
        "from": resolved_settings.resend_from,
        "to": [normalized_recipient],
        "subject": subject,
        "text": _text_body(heading, intro, safety, code),
        "html": _html_body(subject, preheader, heading, intro, safety, code),
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
    except httpx.TimeoutException:
        _log_provider_outcome(outcome="uncertain", reason="request_timeout")
        return "uncertain"
    except httpx.RequestError:
        _log_provider_outcome(outcome="uncertain", reason="request_error")
        return "uncertain"

    status_code = response.status_code
    if 400 <= status_code < 500 and status_code != 409:
        _log_provider_outcome(
            outcome="rejected",
            reason="http_rejected",
            status_code=status_code,
        )
        return "rejected"
    if status_code == 409:
        _log_provider_outcome(
            outcome="uncertain",
            reason="http_conflict",
            status_code=status_code,
        )
        return "uncertain"
    if 500 <= status_code < 600:
        _log_provider_outcome(
            outcome="uncertain",
            reason="http_server_error",
            status_code=status_code,
        )
        return "uncertain"
    if 300 <= status_code < 400:
        _log_provider_outcome(
            outcome="uncertain",
            reason="http_redirect",
            status_code=status_code,
        )
        return "uncertain"
    if status_code != 200:
        _log_provider_outcome(
            outcome="uncertain",
            reason="http_unexpected",
            status_code=status_code,
        )
        return "uncertain"
    try:
        response_body = response.json()
    except (AttributeError, TypeError, ValueError):
        _log_provider_outcome(outcome="uncertain", reason="malformed_success")
        return "uncertain"
    if not isinstance(response_body, dict):
        _log_provider_outcome(outcome="uncertain", reason="malformed_success")
        return "uncertain"
    provider_id = response_body.get("id")
    if not isinstance(provider_id, str) or not provider_id.strip():
        _log_provider_outcome(outcome="uncertain", reason="malformed_success")
        return "uncertain"
    _log_provider_outcome(outcome="sent")
    return "sent"
