"""Redis-backed email verification challenges and one-time flow tickets."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Awaitable
from dataclasses import dataclass, field
from ipaddress import ip_address
from typing import Literal, cast

from redis.asyncio import Redis

from app.config.settings import Settings
from app.services.auth.email_address import normalize_email_address

Purpose = Literal["register", "password_reset"]
AuthAttemptFlow = Literal["login", "register", "password_reset"]
AuthAttemptSubjectKind = Literal["email", "ticket"]

_PURPOSES = frozenset({"register", "password_reset"})
_AUTH_ATTEMPT_FLOWS = frozenset({"login", "register", "password_reset"})
_RATE_LIMIT_CODES = frozenset({"email_cooldown", "email_hourly_limit", "ip_hourly_limit"})
_CHALLENGE_TTL_SECONDS = 10 * 60
_TICKET_TTL_SECONDS = 15 * 60
_MAX_CODE_ATTEMPTS = 5
_CHALLENGE_ID_LENGTH = 32
_TICKET_LENGTH = 43
_KEY_PREFIX = "auth:email:{email-auth}:"
_CHALLENGE_KEY_PREFIX = f"{_KEY_PREFIX}challenge:v1:"

_CREATE_CHALLENGE_LUA = """-- email-auth:create-challenge:v1
local now_parts = redis.call('TIME')
local now = tonumber(now_parts[1]) + tonumber(now_parts[2]) / 1000000
local cutoff = now - 3600
redis.call('ZREMRANGEBYSCORE', KEYS[3], '-inf', cutoff)
redis.call('ZREMRANGEBYSCORE', KEYS[4], '-inf', cutoff)

local latest = redis.call('ZREVRANGE', KEYS[3], 0, 0, 'WITHSCORES')
if #latest > 0 and now - tonumber(latest[2]) < tonumber(ARGV[6]) then
    return {'rate_limited', 'email_cooldown',
            tostring(math.max(1, math.ceil(tonumber(ARGV[6]) - (now - tonumber(latest[2])))))}
end

if redis.call('ZCARD', KEYS[3]) >= tonumber(ARGV[7]) then
    local oldest = redis.call('ZRANGE', KEYS[3], 0, 0, 'WITHSCORES')
    return {'rate_limited', 'email_hourly_limit',
            tostring(math.max(1, math.ceil(tonumber(oldest[2]) + 3600 - now)))}
end

if redis.call('ZCARD', KEYS[4]) >= tonumber(ARGV[8]) then
    local oldest = redis.call('ZRANGE', KEYS[4], 0, 0, 'WITHSCORES')
    return {'rate_limited', 'ip_hourly_limit',
            tostring(math.max(1, math.ceil(tonumber(oldest[2]) + 3600 - now)))}
end

redis.call('HSET', KEYS[2],
    'purpose', ARGV[2],
    'email', ARGV[3],
    'code_digest', ARGV[4],
    'failures', 0,
    'created_at', tostring(math.floor(now)))
redis.call('EXPIRE', KEYS[2], ARGV[5])
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[5])
redis.call('ZADD', KEYS[3], now, ARGV[1])
redis.call('ZADD', KEYS[4], now, ARGV[1])
redis.call('EXPIRE', KEYS[3], 3600)
redis.call('EXPIRE', KEYS[4], 3600)
return {'ok'}
"""

_DISCARD_CHALLENGE_LUA = """-- email-auth:discard-challenge:v1
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('DEL', KEYS[1])
end
local removed = redis.call('DEL', KEYS[2])
if removed == 1 then
    return {'discarded'}
end
return {'missing'}
"""

_VERIFY_CHALLENGE_LUA = """-- email-auth:verify-challenge:v1
local stored_digest = redis.call('HGET', KEYS[2], 'code_digest')
if not stored_digest or redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return {'invalid'}
end
if stored_digest ~= ARGV[2] then
    local failures = redis.call('HINCRBY', KEYS[2], 'failures', 1)
    if failures >= tonumber(ARGV[3]) then
        redis.call('DEL', KEYS[2])
        if redis.call('GET', KEYS[1]) == ARGV[1] then
            redis.call('DEL', KEYS[1])
        end
    end
    return {'invalid'}
end

redis.call('DEL', KEYS[2])
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('DEL', KEYS[1])
end
local now_parts = redis.call('TIME')
redis.call('HSET', KEYS[3],
    'purpose', ARGV[4],
    'email', ARGV[5],
    'created_at', now_parts[1])
