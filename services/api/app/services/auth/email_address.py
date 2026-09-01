"""邮箱认证的邮箱规范化原语（AUTH-F2A）。

行为：离线校验并规范化邮箱，同时保留身份语义相关的本地部分。

邮箱的语法、Unicode/IDNA 与域名规范化全部交给 email-validator
（check_deliverability=False，不做 DNS 或任何网络调用），并直接采用其
normalized 值作为身份唯一字符串；本模块不做全地址 casefold、Gmail
点号或 +tag 改写。非法输入映射为稳定领域错误，不暴露依赖异常文本，
也不记录完整邮箱。
"""

from __future__ import annotations

from email_validator import EmailNotValidError, validate_email

_INVALID_EMAIL_ADDRESS_MESSAGE = "email address is invalid"


class InvalidEmailAddressError(ValueError):
    """非法邮箱地址的稳定领域错误；消息固定，不包含输入内容。"""


def normalize_email_address(raw: str) -> str:
    """校验并规范化邮箱地址，返回可用作身份唯一键的稳定字符串。

    域名小写并按 IDNA 规范化（punycode 输入统一为其 Unicode 小写形式），
    本地部分保留大小写、``+tag`` 与 Gmail 点号。本函数不发起 DNS 或任何
    网络调用。
    """
    if not isinstance(raw, str):
        raise InvalidEmailAddressError(_INVALID_EMAIL_ADDRESS_MESSAGE)
    try:
        validated = validate_email(raw, check_deliverability=False)
    except EmailNotValidError:
        raise InvalidEmailAddressError(_INVALID_EMAIL_ADDRESS_MESSAGE) from None
    normalized = validated.normalized
    if not isinstance(normalized, str) or not normalized:
        raise InvalidEmailAddressError(_INVALID_EMAIL_ADDRESS_MESSAGE)
    return normalized
