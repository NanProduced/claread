"""Isolated email-auth API integration against one-shot Postgres and Redis."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import socket
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.database import connection as db_connection

pytestmark = [
    pytest.mark.chain_auth,
    pytest.mark.seam_service_integration,
    pytest.mark.life_permanent_regression,
]

_OPT_IN = "CLAREAD_AUTH_INTEGRATION_ALLOW"
_PG_IMAGE = "postgres:16-alpine"
_REDIS_IMAGE = "redis:7.2-alpine"
_NAME_PREFIX = "claread-auth-int-a"
_DEV_DATABASE_URL = "postgresql://claread:claread_dev@127.0.0.1:5432/claread"
_DEV_REDIS_URL = "redis://127.0.0.1:6379/0"
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
_MIGRATION = (
    Path(__file__).resolve().parents[4] / "infra" / "migrations" / "0001_initial.sql"
)
_EMAIL = "auth-int-a@example.com"
_OLD_PASSWORD = "correct horse battery staple"
_NEW_PASSWORD = "fresh horse battery staple"
_CLIENT_IP = "203.0.113.10"
_COOLDOWN_SECONDS = 1


@dataclass(frozen=True, slots=True)
class _Runtime:
    postgres_name: str
    redis_name: str
    database_url: str
    redis_url: str


class _CapturedSender:
    def __init__(self) -> None:
        self.codes: list[str] = []
        self.purposes: list[str] = []

    async def __call__(
        self,
        *,
        recipient: str,
        purpose: str,
        code: str,
        challenge_id: str,
        **_kwargs: object,
    ) -> str:
        del recipient, challenge_id
        self.codes.append(code)
        self.purposes.append(purpose)
        return "sent"


class _BlockedTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise RuntimeError(f"external network forbidden: {request.url.host}")


def _require_opt_in() -> None:
    if os.environ.get(_OPT_IN) != "1":
        pytest.skip("email auth integration requires CLAREAD_AUTH_INTEGRATION_ALLOW=1")


def _is_loopback_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.hostname in _LOOPBACK


def _reject_non_ephemeral_url(url: str, *, kind: str) -> None:
    if not _is_loopback_url(url):
        pytest.fail(f"{kind} must be loopback")
    normalized = url.rstrip("/")
    if kind == "DATABASE_URL" and normalized in {
        _DEV_DATABASE_URL,
        "postgresql://claread:claread_dev@localhost:5432/claread",
    }:
        pytest.fail("DATABASE_URL points at the development database")
    if kind == "REDIS_URL" and normalized in {
        _DEV_REDIS_URL,
        "redis://localhost:6379/0",
    }:
        pytest.fail("REDIS_URL points at the development Redis")


def _docker(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        input=input_text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def _require_local_image(image: str) -> None:
    inspected = _docker("image", "inspect", image)
    if inspected.returncode != 0:
        pytest.fail(f"{image} is not present locally; pull is forbidden")


def _container_exists(name: str) -> bool:
    return _docker("inspect", name).returncode == 0


def _unique_name(kind: str) -> str:
    for _ in range(8):
        name = f"{_NAME_PREFIX}-{kind}-{os.getpid()}-{secrets.token_hex(4)}"
        if not _container_exists(name):
            return name
    pytest.fail("unable to allocate a unique container name")


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run_container(*, name: str, image: str, port: int, target: int, extra: list[str]) -> None:
    started = _docker(
        "run",
        "-d",
        "--pull=never",
        "--name",
        name,
        "-p",
        f"127.0.0.1:{port}:{target}",
        *extra,
        image,
    )
    if started.returncode != 0:
        pytest.fail(started.stderr.strip() or f"failed to start {name}")


def _wait_for(command: list[str], *, timeout: float = 40.0, stable: int = 1) -> None:
    deadline = time.monotonic() + timeout
    hits = 0
    while time.monotonic() < deadline:
        result = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            hits += 1
            if hits >= stable:
                return
        else:
            hits = 0
        time.sleep(0.4)
    pytest.fail("timed out waiting for one-shot test instance")


def _stop_and_remove(name: str) -> None:
    if not name or not _container_exists(name):
        return
    _docker("stop", "--time", "5", name)
    _docker("rm", "-f", name)


def _start_runtime() -> _Runtime:
    _require_local_image(_PG_IMAGE)
    _require_local_image(_REDIS_IMAGE)
    postgres_name = _unique_name("pg")
    redis_name = _unique_name("redis")
    pg_port = _free_loopback_port()
    redis_port = _free_loopback_port()
    db_user = "claread_int"
    db_name = "claread_auth_int"
    db_password = secrets.token_urlsafe(24)
    try:
        _run_container(
            name=postgres_name,
            image=_PG_IMAGE,
            port=pg_port,
            target=5432,
            extra=[
                "-e",
                f"POSTGRES_USER={db_user}",
                "-e",
                f"POSTGRES_PASSWORD={db_password}",
                "-e",
                f"POSTGRES_DB={db_name}",
            ],
        )
        _run_container(
            name=redis_name,
            image=_REDIS_IMAGE,
            port=redis_port,
            target=6379,
            extra=[],
        )
        _wait_for(
            [
                "docker",
                "exec",
                postgres_name,
                "psql",
                "-U",
                db_user,
                "-d",
                db_name,
                "-c",
                "SELECT 1",
            ],
            stable=2,
        )
        _wait_for(["docker", "exec", redis_name, "redis-cli", "ping"])
    except Exception:
        _stop_and_remove(postgres_name)
        _stop_and_remove(redis_name)
        raise
    database_url = (
        f"postgresql://{db_user}:{quote(db_password, safe='')}@127.0.0.1:{pg_port}/{db_name}"
    )
    redis_url = f"redis://127.0.0.1:{redis_port}/0"
    _reject_non_ephemeral_url(database_url, kind="DATABASE_URL")
    _reject_non_ephemeral_url(redis_url, kind="REDIS_URL")
    return _Runtime(postgres_name, redis_name, database_url, redis_url)


def _apply_migration(runtime: _Runtime) -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    applied = _docker(
        "exec",
        "-i",
        runtime.postgres_name,
        "psql",
        "-U",
        "claread_int",
        "-d",
        "claread_auth_int",
        "-v",
        "ON_ERROR_STOP=1",
        input_text=sql,
    )
    if applied.returncode != 0:
        detail = (applied.stderr or applied.stdout).strip()
        pytest.fail(detail or "failed to apply 0001_initial.sql")


def _configure_settings(monkeypatch: pytest.MonkeyPatch, runtime: _Runtime) -> None:
    monkeypatch.setenv("DATABASE_URL", runtime.database_url)
    monkeypatch.setenv("REDIS_URL", runtime.redis_url)
    monkeypatch.setenv("REDIS_ENABLED", "true")
    monkeypatch.setenv("EMAIL_AUTH_ENABLED", "true")
    monkeypatch.setenv("EMAIL_AUTH_CODE_HMAC_SECRET", secrets.token_urlsafe(40))
    monkeypatch.setenv("RESEND_API_KEY", "re_test_in_process_stub")
    monkeypatch.setenv("EMAIL_AUTH_EMAIL_COOLDOWN_SECONDS", str(_COOLDOWN_SECONDS))
    monkeypatch.setenv("GRAMMAR_RAG_ENABLED", "false")
    get_settings.cache_clear()


def _json(response: httpx.Response | Any) -> dict[str, Any]:
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


def _assert_absent(haystack: str, values: tuple[str, ...]) -> None:
    lowered = haystack.lower()
    for value in values:
        if value and value.lower() in lowered:
            pytest.fail("sensitive auth material leaked")


def _log_blob(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(record.getMessage() for record in caplog.records)


async def _fetch_db_state(database_url: str) -> dict[str, Any]:
    import asyncpg

    conn = await asyncpg.connect(database_url)
    try:
        identities = await conn.fetch(
            "SELECT provider, provider_user_id FROM user_identities"
        )
        credentials = await conn.fetch(
            "SELECT password_hash FROM user_password_credentials"
        )
        sessions = await conn.fetch(
            """
            SELECT status, session_token_hash, revoked_at
            FROM user_sessions
            ORDER BY created_at
            """
        )
        users = await conn.fetch("SELECT id FROM users")
    finally:
        await conn.close()
    return {
        "identities": [dict(row) for row in identities],
        "credentials": [dict(row) for row in credentials],
        "sessions": [dict(row) for row in sessions],
        "users": [dict(row) for row in users],
    }


async def _redis_keys(redis_url: str) -> list[str]:
    import redis.asyncio as redis

    client = redis.from_url(redis_url, decode_responses=True)
    try:
        return sorted(await client.keys("*"))
    finally:
        await client.aclose()


def test_email_auth_integration_requires_opt_in() -> None:
    if os.environ.get(_OPT_IN) == "1":
        return
    with pytest.raises(pytest.skip.Exception, match="CLAREAD_AUTH_INTEGRATION_ALLOW"):
        _require_opt_in()


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("postgresql://claread:secret@8.8.8.8:5432/claread", "DATABASE_URL"),
        ("redis://10.0.0.4:6379/0", "REDIS_URL"),
        (_DEV_DATABASE_URL, "DATABASE_URL"),
        (_DEV_REDIS_URL, "REDIS_URL"),
    ],
)
def test_email_auth_integration_rejects_non_ephemeral_urls(url: str, kind: str) -> None:
    with pytest.raises(pytest.fail.Exception, match="loopback|development"):
        _reject_non_ephemeral_url(url, kind=kind)


def test_docker_helper_encodes_chinese_stdin_as_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def _fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return subprocess.CompletedProcess(["docker"], 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    chinese = "-- 中文注释：邮箱认证\nSELECT 1;"
    result = _docker("exec", "-i", "unused", input_text=chinese)
    assert result.returncode == 0
    assert seen["encoding"] == "utf-8"
    assert seen["text"] is True
    assert seen["input"] == chinese


@pytest.fixture
def email_auth_runtime(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Runtime]:
    _require_opt_in()
    runtime = _start_runtime()
    try:
        _apply_migration(runtime)
        _configure_settings(monkeypatch, runtime)
        yield runtime
    finally:
        get_settings.cache_clear()
        db_connection.DB_POOL = None
        db_connection.RedisPool = None
        _stop_and_remove(runtime.postgres_name)
        _stop_and_remove(runtime.redis_name)


def test_email_auth_register_login_reset_chain(
    email_auth_runtime: _Runtime,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.main import app
    from app.services.auth import email_auth as email_auth_module

    sender = _CapturedSender()
    monkeypatch.setattr(email_auth_module, "send_verification_email", sender)

    async def _safe_password(_raw: str, **_kwargs: object) -> str:
        return "ok"

    monkeypatch.setattr(email_auth_module, "evaluate_password_safety", _safe_password)
    monkeypatch.setattr("app.api.routes.email_auth._client_ip", lambda _request: _CLIENT_IP)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda *args, **kwargs: _BlockedTransport())

    responses: list[str] = []
    with TestClient(app) as client:
        start = _json(
            client.post("/auth/email/start", json={"email": _EMAIL})
        )
        first_challenge_at = time.monotonic()
        responses.append(json.dumps(start))
        assert "mode" not in start
        assert isinstance(start["challenge_id"], str)
        assert sender.codes and sender.purposes == ["register"]
        register_code = sender.codes[-1]

        verify = _json(
            client.post(
                "/auth/email/otp/verify",
                json={"challenge_id": start["challenge_id"], "code": register_code},
            )
        )
        responses.append(json.dumps(verify))
        register_ticket = verify["ticket"]

        registered = _json(
            client.post(
                "/auth/email/register",
                json={"ticket": register_ticket, "password": _OLD_PASSWORD},
            )
        )
        responses.append(json.dumps(registered))
        register_session = registered["session_token"]
        assert register_session

        logged_in = _json(
            client.post(
                "/auth/email/password/login",
                json={"email": _EMAIL, "password": _OLD_PASSWORD},
            )
        )
        responses.append(json.dumps(logged_in))
        login_session = logged_in["session_token"]

        remaining = _COOLDOWN_SECONDS + 0.3 - (time.monotonic() - first_challenge_at)
        if remaining > 0:
            time.sleep(remaining)

        reset_request = _json(
            client.post("/auth/email/password-reset/request", json={"email": _EMAIL})
        )
        responses.append(json.dumps(reset_request))
        assert reset_request["status"] == "accepted"
        assert sender.purposes[-1] == "password_reset"
        reset_code = sender.codes[-1]

        reset_verify = _json(
            client.post(
                "/auth/email/otp/verify",
                json={"challenge_id": reset_request["challenge_id"], "code": reset_code},
            )
        )
        responses.append(json.dumps(reset_verify))
        reset_ticket = reset_verify["ticket"]

        reset_complete = _json(
            client.post(
                "/auth/email/password-reset/complete",
                json={"ticket": reset_ticket, "password": _NEW_PASSWORD},
            )
        )
        responses.append(json.dumps(reset_complete))
        new_session = reset_complete["session_token"]

        old_password_login = client.post(
            "/auth/email/password/login",
            json={"email": _EMAIL, "password": _OLD_PASSWORD},
        )
        assert old_password_login.status_code == 401
        responses.append(old_password_login.text)
        assert _json(old_password_login) == {"detail": {"code": "invalid_credentials"}}

        new_password_login = _json(
            client.post(
                "/auth/email/password/login",
                json={"email": _EMAIL, "password": _NEW_PASSWORD},
            )
        )
        responses.append(json.dumps(new_password_login))
        assert new_password_login["session_token"]

        db_state = asyncio.run(_fetch_db_state(email_auth_runtime.database_url))
        redis_keys = asyncio.run(_redis_keys(email_auth_runtime.redis_url))

    secrets_to_hide = (
        _OLD_PASSWORD,
        _NEW_PASSWORD,
        *sender.codes,
        register_ticket,
        reset_ticket,
        register_session,
        login_session,
        new_session,
    )
    _assert_absent(_log_blob(caplog), secrets_to_hide)
    for body in responses:
        _assert_absent(body, (_OLD_PASSWORD, _NEW_PASSWORD, *sender.codes))

    assert len(db_state["users"]) == 1
    assert db_state["identities"] == [
        {"provider": "email", "provider_user_id": _EMAIL}
    ]
    assert len(db_state["credentials"]) == 1
    password_hash = db_state["credentials"][0]["password_hash"]
    assert password_hash.startswith("$argon2id$")
    assert _OLD_PASSWORD not in password_hash
    assert _NEW_PASSWORD not in password_hash

    statuses = [row["status"] for row in db_state["sessions"]]
    assert statuses.count("revoked") >= 2
    assert "active" in statuses
    by_hash = {row["session_token_hash"]: row for row in db_state["sessions"]}
    login_hash = hashlib.sha256(login_session.encode()).hexdigest().lower()
    reset_hash = hashlib.sha256(new_session.encode()).hexdigest().lower()
    assert by_hash[login_hash]["status"] == "revoked"
    assert by_hash[login_hash]["revoked_at"] is not None
    assert by_hash[reset_hash]["status"] == "active"
    for token_hash, row in by_hash.items():
        assert token_hash not in {login_session, new_session, register_session}
        assert row["status"] != "revoked" or row["revoked_at"] is not None

    joined_keys = " ".join(redis_keys)
    assert "challenge:v1:" not in joined_keys
    assert "ticket:v1:" not in joined_keys
    assert "active:v1:" not in joined_keys
    _assert_absent(joined_keys, (_OLD_PASSWORD, _NEW_PASSWORD, *sender.codes))
    dumped = json.dumps(db_state, default=str)
    _assert_absent(
        dumped,
        (_OLD_PASSWORD, _NEW_PASSWORD, *sender.codes, register_ticket, reset_ticket),
    )
    _assert_absent(dumped, (register_session, login_session, new_session))
    assert email_auth_runtime.postgres_name.startswith(_NAME_PREFIX)
    assert email_auth_runtime.redis_name.startswith(_NAME_PREFIX)
