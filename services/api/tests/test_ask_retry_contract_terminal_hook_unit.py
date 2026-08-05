# task-history: ASK-RETRY-CONTRACT-R7 (renamed from test_ask_retry_contract_r7.py)
"""Ask retry contract terminal-hook unit gates (no real DB / no migration execution)."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.services.reader_record_ask.submission_gateway import SubmissionTerminalHook

pytestmark = [
    pytest.mark.chain_reader_ask,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.asyncio
async def test_mark_does_not_fire_on_exception() -> None:
    """False-success fix: exception must leave synced=False for compensate."""
    calls = {"n": 0}

    async def boom(**_kwargs: Any) -> None:
        calls["n"] += 1
        raise RuntimeError("transient db blip")

    hook = SubmissionTerminalHook(
        thread_id=uuid4(),
        client_submission_id=uuid4(),
        claim_generation=1,
        assistant_message_id=uuid4(),
    )
    with patch(
        "app.services.reader_record_ask.submission_gateway.mark_submission_terminal",
        new=boom,
    ):
        ok = await hook.mark("completed")
    assert ok is False
    assert hook.synced is False
    assert hook.fired is False
    assert hook.intended_status == "completed"
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_ensure_synced_retries_intended_not_cancelled() -> None:
    """First write fails, second succeeds with same completed status."""
    attempts: list[str] = []

    async def flaky(**kwargs: Any) -> None:
        attempts.append(kwargs["status"])
        if len(attempts) == 1:
            raise RuntimeError("transient")

    hook = SubmissionTerminalHook(
        thread_id=uuid4(),
        client_submission_id=uuid4(),
        claim_generation=1,
    )
    with patch(
        "app.services.reader_record_ask.submission_gateway.mark_submission_terminal",
        new=flaky,
    ):
        assert await hook.mark("completed") is False
        assert hook.intended_status == "completed"
        # Compensate must NOT demote to cancelled.
        assert await hook.ensure_synced(fallback="cancelled") is True
    assert attempts == ["completed", "completed"]
    assert hook.synced is True


@pytest.mark.asyncio
async def test_failed_and_cancelled_preserve_real_status() -> None:
    attempts: list[str] = []

    async def flaky(**kwargs: Any) -> None:
        attempts.append(kwargs["status"])
        if len(attempts) < 2:
            raise RuntimeError("blip")

    hook = SubmissionTerminalHook(
        thread_id=uuid4(),
        client_submission_id=uuid4(),
        claim_generation=2,
    )
    with patch(
        "app.services.reader_record_ask.submission_gateway.mark_submission_terminal",
        new=flaky,
    ):
        await hook.mark("failed")
        await hook.ensure_synced(fallback="cancelled")
    assert attempts == ["failed", "failed"]
    assert hook.intended_status == "failed"

    attempts.clear()
    hook2 = SubmissionTerminalHook(
        thread_id=uuid4(),
        client_submission_id=uuid4(),
        claim_generation=3,
    )
    with patch(
        "app.services.reader_record_ask.submission_gateway.mark_submission_terminal",
        new=flaky,
    ):
        await hook2.mark("cancelled")
        await hook2.ensure_synced(fallback="failed")
    # intended cancelled; second attempt still cancelled (remember keeps it)
    assert attempts == ["cancelled", "cancelled"]


@pytest.mark.asyncio
async def test_remember_never_demotes_completed() -> None:
    hook = SubmissionTerminalHook(
        thread_id=uuid4(),
        client_submission_id=uuid4(),
    )
    hook.remember("completed")
    hook.remember("cancelled")
    assert hook.intended_status == "completed"
    hook.remember("failed")
    assert hook.intended_status == "completed"


def test_agentic_retries_ensure_synced() -> None:
    src = (
        REPO_ROOT
        / "services"
        / "api"
        / "app"
        / "services"
        / "reader_record_ask"
        / "production_stream.py"
    ).read_text(encoding="utf-8")
    assert "ensure_synced" in src
    assert "known_submission_status" in src
    assert "SubmissionTerminalHook" in src


def test_hook_mark_returns_bool() -> None:
    sig = inspect.signature(SubmissionTerminalHook.mark)
    # Return annotation is bool
    assert sig.return_annotation is bool or "bool" in str(sig.return_annotation)


@pytest.mark.asyncio
async def test_stale_generation_still_cas_guarded() -> None:
    """Hook always passes claim_generation through to mark_submission_terminal."""
    seen: dict[str, Any] = {}

    async def capture(**kwargs: Any) -> None:
        seen.update(kwargs)

    gen = 7
    hook = SubmissionTerminalHook(
        thread_id=uuid4(),
        client_submission_id=uuid4(),
        claim_generation=gen,
        assistant_message_id=uuid4(),
    )
    with patch(
        "app.services.reader_record_ask.submission_gateway.mark_submission_terminal",
        new=capture,
    ):
        assert await hook.mark("completed") is True
    assert seen["claim_generation"] == gen
    assert seen["status"] == "completed"


def test_fe_eof_triggers_reconcile_source() -> None:
    panel = (
        REPO_ROOT
        / "apps"
        / "web"
        / "src"
        / "components"
        / "reader"
        / "AiWorkspacePanel.tsx"
    ).read_text(encoding="utf-8")
    assert 'streamResult.kind === "eof"' in panel
    assert 'streamResult.kind === "parse_error"' in panel
    assert "reconcileSubmission" in panel
    # Single helper — catch uses same name
    assert panel.count("const reconcileSubmission") == 1
