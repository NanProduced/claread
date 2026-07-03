import logging
import os

from app.config.settings import Settings

logger = logging.getLogger(__name__)

_LANGSMITH_INITIALIZED = False


def setup_langsmith(settings: Settings) -> bool:
    """Initialize LangSmith tracing for LangGraph + PydanticAI workflows.

    Two trace sources are wired here:

    - **LangGraph callback tracing** (``LANGSMITH_TRACING=true``) plus
      ``@traceable``-decorated functions, covering the legacy AI Workflow
      and Daily Reader pipelines.
    - **PydanticAI 1.x OpenTelemetry instrumentation**
      (``LANGSMITH_OTEL_ENABLED=true`` + ``Agent.instrument_all()`` +
      ``langsmith.integrations.otel.configure``), covering the
      ``reader_orchestration`` workers (translation / vocabulary /
      grammar_bundle / display_title). PydanticAI 1.x ships native
      ``InstrumentationSettings`` and emits OTLP spans following the
      GenAI Semantic Conventions (``gen_ai.usage.input_tokens`` /
      ``gen_ai.request.model`` / ``gen_ai.response.model``).

    OTEL instrumentation is gated by ``settings.langsmith_otel_enabled``.
    Tests keep it off (``conftest.py`` defaults
    ``LANGSMITH_OTEL_ENABLED=false``) to avoid polluting the production
    LangSmith project; production should set ``LANGSMITH_OTEL_ENABLED=true``.

    To disable tracing for a single in-process call (e.g. eval requests with
    ``trace_scope='off'``), use :func:`app.observability.disabled_tracing`
    rather than mutating the env vars set here.

    Args:
        settings: Application settings with LangSmith configuration.

    Returns:
        bool: True if LangSmith was initialized successfully.
    """

    global _LANGSMITH_INITIALIZED

    if _LANGSMITH_INITIALIZED:
        return True

    if not settings.langsmith_enabled:
        logger.info("LangSmith disabled by configuration.")
        return False

    if not settings.langsmith_api_key:
        logger.warning("LangSmith enabled but LANGSMITH_API_KEY is missing.")
        return False

    tracing_enabled = str(settings.langsmith_tracing).lower()
    os.environ["LANGSMITH_TRACING"] = tracing_enabled
    os.environ["LANGSMITH_TRACING_V2"] = tracing_enabled
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    # PydanticAI 1.x OTEL instrumentation is opt-in. Tests keep this
    # ``"false"`` via conftest.py; production should set
    # ``LANGSMITH_OTEL_ENABLED=true``.
    otel_enabled = str(settings.langsmith_otel_enabled).lower()
    os.environ["LANGSMITH_OTEL_ENABLED"] = otel_enabled

    if settings.langsmith_workspace_id:
        os.environ["LANGSMITH_WORKSPACE_ID"] = settings.langsmith_workspace_id

    _configure_pydantic_ai_otel(settings)

    _LANGSMITH_INITIALIZED = True
    logger.info(
        "LangSmith environment initialized for project '%s' "
        "(otel_enabled=%s).",
        settings.langsmith_project,
        otel_enabled,
    )
    return True


_PYDANTIC_AI_INSTRUMENTED = False


def _configure_pydantic_ai_otel(settings: Settings) -> None:
    """Wire PydanticAI 1.x OTEL instrumentation to LangSmith.

    No-op when ``settings.langsmith_otel_enabled`` is False. When True,
    imports ``langsmith.integrations.otel.configure`` and calls
    ``pydantic_ai.Agent.instrument_all()`` exactly once per process. Both
    calls are wrapped in try/except so a missing/older dependency degrades
    to a warning instead of crashing the API bootstrap.

    Also attaches :class:`LangSmithIdBridgeProcessor` to the global OTel
    tracer provider so that ``langsmith.trace.id`` /
    ``langsmith.span.id`` attributes (auto-set by LangSmith SDK on every
    PydanticAI LLM span) are captured into a ContextVar. The recorder
    reads that ContextVar in ``end_span`` to backfill
    ``reader_runtime_spans.langsmith_run_id``. This couples PG span rows
    to LangSmith runs without touching every worker call site.
    """

    global _PYDANTIC_AI_INSTRUMENTED

    if _PYDANTIC_AI_INSTRUMENTED:
        return

    if not settings.langsmith_otel_enabled:
        return

    try:
        from langsmith.integrations.otel import configure as _configure_otel
    except ImportError:
        logger.warning(
            "langsmith.integrations.otel.configure unavailable; "
            "PydanticAI spans will not be sent to LangSmith. "
            "Upgrade langsmith[otel] to >=0.4.26."
        )
        return

    try:
        _configure_otel(project_name=settings.langsmith_project)
    except Exception:
        logger.exception(
            "langsmith.integrations.otel.configure() failed; "
            "PydanticAI spans will not be sent to LangSmith."
        )
        return

    # Attach the LangSmith → PG bridge processor. Idempotent: the
    # ``_claread_bridge_attached`` flag on the tracer provider prevents
    # double-registration when ``setup_langsmith`` is called more than
    # once in the same process (tests, multi-worker bootstrap).
    try:
        from opentelemetry.trace import get_tracer_provider

        from app.observability.langsmith_span_processor import (
            LangSmithIdBridgeProcessor,
        )

        tracer_provider = get_tracer_provider()
        if not getattr(tracer_provider, "_claread_bridge_attached", False):
            tracer_provider.add_span_processor(LangSmithIdBridgeProcessor())  # type: ignore[attr-defined]
            tracer_provider._claread_bridge_attached = True  # type: ignore[attr-defined]
    except Exception:
        logger.exception(
            "Failed to attach LangSmithIdBridgeProcessor; "
            "langsmith_run_id backfill will be skipped."
        )

    try:
        from pydantic_ai import Agent as _PydanticAIAgent

        _PydanticAIAgent.instrument_all()
    except Exception:
        logger.exception(
            "pydantic_ai.Agent.instrument_all() failed; "
            "reader_orchestration LLM spans will not be emitted."
        )
        return

    _PYDANTIC_AI_INSTRUMENTED = True
    logger.info(
        "PydanticAI 1.x OTEL instrumentation enabled for LangSmith "
        "project '%s'.",
        settings.langsmith_project,
    )
