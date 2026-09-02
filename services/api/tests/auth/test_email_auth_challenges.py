"""Email challenge/ticket state machine against a deterministic async Redis fake."""

from __future__ import annotations

import asyncio
import math
import re
import traceback
from collections.abc import Iterable
from typing import Any

import pytest
from pydantic import SecretStr
from redis.cluster import key_slot

from app.config.settings import Settings
from app.services.auth import email_challenges as email_challenges_module
from app.services.auth.email_challenges import (
    ChallengeCreated,
    ChallengeState,
    EmailAuthChallengeService,
    EmailAuthStateError,
    TicketIssued,
)

pytestmark = [
    pytest.mark.chain_auth,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
    pytest.mark.no_network_default,
]

HMAC_SECRET = "hmac-secret-with-at-least-thirty-two-bytes"
EMAIL = "Reader@example.com"
IP_ADDRESS = "203.0.113.42"
FIXED_CODE = "482731"


@pytest.fixture(autouse=True)
def fixed_verification_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.auth.email_challenges.secrets.randbelow",
        lambda _upper_bound: int(FIXED_CODE),
    )


class FakeRedis:
    """Small deterministic interpreter for this module's Lua state transitions."""

    def __init__(self) -> None:
        self.now = 1_800_000_000.0
        self.strings: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.expires_at: dict[str, float] = {}
        self.error: BaseException | None = None
        self.eval_calls: list[tuple[str, tuple[str, ...]]] = []
        self._lock = asyncio.Lock()
        self.pause_next_hgetall = False
        self.hgetall_started = asyncio.Event()
        self.hgetall_continue = asyncio.Event()

    async def eval(self, script: str, numkeys: int, *values: object) -> list[object]:
        async with self._lock:
            keys = [str(value) for value in values[:numkeys]]
            args = [str(value) for value in values[numkeys:]]
            self.eval_calls.append((script, tuple(keys)))
            if self.error is not None:
                raise self.error
            self._purge()
            if script.startswith("-- email-auth:create-challenge:v1"):
                return self._create_challenge(keys, args)
            if script.startswith("-- email-auth:discard-challenge:v1"):
                return self._discard_challenge(keys, args)
            if script.startswith("-- email-auth:verify-challenge:v1"):
                return self._verify_challenge(keys, args)
            if script.startswith("-- email-auth:consume-ticket:v1"):
                return self._consume_ticket(keys, args)
            if script.startswith("-- email-auth:attempt-limit:v1"):
                return self._check_auth_attempt(keys, args)
            if script.startswith("-- email-auth:clear-login-attempts:v1"):
                return self._clear_login_email_attempts(keys, args)
            raise AssertionError("unexpected Lua script")

    async def hgetall(self, key: str) -> dict[str, str]:
        async with self._lock:
            if self.error is not None:
                raise self.error
            self._purge()
            result = dict(self.hashes.get(key, {}))
        if self.pause_next_hgetall:
            self.pause_next_hgetall = False
            self.hgetall_started.set()
            await self.hgetall_continue.wait()
        await asyncio.sleep(0)
        return result

    def advance(self, seconds: float) -> None:
        self.now += seconds
        self._purge()

    def ttl(self, key: str) -> int:
        self._purge()
        if key not in self.expires_at:
            return -2
        return max(0, math.ceil(self.expires_at[key] - self.now))

    def keys(self) -> set[str]:
        self._purge()
        return set(self.strings) | set(self.hashes) | set(self.zsets)

    def state_text(self) -> str:
        self._purge()
        return repr((self.strings, self.hashes, self.zsets))

    def _purge(self) -> None:
        expired = [key for key, expiry in self.expires_at.items() if expiry <= self.now]
        for key in expired:
            self.strings.pop(key, None)
            self.hashes.pop(key, None)
            self.zsets.pop(key, None)
            self.expires_at.pop(key, None)

    def _expire(self, keys: Iterable[str], seconds: int) -> None:
        for key in keys:
            self.expires_at[key] = self.now + seconds

    def _create_challenge(self, keys: list[str], args: list[str]) -> list[object]:
        active_key, challenge_key, email_rate_key, ip_rate_key = keys
        (
            challenge_id,
            purpose,
            email,
            code_digest,
            challenge_ttl,
            cooldown,
            email_limit,
            ip_limit,
        ) = args
        cutoff = self.now - 3600
        for rate_key in (email_rate_key, ip_rate_key):
            members = self.zsets.setdefault(rate_key, {})
            self.zsets[rate_key] = {
                member: score for member, score in members.items() if score > cutoff
            }

        email_scores = sorted(self.zsets[email_rate_key].values())
        ip_scores = sorted(self.zsets[ip_rate_key].values())
        cooldown_seconds = int(cooldown)
        if email_scores and self.now - email_scores[-1] < cooldown_seconds:
            return [
                "rate_limited",
                "email_cooldown",
                math.ceil(cooldown_seconds - (self.now - email_scores[-1])),
            ]
        if len(email_scores) >= int(email_limit):
            retry_after = math.ceil(email_scores[0] + 3600 - self.now)
            return ["rate_limited", "email_hourly_limit", retry_after]
        if len(ip_scores) >= int(ip_limit):
            retry_after = math.ceil(ip_scores[0] + 3600 - self.now)
            return ["rate_limited", "ip_hourly_limit", retry_after]

        self.hashes[challenge_key] = {
            "purpose": purpose,
            "email": email,
            "code_digest": code_digest,
            "failures": "0",
            "created_at": str(int(self.now)),
        }
        self.strings[active_key] = challenge_id
        self.zsets[email_rate_key][challenge_id] = self.now
        self.zsets[ip_rate_key][challenge_id] = self.now
        self._expire((challenge_key, active_key), int(challenge_ttl))
        self._expire((email_rate_key, ip_rate_key), 3600)
        return ["ok"]

    def _discard_challenge(self, keys: list[str], args: list[str]) -> list[object]:
        active_key, challenge_key = keys
        (challenge_id,) = args
        if self.strings.get(active_key) == challenge_id:
            self.strings.pop(active_key, None)
            self.expires_at.pop(active_key, None)
        removed = challenge_key in self.hashes
        self.hashes.pop(challenge_key, None)
        self.expires_at.pop(challenge_key, None)
        return ["discarded" if removed else "missing"]

    def _verify_challenge(self, keys: list[str], args: list[str]) -> list[object]:
        active_key, challenge_key, ticket_key = keys
        challenge_id, candidate_digest, max_attempts, purpose, email, ticket_ttl = args
        challenge = self.hashes.get(challenge_key)
        if challenge is None or self.strings.get(active_key) != challenge_id:
            return ["invalid"]
        if challenge["code_digest"] != candidate_digest:
            failures = int(challenge["failures"]) + 1
            challenge["failures"] = str(failures)
            if failures >= int(max_attempts):
                self.hashes.pop(challenge_key, None)
                self.expires_at.pop(challenge_key, None)
                if self.strings.get(active_key) == challenge_id:
                    self.strings.pop(active_key, None)
                    self.expires_at.pop(active_key, None)
            return ["invalid"]

        self.hashes.pop(challenge_key, None)
        self.expires_at.pop(challenge_key, None)
        if self.strings.get(active_key) == challenge_id:
            self.strings.pop(active_key, None)
            self.expires_at.pop(active_key, None)
        self.hashes[ticket_key] = {
            "purpose": purpose,
            "email": email,
            "created_at": str(int(self.now)),
        }
        self._expire((ticket_key,), int(ticket_ttl))
        return ["ok"]

    def _consume_ticket(self, keys: list[str], args: list[str]) -> list[object]:
        (ticket_key,) = keys
        (expected_purpose,) = args
        ticket = self.hashes.get(ticket_key)
        if ticket is None:
            return ["invalid"]
        if ticket["purpose"] != expected_purpose:
            return ["purpose_mismatch"]
        email = ticket["email"]
        self.hashes.pop(ticket_key, None)
        self.expires_at.pop(ticket_key, None)
        return ["ok", email]

    def _check_auth_attempt(self, keys: list[str], args: list[str]) -> list[object]:
        subject_key, ip_key = keys
        window, subject_limit, ip_limit, member = args
        window_seconds = float(window)
        cutoff = self.now - window_seconds
        for key in (subject_key, ip_key):
            members = self.zsets.setdefault(key, {})
            self.zsets[key] = {
                value: score for value, score in members.items() if score > cutoff
            }

        retry_after = 0
        for key, limit in ((subject_key, subject_limit), (ip_key, ip_limit)):
            scores = sorted(self.zsets[key].values())
            if scores and len(scores) >= int(limit):
                retry_after = max(retry_after, math.ceil(scores[0] + window_seconds - self.now))
        if retry_after:
            return ["rate_limited", max(1, retry_after)]

        self.zsets[subject_key][f"{member}:subject"] = self.now
        self.zsets[ip_key][f"{member}:ip"] = self.now
        self._expire((subject_key, ip_key), int(window_seconds))
        return ["ok"]

    def _clear_login_email_attempts(self, keys: list[str], args: list[str]) -> list[object]:
        (subject_key,) = keys
        self.zsets.pop(subject_key, None)
        self.expires_at.pop(subject_key, None)
        return ["ok"]


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "email_auth_enabled": True,
        "redis_enabled": True,
        "email_auth_code_hmac_secret": SecretStr(HMAC_SECRET),
        "email_auth_email_cooldown_seconds": 60,
        "email_auth_email_hourly_limit": 5,
        "email_auth_ip_hourly_limit": 30,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _challenge_key(redis: FakeRedis, challenge_id: str) -> str:
    return next(
        key
        for key in redis.keys()
        if ":challenge:v1:" in key and key.endswith(f":{challenge_id}")
    )


