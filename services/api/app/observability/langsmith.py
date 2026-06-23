import logging
import os

from app.config.settings import Settings

logger = logging.getLogger(__name__)

_LANGSMITH_INITIALIZED = False


def setup_langsmith(settings: Settings) -> bool:
    """Initialize LangSmith tracing for LangGraph workflows.

    Uses LangGraph's built-in callback tracing (``LANGSMITH_TRACING=true``)
    plus ``@traceable``-decorated functions; these are the only two sources
    of LangSmith spans in the API process. PydanticAI agents do not register
    the ``Instrumentation`` capability and therefore do not emit traces
    (``LANGSMITH_OTEL_ENABLED`` is also kept ``"false"`` to make that
    explicit and avoid duplicates).

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
    os.environ["LANGSMITH_OTEL_ENABLED"] = "false"

    if settings.langsmith_workspace_id:
        os.environ["LANGSMITH_WORKSPACE_ID"] = settings.langsmith_workspace_id

    _LANGSMITH_INITIALIZED = True
    logger.info(
        "LangSmith environment initialized for project '%s'.",
        settings.langsmith_project,
    )
    return True
