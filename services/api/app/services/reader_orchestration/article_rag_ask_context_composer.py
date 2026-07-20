"""D6-I4G: Article RAG Ask Context Composer.

Converts an :class:`ArticleRagContextPack` (D6-I4F) into an Ask-prompt-
embeddable, LLM-safe context bundle.  The composer is a pure
deterministic transform:

  * no LLM calls, no DB writes, no network access;
  * ``prompt_context_text`` is a plain-text rendering of each item
    that includes ONLY ``context_id`` + ``rank`` + ``score`` + ``text``;
    the raw citation dict, ``metadata_json``, ``provider_metadata``,
    ``query_sha256`` are NOT inlined into the prompt text;
  * ``citations`` is a separate structured tuple (one entry per
    ``pack.items[]``), copied verbatim from
    ``pack.items[i].citation`` — the retrieval service has already
    joined hits against the current plan, so these citations are
    plan-backed (Postgres truth).  The composer never reads or
    rewrites the citation dict.
  * ``source_pack_hash`` is a deterministic SHA-256 over the stable
    pack identity (``reading_record_id`` / ``stable_document_id`` /
    ``base_id`` / ``record_generation`` /
    ``plan_content_sha256`` / ``context_ids`` / ``chunk_ids`` /
    per-item text sha256 / per-item citation sha256).  It
    explicitly EXCLUDES ``provider_metadata`` and ``query_sha256``:
    a change in the searcher's diagnostic dict or in the query hash
    must NOT change the bundle's source identity (the bundle is
    about the source content, not about the call that retrieved
    it).

Truth boundary
--------------

Citation truth comes from the retrieval service's plan-backed
citation (Postgres is the truth).  This module copies the citation
verbatim into a structured ``citations`` tuple and NEVER reads
vector-payload text / citation / metadata.  ``provider_metadata`` is
whitelist-scrubbed at the context-service layer (D6-I4F) and is not
re-scrubbed here.

Security contract
-----------------

* ``item.text`` is length-capped (default 12000 chars).  An empty
  text or an over-cap text fails closed with a typed
  :class:`ArticleRagAskContextComposerError` whose message is a
  fixed diagnostic — the chunk text NEVER appears in the error
  message (a long malicious chunk would otherwise surface in logs
  and exception dashboards).
* The composer is pure / synchronous — no I/O.
* All failure codes are stable and machine-readable.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .article_rag_context_service import (
    ArticleRagContextItem,
    ArticleRagContextPack,
)
from .article_rag_index_worker import ArticleRagIndexWorkerError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Hard cap on a single item.text in the prompt bundle.  The I4F
# context service already applies a per-pack character budget; this
# per-item cap is a defence-in-depth backstop — a single chunk
# larger than this would imply a data corruption / wrong caller
# contract.  We fail closed rather than silently truncating (which
# would corrupt the citation offset semantics — the citation is
# keyed to the chunk's full text).
DEFAULT_MAX_ITEM_TEXT_CHARS = 12000

# Failure codes — stable, machine-readable.
FAILURE_CODE_ASK_CONTEXT_EMPTY_TEXT = "ask_context_empty_text"
FAILURE_CODE_ASK_CONTEXT_TEXT_TOO_LONG = "ask_context_text_too_long"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ArticleRagAskContextComposerError(ArticleRagIndexWorkerError):
    """Typed failure for the ask-context composer.

    Inherits :class:`ArticleRagIndexWorkerError` so any future
    orchestrator that catches the worker base class also catches
    composer failures.  ``failure_class`` defaults to
    ``"ask_context"`` so dashboards can route composer failures
    separately from retrieval / context-pack / write-side failures.

    The error message is a fixed diagnostic that names the failure
    class + code + the index of the offending item.  It NEVER
    includes the chunk text (a long / hostile chunk would otherwise
    surface in exception dashboards and logs).
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        failure_code: str,
        failure_class: str = "ask_context",
        rationale_code: str | None = None,
    ) -> None:
        super().__init__(
            message,
            retryable=retryable,
            failure_class=failure_class,
            failure_code=failure_code,
            rationale_code=rationale_code,
        )


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArticleRagAskContextCitation:
    """One citation in the structured citations tuple.

    The citation dict is the EXACT 9-key I4A shape (Postgres
    plan-backed) that the retrieval service produced.  We never
    rewrite it; we never inline it into the prompt text; we only
    expose it here for the LLM/Ask layer to render separately
    (e.g. as a numbered footnote list).
    """

    context_id: str
    chunk_id: str
    citation: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ArticleRagAskContextBlock:
    """One block in the prompt-context text rendering.

    The block carries ONLY the fields that go into the prompt text:
    ``context_id`` + ``rank`` + ``score`` + ``text``.  No citation
    dict, no metadata, no provider_metadata.  Used by the composer
    to assemble :attr:`ArticleRagAskContextBundle.prompt_context_text`.
    """

    context_id: str
    rank: int
    score: float
    text: str


