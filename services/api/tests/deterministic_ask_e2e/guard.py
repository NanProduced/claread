"""Fail-closed external provider guard for the deterministic Ask runtime.

Test-only module. Blocks every network-capable provider surface an Ask v2
turn (or the API lifespan) could touch, at the transport level:

- ALL outbound HTTP: ``httpx.AsyncClient.send`` / ``httpx.Client.send`` —
  every request the openai SDK (DeepSeek/Moonshot/MiniMax/DashScope
  OpenAI-compatible main lane) or the Web Search backends can issue
  funnels through ``send``;
- DashScope native SDK entry points: ``AioGeneration`` / ``Generation``
  (main model) and ``TextEmbedding`` / ``TextReRank`` (article RAG),
  both top-level and on the repo's ``app.infra`` / ``app.llm`` module
  namespaces;
- vector search: ``pymilvus.MilvusClient`` (Zilliz).

Client *construction* is deliberately allowed: production catalog
validation builds model instances as a buildability probe without any
network I/O. The guard fires the moment real I/O would leave the
process — every attempt is recorded in a process-global counter and
raises ``ExternalProviderCallBlocked``. "No API keys in this shell" is
never the safety mechanism — the guard is.

asyncpg (PostgreSQL) and redis-py do not use httpx, so PG and Redis
remain reachable while the guard is installed.

The guard is deliberately independent of pytest so it also protects a
long-running Uvicorn process started from ``deterministic_ask_e2e.app``.
"""

from __future__ import annotations

import threading
from typing import Any

__all__ = [
    "ExternalProviderCallBlocked",
    "guard_report",
    "install_provider_guard",
    "is_installed",
    "uninstall_provider_guard",
]


class ExternalProviderCallBlocked(RuntimeError):
    """Raised when any external provider surface is touched."""


_lock = threading.Lock()
_attempts: list[dict[str, str]] = []
# (module object, attribute name, original value) — restore by identity so
# uninstall never needs to re-import, even when the patched namespace is a
# test double without a ``__name__`` (e.g. conftest SimpleNamespace).
_originals: list[tuple[Any, str, Any]] = []
_patched_ids: set[tuple[int, str]] = set()
_skipped: list[str] = []


def _raise_blocked(surface: str, detail: str) -> None:
    with _lock:
        _attempts.append({"surface": surface, "detail": detail})
    raise ExternalProviderCallBlocked(
        "Deterministic Ask e2e runtime blocked an external provider call: "
        f"surface={surface} detail={detail}"
    )


def _blocked_constructor_class(surface: str) -> type:
    class _BlockedConstructor:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            _raise_blocked(
                surface,
                f"constructor args={len(args)} kwargs={sorted(kwargs)}",
            )

    _BlockedConstructor.__name__ = f"Blocked_{surface.replace('.', '_')}"
    return _BlockedConstructor


async def _blocked_async_send(self: Any, request: Any, *args: Any, **kwargs: Any) -> None:
    _raise_blocked("httpx.AsyncClient.send", f"url={getattr(request, 'url', '?')}")


def _blocked_sync_send(self: Any, request: Any, *args: Any, **kwargs: Any) -> None:
    _raise_blocked("httpx.Client.send", f"url={getattr(request, 'url', '?')}")


def _blocked_sdk_call_class(surface: str) -> type:
    class _BlockedSDKCalls:
        @staticmethod
        def call(*args: Any, **kwargs: Any) -> None:
            _raise_blocked(surface, f"call model={kwargs.get('model')!r}")

        @staticmethod
        async def acall(*args: Any, **kwargs: Any) -> None:
            _raise_blocked(surface, f"acall model={kwargs.get('model')!r}")

        @staticmethod
        async def streaming_call(*args: Any, **kwargs: Any) -> None:
            _raise_blocked(surface, f"streaming_call model={kwargs.get('model')!r}")

    _BlockedSDKCalls.__name__ = f"Blocked_{surface.replace('.', '_')}"
    return _BlockedSDKCalls


def _patch(module: Any, attr: str, replacement: Any, surface: str) -> None:
    key = (id(module), attr)
    if key in _patched_ids:
        return
    _patched_ids.add(key)
    _originals.append((module, attr, getattr(module, attr, None)))
    setattr(module, attr, replacement)


def install_provider_guard() -> None:
    """Install every provider block. Idempotent within one process."""
    import httpx

    constructor = _blocked_constructor_class
    sdk_calls = _blocked_sdk_call_class

    # Transport-level catch-all: ALL outbound HTTP I/O leaves through
    # ``send``. The openai SDK (main model lane + projector) builds its
    # clients on top of ``httpx.AsyncClient`` (subclass method resolution
    # inherits this patch), so DeepSeek/Moonshot/MiniMax/DashScope-compat
    # requests and Web Search backend requests all die here. Construction
    # stays allowed (production buildability probes construct clients
    # without any network I/O).
    _patch(httpx.AsyncClient, "send", _blocked_async_send, "httpx")
    _patch(httpx.Client, "send", _blocked_sync_send, "httpx")

    try:
        import dashscope

        _patch(
            dashscope,
            "AioGeneration",
            sdk_calls("dashscope.AioGeneration"),
            "dashscope",
        )
        _patch(
            dashscope,
            "Generation",
            sdk_calls("dashscope.Generation"),
            "dashscope",
        )
        _patch(
            dashscope,
            "TextEmbedding",
            sdk_calls("dashscope.TextEmbedding"),
            "dashscope",
        )
        _patch(
            dashscope,
            "TextReRank",
            sdk_calls("dashscope.TextReRank"),
            "dashscope",
        )
    except ImportError:  # pragma: no cover
        with _lock:
            _skipped.append("dashscope")

    try:
        import pymilvus

        _patch(
            pymilvus,
            "MilvusClient",
            constructor("pymilvus.MilvusClient"),
            "pymilvus",
        )
    except ImportError:  # pragma: no cover
        with _lock:
            _skipped.append("pymilvus")

    # Repo-internal provider module namespaces (mirror tests/conftest.py
    # surfaces, but always-on for this process rather than pytest-only).
    from app.infra import bailian_embedding, bailian_rerank
    from app.llm import dashscope_stream

    if getattr(bailian_embedding, "dashscope", None) is not None:
        _patch(
            bailian_embedding.dashscope,
            "TextEmbedding",
            sdk_calls("app.infra.bailian_embedding.dashscope.TextEmbedding"),
            "bailian_embedding",
        )
    if getattr(bailian_rerank, "dashscope", None) is not None:
        _patch(
            bailian_rerank.dashscope,
            "TextReRank",
            sdk_calls("app.infra.bailian_rerank.dashscope.TextReRank"),
            "bailian_rerank",
        )
    _patch(
        dashscope_stream,
        "AioGeneration",
        sdk_calls("app.llm.dashscope_stream.AioGeneration"),
        "dashscope_stream",
    )


def uninstall_provider_guard() -> None:
    """Restore every patched attribute (test teardown helper)."""
    for module, attr, original in reversed(_originals):
        setattr(module, attr, original)
    _originals.clear()
    _patched_ids.clear()


def is_installed() -> bool:
    return bool(_originals)


def guard_report() -> dict[str, Any]:
    with _lock:
        return {
            "installed": is_installed(),
            "blocked_call_count": len(_attempts),
            "blocked_attempts": list(_attempts),
            "uninstalled_surfaces": list(_skipped),
        }