async def test_each_lua_eval_uses_one_fixed_redis_cluster_slot() -> None:
    redis = FakeRedis()
    service = EmailAuthChallengeService(redis, _settings())
    challenge = await service.create_challenge(
        email=EMAIL,
        purpose="register",
        client_ip=IP_ADDRESS,
    )
    ticket = await service.verify_challenge(
        challenge_id=challenge.challenge_id,
        code=challenge.code,
    )
    assert await service.consume_ticket(ticket.ticket, expected_purpose="register") == EMAIL
    redis.advance(60)
    disposable = await service.create_challenge(
        email=EMAIL,
        purpose="register",
        client_ip=IP_ADDRESS,
    )
    assert await service.discard_challenge(disposable.challenge_id) is True

    assert len(redis.eval_calls) == 5
    for _script, keys in redis.eval_calls:
        assert all("{email-auth}" in key for key in keys)
        assert len({key_slot(key.encode("utf-8")) for key in keys}) == 1


def test_create_lua_only_accesses_declared_keys() -> None:
    key_arguments = re.findall(
        r"redis\.call\(\s*'[^']+'\s*,\s*([^,\)\r\n]+)",
        email_challenges_module._CREATE_CHALLENGE_LUA,
    )

    assert key_arguments
    assert all(re.fullmatch(r"KEYS\[\d+\]", argument.strip()) for argument in key_arguments)


