"""Test-only Uvicorn entry: deterministic Ask v2 over the REAL app.

Launch (from ``services/api/``):

    PYTHONPATH=tests uv run uvicorn deterministic_ask_e2e.app:app \
        --host 127.0.0.1 --port 8010

This serves the real ``app.main.create_app()`` application — canonical
``/reader/records/{id}/ask/*`` routers, service, repository, auth and
PostgreSQL persistence — with exactly two test-only overlays installed
at import time, before ``app.main`` is imported:

1. ``install_provider_guard()`` — every external provider surface
   (main model, learner projector, Web Search, embedding/rerank, vector
   search, generic httpx) records an attempt and raises. See guard.py.
2. ``install_deterministic_execution()`` — the Ask execution resolver
   returns a deterministic ``FunctionModel`` execution; the production
   auto-wire fallback is blocked. See execution.py.

Provider-safety configuration is FORCED for this process (env overrides
``.env``): Article RAG, grammar RAG/Zilliz, LangSmith/OTel and Web Search
providers are off. Provider reasoning projection is ON — the deterministic
offline FunctionModel streams ThinkingPartDelta through the REAL thinking
transport + ProviderReasoningObserver, and the provider guard still proves
zero real provider calls. ``DATABASE_URL`` / ``REDIS_URL`` still come from
``services/api/.env`` (real shared PG).

``app.main`` never imports this module and there is no production env
flag that activates this behaviour from ``app.main:app``. The extra
``/__deterministic_guard__/provider-calls`` route exists only on this
test process's app instance and is used as zero-provider evidence.
"""

from __future__ import annotations

import json
import os

# Provider-safe configuration for THIS process only. Assigned before any
# ``app`` import so the first ``get_settings()`` sees them. These are the
# non-sensitive switches; DATABASE_URL / REDIS_URL remain env/.env-owned.
_DETERMINISTIC_PROFILE = "deterministic-e2e"
_DETERMINISTIC_MODEL_PROFILES = json.dumps(
    {
        "providers": {
            "deterministic-e2e-provider": {
                "adapter": "openai_compatible",
                "base_url": "https://provider.invalid/v1",
                "api_key": "test-key-never-sent",
            }
        },
        "models": {
            "deterministic-e2e-model": {
                "provider": "deterministic-e2e-provider",
                "model_name": "deterministic-e2e-model",
            }
        },
        "profiles": {_DETERMINISTIC_PROFILE: {"model": "deterministic-e2e-model"}},
    },
    separators=(",", ":"),
)
_DETERMINISTIC_ASK_OPTIONS = json.dumps(
    {
        "default_option": "deterministic-e2e-r0",
        "options": {
            "deterministic-e2e-r0": {
                "label": "Deterministic E2E",
                "selection": {
                    "routes": {
                        "reader_ask": {"profile": _DETERMINISTIC_PROFILE},
                        "reader_ask_replan": {"profile": _DETERMINISTIC_PROFILE},
                    }
                },
            }
        },
    },
    separators=(",", ":"),
)

_FORCED_ENV: dict[str, str] = {
    "READER_ARTICLE_RAG_ENABLED": "false",
    "GRAMMAR_RAG_ENABLED": "false",
    "LANGSMITH_ENABLED": "false",
    "LANGSMITH_TRACING": "false",
    "LANGSMITH_OTEL_ENABLED": "false",
    "LANGCHAIN_TRACING": "false",
    "LANGCHAIN_TRACING_V2": "false",
    "READER_RECORD_ASK_PROVIDER_REASONING_ENABLED": "true",
    "READER_RECORD_ASK_MEMORY_ENABLED": "false",
    "READER_RECORD_ASK_AGENTIC_ENABLED": "true",
    "DEFAULT_MODEL_PROFILE": _DETERMINISTIC_PROFILE,
    "ASK_CLAREAD_PROFILE": _DETERMINISTIC_PROFILE,
    "READER_ASK_REPLAN_MODEL_PROFILE": _DETERMINISTIC_PROFILE,
    "MODEL_PROFILES_JSON": _DETERMINISTIC_MODEL_PROFILES,
    "MODEL_PRESETS_JSON": "{}",
    "READER_ASK_MODEL_OPTIONS_JSON": _DETERMINISTIC_ASK_OPTIONS,
}
for _key, _value in _FORCED_ENV.items():
    os.environ[_key] = _value

from .guard import guard_report, install_provider_guard  # noqa: E402

install_provider_guard()

from .execution import install_deterministic_execution  # noqa: E402

install_deterministic_execution()

from fastapi import APIRouter  # noqa: E402

from app.main import create_app  # noqa: E402

app = create_app()

_guard_router = APIRouter(tags=["deterministic-e2e-test-only"])


@_guard_router.get("/__deterministic_guard__/provider-calls")
def provider_calls_report() -> dict:
    """Test-only diagnostic: external provider call counter."""
    return guard_report()


app.include_router(_guard_router)
