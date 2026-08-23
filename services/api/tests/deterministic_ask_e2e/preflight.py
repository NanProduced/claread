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

import uuid
from typing import Any

import httpx

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

PHONE_AUTH_CODE = "888888"


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
            f"{GUARD_REPORT_PATH} ({exc!r}); refusing to run against an "
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
            "API preflight: deterministic provider guard is not installed "
            f"(report={report!r}); refusing to run against a "
            "non-deterministic runtime"
        )
    if report.get("blocked_call_count") != 0:
        raise PreflightFailed(
            "API preflight: deterministic guard already recorded blocked "
            f"provider calls (report={report!r}); refusing to continue"
        )
    if report.get("blocked_attempts") != []:
        raise PreflightFailed(
            "API preflight: deterministic guard already recorded blocked "
            f"provider attempts (report={report!r}); refusing to continue"
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
            f"({exc!r}); cannot prove the BFF points at the deterministic "
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
            f"(payload={payload!r}); refusing to send any Ask message"
        )
    deterministic_items = [
        item
        for item in items
        if isinstance(item, dict) and item.get("key") == DETERMINISTIC_OPTION_KEY
    ]
    if not deterministic_items:
        raise PreflightFailed(
            f"BFF preflight: model option {DETERMINISTIC_OPTION_KEY!r} is "
            f"absent from the BFF model list (items={items!r}); refusing "
            "to send any Ask message"
        )
    model_name = deterministic_items[0].get("model_name")
    if model_name != DETERMINISTIC_MODEL_NAME:
        raise PreflightFailed(
            f"BFF preflight: model option {DETERMINISTIC_OPTION_KEY!r} "
            f"resolves to model {model_name!r}, expected "
            f"{DETERMINISTIC_MODEL_NAME!r}; refusing to send any Ask message"
        )
    return payload


def bootstrap_deterministic_api_context(
    client: httpx.Client,
    *,
    phone: str,
    article_text: str = FIXTURE_ARTICLE_TEXT,
) -> dict[str, Any]:
    """Live ``api_ctx`` fixture body: preflight, then auth + record + thread.

    The guard preflight is deliberately the FIRST request — before any
    auth or business write — so a wrong API_BASE fails closed with zero
    side effects.
    """
    assert_deterministic_api_preflight(client)

    r = client.post("/auth/phone/request-code", json={"phone": phone})
    assert r.status_code == 200, r.text
    r = client.post("/auth/phone/verify-code", json={"phone": phone, "code": PHONE_AUTH_CODE})
    assert r.status_code == 200, r.text
    token = r.json()["session_token"]
    client.headers["Authorization"] = f"Bearer {token}"

    r = client.get("/health/ready")
    assert r.status_code == 200, r.text

    r = client.post(
        "/reader/records/input",
        json={
            "source_type": "pasted_text",
            "text": article_text,
            "language": "en",
            "client_record_id": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 200, r.text
    submit = r.json()
    assert submit.get("outcome") == "stable_document_ready", submit
    record_id = submit["reading_record_id"]

    r = client.post(f"/reader/records/{record_id}/ask/threads/default", json={})
    assert r.status_code == 200, r.text
    thread_id = r.json()["id"]

    return {
        "client": client,
        "token": token,
        "record_id": record_id,
        "thread_id": thread_id,
    }


def bootstrap_deterministic_bff_context(
    client: httpx.Client,
    *,
    phone: str,
    record_id: str,
) -> dict[str, Any]:
    """Live ``web_ctx`` fixture body: BFF login, then upstream preflight.

    The BFF phone-auth login itself is not proof of anything (a wrong
    BFF's upstream may also accept phone auth); the deterministic proof
    is the model-options check, which must pass before any BFF Ask POST.
    """
    r = client.post("/api/web/auth/phone/request-code", json={"phone": phone})
    assert r.status_code == 200, r.text
    r = client.post(
        "/api/web/auth/phone/verify-code",
        json={"phone": phone, "code": PHONE_AUTH_CODE},
    )
    assert r.status_code == 200, r.text
    assert "claread_web_session" in client.cookies, (
        "BFF must establish a real claread_web_session cookie"
    )
    r = client.get("/api/web/session")
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "signed_in", r.text

    # Deterministic proof BEFORE any BFF Ask POST is allowed.
    assert_deterministic_bff_upstream(client, record_id)

    return {"client": client}