async def test_create_challenge_is_ttl_bound_deidentified_and_never_stores_code() -> None:
    redis = FakeRedis()
    service = EmailAuthChallengeService(redis, _settings())

    created = await service.create_challenge(
        email="Reader@EXAMPLE.com",
        purpose="register",
        client_ip=IP_ADDRESS,
    )

    assert len(created.challenge_id) == 32
    assert len(created.code) == 6 and created.code.isascii() and created.code.isdigit()
    challenge_key = _challenge_key(redis, created.challenge_id)
    active_key = next(key for key in redis.keys() if ":active:v1:" in key)
    assert redis.ttl(challenge_key) == 600
    assert redis.ttl(active_key) == 600
    assert redis.hashes[challenge_key]["purpose"] == "register"
    assert redis.hashes[challenge_key]["email"] == EMAIL
    assert all(EMAIL not in key and IP_ADDRESS not in key for key in redis.keys())
    assert created.code not in redis.state_text()
    assert created.code not in repr(created)
    assert HMAC_SECRET not in repr(service)


async def test_create_challenge_returns_configured_resend_after() -> None:
    redis = FakeRedis()
    service = EmailAuthChallengeService(
        redis, _settings(email_auth_email_cooldown_seconds=73)
    )

    created = await service.create_challenge(
        email=EMAIL,
        purpose="register",
        client_ip=IP_ADDRESS,
    )

    assert created.resend_after == 73
    assert created.expires_in == 600


