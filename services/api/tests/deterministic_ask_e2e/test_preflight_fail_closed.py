"""Fail-closed deterministic preflight regression tests.

These reproduce the fail-open defect that let the live cross-stack
suite authenticate and send real Ask traffic against a
non-deterministic runtime (a wrong API_BASE, or a BFF pointing at a
production API):

- a wrong API base must fail BEFORE the first business write
  (auth / record / thread);
- a reachable BFF that cannot prove a deterministic upstream must fail
  BEFORE the first BFF Ask POST;
- a correct deterministic API/BFF must still bootstrap end-to-end;
- the live module's WEB_BASE must be opt-in only (never a default
  ``:3000``).

Every network scenario runs against in-process ``httpx.MockTransport``
fakes mirroring the real deterministic runtime's responses — no real
server, no DB, no provider, no new test framework.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from .preflight import (
    BUSINESS_WRITE_PREFIXES,
    DETERMINISTIC_MODEL_NAME,
    DETERMINISTIC_OPTION_KEY,
    GUARD_REPORT_PATH,
    PreflightFailed,
    assert_deterministic_api_preflight,
    assert_deterministic_bff_upstream,
    bootstrap_deterministic_api_context,
    bootstrap_deterministic_bff_context,
)

API_ROOT = Path(__file__).resolve().parents[2]
PHONE = "13800138000"
RECORD_ID = "11111111-2222-4333-8444-555555555555"

Handler = Callable[[httpx.Request], httpx.Response]


class RecordingTransport(httpx.MockTransport):
    """MockTransport that records every request as ``METHOD path`` in order."""

    def __init__(self, handler: Handler) -> None:
        self.requests: list[str] = []

        def recording(request: httpx.Request) -> httpx.Response:
            self.requests.append(f"{request.method} {request.url.path}")
            return handler(request)

        super().__init__(recording)


def _guard_report(
    *,
    installed: bool = True,
    blocked_call_count: int = 0,
    blocked_attempts: list | None = None,
) -> dict:
    return {
        "installed": installed,
        "blocked_call_count": blocked_call_count,
        "blocked_attempts": blocked_attempts or [],
        "uninstalled_surfaces": [],
    }


def _model_options(
    *,
    default_key: str = DETERMINISTIC_OPTION_KEY,
    items: list[dict] | None = None,
) -> dict:
    if items is None:
        items = [
            {
                "key": DETERMINISTIC_OPTION_KEY,
                "label": "Deterministic E2E",
                "description": None,
                "model_name": DETERMINISTIC_MODEL_NAME,
                "replan_model_name": DETERMINISTIC_MODEL_NAME,
                "price_multiplier": 1.0,
                "is_default": True,
            }
        ]
    return {"default_key": default_key, "items": items}


def _production_model_options() -> dict:
    return _model_options(
        default_key="deepseek-v4-flash",
        items=[
            {
                "key": "deepseek-v4-flash",
                "label": "DeepSeek V4 Flash",
                "description": None,
                "model_name": "deepseek-v4-flash",
                "replan_model_name": "deepseek-v4-flash",
                "price_multiplier": 1.0,
                "is_default": True,
            }
        ],
    )


def _sse_response(body: str = "event: message.completed\ndata: {}\n\n") -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=body.encode("utf-8"),
    )


def wrong_api_handler(request: httpx.Request) -> httpx.Response:
    """A healthy API WITHOUT the deterministic guard (e.g. production entry).

    Everything the old fail-open bootstrap touched succeeds — exactly
    the shape that let a wrong API_BASE pass ``/health/ready`` and then
    accept real writes.
    """
    path = request.url.path
    if path == "/health/ready":
        return httpx.Response(200, json={"status": "ok"})
    if path == GUARD_REPORT_PATH:
        return httpx.Response(404, json={"detail": "Not Found"})
    if path == "/auth/phone/request-code":
        return httpx.Response(200, json={})
    if path == "/auth/phone/verify-code":
        return httpx.Response(200, json={"session_token": "wrong-runtime-token"})
    if path == "/reader/records/input":
        return httpx.Response(
            200,
            json={"outcome": "stable_document_ready", "reading_record_id": RECORD_ID},
        )
    if path == f"/reader/records/{RECORD_ID}/ask/threads/default":
        return httpx.Response(200, json={"id": "thread-wrong"})
    return httpx.Response(404, json={"detail": "Not Found"})


def deterministic_api_handler(request: httpx.Request) -> httpx.Response:
    """The real deterministic runtime's response surface."""
    path = request.url.path
    if path == GUARD_REPORT_PATH:
        return httpx.Response(200, json=_guard_report())
    return wrong_api_handler(request)


