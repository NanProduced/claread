"""Fail-closed preflight + bootstrap for the live cross-stack deterministic Ask e2e.

Fail-closed deterministic preflight: ``test_cross_stack_live`` previously
trusted ``/health/ready`` (API) and ``/api/web/session`` reachability
(BFF). Both checks are fail-open — any server that answers those URLs
is accepted — which is how an unrelated :3000 dev BFF pointing at a
production API once received a real Ask message and produced a real
model answer.

The helpers here make the live suite fail-closed:

- ``assert_deterministic_api_preflight`` — the API base must serve the
  deterministic guard report with ``installed=true``, zero blocked
  calls and zero blocked attempts BEFORE the first auth / record write;
- ``assert_deterministic_bff_upstream`` — the BFF must expose the
  deterministic model option (default key + item + model name) through
  its product model-options endpoint BEFORE the first BFF Ask POST;
- ``bootstrap_deterministic_api_context`` /
  ``bootstrap_deterministic_bff_context`` — the fixture bodies used by
  the live suite, ordered preflight-first.

Test-only module; never imported from ``app/**``.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from tests.web_real_product_session_fixture import provision_real_product_session

from .models import FIXTURE_ARTICLE_TEXT

__all__ = [
    "DETERMINISTIC_MODEL_NAME",
    "DETERMINISTIC_OPTION_KEY",
    "GUARD_REPORT_PATH",
    "PreflightFailed",
    "assert_deterministic_api_preflight",
    "assert_deterministic_bff_upstream",
    "bootstrap_deterministic_api_context",
    "bootstrap_deterministic_bff_context",
]

DETERMINISTIC_OPTION_KEY = "deterministic-e2e-r0"
DETERMINISTIC_MODEL_NAME = "deterministic-e2e-model"
GUARD_REPORT_PATH = "/__deterministic_guard__/provider-calls"

# Requests under these prefixes mutate real business state (auth
# sessions, reading records, Ask threads/messages). The API preflight
# must complete before the first such request is issued.
BUSINESS_WRITE_PREFIXES = ("/auth/", "/reader/")

class PreflightFailed(AssertionError):
    """A runtime could not be proven deterministic; fail closed."""


def assert_deterministic_api_preflight(client: httpx.Client) -> dict[str, Any]:
    """Prove the API base runs the deterministic provider-guarded runtime.

    Fail-closed on every shape of "not provably deterministic": guard
    endpoint missing (404/5xx), non-JSON body, guard not installed, or
    any already-recorded blocked provider attempt.
    """
    try:
        response = client.get(GUARD_REPORT_PATH)
    except httpx.HTTPError as exc:
        raise PreflightFailed(
            f"API preflight: deterministic guard report is unreachable at "
            f"{GUARD_REPORT_PATH}; refusing to run against an "
            "unproven runtime"
        ) from exc
    if response.status_code != 200:
        raise PreflightFailed(
            "API preflight: deterministic guard report endpoint is missing "
            f"(GET {GUARD_REPORT_PATH} -> {response.status_code}); refusing "
            "to authenticate or write against a non-deterministic runtime"
        )
    try:
        report = response.json()
    except ValueError as exc:
        raise PreflightFailed(
            "API preflight: deterministic guard report is not JSON; "
            "refusing to run against an unproven runtime"
        ) from exc
    if report.get("installed") is not True:
        raise PreflightFailed(
            "API preflight: deterministic provider guard is not installed; "
            "refusing to run against a "
            "non-deterministic runtime"
        )
    if report.get("blocked_call_count") != 0:
        raise PreflightFailed(
            "API preflight: deterministic guard already recorded blocked "
            "provider calls; refusing to continue"
        )
    if report.get("blocked_attempts") != []:
        raise PreflightFailed(
            "API preflight: deterministic guard already recorded blocked "
            "provider attempts; refusing to continue"
        )
    return report


def assert_deterministic_bff_upstream(
    client: httpx.Client,
    record_id: str,
) -> dict[str, Any]:
    """Prove the BFF's upstream is the deterministic runtime.

    The deterministic API forces its model catalog to a single
    ``deterministic-e2e-r0`` option, so a BFF model-options response
    whose default (and item) is that option can only come from the
    deterministic runtime. Any other answer — production options, an
    upstream 4xx/5xx, a missing option — means the BFF does not point
    at the deterministic runtime and no Ask message may be sent.
    """
    path = f"/api/web/reader/records/{record_id}/ask/model-options"
    try:
        response = client.get(path)
    except httpx.HTTPError as exc:
        raise PreflightFailed(
            f"BFF preflight: model-options is unreachable at {path} "
            "cannot prove the BFF points at the deterministic "
            "runtime; refusing to send any Ask message"
        ) from exc
    if response.status_code != 200:
        raise PreflightFailed(
            "BFF preflight: model-options failed "
            f"(GET {path} -> {response.status_code}); cannot prove the BFF "
            "points at the deterministic runtime; refusing to send any "
            "Ask message"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise PreflightFailed(
            "BFF preflight: model-options response is not JSON; cannot "
            "prove the BFF points at the deterministic runtime; refusing "
            "to send any Ask message"
        ) from exc
    default_key = payload.get("default_key")
    if default_key != DETERMINISTIC_OPTION_KEY:
        raise PreflightFailed(
            "BFF preflight: default model option is "
            f"{default_key!r}, expected {DETERMINISTIC_OPTION_KEY!r}; the "
            "BFF does not point at the deterministic runtime; refusing to "
            "send any Ask message"
        )
    items = payload.get("items")
    if not isinstance(items, list):
        raise PreflightFailed(
            "BFF preflight: model-options items is not a list "
            "refusing to send any Ask message"
        )
    deterministic_items = [
        item
        for item in items
        if isinstance(item, dict) and item.get("key") == DETERMINISTIC_OPTION_KEY
    ]
    if not deterministic_items:
        raise PreflightFailed(
            f"BFF preflight: model option {DETERMINISTIC_OPTION_KEY!r} is "
            "absent from the BFF model list; refusing to send any Ask message"
        )
    model_name = deterministic_items[0].get("model_name")
    if model_name != DETERMINISTIC_MODEL_NAME:
        raise PreflightFailed(
            f"BFF preflight: model option {DETERMINISTIC_OPTION_KEY!r} "
            f"resolves to model {model_name!r}, expected "
            f"{DETERMINISTIC_MODEL_NAME!r}; refusing to send any Ask message"
        )
    return payload


def _provision_email_session(email: str) -> dict[str, Any]:
    return asyncio.run(provision_real_product_session(email))


def bootstrap_deterministic_api_context(
    client: httpx.Client,
    *,
    email: str,
    article_text: str = FIXTURE_ARTICLE_TEXT,
    provision_session: Callable[[str], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Live ``api_ctx`` fixture body: preflight, then email session + writes.

    The guard preflight is deliberately the FIRST request — before any
    identity/session provisioning or business write — so a wrong API_BASE
    fails closed with zero side effects.
    """
    assert_deterministic_api_preflight(client)

    session = (provision_session or _provision_email_session)(email)
    token = session.get("session_token")
    if not isinstance(token, str) or not token:
        raise PreflightFailed("API bootstrap: email session fixture returned no session token")
    client.headers["Authorization"] = f"Bearer {token}"

    r = client.get("/health/ready")
    assert r.status_code == 200

    r = client.post(
        "/reader/records/input",
        json={
            "source_type": "pasted_text",
            "text": article_text,
            "language": "en",
            "client_record_id": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 200
    submit = r.json()
    assert submit.get("outcome") == "stable_document_ready", (
        "API input did not return a stable document"
    )
    record_id = submit["reading_record_id"]

    r = client.post(f"/reader/records/{record_id}/ask/threads/default", json={})
    assert r.status_code == 200
    thread_id = r.json()["id"]

    return {
        "client": client,
        "session_token": token,
        "record_id": record_id,
        "thread_id": thread_id,
    }


def bootstrap_deterministic_bff_context(
    client: httpx.Client,
    *,
    session_token: str,
    record_id: str,
) -> dict[str, Any]:
    """Live ``web_ctx`` fixture body: inject session, then upstream preflight.

    The API bootstrap owns the identity/session. The BFF receives the same
    token directly, and the deterministic model-options proof must pass
    before any BFF Ask POST.
    """
    client.cookies.set("claread_web_session", session_token, path="/")
    r = client.get("/api/web/session")
    assert r.status_code == 200
    assert r.json()["state"] == "signed_in"

    # Deterministic proof BEFORE any BFF Ask POST is allowed.
    assert_deterministic_bff_upstream(client, record_id)

    return {"client": client}