async def test_stale_discard_cleans_old_hash_without_deleting_replacement() -> None:
    redis = FakeRedis()
    service = EmailAuthChallengeService(redis, _settings())
    old = await service.create_challenge(email=EMAIL, purpose="register", client_ip=IP_ADDRESS)
    redis.advance(60)
    redis.pause_next_hgetall = True

    discard = asyncio.create_task(service.discard_challenge(old.challenge_id))
    await redis.hgetall_started.wait()
    new = await service.create_challenge(email=EMAIL, purpose="register", client_ip=IP_ADDRESS)
    redis.hgetall_continue.set()

    assert await discard is True
    assert not any(key.endswith(f":{old.challenge_id}") for key in redis.keys())
    assert _challenge_key(redis, new.challenge_id) in redis.keys()
    active_key = next(key for key in redis.keys() if ":active:v1:" in key)
    assert redis.strings[active_key] == new.challenge_id


async def test_replacement_leaves_old_hash_until_original_ttl_but_cannot_verify() -> None:
    redis = FakeRedis()
    service = EmailAuthChallengeService(redis, _settings())
    old = await service.create_challenge(email=EMAIL, purpose="register", client_ip=IP_ADDRESS)
    old_key = _challenge_key(redis, old.challenge_id)
    redis.advance(60)
    new = await service.create_challenge(email=EMAIL, purpose="register", client_ip=IP_ADDRESS)

    assert old_key in redis.keys()
    assert redis.ttl(old_key) == 540
    with pytest.raises(EmailAuthStateError) as rejected:
        await service.verify_challenge(challenge_id=old.challenge_id, code=old.code)
    assert rejected.value.code == "invalid_or_expired_code"
    assert old_key in redis.keys()
    redis.advance(539)
    assert redis.ttl(old_key) == 1
    redis.advance(1)
    assert old_key not in redis.keys()
    assert _challenge_key(redis, new.challenge_id) in redis.keys()


async def test_concurrent_verify_issues_one_hidden_purpose_bound_ticket() -> None:
    redis = FakeRedis()
    service = EmailAuthChallengeService(redis, _settings())
    created = await service.create_challenge(email=EMAIL, purpose="register", client_ip=IP_ADDRESS)

    results = await asyncio.gather(
        service.verify_challenge(challenge_id=created.challenge_id, code=created.code),
        service.verify_challenge(challenge_id=created.challenge_id, code=created.code),
        return_exceptions=True,
    )

    issued = [result for result in results if isinstance(result, TicketIssued)]
    rejected = [result for result in results if isinstance(result, EmailAuthStateError)]
    assert len(issued) == 1
    assert len(rejected) == 1 and rejected[0].code == "invalid_or_expired_code"
    assert not any(key.endswith(f":{created.challenge_id}") for key in redis.keys())
    ticket_key = next(key for key in redis.keys() if ":ticket:v1:" in key)
    assert redis.ttl(ticket_key) == 900
    assert redis.hashes[ticket_key]["purpose"] == "register"
    assert redis.hashes[ticket_key]["email"] == EMAIL
    assert issued[0].ticket not in ticket_key
    assert issued[0].ticket not in redis.state_text()
    assert issued[0].ticket not in repr(issued[0])


async def test_verify_challenge_honors_ticket_purpose_override() -> None:
    redis = FakeRedis()
    service = EmailAuthChallengeService(redis, _settings())
    created = await service.create_challenge(
        email=EMAIL, purpose="register", client_ip=IP_ADDRESS
    )

    issued = await service.verify_challenge(
        challenge_id=created.challenge_id,
        code=created.code,
        ticket_purpose="password_reset",
    )

    assert issued.purpose == "password_reset"
    ticket_key = next(key for key in redis.keys() if ":ticket:v1:" in key)
    assert redis.hashes[ticket_key]["purpose"] == "password_reset"
    assert redis.hashes[ticket_key]["email"] == EMAIL
    assert await service.consume_ticket(issued.ticket, expected_purpose="password_reset") == EMAIL


async def test_read_challenge_exposes_purpose_and_email_without_code() -> None:
    redis = FakeRedis()
    service = EmailAuthChallengeService(redis, _settings())
    created = await service.create_challenge(
        email="Reader@EXAMPLE.com", purpose="register", client_ip=IP_ADDRESS
    )

    state = await service.read_challenge(created.challenge_id)

    assert state == ChallengeState(purpose="register", email=EMAIL)
    assert created.code not in repr(state)
    assert created.code not in redis.state_text()
    assert await service.read_challenge("invalid-shape") is None


