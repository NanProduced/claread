"""Opt-in live cross-stack acceptance: Browser-facing BFF → API → PG.

Requires a running test-only deterministic API (and optionally a running
Web dev server pointing at it). Enable with::

    CLAREAD_RUN_ASK_DETERMINISTIC_E2E=1 \
    CLAREAD_ASK_E2E_API_BASE=http://127.0.0.1:8010 \
    CLAREAD_ASK_E2E_WEB_BASE=http://127.0.0.1:3000 \
    uv run pytest tests/deterministic_ask_e2e/test_cross_stack_live.py -v

Scenarios (handoff spec D):

1. login + fixture record + default thread via real API/PG;
2. send → SSE over canonical routes terminates with a legal v2
   ``message.completed`` carrying the deterministic answer;
3. article citation is bound to the real record (navigate API resolves a
   real anchor_segment/unit present in the live stable document);
4. cold history from PG agrees with the SSE payload (answer, citations,
   execution identity);
5. retry over the canonical retry route preserves thread/message
   identity, persists, and survives a second cold-history read;
6. old namespaces are absent (FastAPI ``/reader-ask/*``, BFF
   ``/api/web/reader-ask/*``) and the provider guard counter is zero.

BFF-layer tests run only when the Web server is reachable; they use a
real ``claread_web_session`` cookie obtained through the BFF phone-auth
flow (upstream FastAPI mock provider, code 888888).
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from .models import (
    DETERMINISTIC_ARTICLE_ANSWER,
    DETERMINISTIC_GENERAL_ANSWER,
    DETERMINISTIC_MARKER,
    FIXTURE_ARTICLE_TEXT,
    FIXTURE_QUESTION,
)

RUN_GATE = os.environ.get("CLAREAD_RUN_ASK_DETERMINISTIC_E2E", "") == "1"
API_BASE = os.environ.get("CLAREAD_ASK_E2E_API_BASE", "http://127.0.0.1:8010").rstrip("/")
WEB_BASE = os.environ.get("CLAREAD_ASK_E2E_WEB_BASE", "http://127.0.0.1:3000").rstrip("/")
PHONE = os.environ.get("CLAREAD_ASK_E2E_PHONE", "13800138000")

EXECUTION_V2 = "reader_record_ask_agentic_v2"

pytestmark = pytest.mark.skipif(
    not RUN_GATE,
    reason=(
        "live cross-stack gate; set CLAREAD_RUN_ASK_DETERMINISTIC_E2E=1 "
        "with the test-only API running (see module docstring)"
    ),
)


def parse_sse_frames(body: str) -> list[tuple[str | None, str]]:
    frames: list[tuple[str | None, str]] = []
    event: str | None = None
    data_lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].strip())
        elif line.strip() == "":
            if event is not None or data_lines:
                frames.append((event, "\n".join(data_lines)))
            event, data_lines = None, []
    if event is not None or data_lines:
        frames.append((event, "\n".join(data_lines)))
    return frames


def terminal_completed(frames: list[tuple[str | None, str]]) -> dict:
    completed = [json.loads(data) for name, data in frames if name == "message.completed"]
    terminals = [(name, data) for name, data in frames if name in {"agentic.terminal", "error"}]
    assert len(completed) == 1, (
        f"expected exactly one message.completed; got {len(completed)} "
        f"completed and terminals={terminals}"
    )
    return completed[0]


def assert_public_surface_is_clean(payload_json: str) -> None:
    assert "evh_" not in payload_json
    assert "envelope_fingerprint" not in payload_json
    assert "source_fingerprint" not in payload_json


def assert_completed_is_deterministic_v2(payload: dict, *, thread_id: str) -> None:
    assert payload["execution_version"] == EXECUTION_V2
    assert payload["final_status"] == "ok"
    assert payload["thread_id"] == thread_id
    assert payload["message_id"]
    assert payload["turn_run_id"]
    assert DETERMINISTIC_MARKER in payload["answer_text"]
    assert DETERMINISTIC_ARTICLE_ANSWER in payload["answer_text"]
    assert DETERMINISTIC_GENERAL_ANSWER in payload["answer_text"]
    bases = [
        "article" if "article" in block["text"] else "general" for block in payload["answer_blocks"]
    ]
    assert len(payload["answer_blocks"]) == 2
    article_blocks = [
        block for block in payload["answer_blocks"] if block["text"] == DETERMINISTIC_ARTICLE_ANSWER
    ]
    assert len(article_blocks) == 1
    assert article_blocks[0]["citation_ids"], "article block misses citation"
    assert bases  # sanity
    citations = payload["citations"]
    assert len(citations) >= 1
    assert all(c["source_kind"] == "article" for c in citations)
    assert all(c.get("citation_id") for c in citations)
    assert any(c.get("snippet") for c in citations), (
        "article citation must carry a real snippet for hover"
    )


@pytest.fixture(scope="module")
def api_ctx() -> Iterator[dict[str, Any]]:
    with httpx.Client(base_url=API_BASE, timeout=60) as client:
        r = client.post("/auth/phone/request-code", json={"phone": PHONE})
        assert r.status_code == 200, r.text
        r = client.post("/auth/phone/verify-code", json={"phone": PHONE, "code": "888888"})
        assert r.status_code == 200, r.text
        token = r.json()["session_token"]
        client.headers["Authorization"] = f"Bearer {token}"

        r = client.get("/health/ready")
        assert r.status_code == 200, r.text

        r = client.post(
            "/reader/records/input",
            json={
                "source_type": "pasted_text",
                "text": FIXTURE_ARTICLE_TEXT,
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

        yield {
            "client": client,
            "token": token,
            "record_id": record_id,
            "thread_id": thread_id,
        }


@pytest.fixture(scope="module")
def send_result(api_ctx) -> dict[str, Any]:
    client: httpx.Client = api_ctx["client"]
    record_id = api_ctx["record_id"]
    thread_id = api_ctx["thread_id"]
    submission_id = str(uuid.uuid4())
    r = client.post(
        f"/reader/records/{record_id}/ask/threads/{thread_id}/messages/stream",
        json={"content": FIXTURE_QUESTION, "client_submission_id": submission_id},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    frames = parse_sse_frames(r.text)
    payload = terminal_completed(frames)
    run_started = [json.loads(data) for name, data in frames if name == "agentic.run_started"]
    return {
        "frames": frames,
        "payload": payload,
        "run_started": run_started,
        "submission_id": submission_id,
    }


def test_send_sse_completes_with_legal_v2_terminal(api_ctx, send_result):
    payload = send_result["payload"]
    assert_completed_is_deterministic_v2(payload, thread_id=api_ctx["thread_id"])
    assert_public_surface_is_clean(json.dumps(send_result["frames"]))
    if send_result["run_started"]:
        assert send_result["run_started"][0]["execution_version"] == (EXECUTION_V2)


def test_citation_navigate_binds_real_record_anchor(api_ctx, send_result):
    client: httpx.Client = api_ctx["client"]
    record_id = api_ctx["record_id"]
    payload = send_result["payload"]
    citation_id = payload["citations"][0]["citation_id"]
    message_id = payload["message_id"]

    r = client.post(
        f"/reader/records/{record_id}/ask/messages/{message_id}/citations/{citation_id}/navigate"
    )
    assert r.status_code == 200, r.text
    navigate = r.json()
    assert navigate["status"] == "ok", navigate
    location = navigate["location"]
    assert location is not None

    r = client.get(f"/reader/records/{record_id}/stable-document")
    assert r.status_code == 200, r.text
    document = r.json()
    segment_ids = {seg["anchor_segment_id"] for seg in document["anchor_segments"]}
    unit_ids = (
        {unit["unit_id"] for unit in document["reading_units"]}
        if ("reading_units" in document)
        else None
    )
    if location["anchor_segment_id"] is not None:
        assert location["anchor_segment_id"] in segment_ids, (
            "citation navigate returned an anchor segment that is not in the live stable document"
        )
    if unit_ids is not None and location["unit_id"] is not None:
        assert location["unit_id"] in unit_ids
    assert location["anchor_segment_id"] is not None or location["unit_id"] is not None, (
        "navigate must bind the citation to a real anchor segment / unit"
    )
    start = location["canonical_text_start_utf16"]
    end = location["canonical_text_end_utf16"]
    if start is not None or end is not None:
        assert start is not None and end is not None and start < end


def test_cold_history_from_pg_matches_completed(api_ctx, send_result):
    client: httpx.Client = api_ctx["client"]
    record_id = api_ctx["record_id"]
    thread_id = api_ctx["thread_id"]
    payload = send_result["payload"]

    r = client.get(f"/reader/records/{record_id}/ask/threads/{thread_id}")
    assert r.status_code == 200, r.text
    detail = r.json()
    messages = detail["messages"]
    roles = [m["role"] for m in messages]
    assert roles.count("user") >= 1 and roles.count("assistant") >= 1

    assistant = next(
        m for m in reversed(messages) if m["role"] == "assistant" and m.get("agentic_answer_blocks")
    )
    assert assistant["execution_version"] == EXECUTION_V2
    assert assistant["id"] == payload["message_id"]
    hot_text = payload["answer_text"]
    cold_texts = [b["text"] for b in assistant["agentic_answer_blocks"]]
    assert DETERMINISTIC_ARTICLE_ANSWER in cold_texts
    assert DETERMINISTIC_GENERAL_ANSWER in cold_texts
    assert hot_text == "\n\n".join(cold_texts)
    cold_citation_ids = {c["citation_id"] for c in assistant["agentic_citations"] or []}
    hot_citation_ids = {c["citation_id"] for c in payload["citations"]}
    assert cold_citation_ids == hot_citation_ids
    assert_public_surface_is_clean(json.dumps(assistant))


def _send_message(
    client: httpx.Client,
    record_id: str,
    thread_id: str,
    *,
    content: str,
    client_submission_id: str | None,
) -> dict:
    body: dict[str, Any] = {"content": content}
    if client_submission_id is not None:
        body["client_submission_id"] = client_submission_id
    r = client.post(
        f"/reader/records/{record_id}/ask/threads/{thread_id}/messages/stream",
        json=body,
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    frames = parse_sse_frames(r.text)
    return terminal_completed(frames)


def test_retry_canonical_route_preserves_identity_and_persists(api_ctx):
    """Retry over the canonical route on a non-submission-id turn.

    A send WITHOUT ``client_submission_id`` creates the user/assistant
    pair through the stream's sequential ``create_message`` path, whose
    rows carry distinct ``created_at`` values; the retry predecessor
    lookup therefore resolves through the strict fallback and the full
    canonical retry contract (identity reuse, v2 execution trust,
    persistence, cold history) is proven end-to-end. The composer-shape
    (submission-bound) turn is covered separately below.
    """
    client: httpx.Client = api_ctx["client"]
    record_id = api_ctx["record_id"]
    thread_id = api_ctx["thread_id"]

    payload = _send_message(
        client,
        record_id,
        thread_id,
        content="Retry-path question: who founded the library?",
        client_submission_id=None,
    )
    assert_completed_is_deterministic_v2(payload, thread_id=thread_id)
    message_id = payload["message_id"]

    r = client.post(
        f"/reader/records/{record_id}/ask/threads/{thread_id}/messages/{message_id}/retry/stream",
        json={},
    )
    assert r.status_code == 200, r.text
    frames = parse_sse_frames(r.text)
    retried = terminal_completed(frames)
    assert_completed_is_deterministic_v2(retried, thread_id=thread_id)
    assert retried["message_id"] == message_id, (
        "retry must reuse the same assistant message identity"
    )

    r = client.get(f"/reader/records/{record_id}/ask/threads/{thread_id}")
    assert r.status_code == 200, r.text
    messages = r.json()["messages"]
    ours = [m for m in messages if m["role"] == "assistant" and m["id"] == message_id]
    assert len(ours) == 1, "retry must not create a second assistant message"
    assert ours[0]["execution_version"] == EXECUTION_V2
    cold_texts = [b["text"] for b in ours[0]["agentic_answer_blocks"] or []]
    assert DETERMINISTIC_ARTICLE_ANSWER in cold_texts
    assert_public_surface_is_clean(json.dumps(ours[0]))


def test_retry_after_submission_id_turn(api_ctx, send_result):
    """Composer-shape turn (client_submission_id) → retry must work.

    ASK-SUBMISSION-RETRY-R1: the submission gateway binds user +
    assistant in ONE transaction sharing one ``created_at``; retry must
    resolve the predecessor through the explicit
    ``reader_ask_client_submissions`` binding instead of timestamp
    ordering. Regression guard for the pre-R1 404.
    """
    client: httpx.Client = api_ctx["client"]
    record_id = api_ctx["record_id"]
    thread_id = api_ctx["thread_id"]
    payload = send_result["payload"]
    message_id = payload["message_id"]

    r = client.post(
        f"/reader/records/{record_id}/ask/threads/{thread_id}/messages/{message_id}/retry/stream",
        json={},
    )
    assert r.status_code == 200, r.text
    retried = terminal_completed(parse_sse_frames(r.text))
    assert_completed_is_deterministic_v2(retried, thread_id=thread_id)
    assert retried["message_id"] == message_id, (
        "retry of a submission-bound turn must reuse the same assistant message identity"
    )

    r = client.get(f"/reader/records/{record_id}/ask/threads/{thread_id}")
    assert r.status_code == 200, r.text
    messages = r.json()["messages"]
    ours = [m for m in messages if m["role"] == "assistant" and m["id"] == message_id]
    assert len(ours) == 1, "retry must not create a second assistant message"
    assert ours[0]["execution_version"] == EXECUTION_V2
    cold_texts = [b["text"] for b in ours[0]["agentic_answer_blocks"] or []]
    assert DETERMINISTIC_ARTICLE_ANSWER in cold_texts


def test_old_namespaces_absent_and_guard_counter_zero(api_ctx):
    client: httpx.Client = api_ctx["client"]

    for old_path in (
        "/reader-ask/threads",
        "/reader-ask/model-options",
        "/ask/threads",
    ):
        r = client.get(old_path)
        assert r.status_code == 404, (old_path, r.status_code)

    r = client.get("/__deterministic_guard__/provider-calls")
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["installed"] is True
    assert report["blocked_call_count"] == 0, report
    assert report["blocked_attempts"] == []


def _web_reachable() -> bool:
    try:
        r = httpx.get(f"{WEB_BASE}/api/web/session", timeout=5)
        return r.status_code < 500
    except httpx.HTTPError:
        return False


@pytest.fixture(scope="module")
def web_ctx() -> Iterator[dict[str, Any]]:
    if not _web_reachable():
        pytest.skip(f"Web server not reachable at {WEB_BASE}")
    with httpx.Client(base_url=WEB_BASE, timeout=60) as client:
        r = client.post("/api/web/auth/phone/request-code", json={"phone": PHONE})
        assert r.status_code == 200, r.text
        r = client.post(
            "/api/web/auth/phone/verify-code",
            json={"phone": PHONE, "code": "888888"},
        )
        assert r.status_code == 200, r.text
        assert "claread_web_session" in client.cookies, (
            "BFF must establish a real claread_web_session cookie"
        )
        r = client.get("/api/web/session")
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "signed_in", r.text
        yield {"client": client}


def test_bff_ask_send_and_history_through_real_cookie(api_ctx, web_ctx, send_result):
    client: httpx.Client = web_ctx["client"]
    record_id = api_ctx["record_id"]
    thread_id = api_ctx["thread_id"]

    r = client.get(f"/api/web/reader/records/{record_id}/ask/model-options")
    assert r.status_code == 200, r.text

    r = client.get(f"/api/web/reader/records/{record_id}/ask/threads/{thread_id}")
    assert r.status_code == 200, r.text
    detail = r.json()
    assistant = next(
        m for m in detail["messages"] if m["role"] == "assistant" and m.get("agentic_answer_blocks")
    )
    assert assistant["execution_version"] == EXECUTION_V2
    assert any(
        b["text"] == DETERMINISTIC_ARTICLE_ANSWER for b in assistant["agentic_answer_blocks"]
    )


def test_bff_send_new_message_over_sse_and_bff_retry_abi(api_ctx, web_ctx):
    client: httpx.Client = web_ctx["client"]
    record_id = api_ctx["record_id"]
    thread_id = api_ctx["thread_id"]

    r = client.post(
        f"/api/web/reader/records/{record_id}/ask/threads/{thread_id}/messages/stream",
        # Composer-shape body required by the BFF DTO (page_identity +
        # attachments + entry_action).
        # R1: real composer shape INCLUDING client_submission_id — the
        # submission-bound turn is exactly what the real Web composer
        # sends, and retry over it must succeed (binding-first
        # predecessor resolution).
        json={
            "content": "One more deterministic question: how big is it now?",
            "page_identity": {
                "record_id": record_id,
                "title": "Riverside Library",
                "surface": "reader",
                "source": "reader_2_0",
                "available_context_capabilities": [],
                "has_article_overview": False,
                "has_sentence_entries": False,
                "has_annotations": False,
                "has_reader_notes": False,
            },
            "attachments": [],
            "entry_action": "ask_about_this",
            "client_submission_id": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    payload = terminal_completed(parse_sse_frames(r.text))
    assert_completed_is_deterministic_v2(payload, thread_id=thread_id)
    message_id = payload["message_id"]

    # Browser ABI is /retry (never /retry/stream).
    r = client.post(
        f"/api/web/reader/records/{record_id}/ask/threads/{thread_id}/messages/{message_id}/retry"
    )
    assert r.status_code == 200, r.text
    retried = terminal_completed(parse_sse_frames(r.text))
    assert retried["message_id"] == message_id
    assert retried["execution_version"] == EXECUTION_V2


def test_bff_old_reader_ask_namespace_is_404(web_ctx):
    client: httpx.Client = web_ctx["client"]
    r = client.get("/api/web/reader-ask/model-options")
    assert r.status_code == 404, r.status_code
    r = client.get("/api/web/reader-ask/threads")
    assert r.status_code == 404, r.status_code
