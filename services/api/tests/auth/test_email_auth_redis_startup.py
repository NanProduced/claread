"""Email-auth Redis startup gate: fail closed before serving, never leak DSNs."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from app.config.settings import Settings
from app.database import connection as db_connection
from app.main import lifespan

pytestmark = [
    pytest.mark.chain_auth,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
    pytest.mark.no_network_default,
]

SECRET_DSN_TOKEN = "super-secret-dsn-token"
LEAKY_REDIS_URL = f"redis://leaky-user:{SECRET_DSN_TOKEN}@203.0.113.10:6379/3"
VALID_HMAC_SECRET = SecretStr("s" * 32)
VALID_RESEND_API_KEY = SecretStr("re_test_" + "k" * 32)


class FakeRedis:
    def __init__(self, url: str, *, ping_error: BaseException | None = None) -> None:
        self.url = url
        self.closed = False
        self._ping_error = ping_error

    async def ping(self) -> bool:
        if self._ping_error is not None:
            raise self._ping_error
        return True

    async def aclose(self) -> None:
        self.closed = True


class _FakeStaleSweeper:
    def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


def _forbid_real_redis_from_url(url: str, **kwargs: object) -> FakeRedis:
    raise AssertionError("real Redis from_url is forbidden in these tests")


def _exception_text(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def _log_text(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(record.getMessage() for record in caplog.records)


def _assert_no_redis_secrets(text: str) -> None:
    assert LEAKY_REDIS_URL not in text
    assert SECRET_DSN_TOKEN not in text
    assert "leaky-user" not in text
    assert "203.0.113.10" not in text


@pytest.fixture
def isolate_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMAIL_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("EMAIL_AUTH_CODE_HMAC_SECRET", raising=False)
    monkeypatch.delenv("EMAIL_AUTH_EMAIL_COOLDOWN_SECONDS", raising=False)
    monkeypatch.delenv("EMAIL_AUTH_EMAIL_HOURLY_LIMIT", raising=False)
    monkeypatch.delenv("EMAIL_AUTH_IP_HOURLY_LIMIT", raising=False)
    monkeypatch.delenv("REDIS_ENABLED", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_FROM", raising=False)
    monkeypatch.delenv("RESEND_REPLY_TO", raising=False)


@pytest.fixture(autouse=True)
def _forbid_real_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("redis.asyncio.from_url", _forbid_real_redis_from_url)


@pytest.fixture
def restore_redis_pool() -> Iterator[None]:
    original_pool = db_connection.RedisPool
    db_connection.RedisPool = None
    yield
    db_connection.RedisPool = original_pool


def test_email_auth_disabled_by_default(isolate_settings_env: None) -> None:
    settings = Settings(_env_file=None)
    assert settings.email_auth_enabled is False
    assert settings.email_auth_code_hmac_secret.get_secret_value() == ""
    assert settings.email_auth_email_cooldown_seconds == 60
    assert settings.email_auth_email_hourly_limit == 5
    assert settings.email_auth_ip_hourly_limit == 30
    assert settings.resend_api_key.get_secret_value() == ""
    assert settings.resend_from == "Claread <login@auth.claread.com>"
    assert settings.resend_reply_to == ""


def test_email_auth_hmac_secret_is_masked_in_settings_repr(isolate_settings_env: None) -> None:
    raw_secret = "masked-secret-with-at-least-thirty-two-bytes"
    settings = Settings(
        _env_file=None,
        email_auth_code_hmac_secret=SecretStr(raw_secret),
    )

    assert raw_secret not in repr(settings)


def test_resend_api_key_is_masked_in_settings_repr(isolate_settings_env: None) -> None:
    raw_secret = "re_test_masked-secret"
    settings = Settings(_env_file=None, resend_api_key=SecretStr(raw_secret))

    assert raw_secret not in repr(settings)


def _startup_settings(
    *,
    email_auth_enabled: bool,
    redis_enabled: bool,
    redis_url: str = LEAKY_REDIS_URL,
    email_auth_code_hmac_secret: SecretStr = VALID_HMAC_SECRET,
    resend_api_key: SecretStr = VALID_RESEND_API_KEY,
) -> SimpleNamespace:
    return SimpleNamespace(
        database_url="postgresql://claread:unused@127.0.0.1:5432/claread",
        database_pool_size=5,
        database_max_overflow=10,
        database_pool_timeout=30,
        database_max_inactive_connection_lifetime=3600,
        redis_url=redis_url,
        redis_enabled=redis_enabled,
        email_auth_enabled=email_auth_enabled,
        email_auth_code_hmac_secret=email_auth_code_hmac_secret,
        resend_api_key=resend_api_key,
        grammar_rag_enabled=False,
        langsmith_enabled=False,
    )


def _patch_lifespan_stores(
    monkeypatch: pytest.MonkeyPatch,
    settings: SimpleNamespace,
) -> dict[str, object]:
    state: dict[str, object] = {
        "opened": [],
        "closed": [],
        "yielded": False,
    }

    async def fake_init_db(**kwargs: object) -> object:
        opened = state["opened"]
        assert isinstance(opened, list)
        opened.append("db")
        return object()

    async def fake_close_db() -> None:
        closed = state["closed"]
        assert isinstance(closed, list)
        closed.append("db")

    async def fake_close_redis() -> None:
        closed = state["closed"]
        assert isinstance(closed, list)
        closed.append("redis")
        db_connection.RedisPool = None

    async def fake_warm_dict_cache() -> None:
        return None

    async def fake_stale_sweep() -> dict[str, int]:
        return {"reconciled": 0, "scanned": 0}

    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    monkeypatch.setattr("app.main.init_db", fake_init_db)
    monkeypatch.setattr("app.main.close_db", fake_close_db)
    monkeypatch.setattr("app.main.close_redis", fake_close_redis)
    monkeypatch.setattr("app.main.setup_langsmith", lambda _settings: None)
    monkeypatch.setattr("app.main.preload_dict_nlp", lambda: False)
    monkeypatch.setattr("app.main._warm_dict_cache", fake_warm_dict_cache)
    monkeypatch.setattr(
        "app.services.reader_record_ask.stale_run_recovery.run_startup_stale_stream_sweep",
        fake_stale_sweep,
    )
    monkeypatch.setattr(
        "app.services.reader_record_ask.stale_run_recovery.StaleStreamSweeper",
        _FakeStaleSweeper,
    )
    return state


async def _enter_lifespan(state: dict[str, object]) -> None:
    app = FastAPI()
    async with lifespan(app):
        state["yielded"] = True
        state["closed_at_yield"] = list(state["closed"])  # type: ignore[arg-type]
        state["redis_at_yield"] = db_connection.RedisPool


@pytest.mark.parametrize("secret", [SecretStr(""), SecretStr("s" * 31)])
async def test_email_auth_on_rejects_unsafe_hmac_secret_before_stores(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    secret: SecretStr,
) -> None:
    settings = _startup_settings(
        email_auth_enabled=True,
        redis_enabled=True,
        email_auth_code_hmac_secret=secret,
    )
    state = _patch_lifespan_stores(monkeypatch, settings)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(
        RuntimeError,
        match="Email auth HMAC secret is not configured securely",
    ) as caught:
        await _enter_lifespan(state)

    assert state["yielded"] is False
    assert state["opened"] == []
    assert state["closed"] == []
    secret_value = secret.get_secret_value()
    if secret_value:
        assert secret_value not in _exception_text(caught.value)
        assert secret_value not in _log_text(caplog)


async def test_email_auth_on_without_resend_key_fails_before_stores(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _startup_settings(
        email_auth_enabled=True,
        redis_enabled=True,
        resend_api_key=SecretStr(""),
    )
    state = _patch_lifespan_stores(monkeypatch, settings)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(RuntimeError, match="Email auth Resend API key is not configured"):
        await _enter_lifespan(state)

    assert state["yielded"] is False
    assert state["opened"] == []
    assert state["closed"] == []
    assert VALID_RESEND_API_KEY.get_secret_value() not in _log_text(caplog)


async def test_email_auth_off_allows_disabled_redis(
    monkeypatch: pytest.MonkeyPatch,
    restore_redis_pool: None,
) -> None:
    settings = _startup_settings(email_auth_enabled=False, redis_enabled=False)
    state = _patch_lifespan_stores(monkeypatch, settings)
    await _enter_lifespan(state)

    assert state["yielded"] is True
    assert state["closed_at_yield"] == []
    assert "db" in state["opened"]
    assert db_connection.RedisPool is None


async def test_email_auth_off_allows_redis_probe_failure(
    monkeypatch: pytest.MonkeyPatch,
    restore_redis_pool: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _startup_settings(email_auth_enabled=False, redis_enabled=True)
    created: list[FakeRedis] = []

    def fake_from_url(url: str, **kwargs: object) -> FakeRedis:
        client = FakeRedis(url, ping_error=ConnectionError(f"Error connecting to {url}"))
        created.append(client)
        return client

    monkeypatch.setattr("redis.asyncio.from_url", fake_from_url)
    state = _patch_lifespan_stores(monkeypatch, settings)
    caplog.set_level(logging.DEBUG)
    await _enter_lifespan(state)

    assert state["yielded"] is True
    assert state["closed_at_yield"] == []
    assert created[0].closed is True
    _assert_no_redis_secrets(_log_text(caplog))


async def test_email_auth_on_without_redis_fails_closed_before_yield(
    monkeypatch: pytest.MonkeyPatch,
    restore_redis_pool: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _startup_settings(email_auth_enabled=True, redis_enabled=False)
    state = _patch_lifespan_stores(monkeypatch, settings)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(RuntimeError, match="Redis is required but disabled") as caught:
        await _enter_lifespan(state)

    assert state["yielded"] is False
    assert "db" in state["opened"]
    assert "db" in state["closed"]
    _assert_no_redis_secrets(_exception_text(caught.value))
    _assert_no_redis_secrets(_log_text(caplog))
    assert caught.value.__cause__ is None


async def test_email_auth_on_probe_failure_fails_closed_and_closes_stores(
    monkeypatch: pytest.MonkeyPatch,
    restore_redis_pool: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _startup_settings(email_auth_enabled=True, redis_enabled=True)
    created: list[FakeRedis] = []

    def fake_from_url(url: str, **kwargs: object) -> FakeRedis:
        client = FakeRedis(url, ping_error=ConnectionError(f"Error connecting to {url}"))
        created.append(client)
        return client

    monkeypatch.setattr("redis.asyncio.from_url", fake_from_url)
    state = _patch_lifespan_stores(monkeypatch, settings)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(RuntimeError, match="Redis startup probe failed") as caught:
        await _enter_lifespan(state)

    assert state["yielded"] is False
    assert "db" in state["opened"]
    assert "db" in state["closed"]
    assert "redis" in state["closed"]
    assert created[0].closed is True
    assert db_connection.RedisPool is None
    _assert_no_redis_secrets(_exception_text(caught.value))
    _assert_no_redis_secrets(_log_text(caplog))
    assert caught.value.__cause__ is None


async def test_email_auth_on_probe_success_yields_without_closing_stores(
    monkeypatch: pytest.MonkeyPatch,
    restore_redis_pool: None,
) -> None:
    settings = _startup_settings(email_auth_enabled=True, redis_enabled=True)
    client = FakeRedis(LEAKY_REDIS_URL)

    def fake_from_url(url: str, **kwargs: object) -> FakeRedis:
        assert url == LEAKY_REDIS_URL
        return client

    monkeypatch.setattr("redis.asyncio.from_url", fake_from_url)
    state = _patch_lifespan_stores(monkeypatch, settings)
    await _enter_lifespan(state)

    assert state["yielded"] is True
    assert state["closed_at_yield"] == []
    assert "db" in state["opened"]
    assert state["redis_at_yield"] is client
    assert client.closed is False