async def test_ticket_rejects_cross_purpose_then_concurrent_consume_succeeds_once() -> None:
    redis = FakeRedis()
    service = EmailAuthChallengeService(redis, _settings())
    created = await service.create_challenge(email=EMAIL, purpose="register", client_ip=IP_ADDRESS)
    issued = await service.verify_challenge(challenge_id=created.challenge_id, code=created.code)

    with pytest.raises(EmailAuthStateError) as wrong_purpose:
        await service.consume_ticket(issued.ticket, expected_purpose="password_reset")
    assert wrong_purpose.value.code == "ticket_purpose_mismatch"

    results = await asyncio.gather(
        service.consume_ticket(issued.ticket, expected_purpose="register"),
        service.consume_ticket(issued.ticket, expected_purpose="register"),
        return_exceptions=True,
    )

    assert results.count(EMAIL) == 1
    rejected = [result for result in results if isinstance(result, EmailAuthStateError)]
    assert len(rejected) == 1 and rejected[0].code == "ticket_invalid_or_expired"
    assert not any(":ticket:v1:" in key for key in redis.keys())


async def test_fifth_wrong_code_deletes_challenge() -> None:
    redis = FakeRedis()
    service = EmailAuthChallengeService(redis, _settings())
    created = await service.create_challenge(email=EMAIL, purpose="register", client_ip=IP_ADDRESS)
    challenge_key = _challenge_key(redis, created.challenge_id)

    for expected_failures in range(1, 5):
        with pytest.raises(EmailAuthStateError) as rejected:
            await service.verify_challenge(challenge_id=created.challenge_id, code="000000")
        assert rejected.value.code == "invalid_or_expired_code"
        assert redis.hashes[challenge_key]["failures"] == str(expected_failures)

    with pytest.raises(EmailAuthStateError):
        await service.verify_challenge(challenge_id=created.challenge_id, code="000000")
    assert challenge_key not in redis.keys()
    assert not any(":active:v1:" in key for key in redis.keys())
    with pytest.raises(EmailAuthStateError) as exhausted:
        await service.verify_challenge(challenge_id=created.challenge_id, code=created.code)
    assert exhausted.value.code == "invalid_or_expired_code"


async def test_challenge_and_ticket_expire_at_contract_ttls() -> None:
    challenge_redis = FakeRedis()
    challenge_service = EmailAuthChallengeService(challenge_redis, _settings())
    challenge = await challenge_service.create_challenge(
        email=EMAIL,
        purpose="register",
        client_ip=IP_ADDRESS,
    )
    challenge_redis.advance(600)
    with pytest.raises(EmailAuthStateError) as expired_challenge:
        await challenge_service.verify_challenge(
            challenge_id=challenge.challenge_id,
            code=challenge.code,
        )
    assert expired_challenge.value.code == "invalid_or_expired_code"

    ticket_redis = FakeRedis()
    ticket_service = EmailAuthChallengeService(ticket_redis, _settings())
    challenge = await ticket_service.create_challenge(
        email=EMAIL,
        purpose="password_reset",
        client_ip=IP_ADDRESS,
    )
    ticket = await ticket_service.verify_challenge(
        challenge_id=challenge.challenge_id,
        code=challenge.code,
    )
    ticket_redis.advance(900)
    with pytest.raises(EmailAuthStateError) as expired_ticket:
        await ticket_service.consume_ticket(ticket.ticket, expected_purpose="password_reset")
    assert expired_ticket.value.code == "ticket_invalid_or_expired"


async def test_only_supported_purposes_are_accepted_and_active_slots_are_purpose_scoped() -> None:
    redis = FakeRedis()
    service = EmailAuthChallengeService(redis, _settings())
    with pytest.raises(EmailAuthStateError) as invalid:
        await service.create_challenge(email=EMAIL, purpose="login", client_ip=IP_ADDRESS)
    assert invalid.value.code == "invalid_purpose"
    assert redis.keys() == set()

    register = await service.create_challenge(
        email=EMAIL,
        purpose="register",
        client_ip=IP_ADDRESS,
    )
    redis.advance(60)
    password_reset = await service.create_challenge(
        email=EMAIL,
        purpose="password_reset",
        client_ip=IP_ADDRESS,
    )
    redis.advance(60)
    replacement = await service.create_challenge(
        email=EMAIL,
        purpose="register",
        client_ip=IP_ADDRESS,
    )

    assert _challenge_key(redis, register.challenge_id) in redis.keys()
    assert _challenge_key(redis, replacement.challenge_id) in redis.keys()
    assert _challenge_key(redis, password_reset.challenge_id) in redis.keys()
    with pytest.raises(EmailAuthStateError) as replaced:
        await service.verify_challenge(challenge_id=register.challenge_id, code=register.code)
    assert replaced.value.code == "invalid_or_expired_code"
    reset_ticket = await service.verify_challenge(
        challenge_id=password_reset.challenge_id,
        code=password_reset.code,
    )
    assert (
        await service.consume_ticket(reset_ticket.ticket, expected_purpose="password_reset")
        == EMAIL
    )


