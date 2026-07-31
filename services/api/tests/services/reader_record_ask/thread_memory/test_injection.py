"""R1A-A3: memory injection + CAS check tests.

Verifies the R1A integration layer:
- flag=False (snapshot=None) → no memory block, behavior equals today
- flag=True + snapshot=None → no memory block
- flag=True + snapshot=present → memory block injected, charged to 'memory'
- memory block wrapped with ``<transcript_data role="data" ...>``
- CAS mismatch → emergency_full_snapshot rebuild (mocked)

A1/A2 (thread_memory package) are not built yet; tests inject fake
modules into ``sys.modules`` so the lazy imports resolve.
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.reader_record_ask.model_view_budget import (
    RESERVE_MEMORY,
    ModelViewRenderer,
    ModelVisibleTurnBudget,
)
from app.services.reader_record_ask.turn_prompt import (
    TurnFramePromptCapability,
    _build_memory_section,
    mint_turn_frame_prompt_capability,
)

# ---------------------------------------------------------------------------
# Fake thread_memory module scaffolding (A1/A2 not built yet)
# ---------------------------------------------------------------------------

_THREAD_MEMORY_PKG = "app.services.reader_record_ask.thread_memory"
_SUBMODULES = (
    "render",
    "allowlist",
    "emergency",
    "fence",
    "repository",
    "schema",
    "mapping",
    "preparation",
)


@pytest.fixture(autouse=True)
def _fake_thread_memory(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Auto-fixture: force fake thread_memory modules for every test.

    Uses ``monkeypatch.setitem(sys.modules, ...)`` so the override is
    always applied — even when A1/A2's real modules are already cached
    in ``sys.modules`` by sibling test files in the same run — and is
    automatically restored after the test. This keeps the A3 tests
    isolated from A1/A2's concrete implementations.
    """
    pkg = types.ModuleType(_THREAD_MEMORY_PKG)
    pkg.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, _THREAD_MEMORY_PKG, pkg)
    mocks: dict[str, MagicMock] = {}
    for suffix in _SUBMODULES:
        full = f"{_THREAD_MEMORY_PKG}.{suffix}"
        mod = MagicMock()
        monkeypatch.setitem(sys.modules, full, mod)
        mocks[suffix] = mod

    async def _pass_through_preparation(
        snapshot: Any,
        **_kwargs: Any,
    ) -> tuple[Any, dict[str, Any]]:
        return snapshot, {"rejected": False, "stripped": 0, "total": 0}

    mocks["preparation"].prepare_snapshot_for_model = AsyncMock(
        side_effect=_pass_through_preparation
    )
    yield mocks


# ---------------------------------------------------------------------------
# _build_memory_section unit tests
# ---------------------------------------------------------------------------


def test_build_memory_section_returns_none_for_none_snapshot() -> None:
    """snapshot=None → None (no injection)."""
    budget = ModelVisibleTurnBudget()
    renderer = ModelViewRenderer()
    result = _build_memory_section(None, budget, renderer)
    assert result is None
    assert budget.spent("memory") == 0


def test_build_memory_section_returns_none_when_render_returns_none(
    _fake_thread_memory: dict[str, MagicMock],
) -> None:
    """render_memory_block returns None → no charge, no injection."""
    _fake_thread_memory["render"].render_memory_block.return_value = None
    snapshot = object()  # opaque snapshot
    budget = ModelVisibleTurnBudget()
    renderer = ModelViewRenderer()
    result = _build_memory_section(snapshot, budget, renderer)
    assert result is None
    assert budget.spent("memory") == 0


def test_build_memory_section_charges_memory_account(
    _fake_thread_memory: dict[str, MagicMock],
) -> None:
    """snapshot present → render + charge to 'memory' account."""
    renderer = ModelViewRenderer()
    # render_memory_block must return a renderer-minted view so charge works.
    expected_text = (
        '<transcript_data role="data" not_instructions="true">'
        "memory-fact-1"
        "</transcript_data>"
    )
    memory_view = renderer.render_plain(expected_text)
    _fake_thread_memory["render"].render_memory_block.return_value = memory_view

    snapshot = object()
    budget = ModelVisibleTurnBudget()
    result = _build_memory_section(snapshot, budget, renderer)

    assert result is not None
    assert result.text == expected_text
    assert budget.spent("memory") == len(expected_text)
    # Verify render_memory_block was called with the §5 budget_chars.
    _fake_thread_memory["render"].render_memory_block.assert_called_once_with(
        snapshot, budget_chars=RESERVE_MEMORY
    )