def bff_handler(
    *,
    model_options: dict | None = None,
    on_ask_post: Callable[[], None] | None = None,
) -> Handler:
    """A Web BFF whose login works; the upstream proof is ``model_options``."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/web/auth/phone/request-code":
            return httpx.Response(200, json={})
        if path == "/api/web/auth/phone/verify-code":
            response = httpx.Response(200, json={})
            response.headers["set-cookie"] = (
                "claread_web_session=bff-session; Path=/; HttpOnly"
            )
            return response
        if path == "/api/web/session":
            return httpx.Response(200, json={"state": "signed_in"})
        if path == f"/api/web/reader/records/{RECORD_ID}/ask/model-options":
            if model_options is None:
                return httpx.Response(500, json={"code": "UPSTREAM_ERROR"})
            return httpx.Response(200, json=model_options)
        if path.endswith("/messages/stream") and request.method == "POST":
            if on_ask_post is not None:
                on_ask_post()
            return _sse_response()
        return httpx.Response(404, json={"detail": "Not Found"})

    return handler


def _business_writes(requests: list[str]) -> list[str]:
    return [
        entry
        for entry in requests
        if entry.split(" ", 1)[1].startswith(BUSINESS_WRITE_PREFIXES)
    ]


def _ask_posts(requests: list[str]) -> list[str]:
    return [
        entry
        for entry in requests
        if entry.startswith("POST ")
        and (entry.endswith("/messages/stream") or entry.endswith("/retry"))
    ]


# ---------------------------------------------------------------------------
# RED: the fail-open defects
# ---------------------------------------------------------------------------


def test_wrong_api_base_fails_before_first_business_write():
    """A healthy non-deterministic API must be rejected before any write."""
    transport = RecordingTransport(wrong_api_handler)
    with httpx.Client(transport=transport, base_url="http://wrong-api.test") as client:
        with pytest.raises(PreflightFailed):
            bootstrap_deterministic_api_context(client, phone=PHONE)
    assert _business_writes(transport.requests) == [], (
        "business writes happened before the preflight failure: "
        f"{transport.requests}"
    )


def test_wrong_bff_upstream_fails_before_ask_post():
    """A reachable BFF serving production model options must be rejected."""
    transport = RecordingTransport(
        bff_handler(model_options=_production_model_options())
    )
    with httpx.Client(transport=transport, base_url="http://wrong-bff.test") as client:
        with pytest.raises(PreflightFailed):
            bootstrap_deterministic_bff_context(client, phone=PHONE, record_id=RECORD_ID)
    assert _ask_posts(transport.requests) == [], (
        "BFF Ask POSTs happened before the preflight failure: "
        f"{transport.requests}"
    )


def test_live_module_web_base_requires_explicit_opt_in():
    """``test_cross_stack_live`` must never default WEB_BASE to :3000."""
    code = (
        "import os\n"
        "os.environ.pop('CLAREAD_ASK_E2E_WEB_BASE', None)\n"
        "from deterministic_ask_e2e import test_cross_stack_live as live\n"
        "print('WEB_BASE=' + repr(live.WEB_BASE))\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "CLAREAD_ASK_E2E_WEB_BASE"}
    env["PYTHONPATH"] = str(API_ROOT / "tests")
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(API_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "WEB_BASE=''" in result.stdout, (
        "WEB_BASE must default to empty (opt-in only), got: "
        f"{result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# GREEN: the correct deterministic runtime still bootstraps
# ---------------------------------------------------------------------------


def test_deterministic_api_bootstraps_end_to_end():
    transport = RecordingTransport(deterministic_api_handler)
    with httpx.Client(transport=transport, base_url="http://deterministic.test") as client:
        ctx = bootstrap_deterministic_api_context(client, phone=PHONE)
    assert ctx["record_id"] == RECORD_ID
    assert ctx["thread_id"] == "thread-wrong"
    assert ctx["client"].headers["Authorization"] == "Bearer wrong-runtime-token"
    # The guard report is the FIRST request — before auth, before writes.
    assert transport.requests[0] == f"GET {GUARD_REPORT_PATH}"
    assert _business_writes(transport.requests), "bootstrap should have written"
    assert f"POST /reader/records/{RECORD_ID}/ask/threads/default" in transport.requests


def test_deterministic_bff_bootstraps_and_allows_ask_post():
    ask_posts: list[str] = []

    def on_ask_post() -> None:
        ask_posts.append("ask")

    transport = RecordingTransport(
        bff_handler(model_options=_model_options(), on_ask_post=on_ask_post)
    )
    with httpx.Client(transport=transport, base_url="http://deterministic-bff.test") as client:
        ctx = bootstrap_deterministic_bff_context(client, phone=PHONE, record_id=RECORD_ID)
        # After the preflight passed, the Ask POST is allowed.
        r = ctx["client"].post(
            f"/api/web/reader/records/{RECORD_ID}/ask/threads/thread-1/messages/stream",
            json={"content": "hello"},
        )
        assert r.status_code == 200
    assert ask_posts == ["ask"]
    assert _ask_posts(transport.requests)


# ---------------------------------------------------------------------------
# Direct preflight contract checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "report",
    [
        {"installed": False, "blocked_call_count": 0, "blocked_attempts": []},
        {"installed": True, "blocked_call_count": 2, "blocked_attempts": []},
        {
            "installed": True,
            "blocked_call_count": 1,
            "blocked_attempts": [{"surface": "httpx", "detail": "x"}],
        },
        {"installed": True},
    ],
)
def test_api_preflight_rejects_unprovable_guard_reports(report):
    transport = RecordingTransport(lambda request: httpx.Response(200, json=report))
    with httpx.Client(transport=transport, base_url="http://api.test") as client:
        with pytest.raises(PreflightFailed):
            assert_deterministic_api_preflight(client)


def test_api_preflight_rejects_non_json_guard_report():
    transport = RecordingTransport(
        lambda request: httpx.Response(200, text="<html>not json</html>")
    )
    with httpx.Client(transport=transport, base_url="http://api.test") as client:
        with pytest.raises(PreflightFailed):
            assert_deterministic_api_preflight(client)


def test_api_preflight_accepts_clean_guard_report():
    transport = RecordingTransport(
        lambda request: httpx.Response(200, json=_guard_report())
    )
    with httpx.Client(transport=transport, base_url="http://api.test") as client:
        report = assert_deterministic_api_preflight(client)
    assert report["installed"] is True
    assert transport.requests == [f"GET {GUARD_REPORT_PATH}"]


@pytest.mark.parametrize(
    "model_options",
    [
        # Production default on a reachable BFF.
        _production_model_options(),
        # Deterministic default but the option item is missing.
        _model_options(default_key=DETERMINISTIC_OPTION_KEY, items=[]),
        # Deterministic key present but resolving to a real provider model.
        _model_options(
            default_key=DETERMINISTIC_OPTION_KEY,
            items=[
                {
                    "key": DETERMINISTIC_OPTION_KEY,
                    "label": "Deterministic E2E",
                    "model_name": "deepseek-v4-flash",
                    "is_default": True,
                }
            ],
        ),
    ],
)
def test_bff_preflight_rejects_non_deterministic_model_options(model_options):
    transport = RecordingTransport(bff_handler(model_options=model_options))
    with httpx.Client(transport=transport, base_url="http://bff.test") as client:
        with pytest.raises(PreflightFailed):
            assert_deterministic_bff_upstream(client, RECORD_ID)


def test_bff_preflight_rejects_model_options_failure():
    transport = RecordingTransport(bff_handler(model_options=None))
    with httpx.Client(transport=transport, base_url="http://bff.test") as client:
        with pytest.raises(PreflightFailed):
            assert_deterministic_bff_upstream(client, RECORD_ID)


def test_bff_preflight_accepts_deterministic_model_options():
    transport = RecordingTransport(bff_handler(model_options=_model_options()))
    with httpx.Client(transport=transport, base_url="http://bff.test") as client:
        payload = assert_deterministic_bff_upstream(client, RECORD_ID)
    assert payload["default_key"] == DETERMINISTIC_OPTION_KEY
    assert transport.requests == [
        f"GET /api/web/reader/records/{RECORD_ID}/ask/model-options"
    ]