async def test_email_cooldown_is_atomic_and_returns_stable_retry_after() -> None:
    redis = FakeRedis()
    service = EmailAuthChallengeService(redis, _settings())
    first = await service.create_challenge(email=EMAIL, purpose="register", client_ip=IP_ADDRESS)

    with pytest.raises(EmailAuthStateError) as limited:
        await service.create_challenge(email=EMAIL, purpose="register", client_ip=IP_ADDRESS)
    assert limited.value.code == "email_cooldown"
    assert limited.value.retry_after == 60
    assert _challenge_key(redis, first.challenge_id) in redis.keys()

    redis.advance(59)
    with pytest.raises(EmailAuthStateError) as almost_ready:
        await service.create_challenge(email=EMAIL, purpose="register", client_ip=IP_ADDRESS)
    assert almost_ready.value.retry_after == 1
    redis.advance(1)
    replacement = await service.create_challenge(
        email=EMAIL,
        purpose="register",
        client_ip=IP_ADDRESS,
    )
    assert _challenge_key(redis, replacement.challenge_id) in redis.keys()


async def test_concurrent_create_admits_one_challenge_before_cooldown() -> None:
    redis = FakeRedis()
    service = EmailAuthChallengeService(redis, _settings())

    results = await asyncio.gather(
        service.create_challenge(email=EMAIL, purpose="register", client_ip=IP_ADDRESS),
        service.create_challenge(email=EMAIL, purpose="register", client_ip=IP_ADDRESS),
        return_exceptions=True,
    )

    assert sum(isinstance(result, ChallengeCreated) for result in results) == 1
    rejected = [result for result in results if isinstance(result, EmailAuthStateError)]
    assert len(rejected) == 1
    assert rejected[0].code == "email_cooldown"
    assert rejected[0].retry_after == 60
    assert sum(":challenge:v1:" in key for key in redis.keys()) == 1


async def test_email_hourly_limit_uses_a_rolling_window() -> None:
    redis = FakeRedis()
    service = EmailAuthChallengeService(redis, _settings())
    for index in range(5):
        if index:
            redis.advance(60)
        await service.create_challenge(email=EMAIL, purpose="register", client_ip=IP_ADDRESS)
    redis.advance(60)

    with pytest.raises(EmailAuthStateError) as limited:
        await service.create_challenge(email=EMAIL, purpose="register", client_ip=IP_ADDRESS)
    assert limited.value.code == "email_hourly_limit"
    assert limited.value.retry_after == 3300
    redis.advance(3299)
    with pytest.raises(EmailAuthStateError) as almost_ready:
        await service.create_challenge(email=EMAIL, purpose="register", client_ip=IP_ADDRESS)
    assert almost_ready.value.retry_after == 1
    redis.advance(1)
    await service.create_challenge(email=EMAIL, purpose="register", client_ip=IP_ADDRESS)


async def test_ip_hourly_limit_is_shared_across_deidentified_emails() -> None:
    redis = FakeRedis()
    service = EmailAuthChallengeService(redis, _settings())
    for index in range(30):
        await service.create_challenge(
            email=f"reader-{index}@example.com",
            purpose="register",
            client_ip=IP_ADDRESS,
        )

    with pytest.raises(EmailAuthStateError) as limited:
        await service.create_challenge(
            email="reader-blocked@example.com",
            purpose="register",
            client_ip=IP_ADDRESS,
        )
    assert limited.value.code == "ip_hourly_limit"
    assert limited.value.retry_after == 3600
    assert all(IP_ADDRESS not in key for key in redis.keys())
    redis.advance(3600)
    await service.create_challenge(
        email="reader-ready@example.com",
        purpose="register",
        client_ip=IP_ADDRESS,
    )