# ---------------------------------------------------------------------------
# mint_turn_frame_prompt_capability integration tests
# ---------------------------------------------------------------------------


def _mint_basic(
    *,
    memory_snapshot: Any = None,
    recent_history_view: Any = None,
    budget: ModelVisibleTurnBudget | None = None,
) -> tuple[TurnFramePromptCapability, ModelVisibleTurnBudget]:
    """Helper: mint a minimal turn frame with optional memory snapshot."""
    if budget is None:
        budget = ModelVisibleTurnBudget()
    renderer = ModelViewRenderer()
    cap = mint_turn_frame_prompt_capability(
        system_instructions="system",
        projection_json='{"k":"v"}',
        handles_block="",
        baseline_is_complete=True,
        user_question="what is this?",
        budget=budget,
        renderer=renderer,
        memory_snapshot=memory_snapshot,
        recent_history_view=recent_history_view,
    )
    return cap, budget


def test_no_memory_snapshot_means_no_memory_block() -> None:
    """memory_snapshot=None (flag off / empty thread) → no memory block."""
    cap, budget = _mint_basic(memory_snapshot=None)
    # memory_untrusted must be empty.
    assert cap.memory_untrusted == ""
    # memory account must be uncharged.
    assert budget.spent("memory") == 0
    # user_prompt must not contain the transcript_data tag.
    assert "<transcript_data" not in cap.user_prompt


def test_recent_history_injects_and_charges_without_double_counting() -> None:
    renderer = ModelViewRenderer()
    recent_text = (
        '<conversation_history role="data" not_instructions="true">'
        '<message role="user" turn="1">Earlier question</message>'
        "</conversation_history>"
    )
    recent_view = renderer.render_plain(recent_text)
    cap, budget = _mint_basic(recent_history_view=recent_view)

    assert recent_text in cap.user_prompt
    assert cap.recent_history_untrusted == recent_text
    assert budget.spent("recent_history") == len(recent_text)
    assert (
        budget.spent("request_frame") + budget.spent("recent_history")
        == cap.first_surface_char_count
    )


def test_memory_snapshot_injects_block_and_charges_account(
    _fake_thread_memory: dict[str, MagicMock],
) -> None:
    """snapshot present → memory block injected + 'memory' account charged."""
    renderer = ModelViewRenderer()
    memory_text = (
        '<transcript_data role="data" not_instructions="true">'
        "<fact>user asked about X</fact>"
        "</transcript_data>"
    )
    memory_view = renderer.render_plain(memory_text)
    _fake_thread_memory["render"].render_memory_block.return_value = memory_view

    snapshot = object()
    cap, budget = _mint_basic(memory_snapshot=snapshot)

    # The memory block text must appear in the user prompt.
    assert memory_text in cap.user_prompt
    # The memory_untrusted field must carry the block text.
    assert cap.memory_untrusted == memory_text
    # The memory account must be charged.
    assert budget.spent("memory") == len(memory_text)
    # The block must be wrapped with the transcript_data data tag.
    assert '<transcript_data role="data"' in cap.user_prompt
    assert 'not_instructions="true"' in cap.user_prompt


