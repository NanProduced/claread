"""设置或重置密码时使用的离线优先安全评估原语。"""

from __future__ import annotations

import hashlib
from typing import Literal

import httpx

from app.services.auth.passwords import normalize_password

PasswordSafetyResult = Literal[
    "ok",
    "common",
    "compromised",
    "hibp_unavailable",
]

_COMMON_PASSWORDS = frozenset(
    {
        "123456789012",
        "password1234",
        "passwordpassword",
        "qwertyuiop12",
    }
)
_HIBP_RANGE_URL = "https://api.pwnedpasswords.com/range/{}"
_HIBP_TIMEOUT_SECONDS = 3.0
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


async def evaluate_password_safety(
    raw: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> PasswordSafetyResult:
    """返回离线常见密码或 HIBP k-anonymity 安全评估结果。

    HIBP 不可用时返回 ``hibp_unavailable``，由调用方 fail-open；结果、异常
    与日志均不携带密码、SHA-1、查询前缀或泄露次数。
    """
    normalized = normalize_password(raw)
    if normalized.casefold() in _COMMON_PASSWORDS:
        return "common"

    digest = hashlib.sha1(  # noqa: S324 -- required by the HIBP range API
        normalized.encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    request = httpx.Request(
        "GET",
        _HIBP_RANGE_URL.format(prefix),
        headers={"User-Agent": "Claread", "Add-Padding": "true"},
        extensions={"timeout": httpx.Timeout(_HIBP_TIMEOUT_SECONDS).as_dict()},
    )
    active_transport = transport or httpx.AsyncHTTPTransport()
    try:
        try:
            response = await active_transport.handle_async_request(request)
            await response.aread()
            await response.aclose()
        finally:
            await active_transport.aclose()
    except httpx.RequestError:
        return "hibp_unavailable"
    if response.status_code != httpx.codes.OK:
        return "hibp_unavailable"

    lines = response.text.splitlines()
    if not lines:
        return "hibp_unavailable"
    compromised = False
    for line in lines:
        try:
            returned_suffix, count = line.split(":")
            breach_count = int(count)
        except ValueError:
            return "hibp_unavailable"
        if (
            len(returned_suffix) != 35
            or any(character not in _HEX_DIGITS for character in returned_suffix)
            or not count.isascii()
            or not count.isdecimal()
        ):
            return "hibp_unavailable"
        if returned_suffix.casefold() == suffix.casefold() and breach_count > 0:
            compromised = True
    return "compromised" if compromised else "ok"