async def test_auth_attempt_limit_is_atomic_and_uses_deidentified_sliding_buckets() -> None:
    redis = FakeRedis()
    service = EmailAuthChallengeService(
        redis,
        _settings(
            email_auth_attempt_window_seconds=60,
            email_auth_attempt_limit=2,
            email_auth_ip_attempt_limit=30,
        ),
    )

    for _ in range(2):
        await service.check_auth_attempt(
            subject=EMAIL,
            subject_kind="email",
            flow="login",
            client_ip=IP_ADDRESS,
        )

    with pytest.raises(EmailAuthStateError) as limited:
        await service.check_auth_attempt(
            subject=EMAIL,
            subject_kind="email",
            flow="login",
            client_ip=IP_ADDRESS,
        )

    assert limited.value.code == "auth_attempt_limit"
    assert limited.value.retry_after == 60
    keys = redis.keys()
    assert any(":attempt:v1:login:subject:" in key for key in keys)
    assert any(":attempt:v1:login:ip:" in key for key in keys)
    assert all(EMAIL not in key and IP_ADDRESS not in key for key in keys)
    assert all("{email-auth}" in key for key in keys)

    redis.advance(59)
    with pytest.raises(EmailAuthStateError) as almost_ready:
        await service.check_auth_attempt(
            subject=EMAIL,
            subject_kind="email",
            flow="login",
            client_ip=IP_ADDRESS,
        )
    assert almost_ready.value.retry_after == 1
    redis.advance(1)
    await service.check_auth_attempt(
        subject=EMAIL,
        subject_kind="email",
        flow="login",
        client_ip=IP_ADDRESS,
    )


async def test_concurrent_auth_attempts_admit_only_the_configured_count() -> None:
    redis = FakeRedis()
    service = EmailAuthChallengeService(
        redis,
        _settings(
            email_auth_attempt_window_seconds=60,
            email_auth_attempt_limit=3,
            email_auth_ip_attempt_limit=30,
        ),
    )

    results = await asyncio.gather(
        *(
            service.check_auth_attempt(
                subject=EMAIL,
                subject_kind="email",
                flow="login",
                client_ip=IP_ADDRESS,
            )
            for _ in range(10)
        ),
        return_exceptions=True,
    )

    assert sum(result is None for result in results) == 3
    limited = [result for result in results if isinstance(result, EmailAuthStateError)]
    assert len(limited) == 7
    assert all(result.code == "auth_attempt_limit" for result in limited)
    assert all(result.retry_after == 60 for result in limited)


async def test_attempt_buckets_share_subject_across_ips_and_ip_across_subjects() -> None:
    subject_redis = FakeRedis()
    subject_service = EmailAuthChallengeService(
        subject_redis,
        _settings(email_auth_attempt_limit=2, email_auth_ip_attempt_limit=30),
    )
    for client_ip in ("198.51.100.1", "198.51.100.2"):
        await subject_service.check_auth_attempt(
            subject=EMAIL,
            subject_kind="email",
            flow="login",
            client_ip=client_ip,
        )
    with pytest.raises(EmailAuthStateError) as subject_limited:
        await subject_service.check_auth_attempt(
            subject=EMAIL,
            subject_kind="email",
            flow="login",
            client_ip="198.51.100.3",
        )
    assert subject_limited.value.code == "auth_attempt_limit"

    ip_redis = FakeRedis()
    ip_service = EmailAuthChallengeService(
        ip_redis,
        _settings(email_auth_attempt_limit=30, email_auth_ip_attempt_limit=2),
    )
    for email in ("one@example.com", "two@example.com"):
        await ip_service.check_auth_attempt(
            subject=email,
            subject_kind="email",
            flow="login",
            client_ip=IP_ADDRESS,
        )
    with pytest.raises(EmailAuthStateError) as ip_limited:
        await ip_service.check_auth_attempt(
            subject="three@example.com",
            subject_kind="email",
            flow="login",
            client_ip=IP_ADDRESS,
        )
    assert ip_limited.value.code == "auth_attempt_limit"


async def test_successful_login_clear_removes_only_email_bucket() -> None:
    redis = FakeRedis()
    service = EmailAuthChallengeService(
        redis,
        _settings(email_auth_attempt_limit=30, email_auth_ip_attempt_limit=2),
    )
    await service.check_auth_attempt(
        subject=EMAIL,
        subject_kind="email",
        flow="login",
        client_ip=IP_ADDRESS,
    )
    await service.check_auth_attempt(
        subject="other@example.com",
        subject_kind="email",
        flow="login",
        client_ip=IP_ADDRESS,
    )

    await service.clear_login_email_attempts(EMAIL)

    with pytest.raises(EmailAuthStateError) as ip_limited:
        await service.check_auth_attempt(
            subject=EMAIL,
            subject_kind="email",
            flow="login",
            client_ip=IP_ADDRESS,
        )
    assert ip_limited.value.code == "auth_attempt_limit"


