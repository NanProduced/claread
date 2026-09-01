"""邮箱认证的密码安全原语（AUTH-F2A）。

Spec: docs/superpowers/specs/2026-09-01-web-email-auth-redesign.md §5.2。

- Argon2id 由 argon2-cffi 提供，不自研密码学；
- 密码先做 NFC 规范化，不 trim、不改变大小写；
- 长度按 NFC 后的 Unicode code point 计，12–128，无字符组合规则；
- Argon2id 参数固定在 OWASP Password Storage Cheat Sheet 当前下限
  （19 MiB / 2 次迭代 / 1 并行度），不引入配置框架；后续在部署主机上
  校准参数时，登录成功后按 needs_rehash 升级即可。

AUTH-F2A-R1：normalize_password 与 verify_password 共用同一输入边界——
长度策略与 UTF-8 可编码性都在 normalize_password 中强制执行；JSON 传输
可产生的 unpaired surrogate 无法 UTF-8 编码，统一映射为固定消息的
InvalidPasswordError，不让 argon2-cffi 的 UnicodeEncodeError 泄漏出去。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

# OWASP Password Storage Cheat Sheet (Argon2id floor): m=19456 KiB (19 MiB),
# t=2, p=1。hash_len=32 / salt_len=16 保持库默认值。
_PASSWORD_HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)

_ARGON2ID_PREFIX = "$argon2id$"
_MIN_PASSWORD_CODEPOINTS = 12
_MAX_PASSWORD_CODEPOINTS = 128
_INVALID_PASSWORD_MESSAGE = "password must be 12 to 128 characters"
_NOT_ENCODABLE_PASSWORD_MESSAGE = "password must be valid unicode text"


class InvalidPasswordError(ValueError):
    """密码不符合策略的稳定领域错误；消息固定，不包含密码内容。"""


@dataclass(frozen=True)
class PasswordVerification:
    """verify_password 的结果；needs_rehash 仅在 valid=True 时有意义。"""

    valid: bool
    needs_rehash: bool


def normalize_password(raw: str) -> str:
    """NFC 规范化密码，并按规范化后的 code point 数校验 12–128 长度。

    不 trim、不改变大小写；超出范围或无法 UTF-8 编码（如 JSON escaped
    unpaired surrogate）抛出 InvalidPasswordError。
    """
    if not isinstance(raw, str):
        raise InvalidPasswordError(_INVALID_PASSWORD_MESSAGE)
    normalized = unicodedata.normalize("NFC", raw)
    if not (_MIN_PASSWORD_CODEPOINTS <= len(normalized) <= _MAX_PASSWORD_CODEPOINTS):
        raise InvalidPasswordError(_INVALID_PASSWORD_MESSAGE)
    try:
        normalized.encode("utf-8")
    except UnicodeEncodeError:
        raise InvalidPasswordError(_NOT_ENCODABLE_PASSWORD_MESSAGE) from None
    return normalized


def hash_password(raw: str) -> str:
    """规范化密码后用 Argon2id（随机 salt）生成完整编码哈希字符串。"""
    return _PASSWORD_HASHER.hash(normalize_password(raw))


def verify_password(raw: str, password_hash: str) -> PasswordVerification:
    """校验密码与 Argon2id 哈希，返回 valid 与 needs_rehash。

    密码输入先经 normalize_password 强制同一策略（长度与 UTF-8
    可编码性），策略违规时安全返回 valid=False。错误密码、损坏
    hash 或非 argon2id 类型的 hash 同样安全返回 valid=False，不抛
    异常、不泄露密码或哈希内容。argon2-cffi 的 verify 会按 hash
    字符串自身识别算法类型，因此必须显式拒绝非 argon2id 前缀的
    hash。
    """
    if not isinstance(raw, str) or not isinstance(password_hash, str):
        return PasswordVerification(valid=False, needs_rehash=False)
    if not password_hash.startswith(_ARGON2ID_PREFIX):
        return PasswordVerification(valid=False, needs_rehash=False)
    try:
        normalized = normalize_password(raw)
    except InvalidPasswordError:
        return PasswordVerification(valid=False, needs_rehash=False)
    try:
        _PASSWORD_HASHER.verify(password_hash, normalized)
    except (Argon2Error, InvalidHashError):
        return PasswordVerification(valid=False, needs_rehash=False)
    return PasswordVerification(
        valid=True,
        needs_rehash=_PASSWORD_HASHER.check_needs_rehash(password_hash),
    )