@dataclass(frozen=True, slots=True)
class ArticleRagAskContextBundle:
    """A deterministic, LLM-safe context bundle for Ask.

    ``prompt_context_text`` is plain text — a concatenation of one
    block per item, separated by a deterministic blank line.  The
    block format is intentionally simple (no Markdown syntax, no
    Plate JSON, no DOM selection, no Slate path, no UI display
    group fields) so the composer can never accidentally treat a
    projection field as a fact source.

    ``citations`` is a separate structured tuple — one
    :class:`ArticleRagAskContextCitation` per item, in the same
    order as ``prompt_context_text``.  The LLM/Ask layer can use
    these to render a separate footnote / source list.

    ``context_ids`` mirrors the order of items; it is a convenience
    for the Ask layer that wants to know which context_ids were
    embedded without re-parsing the text.

    ``source_pack_hash`` is a deterministic SHA-256 over the stable
    pack identity (see :func:`_compute_source_pack_hash`).  It is
    the canonical identity of the BUNDLE — a change in
    ``provider_metadata`` or ``query_sha256`` does NOT change this
    hash.  Callers can use it for cache keys, dedup, or to detect
    source-content drift between consecutive Ask calls.

    ``empty`` is ``True`` when the input pack has no items; the
    bundle's text and citations are both empty, and the bundle
    preserves the pack's ``omitted_hit_count`` /
    ``budget_exceeded`` for ops visibility.
    """

    prompt_context_text: str
    citations: tuple[ArticleRagAskContextCitation, ...]
    context_ids: tuple[str, ...]
    source_pack_hash: str
    omitted_hit_count: int
    budget_exceeded: bool
    empty: bool
    # Echoed from the pack — these are NOT included in the
    # source_pack_hash (see ``_compute_source_pack_hash``).
    reading_record_id: Any = field(default=None)
    stable_document_id: Any = field(default=None)
    base_id: Any = field(default=None)
    record_generation: int = 0
    plan_content_sha256: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_item_text(
    *,
    item: ArticleRagContextItem,
    item_index: int,
    max_item_text_chars: int,
) -> None:
    """Validate one item's text.  Raises typed error on empty / over-cap.

    The error message identifies the failing item by ``context_id`` /
    ``chunk_id`` / index only — NEVER the text.  This guards against
    a long / hostile chunk surfacing in exception dashboards.
    """
    text = item.text
    if not isinstance(text, str) or len(text) == 0:
        raise ArticleRagAskContextComposerError(
            f"ask context composer item at index={item_index} "
            f"(context_id={item.context_id}, chunk_id={item.chunk_id}) "
            "has empty or non-string text; refusing to embed an "
            "empty context block",
            retryable=False,
            failure_code=FAILURE_CODE_ASK_CONTEXT_EMPTY_TEXT,
        )
    if len(text) > max_item_text_chars:
        raise ArticleRagAskContextComposerError(
            f"ask context composer item at index={item_index} "
            f"(context_id={item.context_id}, chunk_id={item.chunk_id}) "
            f"exceeds max_item_text_chars={max_item_text_chars} "
            f"(len={len(text)}); refusing to embed an oversized "
            "context block (truncation would corrupt citation "
            "offset semantics)",
            retryable=False,
            failure_code=FAILURE_CODE_ASK_CONTEXT_TEXT_TOO_LONG,
        )


def _format_block_text(
    *,
    context_id: str,
    rank: int,
    score: float,
    text: str,
) -> str:
    """Render one block as plain text.  No Markdown, no Plate JSON,
    no projection fields.

    Format (deterministic):

        [context_id] rank=<rank> score=<score>
        <text>

    Score is rendered with 6 decimal places to keep the output
    stable across Python's float-formatting changes (we never want
    the bundle hash to change because Python switched its default
    float repr from ``repr`` to a shorter form).
    """
    score_str = f"{score:.6f}"
    return f"[{context_id}] rank={rank} score={score_str}\n{text}"


def _compute_source_pack_hash(
    pack: ArticleRagContextPack,
) -> str:
    """Deterministic SHA-256 over the bundle's source identity.

    Inputs (in this exact order, joined with ``|``):
      * ``reading_record_id`` (str)
      * ``stable_document_id`` (str)
      * ``base_id`` (str)
      * ``record_generation`` (str)
      * ``plan_content_sha256``
      * per-item ``context_id`` (preserves order)
      * per-item ``chunk_id`` (preserves order)
      * per-item SHA-256 of ``item.text`` (preserves order)
      * per-item SHA-256 of ``json.dumps(item.citation, sort_keys=True, default=str)``
        (preserves order; ``sort_keys=True`` for stability; ``default=str``
        for any non-JSON-serialisable UUID values)

    Explicitly EXCLUDED:
      * ``provider_metadata`` — a searcher diagnostic change must
        not change the bundle's source identity;
      * ``query_sha256`` — the bundle is about the source content,
        not about the call that retrieved it;
      * ``omitted_hit_count`` / ``budget_exceeded`` — these are
        ops diagnostics, not source identity;
      * ``max_context_chars`` / ``total_text_chars`` — budget
        state, not source identity.
    """
    parts: list[str] = [
        str(pack.reading_record_id),
        str(pack.stable_document_id),
        str(pack.base_id),
        str(pack.record_generation),
        pack.plan_content_sha256,
    ]
    for item in pack.items:
        parts.append(item.context_id)
        parts.append(item.chunk_id)
        parts.append(
            hashlib.sha256(item.text.encode("utf-8")).hexdigest()
        )
        citation_json = json.dumps(
            item.citation, sort_keys=True, default=str
        )
        parts.append(hashlib.sha256(citation_json.encode("utf-8")).hexdigest())
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Composer
# ---------------------------------------------------------------------------