async def test_ticket_attempts_are_purpose_bound_and_fake_ticket_is_limited() -> None:
    redis = FakeRedis()
    service = EmailAuthChallengeService(
        redis,
        _settings(email_auth_attempt_limit=2, email_auth_ip_attempt_limit=30),
    )
    ticket = "T" * 43
    for _ in range(2):
        await service.check_auth_attempt(
            subject=ticket,
            subject_kind="ticket",
            flow="register",
            client_ip=IP_ADDRESS,
        )
    with pytest.raises(EmailAuthStateError) as limited:
        await service.check_auth_attempt(
            subject=ticket,
            subject_kind="ticket",
            flow="register",
            client_ip=IP_ADDRESS,
        )
    assert limited.value.code == "auth_attempt_limit"
    assert all(ticket not in key for key in redis.keys())


def test_attempt_lua_uses_redis_time_and_declared_cluster_keys() -> None:
    key_arguments = re.findall(
        r"redis\.call\(\s*'[^']+'\s*,\s*([^,\)\r\n]+)",
        email_challenges_module._AUTH_ATTEMPT_LUA,
    )
    assert "redis.call('TIME')" in email_challenges_module._AUTH_ATTEMPT_LUA
    assert key_arguments
    assert all(re.fullmatch(r"KEYS\[\d+\]", argument.strip()) for argument in key_arguments)


async def test_missing_redis_fails_closed_for_every_public_transition() -> None:
    service = EmailAuthChallengeService(None, _settings())

    with pytest.raises(EmailAuthStateError) as create_error:
        await service.create_challenge(email=EMAIL, purpose="register", client_ip=IP_ADDRESS)
    with pytest.raises(EmailAuthStateError) as discard_error:
        await service.discard_challenge("A" * 32)
    with pytest.raises(EmailAuthStateError) as verify_error:
        await service.verify_challenge(challenge_id="A" * 32, code=FIXED_CODE)
    with pytest.raises(EmailAuthStateError) as read_error:
        await service.read_challenge("A" * 32)
    with pytest.raises(EmailAuthStateError) as consume_error:
        await service.consume_ticket("B" * 43, expected_purpose="register")
    with pytest.raises(EmailAuthStateError) as attempt_error:
        await service.check_auth_attempt(
            subject=EMAIL,
            subject_kind="email",
            flow="login",
            client_ip=IP_ADDRESS,
        )
    with pytest.raises(EmailAuthStateError) as clear_error:
        await service.clear_login_email_attempts(EMAIL)

    assert {
        create_error.value.code,
        discard_error.value.code,
        verify_error.value.code,
        read_error.value.code,
        consume_error.value.code,
        attempt_error.value.code,
        clear_error.value.code,
    } == {"backend_unavailable"}


async def test_redis_failure_never_leaks_sensitive_values_to_error_repr_or_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis = FakeRedis()
    service = EmailAuthChallengeService(redis, _settings())
    created = await service.create_challenge(email=EMAIL, purpose="register", client_ip=IP_ADDRESS)
    challenge_key = _challenge_key(redis, created.challenge_id)
    code_digest = redis.hashes[challenge_key]["code_digest"]
    issued = await service.verify_challenge(challenge_id=created.challenge_id, code=created.code)
    sensitive_values = (created.code, EMAIL, IP_ADDRESS, code_digest, issued.ticket, HMAC_SECRET)
    redis.error = RuntimeError(" | ".join(sensitive_values))

    with pytest.raises(EmailAuthStateError) as caught:
        await service.consume_ticket(issued.ticket, expected_purpose="register")

    assert caught.value.code == "backend_unavailable"
    assert caught.value.__cause__ is None
    observed = "\n".join(
        (
            repr(caught.value),
            "".join(
                traceback.format_exception(
                    type(caught.value),
                    caught.value,
                    caught.value.__traceback__,
                )
            ),
            "\n".join(record.getMessage() for record in caplog.records),
        )
    )
    for sensitive in sensitive_values:
        assert sensitive not in observed
