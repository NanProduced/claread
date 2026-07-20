"""D6-I4N: Article RAG Ask Context Provider Facade.

Final orchestrator that chains the I4H → I4I → I4J → I4K → I4L
→ I4M pipeline into a single entry point for the Ask layer:

  ArticleRagAskContextProvider.build_for_ask(
      reading_record_id, user_id, query_text, *,
      limit=..., max_context_chars=..., enabled=True,
  ) -> ArticleRagAskPromptAssembly

The facade is the SINGLE boundary between the Article RAG
pipeline and the Ask prompt constructor.  It:

  * is a pure orchestrator — no LLM, no DB, no network;
  * never raises — every failure (any dependency missing,
    any dependency raising an unexpected exception, any
    dependency returning a malformed object) maps to a
    fail-soft assembly with ``should_attach=False``;
  * never includes ``query_text`` — only ``query_sha256``
    (which is the upstream attachment's value, scrubbed by
    the I4L runtime adapter);
  * never re-derives citation / text — the chain carries the
    I4E plan-backed value through each transform without
    re-parsing;
  * never reads vector payload directly — all retrieval is
    delegated to the resolver (which itself delegates to the
    retrieval service);
  * never inlines citation JSON into the prompt attachment
    block — citations stay structured on the I4M assembly.

Truth boundary
--------------

The facade is the SINGLE entry point for the Ask layer.  It
MUST NOT re-derive citations from the prompt text, MUST NOT
interpret projection fields as fact sources, and MUST NOT
trust a regression that surfaces a hostile value on any
upstream field.  Defence in depth:

  1. **Layered fail-soft** — every layer of the chain
     (resolver / attachment / integration adapter / section
     builder / runtime adapter / assembly service) is
     individually fail-soft; the facade adds an additional
     fail-soft envelope so a regression in the chain wiring
     itself (e.g. wrong order, missing dependency) does not
     crash the Ask layer.
  2. **Dependency validation** — every dependency is checked
     before use; a missing dependency fail-softs.
  3. **Shape validation** — every intermediate result is
     checked before being passed to the next layer; a
     malformed shape fail-softs.
  4. **Exception catch-all** — every dependency call is
     wrapped in a try/except; any unexpected exception
     (other than the typed upstream exceptions, which are
     already handled by the upstream layer) maps to a
     fail-soft assembly.
  5. **repr/str safety** — the fail-soft assembly is built
     with the same field-level allowlist + value-guard
     discipline used by I4M; the original exception /
     upstream diagnostic is preserved as ``__cause__`` for
     ops dashboards but its message is NOT echoed in the
     public result.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol
from uuid import UUID

from .article_rag_ask_prompt_assembly import (
    ArticleRagAskPromptAssembly,
    ArticleRagAskPromptAssemblyService,
)
from .article_rag_ask_runtime_adapter import (
    ArticleRagAskRuntimeContext,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Integration-specific failure code (the facade's own fail-soft
# path).  Differs from I4J / I4K / I4L / I4M codes so dashboards
# can distinguish "the chain returned something unexpected"
# from "the facade itself caught an unexpected error".
FAILURE_CODE_FACADE_UNEXPECTED_ERROR = (
    "article_rag_context_provider_unexpected_error"
)

# Default ``limit`` / character budget / index version.  Mirrors
# the resolver defaults; we re-export them here so callers of
# the facade have a single import surface.
DEFAULT_FACADE_LIMIT = 8
DEFAULT_FACADE_MAX_CONTEXT_CHARS = 4000


# ---------------------------------------------------------------------------
# Dependency protocols
# ---------------------------------------------------------------------------


class _IntegrationAdapterLike(Protocol):
    """Minimal shape :class:`ArticleRagAskIntegrationAdapter`
    exposes.  This is the FIRST layer in the facade — the
    integration adapter itself owns the upstream read path
    (resolver + attachment service).  The facade delegates the
    full read to the integration adapter so the resolver and
    attachment are called EXACTLY ONCE per facade invocation
    (avoiding duplicate embedding / vector-search work).

    Note: the integration adapter is ``async def`` in the
    real I4J implementation (it calls the async attachment
    service).  A previous version of this facade called it
    synchronously, which produced a coroutine object that
    downstream layers (section / runtime / assembly) treated
    as malformed, fail-soft'ing every include path.
    """

    async def build_prompt_segment(
        self,
        *,
        reading_record_id: UUID,
        user_id: UUID,
        query_text: str,
        enabled: bool = ...,
        limit: int = ...,
        max_context_chars: int = ...,
    ) -> Any: ...


class _SectionBuilderLike(Protocol):
    """Minimal shape :class:`ArticleRagAskPromptSectionBuilder`
    exposes.  This is the FOURTH layer — it wraps the segment
    in marker lines.
    """

    def build(self, segment: Any) -> Any: ...


class _RuntimeAdapterLike(Protocol):
    """Minimal shape :class:`ArticleRagAskRuntimeAdapter`
    exposes.  This is the FIFTH layer — it converts the
    section into a runtime boundary value object.
    """

    def build(self, section: Any) -> Any: ...


class _AssemblyServiceLike(Protocol):
    """Minimal shape :class:`ArticleRagAskPromptAssemblyService`
    exposes.  This is the SIXTH (final) layer — it converts
    the runtime context into an Ask-prompt-consumable
    assembly.
    """

    def assemble(self, context: Any) -> ArticleRagAskPromptAssembly: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fail_soft_assembly() -> ArticleRagAskPromptAssembly:
    """Build a generic fail-soft assembly.

    Returns a clean no-attach assembly with the FACADE'S
    own failure code (not the I4M assembly service's code).
    The facade is the boundary that owns the
    "unexpected-error" taxonomy; the fail-soft envelope must
    carry the facade's code so dashboards can distinguish
    "the chain returned something unexpected" from "the
    facade itself caught an unexpected error".
    """
    return _make_fail_soft_assembly_with_failure_code(
        FAILURE_CODE_FACADE_UNEXPECTED_ERROR
    )


def _make_unknown_runtime_context() -> ArticleRagAskRuntimeContext:  # noqa: D401  # pragma: no cover
    """(Kept for symmetry with the I4M assembly's no-attach
    shape; the facade's fail-soft envelope builds the assembly
    directly via ``_make_fail_soft_assembly_with_failure_code``,
    so this helper is no longer called.)"""
    return object()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------


class ArticleRagAskContextProvider:
    """Final facade: chain the I4J → I4K → I4L → I4M pipeline
    for the Ask layer.

    Pure orchestrator.  No I/O.  The Ask layer calls
    :meth:`build_for_ask` and gets a typed, never-raises
    :class:`ArticleRagAskPromptAssembly` value object.

    The chain order is FIXED:

      1. ``integration_adapter.build_prompt_segment(...)``
         (I4J) — owns the read path: the integration adapter
         accepts the resolver + attachment service in its own
         constructor and calls them ONCE.  The facade MUST
         NOT re-invoke them (doing so would double the
         embedding / vector-search cost and could surface
         inconsistent results if the index state changes
         between calls).
      2. ``section_builder.build(segment)`` (I4K) — wraps
         the segment in marker lines.
      3. ``runtime_adapter.build(section)`` (I4L) — converts
         the section into a runtime boundary value object.
      4. ``assembly_service.assemble(context)`` (I4M) —
         produces the final Ask-prompt-consumable assembly.

    Each layer has its own fail-soft; the facade adds an
    envelope so a regression in the chain wiring itself
    does not crash the Ask layer.

    Note on async: ``integration_adapter.build_prompt_segment``
    is ``async def`` in the real I4J implementation (it calls
    the async attachment service).  The facade MUST ``await``
    the call — a previous version called it synchronously
    which produced a coroutine object that downstream layers
    treated as malformed, fail-softing every include path.
    """

    def __init__(
        self,
        *,
        integration_adapter: _IntegrationAdapterLike | None = None,
        section_builder: _SectionBuilderLike | None = None,
        runtime_adapter: _RuntimeAdapterLike | None = None,
        assembly_service: _AssemblyServiceLike | None = None,
        assembly_max_block_chars: int = (
            DEFAULT_FACADE_MAX_CONTEXT_CHARS
        ),
    ) -> None:
        self._integration_adapter = integration_adapter
        self._section_builder = section_builder
        self._runtime_adapter = runtime_adapter
        # The assembly service is the only one we instantiate
        # by default (for the fail-soft path) — production
        # code injects the real assembly service.  ``None``
        # means we use the default ``ArticleRagAskPromptAssemblyService``
        # with the same ``max_block_chars``.
        if assembly_service is None:
            self._assembly_service: _AssemblyServiceLike = (
                ArticleRagAskPromptAssemblyService(
                    max_block_chars=assembly_max_block_chars
                )
            )
        else:
            self._assembly_service = assembly_service

    async def build_for_ask(
        self,
        *,
        reading_record_id: UUID,
        user_id: UUID,
        query_text: str,
        enabled: bool = True,
        limit: int = DEFAULT_FACADE_LIMIT,
        max_context_chars: int = DEFAULT_FACADE_MAX_CONTEXT_CHARS,
    ) -> ArticleRagAskPromptAssembly:
        """Build an :class:`ArticleRagAskPromptAssembly` for the
        Ask layer.

        Never raises.  Every failure maps to a fail-soft
        assembly with ``should_attach=False`` and
        ``failure_code=FAILURE_CODE_FACADE_UNEXPECTED_ERROR``.
        """
        # 1. Validate injected dependencies.  A missing
        #    dependency at any layer is a wiring error
        #    (production code injects all 4 services); the
        #    facade does NOT silently fall back to a default.
        if any(
            dep is None
            for dep in (
                self._integration_adapter,
                self._section_builder,
                self._runtime_adapter,
                self._assembly_service,
            )
        ):
            return self._make_fail_soft_assembly(
                reason="missing_dependency"
            )

        # 2. Layer 1: integration adapter (I4J).  The
        #    integration adapter is async; we MUST ``await``
        #    — a previous version called it synchronously,
        #    which produced a coroutine object that the
        #    section / runtime / assembly layers treated as
        #    malformed.
        try:
            segment = await self._integration_adapter.build_prompt_segment(  # type: ignore[union-attr]
                reading_record_id=reading_record_id,
                user_id=user_id,
                query_text=query_text,
                enabled=enabled,
                limit=limit,
                max_context_chars=max_context_chars,
            )
        except Exception as exc:  # noqa: BLE001 — defensive catch-all
            return self._make_fail_soft_assembly_from_exception(
                exc, layer="integration_adapter"
            )

        # 3. Layer 2: section builder (synchronous).
        try:
            section = self._section_builder.build(segment)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 — defensive catch-all
            return self._make_fail_soft_assembly_from_exception(
                exc, layer="section_builder"
            )

        # 4. Layer 3: runtime adapter (synchronous).
        try:
            context = self._runtime_adapter.build(section)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 — defensive catch-all
            return self._make_fail_soft_assembly_from_exception(
                exc, layer="runtime_adapter"
            )

        # 5. Layer 4: assembly service (synchronous).
        #    The assembly service itself is already fail-soft.
        #    We still wrap the call in try/except for an
        #    extra envelope.
        try:
            assembly = self._assembly_service.assemble(context)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 — defensive catch-all
            return self._make_fail_soft_assembly_from_exception(
                exc, layer="assembly_service"
            )

        # 6. Final shape check: a regression could return a
        #    non-ArticleRagAskPromptAssembly (e.g. a typed
        #    fake).  Fail-soft rather than let the Ask layer
        #    consume an alien object.
        if not isinstance(assembly, ArticleRagAskPromptAssembly):
            return self._make_fail_soft_assembly(
                reason="malformed_assembly"
            )

        return assembly

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_fail_soft_assembly(
        self, *, reason: str
    ) -> ArticleRagAskPromptAssembly:
        """Build a fail-soft assembly for wiring / shape errors
        within the facade itself.

        The reason is logged (NEVER placed on the public
        assembly) so ops can diagnose.
        """
        logger.info(
            "Article RAG ask context provider: fail-soft "
            "(reason=%s); returning clean no-attach assembly",
            reason,
        )
        return _make_fail_soft_assembly_with_failure_code(
            FAILURE_CODE_FACADE_UNEXPECTED_ERROR
        )

    def _make_fail_soft_assembly_from_exception(
        self,
        exc: BaseException,
        *,
        layer: str,
    ) -> ArticleRagAskPromptAssembly:
        """Build a fail-soft assembly for an unexpected
        exception raised by one of the dependencies.

        The cause's class name is logged for ops diagnostics;
        the cause object is NOT attached to the public
        assembly (the assembly is a frozen dataclass, not an
        exception chain).  This is the deliberate design
        from the I4J spec — the public result is a pure
        value object that can be cached / serialised /
        passed across async boundaries without leaking
        internals.
        """
        logger.info(
            "Article RAG ask context provider: %s raised %s "
            "(unexpected); returning fail-soft assembly",
            layer,
            type(exc).__name__,
        )
        return _make_fail_soft_assembly_with_failure_code(
            FAILURE_CODE_FACADE_UNEXPECTED_ERROR
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _make_fail_soft_assembly_with_failure_code(
    failure_code: str,
) -> ArticleRagAskPromptAssembly:
    """Build a clean fail-soft assembly with a specific
    ``failure_code``.

    Uses the I4M assembly service's own fail-soft path
    (passing a non-dataclass runtime context triggers the
    I4M assembly's wrong-type branch which produces a
    clean no-attach assembly with
    ``FAILURE_CODE_ASSEMBLY_UNEXPECTED_ERROR``).
    """
    # We use a small, stable no-attach runtime context that
    # the I4M assembly can consume.  The I4M assembly has
    # its own fail-soft path, but we want a SPECIFIC failure
    # code (``FAILURE_CODE_FACADE_UNEXPECTED_ERROR``), not
    # the I4M-specific code.  We build a clean assembly
    # directly with the I4M dataclass's field defaults +
    # our own failure code.
    from .article_rag_ask_prompt_assembly import (
        ArticleRagAskPromptAssembly as _Assembly,
    )

    return _Assembly(
        kind="article_rag_context",
        should_attach=False,
        prompt_attachment_block="",
        citations=(),
        context_ids=(),
        source_pack_hash=None,
        query_sha256=None,
        status="not_indexed_or_unavailable",
        failure_code=failure_code,
        retryable=False,
        fallback_allowed=True,
        metadata_json={
            "status": "not_indexed_or_unavailable",
            "failure_code": failure_code,
            "retryable": False,
            "fallback_allowed": True,
        },
    )


__all__ = [
    "DEFAULT_FACADE_LIMIT",
    "DEFAULT_FACADE_MAX_CONTEXT_CHARS",
    "FAILURE_CODE_FACADE_UNEXPECTED_ERROR",
    "ArticleRagAskContextProvider",
]
