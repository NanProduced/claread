"""Round 1 spike: documents the pydantic-ai 1.73.0 API surface for
``Tool(requires_approval=True)`` and ``Agent.iter()``.

**Decision: NOT adopted in Round 1.** ``DeferredToolResults`` requires keeping
the agent run alive across an HTTP roundtrip, which conflicts with FastAPI
request lifecycle. The current formal architecture keeps Ask writes on a
proposal-only + user-confirmation path; see
``docs/architecture/ask-claread.md``.

This file exists so that the next refactor round can re-evaluate the decision
without re-deriving the API surface. If these tests fail, that's a real version
surprise that needs discussion before adopting either API.
"""

from __future__ import annotations

import inspect

import pytest
from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.agent import _agent_graph
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel


def _no_op_tool(value: str) -> str:
    return value


def test_tool_constructor_accepts_requires_approval() -> None:
    """`Tool(..., requires_approval=True)` must be accepted on pydantic-ai 1.73.0."""
    tool = Tool(_no_op_tool, takes_ctx=False, requires_approval=True)
    assert tool.requires_approval is True


def test_agent_iter_is_async_context_manager() -> None:
    """`Agent.iter(...)` must be an async context manager yielding graph nodes."""

    async def text_model(messages, info: AgentInfo):
        return ModelResponse(parts=[])

    agent = Agent(FunctionModel(text_model), deps_type=int)

    assert hasattr(agent, "iter")
    assert not inspect.iscoroutinefunction(agent.iter), (
        "Agent.iter should be an async context manager, not a coroutine function"
    )


@pytest.mark.asyncio
async def test_agent_iter_yields_model_request_node() -> None:
    """The graph must yield at least one ``ModelRequestNode`` for a text-only model."""

    from pydantic_ai.messages import TextPart

    async def text_model(messages, info: AgentInfo):
        return ModelResponse(parts=[TextPart(content="hello world")])

    agent: Agent[int, str] = Agent(FunctionModel(text_model), deps_type=int)

    seen_node_types: set[str] = set()
    async with agent.iter("hi", deps=0) as agent_run:
        async for node in agent_run:
            seen_node_types.add(type(node).__name__)

    assert "ModelRequestNode" in seen_node_types


@pytest.mark.asyncio
async def test_call_tools_node_exposes_tool_call_parts() -> None:
    """`CallToolsNode.model_response.parts` must contain a ``ToolCallPart``
    for the tool call, demonstrating how iter-based code reads tool events.
    """

    async def tool_calling_model(messages, info: AgentInfo):
        # First call returns a tool call; second call returns a final answer.
        # This prevents an infinite loop where the FunctionModel keeps
        # returning tool calls and exceeds the request_limit.
        from pydantic_ai.messages import TextPart

        has_tool_call_in_history = any(
            any(isinstance(part, ToolCallPart) for part in getattr(msg, "parts", []))
            for msg in messages
        )
        if has_tool_call_in_history:
            return ModelResponse(parts=[TextPart(content="done")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="_no_op_tool",
                    args='{"value": "x"}',
                    tool_call_id="call-1",
                )
            ]
        )

    agent: Agent[int, str] = Agent(
        FunctionModel(tool_calling_model),
        deps_type=int,
        output_type=str,
        tools=[Tool(_no_op_tool, takes_ctx=False)],
    )

    tool_call_part_seen = False
    async with agent.iter("call the tool", deps=0) as agent_run:
        async for node in agent_run:
            if isinstance(node, _agent_graph.CallToolsNode):
                for part in node.model_response.parts:
                    if isinstance(part, ToolCallPart):
                        tool_call_part_seen = True
                        assert part.tool_name == "_no_op_tool"
                        assert part.tool_call_id == "call-1"

    assert tool_call_part_seen, (
        "CallToolsNode.model_response.parts must contain ToolCallPart for tool events"
    )


def test_run_context_exposes_tool_call_approved_attribute() -> None:
    """`RunContext.tool_call_approved` is the bridge between `requires_approval=True`
    and the tool body — the framework sets it on the resumed invocation.
    """
    init_params = inspect.signature(RunContext.__init__).parameters
    # The attribute may be set dynamically on the instance, but it must be
    # declared in __init__ so the framework can write to it.
    assert "tool_call_approved" in init_params, (
        "RunContext.__init__ must accept tool_call_approved for approval-loop "
        "plumbing to work"
    )
