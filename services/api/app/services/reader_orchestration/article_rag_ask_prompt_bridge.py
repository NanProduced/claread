"""D6-I4O: Article RAG Ask Prompt Bridge.

Pure-synchronous, deterministic transform that combines an
upstream base prompt (built by the existing Ask prompt
preparation) with an
:class:`ArticleRagAskPromptAssembly` (D6-I4M) into a
single Ask-prompt-consumable bridge result.  The bridge is
the LAST layer before the Ask runtime layer actually
assembles / sends the prompt.

This module is the **bridge** between two pipeline branches:

  * **Branch A** (existing): the Ask service composes the
    base prompt payload via
    ``runtime_contract_svc.build_prompt_payload(...)`` and then
    calls ``runtime_contract_svc.prepare_prompt_payload(...)`` for
    budget-aware compaction.  The base prompt contains the
    user's question, history, tool definitions, etc. — but
    no RAG context yet.
  * **Branch B** (new): the ArticleRagAskContextProvider
    (D6-I4N) returns an
    :class:`ArticleRagAskPromptAssembly` carrying the
    RAG-specific section text + structured citations.  This
    is the RAG branch.

The bridge's job is to glue them together without modifying
Branch A.  The bridge:

  * is a pure deterministic transform — no LLM, no DB, no
    network;
  * never raises — every failure (malformed assembly,
    missing base prompt, oversize combined prompt) maps to a
    fail-soft bridge result with ``should_attach=False``;
  * never mutates the I4M assembly's content — the
    ``prompt_attachment_block`` is copied verbatim into a
    fixed-marker envelope;
  * never inlines citation JSON into the prompt — citations
    stay structured in a separate tuple;
  * never includes ``query_text`` — only ``query_sha256``
    (which the I4M assembly already scrubbed);
  * never reads vector payload, Plate / Markdown / DOM /
    Slate / UI projection;
  * metadata is a strict allowlist with a value-level guard
    (mirrors I4M's contract).

Insertion point (READ-ONLY AUDIT)
---------------------------------

The intended future insertion point is between
``runtime_contract_svc.build_prompt_payload(...)`` (line 3241
in ``app/services/reader_ask/service.py``) and
``runtime_contract_svc.prepare_prompt_payload(...)`` (line 3275
in the same file).  At that boundary, ``prompt_payload`` is
a plain ``dict[str, Any]``; the RAG context would be added
by mutating the dict's ``messages`` list (or a parallel
``attachments`` slot) to include the bridge's
``prompt_text`` + a structured ``citations`` slot for
downstream citation rendering.

This module DOES NOT modify that file.  It only provides a
tested bridge contract for future integration.  See the
module-level ``__all__`` and the bridge tests for the
contract.

The integration test ``test_bridge_consumes_real_i4n_assembly``
is the contract pin: it builds a real
``ArticleRagAskPromptAssembly`` via the production I4N
provider (with fake dependencies) and feeds the result
through the bridge.  The test asserts the bridge honours the
include / no-include contract without ever calling the
LLM or mutating the production Ask code path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .article_rag_ask_prompt_assembly import ArticleRagAskPromptAssembly

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default combined-prompt cap.  Mirrors the I4M default block
# cap (4000) but is conservative for the COMBINED prompt (the
# bridge result is what the LLM actually sees).  The bridge
# fail-softs if the combined length exceeds this cap — the
# Ask layer can decide to truncate / drop the attachment or
# split the prompt across turns.
DEFAULT_MAX_BRIDGE_CHARS = 16000

# The two fixed markers we use to bracket the RAG attachment
# in the combined prompt.  The bridge writes them verbatim —
# the markers are NOT Plate / Markdown / DOM / Slate / UI
# projection syntax; they are plain strings the Ask runtime
# can match on.
ATTACHMENT_BEGIN_MARKER = "[ARTICLE_RAG_ATTACHMENT_BEGIN]"
ATTACHMENT_END_MARKER = "[ARTICLE_RAG_ATTACHMENT_END]"

# Substrings we refuse to surface even on allowlisted metadata
# values.  Mirrors the I4J / I4K / I4L / I4M sets so the
# value policy is consistent across the chain.
_FORBIDDEN_METADATA_VALUE_SUBSTRINGS = (
    "token",
    "uri",
    "url=",
    "secret",
    "api_key",
    "apikey",
    "password",
    "passwd",
    "credential",
    "auth=",
    "bearer ",
    "query_text",
    "query=",
    "query_vector",
    "embedding=",
    "sdk_message",
    "error_message",
    "exception",
    "traceback",
    "stacktrace",
    "plate",
    "markdown",
    "dom",
    "slate",
    "render",
    "html=",
    "innerhtml",
    "innertext",
)

# Length cap on allowlisted string values.
_MAX_METADATA_VALUE_LEN = 256

# Strict 12-key allowlist for the metadata projection.  Mirrors
# the I4M contract.  The bridge DOES NOT re-introduce the
# upstream chain's allowlist; the I4M assembly has already
# scrubbed the underlying fields.  This allowlist is a
# defence-in-depth pass on the bridge itself.
_ALLOWED_METADATA_KEYS = frozenset(
    {
        "status",
        "failure_code",
        "retryable",
        "fallback_allowed",
        "omitted_hit_count",
        "budget_exceeded",
        "stable_document_id",
        "base_id",
        "record_generation",
        "plan_content_sha256",
        "index_version",
        "source_pack_hash",
    }
)


# Bridge-owned failure codes.  Distinguish "RAG unavailable
# upstream" (the assembly's own failure_code, e.g.
# "context_no_indexed_run") from "the bridge itself refused to
# attach" (these codes).  Downstream can dispatch on these
# codes independently.
FAILURE_CODE_BRIDGE_UNEXPECTED_ERROR = (
    "article_rag_prompt_bridge_unexpected_error"
)
FAILURE_CODE_BRIDGE_SHAPE_INVALID = (
    "article_rag_prompt_bridge_shape_invalid"
)
FAILURE_CODE_BRIDGE_OVERSIZE = (
    "article_rag_prompt_bridge_oversize"
)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArticleRagAskPromptBridgeResult:
    """The deterministic bridge output the Ask runtime layer
    consumes.

    The Ask layer's policy is keyed on ``should_attach``:

      * ``True`` — ``prompt_text`` is the combined prompt:
        ``base_prompt_text`` + the bridge's envelope around
        the I4M assembly's ``prompt_attachment_block``.  The
        Ask layer embeds ``prompt_text`` verbatim; it then
        renders ``citations`` as a separate footnote /
        source list.
      * ``False`` — no RAG context.  ``prompt_text`` is the
        original ``base_prompt_text`` unchanged.  ``citations``
        is empty.  The Ask runtime answers without RAG.

    ``metadata_json`` is a STRICT allowlist of the upstream
    assembly's safe fields.  Provider / query / vector /
    projection fields are NEVER surfaced.

    Every field that may carry user-derived content uses
    ``field(repr=False)`` so the bridge result's default
    repr / str does NOT echo it.
    """

    # Whether the RAG block is embedded in ``prompt_text``.
    should_attach: bool
    # The combined prompt (base prompt + RAG block envelope)
    # on the attach path; the original base prompt on the
    # no-attach path.  The bridge MUST NOT mutate the
    # underlying base prompt text or the assembly's
    # ``prompt_attachment_block`` on the no-attach path.
    #
    # ``repr=False``: contains chunk text / query fragments /
    # base prompt text.
    prompt_text: str = field(repr=False)
    # The verbatim RAG attachment block (after the begin /
    # end markers).  Empty on the no-attach path.  Surfaced
    # so the Ask runtime can render / extract / log the
    # block separately.
    #
    # ``repr=False``: contains chunk text.
    attachment_block: str = field(repr=False)
    # Structured citations (verbatim from the I4M assembly).
    # The bridge MUST NOT re-parse the attachment block to
    # extract citations.
    #
    # ``repr=False``: the citation dicts are plan-backed
    # content.
    citations: tuple[dict[str, Any], ...] = field(repr=False)
    # Stable context ids embedded in the attachment block.
    #
    # ``repr=False``.
    context_ids: tuple[str, ...] = field(repr=False)
    # Source identity hash from the I4G composer.
    #
    # ``repr=False``: a regression could surface a
    # secret-bearing value here; the value-level guard
    # drops such values.
    source_pack_hash: str | None = field(repr=False)
    # SHA-256 of the query text, for traceability.  NEVER the
    # raw query text.
    #
    # ``repr=False``.
    query_sha256: str | None = field(repr=False)
    # Upstream status (propagated unchanged).
    #
    # ``repr=False``.
    status: str = field(repr=False)
    # Upstream failure code (propagated unchanged).
    #
    # ``repr=False``.
    failure_code: str | None = field(repr=False)
    # Upstream retryable flag.
    retryable: bool
    # Upstream fallback-allowed flag.  Always ``True`` on the
    # no-attach path (the Ask layer can answer without RAG).
    fallback_allowed: bool
    # Strict-allowlist metadata.
    #
    # ``repr=False``.
    metadata_json: dict[str, Any] = field(
        default_factory=dict, repr=False
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_metadata_value(value: Any) -> Any:
    """Same value guard as I4J / I4K / I4L / I4M."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        if len(value) > _MAX_METADATA_VALUE_LEN:
            return None
        lowered = value.lower()
        for substr in _FORBIDDEN_METADATA_VALUE_SUBSTRINGS:
            if substr in lowered:
                return None
        return value
    try:
        import uuid as _uuid

        if isinstance(value, _uuid.UUID):
            return str(value)
    except ImportError:  # pragma: no cover — stdlib always available
        pass
    return None


