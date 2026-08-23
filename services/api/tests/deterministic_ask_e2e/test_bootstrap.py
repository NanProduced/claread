"""Focused gates for the deterministic Ask e2e runtime (no real DB).

Covers:

- the fail-closed provider guard blocks every guarded surface and counts
  attempts, and uninstall restores the originals;
- the deterministic model refuses to answer without a server-minted
  ``evh_`` handle and otherwise emits a schema-legal Ask v2 draft;
- the execution swap returns a real ``ReaderRecordAskExecutionConfig``
  wrapping a ``FunctionModel`` (send + retry share this resolver) and the
  production auto-wire fallback is blocked;
- the test-only Uvicorn entry module installs both overlays, serves the
  real canonical Ask routes and exposes the guard report route
  (subprocess isolation — the production entry is never touched here).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from .execution import (
    build_deterministic_execution_config,
    install_deterministic_execution,
    uninstall_deterministic_execution,
)
from .execution import (
    is_installed as execution_is_installed,
)
from .guard import (
    ExternalProviderCallBlocked,
    guard_report,
    install_provider_guard,
    uninstall_provider_guard,
)
from .guard import (
    is_installed as guard_is_installed,
)
from .models import (
    DETERMINISTIC_ARTICLE_ANSWER,
    DETERMINISTIC_REASONING_ROUND1_CHUNKS,
    DETERMINISTIC_REASONING_ROUND2,
    DeterministicModelMissingEvidenceError,
    deterministic_ask_model_fn,
    deterministic_ask_stream_fn,
    stream_delay_seconds,
)

API_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def provider_guard():
    install_provider_guard()
    yield
    uninstall_provider_guard()


@pytest.fixture()
def deterministic_execution():
    install_deterministic_execution()
    yield
    uninstall_deterministic_execution()


def test_guard_blocks_provider_surfaces_and_counts(provider_guard):
    import asyncio

    import httpx

    assert guard_is_installed()

    # Client construction is allowed (production buildability probes);
    # every actual request must die at the transport layer.
    client = httpx.AsyncClient()
    request = httpx.Request("GET", "https://api.deepseek.com/v1/chat/completions")
    with pytest.raises(ExternalProviderCallBlocked):
        asyncio.run(client.send(request))
    sync_client = httpx.Client()
    with pytest.raises(ExternalProviderCallBlocked):
        sync_client.send(httpx.Request("GET", "https://api.example.com/"))

    # The openai SDK lane constructs fine but inherits the httpx block.
    import openai

    sdk_client = openai.AsyncOpenAI(api_key="not-used")
    assert sdk_client is not None

    from app.infra import bailian_embedding, bailian_rerank
    from app.llm import dashscope_stream

    for blocked in (
        dashscope_stream.AioGeneration,
        bailian_embedding.dashscope.TextEmbedding,
        bailian_rerank.dashscope.TextReRank,
    ):
        with pytest.raises(ExternalProviderCallBlocked):
            blocked.call(model="some-model")

    report = guard_report()
    assert report["installed"] is True
    assert report["blocked_call_count"] >= 5
    surfaces = {a["surface"] for a in report["blocked_attempts"]}
    assert "httpx.AsyncClient.send" in surfaces
    assert "httpx.Client.send" in surfaces


def test_guard_uninstall_restores_originals(provider_guard):
    import httpx

    blocked_send = httpx.AsyncClient.send
    uninstall_provider_guard()
    assert httpx.AsyncClient.send is not blocked_send
    assert not guard_is_installed()


def test_deterministic_model_refuses_without_visible_handles():
    empty_message = SimpleNamespace(parts=[SimpleNamespace(content="no handles")])
    with pytest.raises(DeterministicModelMissingEvidenceError):
        deterministic_ask_model_fn([empty_message], None)


def test_deterministic_model_output_validates_against_agent_schema():
    handle = "evh_" + "ab" * 16
    message = SimpleNamespace(
        parts=[SimpleNamespace(content=f"baseline article context with handle {handle} inside")]
    )
    response = deterministic_ask_model_fn([message], None)
    (part,) = response.parts
    assert part.tool_name == "final_result"
    payload = json.loads(part.args)
    assert payload["response_kind"] == "grounded_answer"

    from app.services.reader_record_ask.grounding_validator import (
        AgentAnswerDraftOutput,
    )

    draft = AgentAnswerDraftOutput.model_validate(payload)
    assert draft.response_kind == "grounded_answer"
    assert [block.basis for block in draft.answer_blocks] == [
        "article",
        "general",
    ]
    assert draft.answer_blocks[0].evidence_handles == [handle]
    assert draft.answer_blocks[1].evidence_handles == []
    assert DETERMINISTIC_ARTICLE_ANSWER in draft.answer_blocks[0].text


# ---------------------------------------------------------------------------
# Provider reasoning streaming model script (offline, no DB).
# ---------------------------------------------------------------------------

_Handle = "evh_" + "ab" * 16


def _stream_items(messages, info):
    async def _collect():
        return [item async for item in deterministic_ask_stream_fn(messages, info)]

    return asyncio.run(_collect())


def _messages_with_parts(parts):
    return [SimpleNamespace(parts=parts)]


def test_stream_model_round1_expands_evidence_when_tool_mounted():
    from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall

    info = SimpleNamespace(function_tools=[SimpleNamespace(name="expand_evidence")])
    messages = _messages_with_parts(
        [SimpleNamespace(content=f"baseline context with handle {_Handle}")]
    )
    items = _stream_items(messages, info)
    assert len(items) == 3
    assert items[0] == {0: DeltaThinkingPart(content=DETERMINISTIC_REASONING_ROUND1_CHUNKS[0])}
    assert items[1] == {0: DeltaThinkingPart(content=DETERMINISTIC_REASONING_ROUND1_CHUNKS[1])}
    tool_call = items[2][1]
    assert isinstance(tool_call, DeltaToolCall)
    assert tool_call.name == "expand_evidence"
    assert json.loads(tool_call.json_args) == {"pointer": _Handle}


def test_stream_model_round1_falls_back_to_output_retry_without_tool():
    from pydantic_ai.models.function import DeltaToolCall

    info = SimpleNamespace(function_tools=[])
    messages = _messages_with_parts(
        [SimpleNamespace(content=f"baseline context with handle {_Handle}")]
    )
    items = _stream_items(messages, info)
    assert len(items) == 3
    tool_call = items[2][1]
    assert isinstance(tool_call, DeltaToolCall)
    assert tool_call.name == "final_result"
    payload = json.loads(tool_call.json_args)
    # Schema-legal draft citing an unknown handle → grounding ModelRetry.
    assert payload["answer_blocks"][0]["evidence_handles"] == ["evh_" + "0" * 32]


def test_stream_model_round2_streams_thinking_then_text_answer():
    ToolReturnPart = type("ToolReturnPart", (), {})
    info = SimpleNamespace(function_tools=[SimpleNamespace(name="expand_evidence")])
    messages = _messages_with_parts(
        [
            SimpleNamespace(content=f"baseline context with handle {_Handle}"),
            ToolReturnPart(),
        ]
    )
    items = _stream_items(messages, info)
    # Round 2: one thinking delta, then text chunks of the answer JSON.
    from pydantic_ai.models.function import DeltaThinkingPart

    assert items[0] == {0: DeltaThinkingPart(content=DETERMINISTIC_REASONING_ROUND2)}
    text = "".join(item for item in items[1:] if isinstance(item, str))
    payload = json.loads(text)
    assert payload["response_kind"] == "grounded_answer"
    assert payload["answer_blocks"][0]["evidence_handles"] == [_Handle]


def test_stream_model_refuses_without_visible_handles():
    info = SimpleNamespace(function_tools=[])
    messages = _messages_with_parts([SimpleNamespace(content="no handles here")])
    with pytest.raises(DeterministicModelMissingEvidenceError):
        _stream_items(messages, info)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", 0.0),
        ("0", 0.0),
        ("250", 0.25),
        ("12.5", 0.0125),
        ("-5", 0.0),
        ("not-a-number", 0.0),
    ],
)
def test_stream_delay_env_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("DETERMINISTIC_E2E_STREAM_DELAY_MS", raw)
    assert stream_delay_seconds() == expected


def test_stream_delay_defaults_to_zero_when_unset(monkeypatch):
    monkeypatch.delenv("DETERMINISTIC_E2E_STREAM_DELAY_MS", raising=False)
    assert stream_delay_seconds() == 0.0


def test_execution_swap_returns_function_model_config(deterministic_execution):
    from pydantic_ai.models.function import FunctionModel

    import app.services.reader_record_ask.production_stream as stream_mod
    import app.services.reader_record_ask.service as service_mod

    assert execution_is_installed()

    option = SimpleNamespace(key="deepseek-v4-flash")
    config = service_mod.resolve_reader_record_ask_execution(option, web_search_mode="allowed")
    assert isinstance(config.model, FunctionModel)
    assert config.option_key == "deepseek-v4-flash"
    assert config.model_settings_payload == {"max_tokens": 3200}
    assert config.usage_limits is not None
    assert config.usage_limits.output_tokens_limit == 9600
    # Web Search must stay unmounted even if the request authorizes it.
    assert config.web_search_capability is None
    assert config.web_search_backend is None

    with pytest.raises(RuntimeError, match="auto-wire"):
        stream_mod.resolve_agentic_model(None, explicit=None)


def test_execution_swap_uninstall_restores_resolver(deterministic_execution):
    import app.services.reader_record_ask.service as service_mod

    uninstall_deterministic_execution()
    assert not execution_is_installed()
    assert service_mod.resolve_reader_record_ask_execution.__module__ == (
        "app.services.reader_record_ask.execution_config"
    )


def test_config_builder_matches_real_dataclass_contract():
    config = build_deterministic_execution_config(SimpleNamespace(key="k"))
    # The real dataclass exposes these consumed-by-service attributes.
    assert hasattr(config, "model_settings")
    assert config.model_settings() is not None
    assert config.resolved_model_config is None


def test_test_only_app_entry_installs_overlays_in_isolated_process():
    code = (
        "import json\n"
        "import deterministic_ask_e2e.app as app_mod\n"
        "from deterministic_ask_e2e import execution, guard\n"
        "paths = sorted({getattr(r, 'path', '') for r in app_mod.app.routes})\n"
        "report = {\n"
        "    'guard_installed': guard.is_installed(),\n"
        "    'execution_installed': execution.is_installed(),\n"
        "    'guard_route': '/__deterministic_guard__/provider-calls' in paths,\n"
        "    'ask_send': '/reader/records/{reading_record_id}/ask/threads/"
        "{thread_id}/messages/stream' in paths,\n"
        "    'ask_retry': '/reader/records/{reading_record_id}/ask/threads/"
        "{thread_id}/messages/{message_id}/retry/stream' in paths,\n"
        "    'ask_history': '/reader/records/{reading_record_id}/ask/threads/"
        "{thread_id}' in paths,\n"
        "    'root': '/' in paths,\n"
        "}\n"
        "print(json.dumps(report))\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(API_ROOT / "tests")
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(API_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report == {
        "guard_installed": True,
        "execution_installed": True,
        "guard_route": True,
        "ask_send": True,
        "ask_retry": True,
        "ask_history": True,
        "root": True,
    }


def test_test_only_app_entry_owns_its_model_catalog_configuration():
    code = (
        "import json\n"
        "import deterministic_ask_e2e.app\n"
        "from app.config.settings import get_settings\n"
        "from app.services.reader_record_ask.model_options import "
        "resolve_default_reader_ask_model_option\n"
        "settings = get_settings()\n"
        "option = resolve_default_reader_ask_model_option(settings)\n"
        "print(json.dumps({\n"
        "    'option_key': option.key,\n"
        "    'main_model_name': option.main_model_name,\n"
        "    'replan_model_name': option.replan_model_name,\n"
        "    'profiles_are_inline': settings.model_profiles_json.startswith('{'),\n"
        "}))\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(API_ROOT / "tests")
    env["MODEL_PROFILES_JSON"] = "config/private-model-profiles.json"
    env["MODEL_PRESETS_JSON"] = "config/private-model-presets.json"
    env["READER_ASK_MODEL_OPTIONS_JSON"] = "config/private-reader-options.json"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(API_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report == {
        "option_key": "deterministic-e2e-r0",
        "main_model_name": "deterministic-e2e-model",
        "replan_model_name": "deterministic-e2e-model",
        "profiles_are_inline": True,
    }