class ArticleRagAskContextComposer:
    """Deterministic composer: pack → Ask-prompt-embeddable bundle.

    No LLM, no DB, no network.  Pure synchronous transform.  Safe
    to call from any context.

    The composer is a class (not a module-level function) so the
    ``max_item_text_chars`` cap can be configured per-deployment /
    per-call without making the contract a global.  ``compose``
    itself is the public entry point.
    """

    def __init__(
        self,
        *,
        max_item_text_chars: int = DEFAULT_MAX_ITEM_TEXT_CHARS,
    ) -> None:
        if max_item_text_chars <= 0:
            raise ArticleRagAskContextComposerError(
                "ArticleRagAskContextComposer constructed with "
                f"max_item_text_chars={max_item_text_chars}; must "
                "be a positive integer",
                retryable=False,
                failure_code=FAILURE_CODE_ASK_CONTEXT_TEXT_TOO_LONG,
            )
        self._max_item_text_chars = max_item_text_chars

    def compose(
        self, pack: ArticleRagContextPack
    ) -> ArticleRagAskContextBundle:
        """Compose an :class:`ArticleRagAskContextBundle` from
        ``pack``.

        The transform is deterministic: identical packs always
        produce identical bundles, and any change to the source
        content (text, citation, stable ids, plan hash) produces a
        different ``source_pack_hash``.

        Empty packs return ``empty=True`` with empty text and an
        empty citations tuple — NOT an error.
        """
        # 1. Empty pack short-circuit.
        if not pack.items:
            bundle = ArticleRagAskContextBundle(
                prompt_context_text="",
                citations=(),
                context_ids=(),
                source_pack_hash=_compute_source_pack_hash(pack),
                omitted_hit_count=pack.omitted_hit_count,
                budget_exceeded=pack.budget_exceeded,
                empty=True,
                reading_record_id=pack.reading_record_id,
                stable_document_id=pack.stable_document_id,
                base_id=pack.base_id,
                record_generation=pack.record_generation,
                plan_content_sha256=pack.plan_content_sha256,
            )
            return bundle

        # 2. Validate every item.  Failure on any item is fail-closed
        #    (a partial bundle would be worse than no bundle — the
        #    LLM would either hallucinate a missing context_id or
        #    silently truncate a citation).
        for idx, item in enumerate(pack.items):
            _validate_item_text(
                item=item,
                item_index=idx,
                max_item_text_chars=self._max_item_text_chars,
            )

        # 3. Build the per-item blocks.  Plain text format; no
        #    projection fields.
        blocks: list[str] = []
        citations: list[ArticleRagAskContextCitation] = []
        context_ids: list[str] = []
        for item in pack.items:
            blocks.append(
                _format_block_text(
                    context_id=item.context_id,
                    rank=item.rank,
                    score=item.score,
                    text=item.text,
                )
            )
            citations.append(
                ArticleRagAskContextCitation(
                    context_id=item.context_id,
                    chunk_id=item.chunk_id,
                    citation=dict(item.citation),
                )
            )
            context_ids.append(item.context_id)

        # Two blank lines between blocks.  Plain text only.
        prompt_text = "\n\n".join(blocks)

        return ArticleRagAskContextBundle(
            prompt_context_text=prompt_text,
            citations=tuple(citations),
            context_ids=tuple(context_ids),
            source_pack_hash=_compute_source_pack_hash(pack),
            omitted_hit_count=pack.omitted_hit_count,
            budget_exceeded=pack.budget_exceeded,
            empty=False,
            reading_record_id=pack.reading_record_id,
            stable_document_id=pack.stable_document_id,
            base_id=pack.base_id,
            record_generation=pack.record_generation,
            plan_content_sha256=pack.plan_content_sha256,
        )


__all__ = [
    "DEFAULT_MAX_ITEM_TEXT_CHARS",
    "FAILURE_CODE_ASK_CONTEXT_EMPTY_TEXT",
    "FAILURE_CODE_ASK_CONTEXT_TEXT_TOO_LONG",
    "ArticleRagAskContextComposerError",
    "ArticleRagAskContextCitation",
    "ArticleRagAskContextBlock",
    "ArticleRagAskContextBundle",
    "ArticleRagAskContextComposer",
]