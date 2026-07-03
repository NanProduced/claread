from app.config.settings import Settings
from app.observability import langsmith as langsmith_module


def test_setup_langsmith_sets_v1_and_v2_tracing_env(monkeypatch) -> None:
    monkeypatch.setattr(langsmith_module, "_LANGSMITH_INITIALIZED", False)
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_TRACING_V2", raising=False)

    settings = Settings(
        langsmith_enabled=True,
        langsmith_tracing=True,
        langsmith_api_key="test-key",
        langsmith_project="test-project",
        langsmith_endpoint="https://api.smith.langchain.com",
    )

    assert langsmith_module.setup_langsmith(settings) is True
    assert langsmith_module.os.environ["LANGSMITH_TRACING"] == "true"
    assert langsmith_module.os.environ["LANGSMITH_TRACING_V2"] == "true"


def _otel_test_settings() -> Settings:
    return Settings(
        langsmith_enabled=True,
        langsmith_tracing=True,
        langsmith_otel_enabled=True,
        langsmith_api_key="test-key",
        langsmith_project="test-project",
        langsmith_endpoint="https://api.smith.langchain.com",
    )


def test_setup_langsmith_enables_pydantic_ai_otel_when_flag_true(monkeypatch) -> None:
    """When ``langsmith_otel_enabled=True``, ``setup_langsmith`` should call
    ``Agent.instrument_all()`` exactly once via ``_configure_pydantic_ai_otel``."""

    monkeypatch.setattr(langsmith_module, "_LANGSMITH_INITIALIZED", False)
    monkeypatch.setattr(langsmith_module, "_PYDANTIC_AI_INSTRUMENTED", False)

    # Stub the lazy-imported dependencies so no real OTel exporter is configured.
    monkeypatch.setattr(
        "langsmith.integrations.otel.configure",
        lambda **kwargs: None,
    )

    instrument_calls: list[bool] = []

    def _fake_instrument_all(*args: object, **kwargs: object) -> None:
        instrument_calls.append(True)

    monkeypatch.setattr(
        "pydantic_ai.Agent.instrument_all",
        _fake_instrument_all,
    )

    # Provide a fake tracer provider so the bridge-processor attachment path
    # does not blow up on the real OTel global state.
    class _FakeTracerProvider:
        _claread_bridge_attached = False

        def add_span_processor(self, processor: object) -> None:
            return

    monkeypatch.setattr(
        "opentelemetry.trace.get_tracer_provider",
        lambda: _FakeTracerProvider(),
    )

    assert langsmith_module.setup_langsmith(_otel_test_settings()) is True
    assert len(instrument_calls) == 1, "Agent.instrument_all should be called once"


def test_setup_langsmith_attaches_bridge_processor(monkeypatch) -> None:
    """``_configure_pydantic_ai_otel`` should attach
    ``LangSmithIdBridgeProcessor`` to the OTel tracer provider with the
    idempotent ``_claread_bridge_attached`` flag preventing double-registration
    on subsequent calls."""

    monkeypatch.setattr(langsmith_module, "_LANGSMITH_INITIALIZED", False)
    monkeypatch.setattr(langsmith_module, "_PYDANTIC_AI_INSTRUMENTED", False)

    monkeypatch.setattr(
        "langsmith.integrations.otel.configure",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "pydantic_ai.Agent.instrument_all",
        lambda *args, **kwargs: None,
    )

    attached_processors: list[object] = []

    class _FakeTracerProvider:
        _claread_bridge_attached = False

        def add_span_processor(self, processor: object) -> None:
            attached_processors.append(processor)

    fake_provider = _FakeTracerProvider()
    monkeypatch.setattr(
        "opentelemetry.trace.get_tracer_provider",
        lambda: fake_provider,
    )

    settings = _otel_test_settings()
    # Call _configure_pydantic_ai_otel directly so we can reset the
    # _PYDANTIC_AI_INSTRUMENTED flag between calls and exercise the
    # _claread_bridge_attached idempotency branch.
    langsmith_module._configure_pydantic_ai_otel(settings)

    from app.observability.langsmith_span_processor import (
        LangSmithIdBridgeProcessor,
    )

    bridge_count = sum(
        1 for p in attached_processors if isinstance(p, LangSmithIdBridgeProcessor)
    )
    assert bridge_count == 1, "LangSmithIdBridgeProcessor should be attached once"
    assert fake_provider._claread_bridge_attached is True, (
        "tracer provider should be marked as bridge-attached"
    )

    # Reset _PYDANTIC_AI_INSTRUMENTED to simulate the second-call path
    # (e.g. setup_langsmith called again after a process-level reset).
    monkeypatch.setattr(langsmith_module, "_PYDANTIC_AI_INSTRUMENTED", False)
    langsmith_module._configure_pydantic_ai_otel(settings)

    bridge_count_after_second = sum(
        1 for p in attached_processors if isinstance(p, LangSmithIdBridgeProcessor)
    )
    assert bridge_count_after_second == 1, (
        "second _configure_pydantic_ai_otel call should not double-register "
        "LangSmithIdBridgeProcessor"
    )