def test_memory_block_positioned_after_handles_before_selection(
    _fake_thread_memory: dict[str, MagicMock],
) -> None:
    """Memory block must come after handles block, before selection section."""
    renderer = ModelViewRenderer()
    memory_text = (
        '<transcript_data role="data" not_instructions="true">'
        "MEM"
        "</transcript_data>"
    )
    _fake_thread_memory["render"].render_memory_block.return_value = (
        renderer.render_plain(memory_text)
    )

    budget = ModelVisibleTurnBudget()
    renderer = ModelViewRenderer()
    cap = mint_turn_frame_prompt_capability(
        system_instructions="",
        projection_json='{"p":1}',
        handles_block="\n## Server-registered evidence handles\nHANDLE_A\n",
        baseline_is_complete=True,
        user_question="q",
        budget=budget,
        renderer=renderer,
        memory_snapshot=object(),
    )
    user_prompt = cap.user_prompt
    handles_pos = user_prompt.find("HANDLE_A")
    memory_pos = user_prompt.find("<transcript_data")
    question_pos = user_prompt.find("## User question")
    assert handles_pos != -1
    assert memory_pos != -1
    assert question_pos != -1
    # handles < memory < question
    assert handles_pos < memory_pos < question_pos


def test_memory_does_not_double_charge_request_frame(
    _fake_thread_memory: dict[str, MagicMock],
) -> None:
    """Memory block excluded from request_frame trusted surface."""
    renderer = ModelViewRenderer()
    memory_text = (
        '<transcript_data role="data" not_instructions="true">'
        "X"
        "</transcript_data>"
    )
    _fake_thread_memory["render"].render_memory_block.return_value = (
        renderer.render_plain(memory_text)
    )

    budget = ModelVisibleTurnBudget()
    cap, budget = _mint_basic(memory_snapshot=object(), budget=budget)

    # request_frame charge must NOT include the memory block chars.
    # The trusted frame = system + "\n" + user_prompt_minus_untrusted_bodies.
    # Since memory_untrusted == memory_text, it must be removed from the
    # request_frame charge.
    assert budget.spent("memory") == len(memory_text)
    # The request_frame charge should be the same as if no memory was
    # injected (modulo the memory_section placeholder in user_prompt).
    # Verify: request_frame_spent + memory_spent == first_surface_char_count.
    assert (
        budget.spent("request_frame") + budget.spent("memory")
        == cap.first_surface_char_count
    )


# ---------------------------------------------------------------------------
# CAS check tests (turn_coordinator._load_memory_snapshot)
# ---------------------------------------------------------------------------


class _FakeSnapshot:
    """Minimal fake snapshot for CAS / fence tests.

    R1B fix: production code now reads source_bindings from
    ``snapshot.episodes[*].source_bindings`` (schema §6), not from the
    snapshot root. Tests construct fake episodes to match.

    R1.5 P0-4 fix: production code now calls ``snapshot.model_copy(...)``
    during fence rebuild + ``validate_snapshot`` returns a tuple. The
    fake must support both so the CAS-match / fence-exception paths can
    reach the final ``return snapshot``.
    """

    def __init__(
        self,
        *,
        watermark: str = "match",
        source_bindings: list[Any] | None = None,
        episodes: list[Any] | None = None,
    ) -> None:
        self.watermark = watermark
        # Build episodes from source_bindings for backwards-compat with
        # the old root-level field; production code reads ep.source_bindings.
        if episodes is not None:
            self.episodes = episodes
        else:
            self.episodes = [
                _FakeEpisode(source_bindings=source_bindings or [])
            ]

    def model_copy(self, *, update: dict[str, Any] | None = None) -> _FakeSnapshot:
        """Pydantic-compatible shim: return a shallow copy with updates."""
        clone = _FakeSnapshot(
            watermark=self.watermark,
            episodes=(update or {}).get("episodes", self.episodes),
        )
        return clone


class _FakeEpisode:
    """Minimal fake episode for fence tests."""

    def __init__(
        self,
        *,
        source_bindings: list[Any] | None = None,
        structured_facts: list[Any] | None = None,
    ) -> None:
        self.source_bindings = source_bindings or []
        # R1.5 P0-4: production code checks ``ep.structured_facts`` in the
        # final guard before returning. Default to a non-empty list so the
        # snapshot is not discarded as empty.
        self.structured_facts = structured_facts if structured_facts is not None else [
            object()
        ]

    def model_copy(self, *, update: dict[str, Any] | None = None) -> _FakeEpisode:
        """Pydantic-compatible shim for fence rebuild path."""
        upd = update or {}
        clone = _FakeEpisode(
            source_bindings=upd.get("source_bindings", self.source_bindings),
            structured_facts=upd.get("structured_facts", self.structured_facts),
        )
        return clone


