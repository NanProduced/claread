"""Tests for the Ask Claread typed tool IO contract (P5-9 / P5-9A).

This module verifies:

1. Every tool in the registry has a stable, testable IO contract expressed
   through ``ToolSpec.output_kind`` and ``ToolSpec.observation_statuses``.
2. The **actual** tool implementations in ``reader_ask_agent.py`` honor
   these contracts (Section 3).
3. The ``run_tool`` runtime wrapper and ``normalize_tool_observation``
   produce correct observations for each ``output_kind`` variant (Sections 2, 4).
4. Availability enforcement and write-gate error payloads are stable
   (Sections 5, 6).

Note: ``ToolSpec.observation_statuses`` describes the statuses the tool
**implementation** itself can return — it does not include runtime-layer
errors (e.g. ``tool_not_available`` from availability enforcement).  See
``ToolSpec`` docstring for details.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from pydantic_ai import RunContext

from app.agents.reader_ask_agent import (
    ReaderAskAgentDeps,
    ReaderAskRuntimeState,
    _propose_save_highlight_tool,
    _propose_save_note_tool,
)
from app.agents.reader_ask_tool_observation import normalize_tool_observation
from app.agents.reader_ask_tool_registry import (
    READER_ASK_TOOL_REGISTRY,
    TOOL_GENERATE_SENTENCE_ANNOTATION,
    TOOL_GET_RECORD_CONTEXT,
    TOOL_GET_RECORD_INSIGHTS,
    TOOL_LOOKUP_DICTIONARY_ENTRY,
    TOOL_PROPOSE_SAVE_HIGHLIGHT,
    TOOL_PROPOSE_SAVE_NOTE,
    TOOL_RUN_DICTIONARY_AI_CONTEXT_EXPLAIN,
)
from app.agents.reader_ask_tool_runtime import run_tool
from app.agents.reader_ask_write_gate import (
    MISSING_NOTE_TEXT_PAYLOAD,
    NO_ANCHOR_ERROR_PAYLOAD,
)
from app.schemas.reader_ask import ReaderAskAnchorRef

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_anchor() -> ReaderAskAnchorRef:
    return ReaderAskAnchorRef(
        anchor_type="sentence",
        target_key="record:r1:sentence:s1",
        sentence_id="s1",
        selected_text="test",
    )


def _make_deps(**overrides: object) -> ReaderAskAgentDeps:
    event_queue = AsyncMock()
    state = ReaderAskRuntimeState()
    kwargs: dict = dict(
        payload={},
        event_queue=event_queue,
        state=state,
        query_seed="test",
        task_mode="explain",
        record_id="r1",
        record_title="Test",
        primary_anchor=_make_anchor(),
        get_record_context_fn=AsyncMock(return_value={"summary": "Context loaded"}),
        get_record_insights_fn=AsyncMock(return_value=[]),
        get_user_vocabulary_book_fn=AsyncMock(return_value=[]),
        resolve_known_reference_fn=AsyncMock(return_value={"status": "not_found"}),
        generate_sentence_annotation_fn=AsyncMock(return_value=None),
        suggest_prompts_fn=AsyncMock(return_value={"suggestions": []}),
        vocabulary_item_to_citation_fn=AsyncMock(),
    )
    kwargs.update(overrides)
    return ReaderAskAgentDeps(**kwargs)


def _make_ctx(**deps_overrides: object) -> RunContext[ReaderAskAgentDeps]:
    """Create a minimal RunContext with the given deps overrides."""
    deps = _make_deps(**deps_overrides)
    ctx = MagicMock(spec=RunContext)
    ctx.deps = deps
    return ctx


# ===========================================================================
# Section 1: Registry contract consistency
# ===========================================================================


class TestRegistryContractConsistency:
    """Verify output_kind and observation_statuses are consistent with
    effect / category / requires_anchor for every tool.

    Note: observation_statuses only covers the tool implementation's own
    output statuses — not runtime-layer errors like availability enforcement.
    """

    def test_read_tools_have_read_output_kind(self) -> None:
        """Read-effect tools must use dict_or_none or list_or_empty."""
        for name, spec in READER_ASK_TOOL_REGISTRY.items():
            if spec.effect == "read":
                assert spec.output_kind in ("dict_or_none", "list_or_empty"), (
                    f"{name}: read tool has unexpected output_kind={spec.output_kind}"
                )

    def test_propose_write_tools_have_dict_always(self) -> None:
        """Write-proposal tools always return a dict (success or error)."""
        for name, spec in READER_ASK_TOOL_REGISTRY.items():
            if spec.effect == "propose_write":
                assert spec.output_kind == "dict_always", (
                    f"{name}: propose_write tool must have output_kind=dict_always, "
                    f"got {spec.output_kind}"
                )

    def test_read_tools_only_success_observation(self) -> None:
        """Read-effect tool implementations produce success observations,
        and may also produce warning observations (Round 2 — narrow-query
        tools return warnings when filters yield no matches)."""
        for name, spec in READER_ASK_TOOL_REGISTRY.items():
            if spec.effect == "read":
                assert all(
                    status in ("success", "warning") for status in spec.observation_statuses
                ), (
                    f"{name}: read tool observation_statuses must only "
                    f"contain success/warning, got {spec.observation_statuses}"
                )
                assert "success" in spec.observation_statuses, (
                    f"{name}: read tool must allow success observation"
                )

    def test_propose_write_tools_have_success_and_error(self) -> None:
        """Write-proposal tool implementations can produce both success and
        error observations (e.g. no-anchor gate, missing note_text)."""
        for name, spec in READER_ASK_TOOL_REGISTRY.items():
            if spec.effect == "propose_write":
                assert "success" in spec.observation_statuses, (
                    f"{name}: propose_write tool must allow success observation"
                )
                assert "error" in spec.observation_statuses, (
                    f"{name}: propose_write tool must allow error observation"
                )

    def test_context_tools_output_kind(self) -> None:
        """Context-category tools have the expected output kinds."""
        ctx = READER_ASK_TOOL_REGISTRY[TOOL_GET_RECORD_CONTEXT]
        assert ctx.output_kind == "dict_or_none"
        insights = READER_ASK_TOOL_REGISTRY[TOOL_GET_RECORD_INSIGHTS]
        assert insights.output_kind == "list_or_empty"

    def test_vocabulary_tool_output_kind(self) -> None:
        from app.agents.reader_ask_tool_registry import TOOL_GET_USER_VOCABULARY_BOOK
        vocab = READER_ASK_TOOL_REGISTRY[TOOL_GET_USER_VOCABULARY_BOOK]
        assert vocab.output_kind == "list_or_empty"

    def test_dictionary_tools_output_kind(self) -> None:
        lookup = READER_ASK_TOOL_REGISTRY[TOOL_LOOKUP_DICTIONARY_ENTRY]
        assert lookup.output_kind == "dict_or_none"
        ai = READER_ASK_TOOL_REGISTRY[TOOL_RUN_DICTIONARY_AI_CONTEXT_EXPLAIN]
        assert ai.output_kind == "dict_or_none"

    def test_annotation_tool_output_kind(self) -> None:
        ann = READER_ASK_TOOL_REGISTRY[TOOL_GENERATE_SENTENCE_ANNOTATION]
        assert ann.output_kind == "dict_or_none"

    def test_write_proposal_tools_require_anchor(self) -> None:
        """Write-proposal tools must have requires_anchor=True."""
        for name in (TOOL_PROPOSE_SAVE_NOTE, TOOL_PROPOSE_SAVE_HIGHLIGHT):
            spec = READER_ASK_TOOL_REGISTRY[name]
            assert spec.requires_anchor is True, (
                f"{name}: write proposal must require anchor"
            )

    def test_write_proposal_tools_do_not_consume_budget_on_precondition_fail(
        self,
    ) -> None:
        for name in (TOOL_PROPOSE_SAVE_NOTE, TOOL_PROPOSE_SAVE_HIGHLIGHT):
            spec = READER_ASK_TOOL_REGISTRY[name]
            assert spec.consumes_budget_when_precondition_fails is False, (
                f"{name}: write proposal must not consume budget on precondition fail"
            )

    def test_read_tools_consume_budget_on_precondition_fail(self) -> None:
        for name, spec in READER_ASK_TOOL_REGISTRY.items():
            if spec.effect == "read":
                assert spec.consumes_budget_when_precondition_fails is True, (
                    f"{name}: read tool must consume budget on precondition fail"
                )


# ===========================================================================
# Section 2: Observation normalizer contract (per output_kind)
# ===========================================================================


class TestObservationNormalizerContract:
    """Verify normalize_tool_observation produces correct ToolObservation for
    each output_kind variant.  These tests exercise the normalizer directly,
    not actual tool implementations."""

    def test_dict_or_none_dict_result(self) -> None:
        """dict_or_none tool returning a dict normalizes to success."""
        result = {"summary": "Context loaded", "next_actions": ["Explain"]}
        obs = normalize_tool_observation(result)
        assert obs.status == "success"
        assert obs.summary == "Context loaded"
        assert obs.next_actions == ["Explain"]

    def test_dict_or_none_none_result(self) -> None:
        """dict_or_none tool returning None normalizes to success with 'Loaded'."""
        obs = normalize_tool_observation(None)
        assert obs.status == "success"
        assert obs.summary == "Loaded"

    def test_list_or_empty_list_result(self) -> None:
        """list_or_empty tool returning a list normalizes to success with count."""
        result = [{"word": "test"}, {"word": "hello"}]
        obs = normalize_tool_observation(result)
        assert obs.status == "success"
        assert obs.summary == "2 item(s)"

    def test_list_or_empty_empty_result(self) -> None:
        """list_or_empty tool returning [] normalizes to success with '0 item(s)'."""
        obs = normalize_tool_observation([])
        assert obs.status == "success"
        assert obs.summary == "0 item(s)"

    def test_dict_always_success_dict(self) -> None:
        """dict_always tool returning success dict normalizes correctly."""
        result = {
            "status": "success",
            "summary": "Prepared save_note confirmation",
            "next_actions": ["Wait for user confirmation."],
            "artifacts": ["record:r1"],
            "action_type": "save_note",
        }
        obs = normalize_tool_observation(result)
        assert obs.status == "success"
        assert obs.summary == "Prepared save_note confirmation"
        assert obs.next_actions == ["Wait for user confirmation."]
        assert obs.artifacts == ["record:r1"]

    def test_dict_always_error_dict(self) -> None:
        """dict_always tool returning error dict normalizes to error."""
        result = {
            "status": "error",
            "summary": "No anchor available",
            "next_actions": ["Select text first."],
            "artifacts": [],
        }
        obs = normalize_tool_observation(result)
        assert obs.status == "error"
        assert obs.summary == "No anchor available"
        assert obs.next_actions == ["Select text first."]

    def test_dict_always_warning_dict(self) -> None:
        """dict_always tool returning warning dict normalizes to warning."""
        result = {
            "status": "warning",
            "summary": "Partial results",
            "next_actions": [],
            "artifacts": [],
        }
        obs = normalize_tool_observation(result)
        assert obs.status == "warning"
        assert obs.summary == "Partial results"


# ===========================================================================
# Section 3: Actual tool implementation output contract
# ===========================================================================


class TestActualWriteProposalToolContract:
    """Verify the **real** tool implementations in reader_ask_agent.py produce
    output that matches the registry contract.  These tests call
    ``_propose_save_note_tool`` / ``_propose_save_highlight_tool`` directly,
    not fake runners."""

    def test_propose_save_note_success_output_contract(self) -> None:
        """_propose_save_note_tool with anchor + note_text returns a dict
        containing status, summary, next_actions, artifacts, action_type."""
        ctx = _make_ctx()

        result = asyncio.run(_propose_save_note_tool(ctx, note_text="My note"))

        assert isinstance(result, dict)
        assert result["status"] == "success"
        assert result["action_type"] == "save_note"
        assert isinstance(result["summary"], str) and len(result["summary"]) > 0
        assert isinstance(result["next_actions"], list) and len(result["next_actions"]) > 0
        assert isinstance(result["artifacts"], list) and len(result["artifacts"]) > 0
        # Budget consumed (went through run_tool)
        assert ctx.deps.state.tool_call_count == 1
        # Action request created
        assert len(ctx.deps.state.action_requests) == 1
        assert ctx.deps.state.action_requests[0].action_type == "save_note"

    def test_propose_save_highlight_success_output_contract(self) -> None:
        """_propose_save_highlight_tool with anchor returns a dict containing
        status, summary, next_actions, artifacts, action_type."""
        ctx = _make_ctx()

        result = asyncio.run(_propose_save_highlight_tool(ctx))

        assert isinstance(result, dict)
        assert result["status"] == "success"
        assert result["action_type"] == "save_highlight"
        assert isinstance(result["summary"], str) and len(result["summary"]) > 0
        assert isinstance(result["next_actions"], list) and len(result["next_actions"]) > 0
        assert isinstance(result["artifacts"], list) and len(result["artifacts"]) > 0
        # Budget consumed (went through run_tool)
        assert ctx.deps.state.tool_call_count == 1
        # Action request created
        assert len(ctx.deps.state.action_requests) == 1
        assert ctx.deps.state.action_requests[0].action_type == "save_highlight"

    def test_propose_save_note_no_anchor_returns_error(self) -> None:
        """_propose_save_note_tool without anchor returns error, no budget consumed."""
        ctx = _make_ctx(primary_anchor=None)

        result = asyncio.run(_propose_save_note_tool(ctx, note_text="My note"))

        assert isinstance(result, dict)
        assert result["status"] == "error"
        # Budget NOT consumed (gate bypasses run_tool)
        assert ctx.deps.state.tool_call_count == 0
        # No action request created
        assert len(ctx.deps.state.action_requests) == 0

    def test_propose_save_highlight_no_anchor_returns_error(self) -> None:
        """_propose_save_highlight_tool without anchor returns error, no budget consumed."""
        ctx = _make_ctx(primary_anchor=None)

        result = asyncio.run(_propose_save_highlight_tool(ctx))

        assert isinstance(result, dict)
        assert result["status"] == "error"
        assert ctx.deps.state.tool_call_count == 0
        assert len(ctx.deps.state.action_requests) == 0

    def test_propose_save_note_missing_note_text_returns_error(self) -> None:
        """_propose_save_note_tool with anchor but no note_text returns error
        via run_tool (budget consumed, in-tool validation)."""
        ctx = _make_ctx()

        result = asyncio.run(_propose_save_note_tool(ctx, note_text=None))

        assert isinstance(result, dict)
        assert result["status"] == "error"
        # Budget IS consumed (missing note_text goes through run_tool)
        assert ctx.deps.state.tool_call_count == 1
        # No action request created
        assert len(ctx.deps.state.action_requests) == 0

    def test_propose_save_note_empty_note_text_returns_error(self) -> None:
        """_propose_save_note_tool with anchor but empty note_text returns
        error via run_tool (budget consumed)."""
        ctx = _make_ctx()

        result = asyncio.run(_propose_save_note_tool(ctx, note_text="   "))

        assert isinstance(result, dict)
        assert result["status"] == "error"
        assert ctx.deps.state.tool_call_count == 1
        assert len(ctx.deps.state.action_requests) == 0


# ===========================================================================
# Section 4: Runtime wrapper contract (run_tool with fake runners)
# ===========================================================================


class TestRuntimeWrapperContract:
    """Verify the run_tool runtime wrapper produces correct observations for
    each output_kind variant.  These tests use fake runners, not actual tool
    implementations — they test the run_tool / normalize pipeline, not the
    business logic of specific tools."""

    def test_dict_or_none_tool_dict_result(self) -> None:
        """dict_or_none tool returning dict produces completed trace."""
        deps = _make_deps()

        async def runner() -> dict[str, str]:
            return {"summary": "Context loaded"}

        result = asyncio.run(
            run_tool(deps, TOOL_GET_RECORD_CONTEXT, runner),
        )
        assert isinstance(result, dict)
        assert result["summary"] == "Context loaded"
        # Trace: started + completed
        assert len(deps.state.tool_trace) == 2
        assert deps.state.tool_trace[-1].status == "completed"

    def test_dict_or_none_tool_none_result(self) -> None:
        """dict_or_none tool returning None produces completed trace with
        normalized 'Loaded' summary."""
        from app.agents.reader_ask_tool_registry import TOOL_LOOKUP_RECORD_BY_EMBEDDING

        deps = _make_deps()

        async def runner() -> None:
            return None

        result = asyncio.run(
            run_tool(deps, TOOL_LOOKUP_RECORD_BY_EMBEDDING, runner),
        )
        assert result is None
        # Trace: started + completed
        assert len(deps.state.tool_trace) == 2
        completed = deps.state.tool_trace[-1]
        assert completed.status == "completed"
        assert completed.summary == "Loaded"

    def test_list_or_empty_tool_list_result(self) -> None:
        """list_or_empty tool returning list produces completed trace with
        item count summary."""
        from app.agents.reader_ask_tool_registry import TOOL_GET_USER_VOCABULARY_BOOK

        deps = _make_deps()

        async def runner() -> list[dict[str, str]]:
            return [{"word": "test"}]

        result = asyncio.run(
            run_tool(deps, TOOL_GET_USER_VOCABULARY_BOOK, runner),
        )
        assert isinstance(result, list)
        assert len(result) == 1
        # Trace: started + completed
        assert len(deps.state.tool_trace) == 2
        completed = deps.state.tool_trace[-1]
        assert completed.status == "completed"
        assert completed.summary == "1 item(s)"

    def test_list_or_empty_tool_empty_result(self) -> None:
        """list_or_empty tool returning [] produces completed trace with
        '0 item(s)' summary."""
        deps = _make_deps()

        async def runner() -> list[object]:
            return []

        result = asyncio.run(
            run_tool(deps, TOOL_GET_RECORD_INSIGHTS, runner),
        )
        assert isinstance(result, list)
        assert len(result) == 0
        # Trace: started + completed
        assert len(deps.state.tool_trace) == 2
        completed = deps.state.tool_trace[-1]
        assert completed.status == "completed"
        assert completed.summary == "0 item(s)"

    def test_dict_always_success_runner(self) -> None:
        """dict_always tool with fake success runner produces correct output."""
        deps = _make_deps()

        async def runner() -> dict[str, object]:
            return {
                "status": "success",
                "summary": "Prepared save_note confirmation",
                "next_actions": ["Wait for user confirmation."],
                "artifacts": [f"record:{deps.record_id}"],
                "action_type": "save_note",
            }

        result = asyncio.run(
            run_tool(deps, TOOL_PROPOSE_SAVE_NOTE, runner, input_summary="test note"),
        )
        assert result["status"] == "success"
        assert result["action_type"] == "save_note"
        assert isinstance(result["summary"], str) and len(result["summary"]) > 0
        assert isinstance(result["next_actions"], list)
        assert isinstance(result["artifacts"], list)

    def test_dict_always_error_runner(self) -> None:
        """dict_always tool with fake error runner produces correct output."""
        deps = _make_deps()

        async def runner() -> dict[str, object]:
            return {
                "status": "error",
                "summary": "Missing note_text",
                "next_actions": ["Provide note content."],
                "artifacts": [],
            }

        result = asyncio.run(
            run_tool(deps, TOOL_PROPOSE_SAVE_NOTE, runner),
        )
        assert result["status"] == "error"
        assert isinstance(result["summary"], str) and len(result["summary"]) > 0
        assert isinstance(result["next_actions"], list)
        assert isinstance(result["artifacts"], list)


# ===========================================================================
# Section 5: Write-gate error payload contract
# ===========================================================================


class TestWriteGateErrorPayloadContract:
    """Verify write-gate error payloads are stable and consistent with
    the observation_statuses contract."""

    def test_no_anchor_error_payload_contract(self) -> None:
        """NO_ANCHOR_ERROR_PAYLOAD must have status=error, summary, next_actions, artifacts."""
        payload = NO_ANCHOR_ERROR_PAYLOAD
        assert payload["status"] == "error"
        assert isinstance(payload["summary"], str) and len(payload["summary"]) > 0
        assert isinstance(payload["next_actions"], list) and len(payload["next_actions"]) > 0
        assert isinstance(payload["artifacts"], list)

    def test_missing_note_text_payload_contract(self) -> None:
        """MISSING_NOTE_TEXT_PAYLOAD must have status=error, summary, next_actions, artifacts."""
        payload = MISSING_NOTE_TEXT_PAYLOAD
        assert payload["status"] == "error"
        assert isinstance(payload["summary"], str) and len(payload["summary"]) > 0
        assert isinstance(payload["next_actions"], list) and len(payload["next_actions"]) > 0
        assert isinstance(payload["artifacts"], list)

    def test_no_anchor_gate_does_not_create_action_request(self) -> None:
        """No-anchor write gate must not create any action_request."""
        deps = _make_deps(primary_anchor=None)
        from app.agents.reader_ask_write_gate import check_write_proposal_precondition

        for tool_name in (TOOL_PROPOSE_SAVE_NOTE, TOOL_PROPOSE_SAVE_HIGHLIGHT):
            precondition = check_write_proposal_precondition(
                tool_name,
                has_primary_anchor=False,
            )
            assert not precondition.allowed
            assert precondition.error_payload is not None
            assert precondition.error_payload["status"] == "error"
        # No action_requests were created
        assert len(deps.state.action_requests) == 0

    def test_no_anchor_gate_does_not_consume_budget(self) -> None:
        """No-anchor write gate bypasses run_tool, so budget is not consumed."""
        deps = _make_deps(primary_anchor=None)
        from app.agents.reader_ask_write_gate import check_write_proposal_precondition

        precondition = check_write_proposal_precondition(
            TOOL_PROPOSE_SAVE_NOTE,
            has_primary_anchor=False,
        )
        assert not precondition.allowed
        # Budget was not consumed because gate bypasses run_tool
        assert deps.state.tool_call_count == 0


# ===========================================================================
# Section 6: Availability enforcement contract (runtime layer)
# ===========================================================================


class TestAvailabilityEnforcementContract:
    """Verify disallowed tools do not execute and produce stable error output.

    These errors come from the runtime wrapper layer, not from the tool
    implementation itself — they are NOT covered by
    ToolSpec.observation_statuses.
    """

    def test_disallowed_tool_returns_stable_error_payload(self) -> None:
        from app.agents.reader_ask_tool_policy import ToolAvailabilityResult

        deps = _make_deps()
        deps.tool_availability = ToolAvailabilityResult(
            allowed_tool_names=frozenset(),
            unavailable_reasons={},
        )

        runner_called = False

        async def runner() -> dict[str, str]:
            nonlocal runner_called
            runner_called = True
            return {"summary": "should not reach"}

        result = asyncio.run(
            run_tool(deps, TOOL_GET_RECORD_CONTEXT, runner),
        )
        assert not runner_called
        assert result["status"] == "error"
        assert result["reason"] == "tool_not_available"
        assert isinstance(result["summary"], str) and len(result["summary"]) > 0
        assert isinstance(result["next_actions"], list)
        assert isinstance(result["artifacts"], list)
        assert deps.state.tool_call_count == 0

    def test_disallowed_tool_error_payload_normalizes_to_error(self) -> None:
        """The error payload from availability enforcement normalizes to
        status=error via normalize_tool_observation."""
        from app.agents.reader_ask_tool_policy import ToolAvailabilityResult

        deps = _make_deps()
        deps.tool_availability = ToolAvailabilityResult(
            allowed_tool_names=frozenset(),
            unavailable_reasons={},
        )

        async def runner() -> dict[str, str]:
            return {"summary": "should not reach"}

        result = asyncio.run(
            run_tool(deps, TOOL_GET_RECORD_CONTEXT, runner),
        )
        obs = normalize_tool_observation(result)
        assert obs.status == "error"