redis.call('EXPIRE', KEYS[3], ARGV[6])
return {'ok'}
"""

_CONSUME_TICKET_LUA = """-- email-auth:consume-ticket:v1
local purpose = redis.call('HGET', KEYS[1], 'purpose')
if not purpose then
    return {'invalid'}
end
if purpose ~= ARGV[1] then
    return {'purpose_mismatch'}
end
local email = redis.call('HGET', KEYS[1], 'email')
if not email then
    return {'invalid'}
end
redis.call('DEL', KEYS[1])
return {'ok', email}
"""

_AUTH_ATTEMPT_LUA = """-- email-auth:attempt-limit:v1
local now_parts = redis.call('TIME')
local now = tonumber(now_parts[1]) + tonumber(now_parts[2]) / 1000000
local window = tonumber(ARGV[1])
local cutoff = now - window
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', cutoff)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', cutoff)

local retry_after = 0
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then
    local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    retry_after = math.max(
        retry_after,
        math.ceil(tonumber(oldest[2]) + window - now)
    )
end
if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[3]) then
    local oldest = redis.call('ZRANGE', KEYS[2], 0, 0, 'WITHSCORES')
    retry_after = math.max(
        retry_after,
        math.ceil(tonumber(oldest[2]) + window - now)
    )
end
if retry_after > 0 then
    return {'rate_limited', tostring(math.max(1, retry_after))}
end