def _make_coordinator(
    *,
    memory_enabled: bool = True,
    thread_id: str = "11111111-1111-4111-8111-111111111111",
) -> Any:
    """Build a TurnCoordinator with memory enabled + mock repository.

    R1.5 P0-1: ``thread_id`` MUST be a valid UUID string — production
    code now unifies on UUID and rejects non-UUID values (fail-soft →
    None). The old ``"thread-1"`` placeholder broke CAS tests because
    the UUID parse fails before any repository call is made.
    """
    from uuid import UUID

    from app.services.reader_record_ask.context_envelope import (
        VerifiedEnvelopeInput,
        build_context_envelope,
    )
    from app.services.reader_record_ask.document_access import DocumentAccess
    from app.services.reader_record_ask.turn_coordinator import TurnCoordinator

    envelope = build_context_envelope(
        VerifiedEnvelopeInput(
            user_id=UUID("11111111-1111-1111-1111-111111111111"),
            reading_record_id=UUID("22222222-2222-2222-2222-222222222222"),
            base_id=UUID("33333333-3333-3333-3333-333333333333"),
            record_generation=1,
            product_state="ready",
            readiness_state="ready",
        )
    )
    document_access = MagicMock(spec=DocumentAccess)
    repo = MagicMock()
    coord = TurnCoordinator(
        envelope=envelope,
        document_access=document_access,
        user_message="hello",
        system_instructions="sys",
        memory_enabled=memory_enabled,
        memory_repository=repo,
        thread_id=thread_id,
    )
    return coord


async def test_cas_match_uses_existing_snapshot(
    _fake_thread_memory: dict[str, MagicMock],
) -> None:
    """When watermark matches, the existing snapshot is used (no rebuild)."""
    coord = _make_coordinator()
    snapshot = _FakeSnapshot(watermark="match")
    coord.memory_repository.load_canonical_memory_view = AsyncMock(
        return_value=types.SimpleNamespace(
            snapshot=snapshot,
            canonical_messages=("msg1", "msg2"),
            ok_turn_runs=(),
        )
    )
    _fake_thread_memory["allowlist"].compute_watermark.return_value = "match"
    # R1.5 P0-4: validate_snapshot returns (snapshot, metrics). Configure
    # the fake to pass through (rejected=False) so the snapshot survives.
    _fake_thread_memory["allowlist"].validate_snapshot.return_value = (
        snapshot,
        {"rejected": False, "stripped": 0, "total": 0},
    )
    _fake_thread_memory["allowlist"].build_allowlist.return_value = set()
    _fake_thread_memory["emergency"].emergency_full_snapshot = MagicMock(
        return_value=snapshot
    )

    result = await coord._load_memory_snapshot()

    assert result is snapshot
    _fake_thread_memory["emergency"].emergency_full_snapshot.assert_not_called()


async def test_cas_mismatch_triggers_emergency_rebuild(
    _fake_thread_memory: dict[str, MagicMock],
) -> None:
    """Watermark mismatch → emergency_full_snapshot called to rebuild."""
    coord = _make_coordinator()
    stale_snapshot = _FakeSnapshot(watermark="stale")
    rebuilt_snapshot = _FakeSnapshot(watermark="fresh")
    coord.memory_repository.load_canonical_memory_view = AsyncMock(
        return_value=types.SimpleNamespace(
            snapshot=stale_snapshot,
            canonical_messages=("msg1",),
            ok_turn_runs=(),
        )
    )
    _fake_thread_memory["allowlist"].compute_watermark.return_value = "fresh"
    # R1.5 P0-4: validate_snapshot must pass on the rebuilt snapshot.
    _fake_thread_memory["allowlist"].validate_snapshot.return_value = (
        rebuilt_snapshot,
        {"rejected": False, "stripped": 0, "total": 0},
    )
    _fake_thread_memory["allowlist"].build_allowlist.return_value = set()
    _fake_thread_memory["emergency"].emergency_full_snapshot = MagicMock(
        return_value=rebuilt_snapshot
    )

    result = await coord._load_memory_snapshot()

    # emergency_full_snapshot must have been called with canonical messages.
    assert result is rebuilt_snapshot
    _fake_thread_memory["emergency"].emergency_full_snapshot.assert_called_once()
    call_kwargs = (
        _fake_thread_memory["emergency"].emergency_full_snapshot.call_args
    )
    assert call_kwargs.kwargs["canonical_messages"] == ["msg1"]
    assert call_kwargs.kwargs["thread_id"] == "11111111-1111-4111-8111-111111111111"