def _scrub_metadata(metadata: Any) -> dict[str, Any]:
    """Strict allowlist + value guard for ``metadata_json``."""
    if not isinstance(metadata, dict):
        return {}
    safe: dict[str, Any] = {}
    for key, raw_value in metadata.items():
        if key not in _ALLOWED_METADATA_KEYS:
            continue
        safe_value = _safe_metadata_value(raw_value)
        if safe_value is None and raw_value is not None:
            continue
        safe[str(key)] = safe_value
    return safe


def _scrub_top_level_string(value: Any) -> str | None:
    """Scrub a top-level string field (``source_pack_hash`` /
    ``failure_code``).  Untrusted values are dropped
    (replaced with ``None``).  ``None`` is preserved."""
    if value is None:
        return None
    safe_value = _safe_metadata_value(value)
    if safe_value is None:
        return None
    if not isinstance(safe_value, str):
        return None
    return safe_value


# Strict 64-char lowercase-hex validation for ``query_sha256``.
# The field is contractually a SHA-256 hex digest; raw query
# text / non-hex strings / non-strings are dropped to
# ``None``.  Mirrors the I4K / I4L / I4M contract.
_SHA256_HEX_LEN = 64


def _scrub_sha256(value: Any) -> str | None:
    """Return the value only if it is a 64-char lowercase-hex
    string.  Otherwise return ``None`` (dropped).  ``None``
    is preserved.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    if len(value) != _SHA256_HEX_LEN:
        return None
    for ch in value:
        if not (("0" <= ch <= "9") or ("a" <= ch <= "f")):
            return None
    return value


def _build_envelope(attachment_block: str) -> str:
    """Wrap ``attachment_block`` in fixed markers.

    The format is:

        [ARTICLE_RAG_ATTACHMENT_BEGIN]
        {attachment_block}
        [ARTICLE_RAG_ATTACHMENT_END]

    ``attachment_block`` is copied verbatim — the bridge
    does NOT mutate / re-parse / truncate the I4M assembly
    output.  The markers are deliberately simple strings
    (not Plate / Markdown / DOM / Slate / UI projection
    syntax) so the Ask runtime can extract the inner
    content verbatim.
    """
    return (
        f"{ATTACHMENT_BEGIN_MARKER}\n"
        f"{attachment_block}\n"
        f"{ATTACHMENT_END_MARKER}"
    )


# ---------------------------------------------------------------------------
# Status / shape invariants (mirrors I4J / I4K / I4L / I4M)
# ---------------------------------------------------------------------------


# The 5 status values the assembly / runtime adapter chain
# uses.  ``ArticleRagAskPromptAssembly.status`` is typed as
# a ``typing.Literal`` at compile time only — at runtime a
# regression / hostile fake could surface an unrecognised
# string.  We validate the assembly's status against this
# allowlist BEFORE the attach / no-attach branching so a
# hostile status can never slip through to a happy-path
# result.
_ALLOWED_ASSEMBLY_STATUSES = frozenset(
    {
        "available",
        "empty",
        "not_indexed_or_unavailable",
        "composer_rejected",
        "disabled",
    }
)


def _assembly_status_ok(status: Any) -> bool:
    """Runtime guard on the assembly's ``status`` field.

    Mirrors the I4J / I4K / I4L / I4M contract.  ``status``
    MUST be in :data:`_ALLOWED_ASSEMBLY_STATUSES`; a hostile
    or regressed status is treated as a shape error and
    fail-softs to ``shape_invalid``.
    """
    if not isinstance(status, str):
        return False
    return status in _ALLOWED_ASSEMBLY_STATUSES


def _no_attach_path_shape_ok(assembly: ArticleRagAskPromptAssembly) -> bool:
    """Validate the no-attach path's shape invariant.

    The contract is: ``should_attach=False`` ⇒
    ``status != "available"`` AND
    ``prompt_attachment_block == ""`` AND
    ``citations == ()`` AND ``context_ids == ()``.

    A regression / hostile fake could surface
    ``should_attach=False`` with a non-empty attachment
    block or populated citations (e.g. a regression that
    resets ``should_attach`` but leaves the assembly's other
    fields populated).  That would create dangling citation
    UI in the Ask layer.  The bridge fail-softs to
    ``shape_invalid`` when the no-attach path is
    state-inconsistent.
    """
    if assembly.status == "available":
        return False
    if not isinstance(assembly.prompt_attachment_block, str):
        return False
    if assembly.prompt_attachment_block != "":
        return False
    if not (
        isinstance(assembly.citations, (tuple, list))
        and len(assembly.citations) == 0
    ):
        return False
    if not (
        isinstance(assembly.context_ids, (tuple, list))
        and len(assembly.context_ids) == 0
    ):
        return False
    return True


def _attach_path_shape_ok(assembly: ArticleRagAskPromptAssembly) -> bool:
    """Validate the attach path's shape invariant.

    The contract is: ``should_attach=True`` ⇒
    ``status == "available"`` AND
    ``isinstance(prompt_attachment_block, str) and
    prompt_attachment_block`` non-empty AND
    ``citations`` and ``context_ids`` are iterable
    sequences with matching lengths.

    A regression / hostile fake could surface
    ``should_attach=True`` with a non-``available`` status
    (a state-semantic inconsistency) or with citation /
    context_id length mismatch (the LLM would have a hard
    time mapping ``[rag-1]`` markers to citation rows).  The
    bridge fail-softs to ``shape_invalid`` when the attach
    path is state-inconsistent.
    """
    if assembly.status != "available":
        return False
    if not isinstance(assembly.prompt_attachment_block, str):
        return False
    if not assembly.prompt_attachment_block.strip():
        return False
    if not (
        isinstance(assembly.citations, (tuple, list))
        and isinstance(assembly.context_ids, (tuple, list))
    ):
        return False
    if len(assembly.citations) != len(assembly.context_ids):
        return False
    return True


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------


class ArticleRagAskPromptBridge:
    """Pure deterministic bridge: base prompt + RAG assembly →
    combined Ask-prompt-consumable bridge result.

    The bridge is the LAST layer before the Ask runtime layer
    actually assembles / sends the prompt.  The integration
    test ``test_bridge_consumes_real_i4n_assembly`` is the
    contract pin.
    """

    def __init__(
        self,
        *,
        max_bridge_chars: int = DEFAULT_MAX_BRIDGE_CHARS,
    ) -> None:
        if max_bridge_chars <= 0:
            raise ValueError(
                "ArticleRagAskPromptBridge constructed with "
                f"max_bridge_chars={max_bridge_chars}; must be a "
                "positive integer"
            )
        self._max_bridge_chars = max_bridge_chars

    def bridge(
        self,
        *,
        base_prompt_text: str | None,
        rag_assembly: Any,
    ) -> ArticleRagAskPromptBridgeResult:
        """Bridge ``base_prompt_text`` with ``rag_assembly``.

        Never raises.  Every failure (malformed assembly,
        missing base prompt, oversize combined prompt) maps to
        a fail-soft bridge result with ``should_attach=False``.
        """
        # 1. Defensive shape checks.
        if not isinstance(rag_assembly, ArticleRagAskPromptAssembly):
            # Malformed assembly: return a clean no-attach
            # bridge with the original base prompt preserved
            # (or empty if missing).  The bridge-owned
            # ``shape_invalid`` failure code distinguishes
            # "bridge refused to attach" from "RAG unavailable
            # upstream".
            return self._make_fail_soft_bridge(
                base_prompt_text=base_prompt_text,
                rag_assembly=None,
                reason="malformed_assembly",
                failure_code=FAILURE_CODE_BRIDGE_SHAPE_INVALID,
            )
        if not isinstance(base_prompt_text, str):
            # Missing or non-string base prompt.  We still
            # propagate the base prompt verbatim (the Ask
            # runtime can still send the no-RAG prompt to the
            # LLM).  Bridge-owned shape_invalid failure code.
            return self._make_fail_soft_bridge(
                base_prompt_text=None,
                rag_assembly=rag_assembly,
                reason="missing_base_prompt",
                failure_code=FAILURE_CODE_BRIDGE_SHAPE_INVALID,
            )

        # 1.5 Runtime invariant: the assembly's ``status``
        #     MUST be in the 5-value allowlist.  A
        #     regression / hostile fake could surface an
        #     unrecognised string (``"paused"``, ``""``,
        #     ``"SECRET-..."``).  The bridge fail-softs to
        #     ``shape_invalid`` so the Ask layer can dispatch
        #     on the bridge-owned reason.
        if not _assembly_status_ok(rag_assembly.status):
            return self._make_fail_soft_bridge(
                base_prompt_text=base_prompt_text,
                rag_assembly=rag_assembly,
                reason="unknown_assembly_status",
                failure_code=FAILURE_CODE_BRIDGE_SHAPE_INVALID,
            )

        # 2. No-attach path: copy the assembly as a no-attach
        #    bridge.  The base prompt is returned verbatim (no
        #    RAG block embedded).
        if not rag_assembly.should_attach:
            # Runtime invariant: the no-attach path's shape
            # MUST be self-consistent — empty attachment
            # block, empty citations, empty context_ids, and
            # a status that is NOT ``"available"``.  A
            # regression / hostile fake could surface
            # ``should_attach=False`` with a non-``available``
            # status OR populated citation fields, which
            # would create dangling citation UI in the Ask
            # layer.  Fail soft.
            if not _no_attach_path_shape_ok(rag_assembly):
                return self._make_fail_soft_bridge(
                    base_prompt_text=base_prompt_text,
                    rag_assembly=rag_assembly,
                    reason="inconsistent_no_attach_shape",
                    failure_code=FAILURE_CODE_BRIDGE_SHAPE_INVALID,
                )
            return ArticleRagAskPromptBridgeResult(
                should_attach=False,
                prompt_text=base_prompt_text,
                attachment_block="",
                citations=(),
                context_ids=(),
                source_pack_hash=_scrub_top_level_string(
                    rag_assembly.source_pack_hash
                ),
                query_sha256=_scrub_sha256(rag_assembly.query_sha256),
                status=rag_assembly.status,
                failure_code=_scrub_top_level_string(
                    rag_assembly.failure_code
                ),
                retryable=bool(rag_assembly.retryable),
                fallback_allowed=bool(rag_assembly.fallback_allowed),
                metadata_json=_scrub_metadata(
                    rag_assembly.metadata_json
                ),
            )

        # 3. Attach path: build the envelope around the
        #    assembly's ``prompt_attachment_block`` (verbatim)
        #    and append to the base prompt.
        #
        #    Runtime invariant: the attach path's shape MUST
        #    be self-consistent — ``status == "available"``,
        #    non-empty attachment block, citations /
        #    context_ids are sequences with matching lengths.
        #    A regression / hostile fake could surface
        #    ``should_attach=True`` with a non-``available``
        #    status OR citation / context_id length mismatch;
        #    the LLM would have a hard time mapping
        #    ``[rag-1]`` markers to citation rows.  Fail soft.
        if not _attach_path_shape_ok(rag_assembly):
            return self._make_fail_soft_bridge(
                base_prompt_text=base_prompt_text,
                rag_assembly=rag_assembly,
                reason="inconsistent_attach_shape",
                failure_code=FAILURE_CODE_BRIDGE_SHAPE_INVALID,
            )

        attachment_block = rag_assembly.prompt_attachment_block
        # The shape check above guarantees this is a
        # non-empty str, but we keep the original check for
        # defence in depth.
        if not isinstance(attachment_block, str) or not attachment_block:
            return self._make_fail_soft_bridge(
                base_prompt_text=base_prompt_text,
                rag_assembly=rag_assembly,
                reason="empty_attachment_block",
                failure_code=FAILURE_CODE_BRIDGE_SHAPE_INVALID,
            )

        envelope = _build_envelope(attachment_block)
        combined_prompt = base_prompt_text + "\n\n" + envelope

        # 4. Length cap.  If the combined prompt exceeds the
        #    cap, fail soft.  Truncation would corrupt the
        #    marker alignment with the citation list and
        #    confuse the LLM about which citation maps to which
        #    block.
        if len(combined_prompt) > self._max_bridge_chars:
            logger.info(
                "Article RAG ask prompt bridge: combined prompt "
                "exceeds max_bridge_chars (actual=%d, max=%d); "
                "returning fail-soft no-attach bridge (no "
                "truncation)",
                len(combined_prompt),
                self._max_bridge_chars,
            )
            return self._make_fail_soft_bridge(
                base_prompt_text=base_prompt_text,
                rag_assembly=rag_assembly,
                reason="oversize_combined_prompt",
                failure_code=FAILURE_CODE_BRIDGE_OVERSIZE,
            )

        return ArticleRagAskPromptBridgeResult(
            should_attach=True,
            prompt_text=combined_prompt,
            attachment_block=envelope,
            citations=tuple(rag_assembly.citations),
            context_ids=tuple(rag_assembly.context_ids),
            source_pack_hash=_scrub_top_level_string(
                rag_assembly.source_pack_hash
            ),
            query_sha256=_scrub_sha256(rag_assembly.query_sha256),
            status=rag_assembly.status,
            failure_code=_scrub_top_level_string(
                rag_assembly.failure_code
            ),
            retryable=bool(rag_assembly.retryable),
            fallback_allowed=bool(rag_assembly.fallback_allowed),
            metadata_json=_scrub_metadata(
                rag_assembly.metadata_json
            ),
        )

    # ------------------------------------------------------------------
    # Internal fail-soft helpers
    # ------------------------------------------------------------------

    def _make_fail_soft_bridge(
        self,
        *,
        base_prompt_text: str | None,
        rag_assembly: Any,
        reason: str,
        failure_code: str = FAILURE_CODE_BRIDGE_UNEXPECTED_ERROR,
    ) -> ArticleRagAskPromptBridgeResult:
        """Build a clean no-attach bridge for any fail-soft
        path.  Logs the reason for ops dashboards; the
        reason MUST NOT appear on the public result.

        The original ``base_prompt_text`` is preserved (the
        Ask runtime can still send the no-RAG prompt to the
        LLM); ``prompt_attachment_block`` is empty;
        ``citations`` and ``context_ids`` are ALWAYS empty on
        every fail-soft path (the attachment is not in the
        prompt, so the Ask layer must not render citations
        for a non-existent block).  ``failure_code`` carries
        the bridge-owned reason (vs. the assembly's own
        failure_code, which would describe upstream
        RAG-unavailability — not "the bridge refused to
        attach").
        """
        logger.info(
            "Article RAG ask prompt bridge: fail-soft "
            "(reason=%s; failure_code=%s); returning no-attach "
            "bridge",
            reason, failure_code,
        )
        prompt_text = (
            base_prompt_text if isinstance(base_prompt_text, str) else ""
        )
        return ArticleRagAskPromptBridgeResult(
            should_attach=False,
            prompt_text=prompt_text,
            attachment_block="",
            # ``citations`` and ``context_ids`` are ALWAYS empty
            # on every fail-soft path.  ``should_attach=False``
            # means the prompt does NOT include the RAG block;
            # the Ask layer must not render citations for a
            # non-existent block (this was the P1a review
            # finding).
            citations=(),
            context_ids=(),
            source_pack_hash=None,
            query_sha256=None,
            # The status is the universal
            # ``not_indexed_or_unavailable`` so the Ask layer's
            # default fallback branch fires.  ``failure_code``
            # carries the bridge-owned reason (vs. the
            # assembly's own failure_code, which would describe
            # upstream RAG-unavailability — not "the bridge
            # refused to attach").
            status="not_indexed_or_unavailable",
            failure_code=failure_code,
            retryable=False,
            fallback_allowed=True,
            metadata_json={},
        )


__all__ = [
    "DEFAULT_MAX_BRIDGE_CHARS",
    "ATTACHMENT_BEGIN_MARKER",
    "ATTACHMENT_END_MARKER",
    "FAILURE_CODE_BRIDGE_UNEXPECTED_ERROR",
    "FAILURE_CODE_BRIDGE_SHAPE_INVALID",
    "FAILURE_CODE_BRIDGE_OVERSIZE",
    "ArticleRagAskPromptBridgeResult",
    "ArticleRagAskPromptBridge",
]