redis.call('ZADD', KEYS[1], now, ARGV[4] .. ':subject')
redis.call('ZADD', KEYS[2], now, ARGV[4] .. ':ip')
redis.call('EXPIRE', KEYS[1], math.ceil(window))
redis.call('EXPIRE', KEYS[2], math.ceil(window))
return {'ok'}
"""

_CLEAR_LOGIN_ATTEMPTS_LUA = """-- email-auth:clear-login-attempts:v1
redis.call('DEL', KEYS[1])
return {'ok'}
"""


class EmailAuthStateError(RuntimeError):
    """Stable, non-sensitive email-auth state-machine failure."""

    def __init__(self, code: str, *, retry_after: int | None = None) -> None:
        self.code = code
        self.retry_after = retry_after
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ChallengeCreated:
    challenge_id: str
    code: str = field(repr=False)
    resend_after: int
    expires_in: int = _CHALLENGE_TTL_SECONDS


@dataclass(frozen=True, slots=True)
class ChallengeState:
    purpose: Purpose
    email: str


@dataclass(frozen=True, slots=True)
class TicketIssued:
    ticket: str = field(repr=False)
    purpose: Purpose
    expires_in: int = _TICKET_TTL_SECONDS


class EmailAuthChallengeService:
    def __init__(self, redis_client: Redis | None, settings: Settings) -> None:
        try:
            secret = settings.email_auth_code_hmac_secret.get_secret_value().encode("utf-8")
        except UnicodeEncodeError:
            raise EmailAuthStateError("invalid_configuration") from None
        if len(secret) < 32:
            raise EmailAuthStateError("invalid_configuration")
        self._redis = redis_client
        self._secret = secret
        self._email_cooldown_seconds = settings.email_auth_email_cooldown_seconds
        self._email_hourly_limit = settings.email_auth_email_hourly_limit
        self._ip_hourly_limit = settings.email_auth_ip_hourly_limit
        self._attempt_window_seconds = settings.email_auth_attempt_window_seconds
        self._attempt_limit = settings.email_auth_attempt_limit
        self._ip_attempt_limit = settings.email_auth_ip_attempt_limit

    async def create_challenge(
        self,
        *,
        email: str,
        purpose: str,
        client_ip: str,
    ) -> ChallengeCreated:
        normalized_purpose = self._purpose(purpose)
        normalized_email = normalize_email_address(email)
        try:
            normalized_ip = ip_address(client_ip).compressed
        except ValueError:
            raise EmailAuthStateError("invalid_client_ip") from None

        challenge_id = secrets.token_urlsafe(24)
        code = f"{secrets.randbelow(1_000_000):06d}"
        email_token = self._digest("email-key", normalized_email)
        ip_token = self._digest("ip-key", normalized_ip)
        response = await self._eval(
            _CREATE_CHALLENGE_LUA,
            [
                f"{_KEY_PREFIX}active:v1:{normalized_purpose}:{email_token}",
                f"{_CHALLENGE_KEY_PREFIX}{challenge_id}",
                f"{_KEY_PREFIX}rate:v1:email:{email_token}",
                f"{_KEY_PREFIX}rate:v1:ip:{ip_token}",
            ],
            [
                challenge_id,
                normalized_purpose,
                normalized_email,
                self._digest("code", challenge_id, normalized_purpose, normalized_email, code),
                _CHALLENGE_TTL_SECONDS,
                self._email_cooldown_seconds,
                self._email_hourly_limit,
                self._ip_hourly_limit,
            ],
        )
        status = self._text(response[0]) if response else ""
        if status == "rate_limited" and len(response) == 3:
            rate_code = self._text(response[1])
            if rate_code not in _RATE_LIMIT_CODES:
                raise EmailAuthStateError("backend_unavailable")
            try:
                retry_after = max(1, int(self._text(response[2])))
            except ValueError:
                raise EmailAuthStateError("backend_unavailable") from None
            raise EmailAuthStateError(
                rate_code,
                retry_after=retry_after,
            )
        if status != "ok":
            raise EmailAuthStateError("backend_unavailable")
        return ChallengeCreated(
            challenge_id=challenge_id,
            code=code,
            resend_after=self._email_cooldown_seconds,
        )

    async def discard_challenge(self, challenge_id: str) -> bool:
        if not self._is_token(challenge_id, _CHALLENGE_ID_LENGTH):
            return False
        challenge_key = f"{_CHALLENGE_KEY_PREFIX}{challenge_id}"
        state = await self._hgetall(challenge_key)
        if not state:
            return False
        purpose = state.get("purpose")
        email = state.get("email")
        if purpose not in _PURPOSES or not email:
            raise EmailAuthStateError("backend_unavailable")
        email_token = self._digest("email-key", email)
        response = await self._eval(
            _DISCARD_CHALLENGE_LUA,
            [f"{_KEY_PREFIX}active:v1:{purpose}:{email_token}", challenge_key],
            [challenge_id],
        )
        status = self._text(response[0]) if response else ""
        if status not in {"discarded", "missing"}:
            raise EmailAuthStateError("backend_unavailable")
        return status == "discarded"

    async def read_challenge(self, challenge_id: str) -> ChallengeState | None:
        """Return the challenge's purpose and email without its code material."""
        if not self._is_token(challenge_id, _CHALLENGE_ID_LENGTH):
            return None
        state = await self._hgetall(f"{_CHALLENGE_KEY_PREFIX}{challenge_id}")
        purpose = state.get("purpose")
        email = state.get("email")
        if purpose not in _PURPOSES or not email:
            return None
        return ChallengeState(purpose=cast(Purpose, purpose), email=email)

    async def verify_challenge(
        self,
        *,
        challenge_id: str,
        code: str,
        ticket_purpose: str | None = None,
    ) -> TicketIssued:
        if not self._is_token(challenge_id, _CHALLENGE_ID_LENGTH):
            raise EmailAuthStateError("invalid_or_expired_code")
        challenge_key = f"{_CHALLENGE_KEY_PREFIX}{challenge_id}"
        state = await self._hgetall(challenge_key)
        purpose = state.get("purpose")
        email = state.get("email")
        if purpose not in _PURPOSES or not email:
            raise EmailAuthStateError("invalid_or_expired_code")
        resolved_purpose = self._purpose(purpose if ticket_purpose is None else ticket_purpose)

        candidate = code if self._is_code(code) else "invalid"
        email_token = self._digest("email-key", email)
        ticket = secrets.token_urlsafe(32)
        ticket_token = self._digest("ticket", ticket)
        response = await self._eval(
            _VERIFY_CHALLENGE_LUA,
            [
                f"{_KEY_PREFIX}active:v1:{purpose}:{email_token}",
                challenge_key,
                f"{_KEY_PREFIX}ticket:v1:{ticket_token}",
            ],
            [
                challenge_id,
                self._digest("code", challenge_id, purpose, email, candidate),
                _MAX_CODE_ATTEMPTS,
                resolved_purpose,
                email,
                _TICKET_TTL_SECONDS,
            ],
        )
        status = self._text(response[0]) if response else ""
        if status == "invalid":
            raise EmailAuthStateError("invalid_or_expired_code")
        if status != "ok":
            raise EmailAuthStateError("backend_unavailable")
        return TicketIssued(ticket=ticket, purpose=resolved_purpose)

    async def consume_ticket(self, ticket: str, *, expected_purpose: str) -> str:
        purpose = self._purpose(expected_purpose)
        if not self._is_token(ticket, _TICKET_LENGTH):
            raise EmailAuthStateError("ticket_invalid_or_expired")
        ticket_token = self._digest("ticket", ticket)
        response = await self._eval(
            _CONSUME_TICKET_LUA,
            [f"{_KEY_PREFIX}ticket:v1:{ticket_token}"],
            [purpose],
        )
        status = self._text(response[0]) if response else ""
        if status == "invalid":
            raise EmailAuthStateError("ticket_invalid_or_expired")
        if status == "purpose_mismatch":
            raise EmailAuthStateError("ticket_purpose_mismatch")
        if status != "ok" or len(response) != 2:
            raise EmailAuthStateError("backend_unavailable")
        return self._text(response[1])

    async def check_auth_attempt(
        self,
        *,
        subject: str,
        subject_kind: AuthAttemptSubjectKind,
        flow: AuthAttemptFlow,
        client_ip: str,
    ) -> None:
        if flow not in _AUTH_ATTEMPT_FLOWS:
            raise EmailAuthStateError("invalid_purpose")
        if subject_kind == "email":
            subject_value = normalize_email_address(subject)
            subject_digest = self._digest("auth-attempt-email", subject_value)
        elif subject_kind == "ticket":
            subject_digest = self._digest("auth-attempt-ticket", flow, subject)
        else:
            raise EmailAuthStateError("invalid_purpose")
        try:
            normalized_ip = ip_address(client_ip).compressed
        except ValueError:
            raise EmailAuthStateError("invalid_client_ip") from None
        ip_digest = self._digest("auth-attempt-ip", normalized_ip)
        response = await self._eval(
            _AUTH_ATTEMPT_LUA,
            [
                f"{_KEY_PREFIX}attempt:v1:{flow}:subject:{subject_digest}",
                f"{_KEY_PREFIX}attempt:v1:{flow}:ip:{ip_digest}",
            ],
            [
                self._attempt_window_seconds,
                self._attempt_limit,
                self._ip_attempt_limit,
                secrets.token_hex(16),
            ],
        )
        status = self._text(response[0]) if response else ""
        if status == "rate_limited" and len(response) == 2:
            try:
                retry_after = max(1, int(self._text(response[1])))
            except ValueError:
                raise EmailAuthStateError("backend_unavailable") from None
            raise EmailAuthStateError("auth_attempt_limit", retry_after=retry_after)
        if status != "ok":
            raise EmailAuthStateError("backend_unavailable")

    async def clear_login_email_attempts(self, email: str) -> None:
        normalized_email = normalize_email_address(email)
        subject_digest = self._digest("auth-attempt-email", normalized_email)
        response = await self._eval(
            _CLEAR_LOGIN_ATTEMPTS_LUA,
            [f"{_KEY_PREFIX}attempt:v1:login:subject:{subject_digest}"],
            [],
        )
        if not response or self._text(response[0]) != "ok":
            raise EmailAuthStateError("backend_unavailable")

    async def _hgetall(self, key: str) -> dict[str, str]:
        if self._redis is None:
            raise EmailAuthStateError("backend_unavailable")
        try:
            pending = self._redis.hgetall(key)
            result = await cast(Awaitable[dict[object, object]], pending)
        except Exception:
            raise EmailAuthStateError("backend_unavailable") from None
        if not isinstance(result, dict):
            raise EmailAuthStateError("backend_unavailable")
        return {self._text(key): self._text(value) for key, value in result.items()}

    async def _eval(self, script: str, keys: list[str], args: list[object]) -> list[object]:
        if self._redis is None:
            raise EmailAuthStateError("backend_unavailable")
        try:
            serialized = [str(value) for value in (*keys, *args)]
            pending = self._redis.eval(script, len(keys), *serialized)
            result = await cast(Awaitable[object], pending)
        except Exception:
            raise EmailAuthStateError("backend_unavailable") from None
        if not isinstance(result, list):
            raise EmailAuthStateError("backend_unavailable")
        return cast(list[object], result)

    def _digest(self, domain: str, *parts: str) -> str:
        digest = hmac.new(self._secret, digestmod=hashlib.sha256)
        for part in (domain, *parts):
            encoded = part.encode("utf-8")
            digest.update(len(encoded).to_bytes(4, "big"))
            digest.update(encoded)
        return digest.hexdigest()

    @staticmethod
    def _purpose(purpose: str) -> Purpose:
        if purpose not in _PURPOSES:
            raise EmailAuthStateError("invalid_purpose")
        return cast(Purpose, purpose)

    @staticmethod
    def _is_code(code: object) -> bool:
        return (
            isinstance(code, str)
            and len(code) == 6
            and code.isascii()
            and code.isdigit()
        )

    @staticmethod
    def _is_token(value: object, length: int) -> bool:
        return (
            isinstance(value, str)
            and len(value) == length
            and value.isascii()
            and all(character.isalnum() or character in "-_" for character in value)
        )

    @staticmethod
    def _text(value: object) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)