async def test_cas_mismatch_emergency_returns_none_skips_injection(
    _fake_thread_memory: dict[str, MagicMock],
) -> None:
    """emergency rebuild returns None → skip injection (behavior = today)."""
    coord = _make_coordinator()
    stale_snapshot = _FakeSnapshot(watermark="stale")
    coord.memory_repository.load_canonical_memory_view = AsyncMock(
        return_value=types.SimpleNamespace(
            snapshot=stale_snapshot,
            canonical_messages=(),
            ok_turn_runs=(),
        )
    )
    _fake_thread_memory["allowlist"].compute_watermark.return_value = "fresh"
    _fake_thread_memory["allowlist"].build_allowlist.return_value = set()
    _fake_thread_memory["emergency"].emergency_full_snapshot = MagicMock(
        return_value=None
    )

    result = await coord._load_memory_snapshot()

    assert result is None


async def test_no_snapshot_means_none(
    _fake_thread_memory: dict[str, MagicMock],
) -> None:
    """Repository returns None (empty thread) → no injection."""
    coord = _make_coordinator()
    coord.memory_repository.load_canonical_memory_view = AsyncMock(
        return_value=types.SimpleNamespace(
            snapshot=None,
            canonical_messages=(),
            ok_turn_runs=(),
        )
    )
    _fake_thread_memory["allowlist"].build_allowlist.return_value = set()
    _fake_thread_memory["emergency"].emergency_full_snapshot = MagicMock(
        return_value=None
    )

    result = await coord._load_memory_snapshot()

    assert result is None


async def test_memory_disabled_returns_none() -> None:
    """memory_enabled=False → immediate None, no repository calls."""
    coord = _make_coordinator(memory_enabled=False)
    coord.memory_repository = MagicMock()
    coord.memory_repository.load_canonical_memory_view = AsyncMock()

    result = await coord._load_memory_snapshot()

    assert result is None
    coord.memory_repository.load_canonical_memory_view.assert_not_called()


async def test_fence_check_does_not_abort_on_exception(
    _fake_thread_memory: dict[str, MagicMock],
) -> None:
    """R1.6 P1-1: fence check failure → no memory injection (return None).

    The Ask pipeline itself does NOT crash — it continues without memory.
    Old bindings must NEVER be reused because they may carry a stale
    ``validity_check='valid'`` from a previous run.
    """
    coord = _make_coordinator()
    snapshot = _FakeSnapshot(
        watermark="match",
        source_bindings=[{"binding": "b1"}],
    )
    coord.memory_repository.load_canonical_memory_view = AsyncMock(
        return_value=types.SimpleNamespace(
            snapshot=snapshot,
            canonical_messages=("msg1",),
            ok_turn_runs=(),
        )
    )
    _fake_thread_memory["allowlist"].compute_watermark.return_value = "match"
    # R1.5 P0-4: validate_snapshot must pass through.
    _fake_thread_memory["allowlist"].validate_snapshot.return_value = (
        snapshot,
        {"rejected": False, "stripped": 0, "total": 0},
    )
    _fake_thread_memory["allowlist"].build_allowlist.return_value = set()
    # The production preparation seam owns Host materialization + fence.
    _fake_thread_memory[
        "preparation"
    ].prepare_snapshot_for_model.side_effect = RuntimeError(
        "fence failure"
    )

    result = await coord._load_memory_snapshot()

    # R1.6 P1-1: fence crash → no validity info → return None (no memory).
    assert result is None
    _fake_thread_memory[
        "preparation"
    ].prepare_snapshot_for_model.assert_awaited_once()
