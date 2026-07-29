"""Turn coordinator — single turn state center for Ask model-views (R4-A5-7).

Narrow public surface
---------------------
- :meth:`TurnCoordinator.assemble_turn` → :class:`TurnAssembly`
- :meth:`TurnCoordinator.expand_evidence` → :class:`MeteredToolReturn`
- :meth:`TurnCoordinator.search_current_article` → :class:`MeteredToolReturn`
- :meth:`TurnCoordinator.search_web` → :class:`MeteredToolReturn`

Owns registry, six-account budget, renderer, fence, pointer ledger,
selection expansion session, map expander, RAG identity, and search
counter. Outer initial-assembly transaction is plan-then-commit with
host-only rollback receipts; once ``agent.run`` begins the assembly is
committed and is never refunded because the model already saw content.
"""

from __future__ import annotations

import asyncio
import datetime as _datetime
import hmac
import json
import secrets as _secrets
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from xml.sax.saxutils import escape as _xml_escape_web

if TYPE_CHECKING:
    # Avoid a circular import at module load time:
    #   map_source_material_provider
    #    → source_evidence_descriptor
    #    → reader_record_ask.article_map_model_view
    #    → reader_record_ask.__init__ (package init)
    #    → reader_record_ask.runtime
    #    → reader_record_ask.turn_coordinator (this module)
    #    → map_source_material_provider  ← cycle
    # ``from __future__ import annotations`` makes all annotations strings,
    # so the imports are only needed for static type checking, not runtime.
    from app.services.reader_orchestration.map_source_material_provider import (
        MapSourceMaterial,
        MapSourceMaterialProvider,
    )

from app.services.reader_record_ask.answer_block_provenance import ArticleScope
from app.services.reader_record_ask.article_map_model_view import (
    ArticleMapEntrySource,
    ArticleMapExpander,
    ArticleMapPromptCapability,
    ArticleMapResult,
    assemble_article_map,
)
from app.services.reader_record_ask.article_rag_model_view import (
    RagEnvelopeIdentity,
    assemble_rag_model_view,
)
from app.services.reader_record_ask.article_rag_port import ArticleRagSearchPort
from app.services.reader_record_ask.baseline_context import BaselineAgentContext
from app.services.reader_record_ask.baseline_model_view import (
    BaselineModelViewResult,
    BaselinePromptCapability,
    assemble_baseline_model_view,
    rollback_baseline_inject,
)
from app.services.reader_record_ask.context_envelope import (
    ReadingRecordAskContextEnvelope,
)
from app.services.reader_record_ask.document_access import (
    DocumentAccess,
    DocumentScopeSnapshot,
    scope_identity_mismatch_reason,
)
from app.services.reader_record_ask.evidence import ServerEvidenceObservation
from app.services.reader_record_ask.evidence_expansion import (
    EvidenceExpansionSession,
    ExpansionEnvelopeIdentity,
    ExpansionPointerLedger,
    metered_expand_error_outcome,
)
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.evidence_transaction import (
    rollback_charged_observation,
)
from app.services.reader_record_ask.fence import FenceFn, StaticGenerationFence
from app.services.reader_record_ask.model_view_budget import (
    ModelViewBudgetError,
    ModelViewRenderer,
    ModelVisibleTurnBudget,
    RenderedModelView,
)
from app.services.reader_record_ask.pointer_ledger_owner import (
    get_process_pointer_ledger,
)
from app.services.reader_record_ask.selection_model_view import (
    SelectionModelViewResult,
    SelectionPromptCapability,
    assemble_selection_model_view,
)
from app.services.reader_record_ask.tool_contracts import (
    ToolBudgetExhaustedView,
    is_expand_pointer_shape,
)
from app.services.reader_record_ask.turn_capability_projection import (
    TurnCapabilityProjection,
    build_turn_capability_projection,
    mint_turn_id,
)
from app.services.reader_record_ask.turn_prompt import (
    TurnFramePromptCapability,
    mint_turn_frame_prompt_capability,
    render_handles_listing,
)
from app.services.reader_record_ask.web_evidence_registry import (
    WebEvidenceRegistry,
)
from app.services.reader_record_ask.web_search_contracts import (
    WEB_MAX_CALLS_PER_TURN,
    WEB_MAX_RESULTS_PER_CALL,
    WEB_QUERY_MAX_LEN,
    WEB_SEARCH_PROVIDER_TIMEOUT_SECONDS,
    WEB_SEARCH_TURN_DEADLINE_SECONDS,
    ResolvedWebSearchCapability,
    WebEvidence,
    WebSearchOutcome,
    WebSearchTurnObservation,
    canonicalize_url,
    compute_web_source_fingerprint,
    display_domain_from_canonical_url,
    registrable_domain_from_canonical_url,
)
from app.services.reader_record_ask.web_search_port import (
    WebSearchBackend,
    WebSearchResult,
)

# Default search call limit (mirrors legacy executor).
DEFAULT_MAX_SEARCH_CURRENT_ARTICLE_CALLS = 1

# Max map entry sources from document units (content policy; map budget fits).
_MAX_MAP_ENTRY_SOURCES = 32

# §5.4.2 descriptor source hard cap (rag_ask_only block candidates).
# Provider already caps at 8 per the frozen contract; this is a defensive
# cap that enforces the invariant if the contract is ever violated.
_MAX_DESCRIPTOR_MAP_ENTRY_SOURCES = 8

# G1-b5: default web search call limit. Mirrors the G0/G1 capability
# resolver default (2 as of ASK-WEB-QUALITY-R5). The actual limit comes from the
# resolved capability's ``max_calls`` field; this constant is only used
# when the coordinator is constructed without an explicit
# ``max_web_search_calls`` AND the capability is unavailable (defensive
# fail-soft path).
_DEFAULT_MAX_WEB_SEARCH_CALLS: int = 2

# A port is an adapter boundary, so its diagnostic code is not trusted even
# though production adapters currently use fixed constants.  This finite list
# is deliberately stricter than a shape regex: a query such as
# ``top_secret_query`` could otherwise look like a harmless snake_case code.
# Unknown future adapter codes degrade to a host-owned fallback until they are
# consciously added here.
_SAFE_WEB_SEARCH_DETAIL_CODES = frozenset(
    {
        "ok",
        "empty",
        "call_limit",
        "equivalent_query",
        "insufficient_rewrite",
        "unsafe_query_comparison",
        "deadline_exhausted",
        "provider_timeout",
        "fence_pre",
        "fence_post",
        "capability_or_backend_missing",
        "backend_exception",
        "port_unavailable",
        "port_failed",
        "budget_exhausted",
        "unknown",
        "qwen_completed",
        "qwen_no_canonical_hits",
        "qwen_rate_limit",
        "qwen_service_unavailable",
        "qwen_timeout",
        "qwen_malformed_response",
        "qwen_http_400",
        "qwen_http_422",
        "qwen_http_500",
        "qwen_http_error",
        "qwen_http_transport_error",
        "qwen_unexpected_error",
        "deepseek_completed",
        "deepseek_no_canonical_hits",
        "deepseek_partial_citations_refused",
        "deepseek_citations_ignored",
        "deepseek_rate_limit",
        "deepseek_service_unavailable",
        "deepseek_timeout",
        "deepseek_malformed_response",
        "deepseek_http_400",
        "deepseek_http_422",
        "deepseek_http_500",
        "deepseek_http_error",
        "deepseek_http_transport_error",
        "deepseek_unexpected_error",
    }
)

# The Host does not retain plaintext query comparison state.  It uses a
# per-turn HMAC key over lexical units and 2/3-unit shingles, then compares
# only the resulting opaque digest sets.  The Jaccard threshold is a
# necessary condition for a second provider call; a short edge append/prepend
# is additionally rejected so a date or one small word cannot consume the
# only retry merely because a short query has a small feature set.
_WEB_QUERY_SIGNIFICANT_REWRITE_JACCARD_THRESHOLD = 0.8
_WEB_QUERY_LIGHT_EDGE_DELTA_MAX_CHARS = 8


@dataclass(frozen=True, slots=True)
class _WebQuerySimilaritySignature:
    """Per-turn opaque comparison state; never emitted or persisted.

    Every digest is HMAC-SHA256 under a random per-turn key.  The dataclass
    deliberately has no plaintext query, normalized query, lexical token, or
    shingle field.
    """

    full_digest: bytes
    feature_digests: frozenset[bytes]
    normalized_length: int
    edge_trim_digests: frozenset[bytes]
    edge_unit_trim_digests: frozenset[bytes]


def _safe_web_search_detail_code(value: object, *, fallback: str) -> str:
    """Return only a host-approved diagnostic code for logs/events.

    The fallback is always a literal at the call site.  Do not accept an
    arbitrary string by shape: it could be provider text, a query, or a URL.
    """
    if isinstance(value, str) and value in _SAFE_WEB_SEARCH_DETAIL_CODES:
        return value
    return fallback


class HostBudgetExhausted(Exception):
    """Host-only budget terminal — never ModelRetry, never model-visible text.

    Raised from tool paths when even a minimal safe tool-view cannot be
    charged, and from assemble when request-frame cannot fit. Runtime /
    production_stream map this to ``terminal_reason="budget_exhausted"``.
    """

    def __init__(self, *, account: str, reason: str = "budget_exhausted") -> None:
        self.account = account
        self.reason = reason
        super().__init__(f"host_budget_exhausted account={account} reason={reason}")


@dataclass(frozen=True, slots=True)
class MeteredToolReturn:
    """Already-charged tool return for the agent model surface."""

    text: str
    status: str
    summary: str
    evidence_handle_ids: tuple[str, ...] = ()
    duration_ms: int = 0
    # True when the host must abort the agent (no ToolReturnPart to model).
    host_budget_abort: bool = False
    # ASK-WEB-R4-R1: safe, restricted diagnostic short-code carried from
    # the Coordinator's web search decision tree. ONLY short codes from
    # the frozen allowlist appear here (``ok`` / ``empty`` / ``call_limit``
    # / ``capability_or_backend_missing`` / ``backend_exception`` /
    # ``fence_pre`` / ``fence_post`` / ``budget_exhausted`` / provider
    # detail codes such as ``qwen_*`` / ``deepseek_*``). The field NEVER
    # carries query text, URLs, provider payload, exception strings, or
    # credentials. The public ``completed`` DTO is unchanged — this field
    # is consumed only by internal event projection and safe logs.
    detail_code: str = ""


@dataclass(frozen=True, slots=True)
class TurnAssembly:
    """Committed initial turn assembly ready for agent.run."""

    turn_id: str
    user_prompt: str
    system_instructions: str
    turn_frame: TurnFramePromptCapability
    projection: TurnCapabilityProjection
    baseline_context: BaselineAgentContext
    confirmed_article_scopes: frozenset[ArticleScope]
    selection_result: SelectionModelViewResult
    baseline_result: BaselineModelViewResult
    map_result: ArticleMapResult
    available_handle_ids: tuple[str, ...]


@dataclass
class _OuterTxnReceipt:
    """Host-only rollback receipt for one initial-assembly attempt."""

    selection_result: SelectionModelViewResult | None = None
    selection_observation: ServerEvidenceObservation | None = None
    selection_charge: int = 0
    baseline_result: BaselineModelViewResult | None = None
    map_result: ArticleMapResult | None = None
    map_charge: int = 0
    request_frame_charge: int = 0
    # Server-only map cursor issue markers from this assembly (parallel to
    # issued cursors). Outer rollback uses
    # ``ledger.rollback_transition_by_marker`` only — never raw token pop.
    map_issue_markers: tuple[str, ...] = ()


class TurnCoordinator:
    """Single turn state center for production Ask model-views."""

    def __init__(
        self,
        *,
        envelope: ReadingRecordAskContextEnvelope,
        document_access: DocumentAccess,
        user_message: str,
        system_instructions: str,
        article_rag: ArticleRagSearchPort | None = None,
        fence: FenceFn | None = None,
        evidence_registry: EvidenceRegistry | None = None,
        pointer_ledger: ExpansionPointerLedger | None = None,
        budget: ModelVisibleTurnBudget | None = None,
        renderer: ModelViewRenderer | None = None,
        max_search_current_article_calls: int = (DEFAULT_MAX_SEARCH_CURRENT_ARTICLE_CALLS),
        product_search_enabled: bool = True,
        map_source_material_provider: MapSourceMaterialProvider | None = None,
        web_search_capability: ResolvedWebSearchCapability | None = None,
        web_search_backend: WebSearchBackend | None = None,
        web_evidence_registry: WebEvidenceRegistry | None = None,
        max_web_search_calls: int | None = None,
        web_search_deadline_seconds: float = WEB_SEARCH_TURN_DEADLINE_SECONDS,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> None:
        if not isinstance(user_message, str):
            raise TypeError("user_message must be str")
        self.envelope = envelope
        self.document_access = document_access
        self.user_message = user_message  # exact; never strip
        self.system_instructions = system_instructions
        self.article_rag = article_rag
        self.fence: FenceFn = fence or StaticGenerationFence(
            live_generation=envelope.record_generation
        )
        if evidence_registry is not None:
            if evidence_registry.envelope_fingerprint != envelope.envelope_fingerprint:
                raise ValueError(
                    "evidence_registry envelope_fingerprint does not match the turn envelope"
                )
            self.registry = evidence_registry
        else:
            self.registry = EvidenceRegistry(envelope.envelope_fingerprint)
        # Never default to a fresh ExpansionPointerLedger() — production
        # and multi-turn tests must share recognition via an explicit owner.
        self.ledger = pointer_ledger if pointer_ledger is not None else get_process_pointer_ledger()
        self.budget = budget if budget is not None else ModelVisibleTurnBudget()
        self.renderer = renderer if renderer is not None else ModelViewRenderer()
        self.max_search_current_article_calls = max_search_current_article_calls
        self.product_search_enabled = product_search_enabled
        # M3 C2: server-only map-source material provider (§3.4 preflight).
        # None = no provider configured → coordinator falls back to the
        # existing unit-window map (C2 skeleton; production wiring is a
        # separate task). When set, load() runs in preflight before the
        # outer transaction.
        self._map_source_material_provider = map_source_material_provider

        # G1-b5: web search capability + port + registry. The capability
        # is the server-owned execution truth — when ``None`` the
        # ``search_web`` tool must NOT be mounted (handled by the agent
        # registration seam). The backend port is provider-neutral;
        # ``None`` means fail-soft even when ``enabled_for_turn=True``
        # (defensive — the tool returns ``unavailable``). The registry
        # is bound to the same envelope fingerprint as the article
        # evidence registry.
        self.web_search_capability = web_search_capability
        self.web_search_backend = web_search_backend
        if web_evidence_registry is not None:
            if web_evidence_registry.envelope_fingerprint != envelope.envelope_fingerprint:
                raise ValueError(
                    "web_evidence_registry envelope_fingerprint does not match the turn envelope"
                )
            self.web_evidence_registry = web_evidence_registry
        else:
            self.web_evidence_registry = WebEvidenceRegistry(
                envelope.envelope_fingerprint
            )
        # Effective max-calls: explicit override → capability → default.
        if max_web_search_calls is not None:
            self.max_web_search_calls = max(
                1, min(int(max_web_search_calls), WEB_MAX_CALLS_PER_TURN)
            )
        elif web_search_capability is not None:
            self.max_web_search_calls = max(
                1, min(web_search_capability.max_calls, WEB_MAX_CALLS_PER_TURN)
            )
        else:
            self.max_web_search_calls = _DEFAULT_MAX_WEB_SEARCH_CALLS

        self.turn_id: str = mint_turn_id()
        self.search_current_article_calls: int = 0
        # ``web_search_calls`` counts actual provider invocations.  Host-only
        # rejections (for example equivalent normalized re-queries) are kept
        # separate in ``web_search_tool_requests`` so telemetry never claims
        # a provider call that did not happen.
        self.web_search_calls: int = 0
        self.web_search_tool_requests: int = 0
        self._web_search_lock = asyncio.Lock()
        self._web_search_clock = monotonic_clock or time.perf_counter
        if not isinstance(web_search_deadline_seconds, int | float):
            raise TypeError("web_search_deadline_seconds must be numeric")
        if web_search_deadline_seconds <= 0:
            raise ValueError("web_search_deadline_seconds must be positive")
        self.web_search_deadline_seconds = float(web_search_deadline_seconds)
        self._web_search_started_at: float | None = None
        self._web_search_finished_at: float | None = None
        # HMAC, not plaintext: this comparison state exists only for this
        # coordinator instance and is never emitted, logged, persisted, or
        # included in a public DTO/SSE payload. It contains opaque token /
        # shingle feature digests only, never a query or normalized query.
        self._web_search_query_digest_key = _secrets.token_bytes(32)
        self._first_no_results_query_signature: _WebQuerySimilaritySignature | None = (
            None
        )
        self._web_search_deadline_exhausted = False
        self._web_search_second_query_changed: bool | None = None
        self._web_search_final_detail_code: str | None = None
        # G1-b5: last translated public outcome for ``web_search_outcome``
        # property / finalizer. ``None`` until the first call sets it.
        self._last_web_search_outcome: WebSearchOutcome | None = None
        self._last_web_search_attempt_outcome: WebSearchOutcome | None = None
        self._selection_session: EvidenceExpansionSession | None = None
        self._map_expander: ArticleMapExpander | None = None
        self._assembled = False
        self._assembly: TurnAssembly | None = None

    # ------------------------------------------------------------------
    # Public: assemble
    # ------------------------------------------------------------------

    async def assemble_turn(self) -> TurnAssembly:
        """Plan (no mutation) then commit outer transaction.

        Request-frame excess fail-closes **before** any durable mutation
        that would leave residual registry/ledger/budget state (and before
        agent.run). The full original user question is never truncated.
        """
        if self._assembled and self._assembly is not None:
            return self._assembly

        # ---- pure I/O + planning (no budget/registry/ledger mutation) ----
        scope = await self._load_document_scope()
        if scope is None:
            # Typed fail-closed baseline-unavailable path is constructed
            # without mutation below.
            return self._fail_closed_assembly(
                baseline_status="document_scope_unavailable",
                reason="baseline document scope could not be loaded",
            )

        # Snapshot budget spends (must stay zero before commit).
        if self.budget.total_spent() != 0:
            raise RuntimeError("turn coordinator requires a fresh budget before assemble")
        if len(self.registry) != 0:
            raise RuntimeError("turn coordinator requires an empty registry before assemble")

        # ---- M3 C2: map-source material preflight (§3.4 — before outer txn).
        # Pure I/O + planning: loads server-owned heading + descriptor
        # candidates. Fence failure (§5.1 6(b)) returns a material with
        # material_fence_ok=False; _map_sources_from_scope then falls back
        # to the unit-window map. No cursor / ledger / budget mutation
        # here — those happen inside _commit_assembly's single
        # assemble_article_map() call.
        material = await self._load_map_source_material()

        # ---- outer commit (selection → baseline → map → request_frame) ----
        receipt = _OuterTxnReceipt()
        try:
            assembly = self._commit_assembly(scope=scope, material=material, receipt=receipt)
        except ModelViewBudgetError as exc:
            self._rollback_outer(receipt)
            raise HostBudgetExhausted(
                account=exc.denial.account,
                reason=exc.denial.reason,
            ) from None
        except Exception:
            self._rollback_outer(receipt)
            raise

        self._assembled = True
        self._assembly = assembly
        return assembly

    def _fail_closed_assembly(
        self,
        *,
        baseline_status: str,
        reason: str,
    ) -> TurnAssembly:
        """Return a non-runnable assembly when document/baseline is unavailable.

        No registry/budget/ledger mutation. Callers must not invoke agent.run
        when ``baseline_context.is_injected`` is False.
        """
        from app.services.reader_record_ask.turn_capability_projection import (
            SelectionCapabilityView,
        )

        baseline_ctx = BaselineAgentContext(
            baseline_status=baseline_status,  # type: ignore[arg-type]
            baseline_failure_reason=reason,
        )
        selection_absent = SelectionModelViewResult(
            status="absent",
            selection=SelectionCapabilityView(present=False),
            visible_prefix="",
            full_char_count=0,
            continuation_start=0,
        )
        map_absent = ArticleMapResult(status="absent")
        baseline_mv = BaselineModelViewResult(
            status="document_scope_unavailable"
            if baseline_status == "document_scope_unavailable"
            else "no_units",
            baseline_failure_reason=reason,
        )
        projection = build_turn_capability_projection(
            article_rag_port=self.article_rag,
            stable_document_id=self.envelope.stable_document_id,
            product_search_enabled=self.product_search_enabled,
            web_search_allowed=(
                self.envelope.capabilities.web_search_mode == "allowed"
            ),
            baseline_injected=False,
            baseline_complete=False,
            can_read_range=False,
            has_visible_range=False,
            turn_id=self.turn_id,
        )
        # Diagnostic-only frame: unbranded; agent must not run.
        empty_view = self.renderer.render_plain("")
        turn_frame = TurnFramePromptCapability(
            system_instructions=self.system_instructions,
            user_prompt=self.user_message,
            request_frame_view=empty_view,
            request_frame_charge_cost=0,
        )
        assembly = TurnAssembly(
            turn_id=self.turn_id,
            user_prompt=self.user_message,
            system_instructions=self.system_instructions,
            turn_frame=turn_frame,
            projection=projection,
            baseline_context=baseline_ctx,
            confirmed_article_scopes=frozenset(),
            selection_result=selection_absent,
            baseline_result=baseline_mv,
            map_result=map_absent,
            available_handle_ids=(),
        )
        self._assembled = True
        self._assembly = assembly
        return assembly

    def _commit_assembly(
        self,
        *,
        scope: DocumentScopeSnapshot,
        material: MapSourceMaterial | None,
        receipt: _OuterTxnReceipt,
    ) -> TurnAssembly:
        envelope = self.envelope
        fp = envelope.envelope_fingerprint

        # 1) Selection inject (may be absent / budget_denied / injected).
        selected_text = (
            envelope.initial_anchor.selected_text if envelope.initial_anchor is not None else None
        )
        selection_kwargs: dict[str, Any] = {}
        if envelope.initial_anchor is not None:
            anchor = envelope.initial_anchor
            selection_kwargs = {
                "unit_id": anchor.unit_id,
                "anchor_segment_id": anchor.anchor_segment_id,
                "text_hash": anchor.text_hash,
                "offset_unit": anchor.offset_unit,
                "start_offset": anchor.start_offset,
                "end_offset": anchor.end_offset,
            }
        selection = assemble_selection_model_view(
            canonical_selected_text=selected_text,
            envelope_fingerprint=fp,
            budget=self.budget,
            registry=self.registry,
            renderer=self.renderer,
            **selection_kwargs,
        )
        receipt.selection_result = selection
        if selection.status == "injected" and selection.handle_ref is not None:
            obs = self.registry.get(selection.handle_ref.handle_id)
            receipt.selection_observation = obs
            receipt.selection_charge = (
                selection.rendered_untrusted_block.char_cost
                if selection.rendered_untrusted_block is not None
                else 0
            )

        # 2) Baseline inject (renderer-only).
        baseline = assemble_baseline_model_view(
            units=scope.units,
            envelope_fingerprint=fp,
            budget=self.budget,
            registry=self.registry,
            renderer=self.renderer,
        )
        receipt.baseline_result = baseline
        if not baseline.is_injected:
            # Fail-closed: rollback selection if any, no agent.
            self._rollback_outer(receipt)
            return self._fail_closed_assembly(
                baseline_status=(
                    "document_scope_unavailable"
                    if baseline.status == "document_scope_unavailable"
                    else "no_units"
                ),
                reason=baseline.baseline_failure_reason or "baseline unavailable",
            )

        # 3) Map assemble (optional; budget_denied → absent-like, no raise).
        # M3 C2: material (heading + descriptor candidates) is merged into
        # the SAME assemble_article_map() call — shared fit → charge →
        # issue cursor → rollback transaction (§5.3 19). Descriptor sources
        # are candidates (§3.5.1.3 / §5.1 25): cost-fit may silently drop
        # them; dropped candidates produce no cursor / stale_evidence /
        # invalid_cursor.
        map_sources = self._map_sources_from_scope(scope, material=material)
        identity = ExpansionEnvelopeIdentity(
            turn_id=self.turn_id,
            envelope_fingerprint=fp,
            record_generation=envelope.record_generation,
            base_id=envelope.base_id,
            reading_record_id=envelope.reading_record_id,
        )
        map_result = assemble_article_map(
            entry_sources=map_sources,
            envelope_identity=identity,
            registry=self.registry,
            budget=self.budget,
            renderer=self.renderer,
            pointer_ledger=self.ledger,
        )
        receipt.map_result = map_result
        if map_result.is_ok and map_result.rendered_block is not None:
            receipt.map_charge = map_result.rendered_block.char_cost
            receipt.map_issue_markers = map_result.issue_markers
            self._map_expander = map_result.expander

        # 4) Projection (turn_id server-minted; same value everywhere).
        sel_view = selection.selection
        map_present = map_result.is_ok and map_result.entry_count > 0
        can_expand = bool((selection.status == "injected" and sel_view.expandable) or map_present)
        # budget_denied selection: can_read_range=False, has_visible_range=False
        if selection.status == "budget_denied":
            can_read_range = False
            has_visible_range = False
        else:
            can_read_range = can_expand
            has_visible_range = bool(sel_view.present and sel_view.visible_char_count > 0)

        projection = build_turn_capability_projection(
            article_rag_port=self.article_rag,
            stable_document_id=envelope.stable_document_id,
            product_search_enabled=self.product_search_enabled,
            web_search_allowed=(
                envelope.capabilities.web_search_mode == "allowed"
            ),
            baseline_injected=True,
            baseline_complete=baseline.is_complete,
            can_read_range=can_read_range,
            has_visible_range=has_visible_range,
            selection_present=sel_view.present,
            selection_handle_id=sel_view.handle_id,
            selection_expandable=sel_view.expandable,
            selection_visible_char_count=sel_view.visible_char_count,
            selection_full_char_count=sel_view.full_char_count,
            article_map_present=map_present,
            article_map_entry_count=map_result.entry_count,
            article_map_truncated=map_result.truncated,
            turn_id=self.turn_id,
        )
        projection_json = json.dumps(
            projection.to_model_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        confirmed_article_scopes: set[ArticleScope] = {"evidence_bounded"}
        if selection.status == "injected" and sel_view.present:
            confirmed_article_scopes.add("selection_bounded")
        if baseline.is_complete:
            confirmed_article_scopes.update(
                {"article_overview", "full_article"}
            )

        # 6) Handles listing (selection + baseline seeds).
        handle_ids: list[str] = []
        if sel_view.handle_id:
            handle_ids.append(sel_view.handle_id)
        handle_ids.extend(baseline.available_seed_handle_ids)
        handles_block = render_handles_listing(handle_ids)

        # 7) Request-frame charge (full question; fail closed on excess).
        selection_prompt: SelectionPromptCapability | None = (
            selection.prompt_capability if selection.status == "injected" else None
        )
        baseline_prompt: BaselinePromptCapability | None = baseline.prompt_capability
        map_prompt: ArticleMapPromptCapability | None = (
            map_result.prompt_capability if map_result.is_ok else None
        )

        try:
            turn_frame = mint_turn_frame_prompt_capability(
                system_instructions=self.system_instructions,
                projection_json=projection_json,
                handles_block=handles_block,
                baseline_is_complete=baseline.is_complete,
                user_question=self.user_message,
                budget=self.budget,
                renderer=self.renderer,
                selection_prompt=selection_prompt,
                baseline_prompt=baseline_prompt,
                map_prompt=map_prompt,
                charge=True,
            )
        except ModelViewBudgetError:
            # Leave residual cleanup to caller via receipt.
            raise

        receipt.request_frame_charge = turn_frame.request_frame_charge_cost

        # 8) Selection expansion session (only when expandable injected).
        if selection.status == "injected" and selection.selection.expandable and selected_text:
            self._selection_session = EvidenceExpansionSession(
                canonical_selected_text=selected_text,
                selection_result=selection,
                envelope_identity=identity,
                registry=self.registry,
                budget=self.budget,
                renderer=self.renderer,
                pointer_ledger=self.ledger,
            )

        assembly = TurnAssembly(
            turn_id=self.turn_id,
            user_prompt=turn_frame.user_prompt,
            system_instructions=self.system_instructions,
            turn_frame=turn_frame,
            projection=projection,
            baseline_context=baseline.to_baseline_agent_context(),
            confirmed_article_scopes=frozenset(confirmed_article_scopes),
            selection_result=selection,
            baseline_result=baseline,
            map_result=map_result,
            available_handle_ids=tuple(handle_ids),
        )
        return assembly

    def _rollback_outer(self, receipt: _OuterTxnReceipt) -> None:
        """Reverse-order cleanup of this transaction's writes only.

        Map cursors are revoked **only** via marker-scoped
        :meth:`ExpansionPointerLedger.rollback_transition_by_marker` so
        foreign issue markers under the same token are never deleted.
        Compensation continues through every step even when a ledger
        rollback is incomplete; a single stable fail-closed code is raised
        at the end when any step is unproven. Never touches private
        ``_records``.
        """
        # Stable failure codes only — no body / token / marker / repr.
        unproven: list[str] = []

        # 1) Request-frame refund (if charged).
        if receipt.request_frame_charge > 0:
            try:
                spent = self.budget.spent("request_frame")
                if spent >= receipt.request_frame_charge:
                    self.budget._refund_chars(  # noqa: SLF001
                        "request_frame", receipt.request_frame_charge
                    )
                elif spent > 0:
                    unproven.append("request_frame_refund")
            except Exception:  # noqa: BLE001
                unproven.append("request_frame_refund")

        # 2) Map: marker-scoped ledger revoke, then always attempt refund.
        if receipt.map_issue_markers or receipt.map_charge > 0:
            ledger_complete = True
            for marker in receipt.map_issue_markers:
                try:
                    status = self.ledger.rollback_transition_by_marker(marker)
                    if status != "rolled_back":
                        ledger_complete = False
                except Exception:  # noqa: BLE001
                    ledger_complete = False
            if not ledger_complete:
                unproven.append("map_ledger")
            if receipt.map_charge > 0:
                try:
                    if self.budget.spent("map") >= receipt.map_charge:
                        self.budget._refund_chars(  # noqa: SLF001
                            "map", receipt.map_charge
                        )
                    elif self.budget.spent("map") > 0:
                        unproven.append("map_refund")
                except Exception:  # noqa: BLE001
                    unproven.append("map_refund")

        # 3) Baseline.
        if receipt.baseline_result is not None and receipt.baseline_result.status == "injected":
            try:
                rollback_baseline_inject(
                    budget=self.budget,
                    registry=self.registry,
                    result=receipt.baseline_result,
                )
            except Exception:  # noqa: BLE001
                unproven.append("baseline_refund")

        # 4) Selection.
        if (
            receipt.selection_result is not None
            and receipt.selection_result.status == "injected"
            and receipt.selection_observation is not None
            and receipt.selection_charge > 0
        ):
            try:
                rollback_charged_observation(
                    budget=self.budget,
                    account="selection",
                    charge_cost=receipt.selection_charge,
                    registry=self.registry,
                    observation=receipt.selection_observation,
                    failure_domain="selection_inject",
                )
            except Exception:  # noqa: BLE001
                unproven.append("selection_refund")

        if unproven:
            # Prefer the first unproven domain; never embed markers/tokens.
            raise RuntimeError(f"turn_assembly_rollback_failed code={unproven[0]}") from None

    async def _load_document_scope(self) -> DocumentScopeSnapshot | None:
        try:
            scope = await self.document_access.load_document_scope(
                user_id=self.envelope.user_id,
                reading_record_id=self.envelope.reading_record_id,
                base_id=self.envelope.base_id,
                record_generation=self.envelope.record_generation,
            )
        except Exception:  # noqa: BLE001
            return None
        mismatch = scope_identity_mismatch_reason(scope, self.envelope)
        if mismatch is not None:
            return None
        return scope

    async def _load_map_source_material(self) -> MapSourceMaterial | None:
        """§3.4 preflight — load map-source material before outer transaction.

        Returns ``None`` when no provider is configured (C2 skeleton —
        production wiring is a separate task; coordinator falls back to
        the unit-window map). When the provider is configured, returns
        its :class:`MapSourceMaterial` — which may carry
        ``material_fence_ok=False`` (§5.1 6(b)); the caller
        (``_map_sources_from_scope``) handles the fallback.

        ``include_rag_ask_only`` is fixed to ``False`` in M3 stage C
        (B3 heading baseline + wiring skeleton only; opt-in is a later
        stage). Heading enrichments are populated regardless of opt-in
        per §3.5.2 B3 heading-enabled baseline.

        No cursor / ledger / budget mutation here — pure preflight I/O.
        """
        if self._map_source_material_provider is None:
            return None
        return await self._map_source_material_provider.load(
            envelope=self.envelope,
            turn_id=self.turn_id,
            include_rag_ask_only=False,
        )

    @staticmethod
    def _map_sources_from_scope(
        scope: DocumentScopeSnapshot,
        *,
        material: MapSourceMaterial | None = None,
    ) -> list[ArticleMapEntrySource]:
        """Build map entry sources from document scope + map-source material.

        M3 C2 — implements §5.4.1 (deterministic merge order), §5.4.2
        (hard caps: 32 body + 8 descriptor = 40 max), §5.4.3 (overflow
        drop, no cross-kind substitution), §5.2 13 (heading only onto
        same unit source — no standalone heading entry), §5.1 6(b)
        (material fence failure → unit-window fallback).

        Order (§5.4.1 sort key):
        1. Body unit sources (rank=0) — sorted by ``order_index``,
           heading injected from ``material.heading_enrichments`` when
           ``unit_id`` matches. Capped at 32 (§5.4.2).
        2. Descriptor sources (rank=1) — appended after body sources.
           Provider already sorts + caps at 8 (frozen contract); a
           defensive ``[:8]`` cap enforces the invariant.

        The combined list feeds a SINGLE ``assemble_article_map()``
        call in ``_commit_assembly`` — shared transaction (§5.3 19).
        """
        # §5.1 6(b): material None (no provider) or fence failure →
        # unit-window fallback (no heading, no descriptor). This is the
        # pre-C2 behavior, preserving the B3-heading-enabled-baseline-
        # before fallback shape per §5.1 6(b).
        if material is None or not material.material_fence_ok:
            units = sorted(scope.units, key=lambda u: u.order_index)
            sources: list[ArticleMapEntrySource] = []
            for unit in units:
                if not unit.text:
                    continue
                sources.append(ArticleMapEntrySource(window_text=unit.text))
                if len(sources) >= _MAX_MAP_ENTRY_SOURCES:
                    break
            return sources

        # §5.2 13: heading only onto same unit source (no standalone
        # entry). Provider side already dedups — one heading per unit_id.
        heading_by_unit_id: dict[str, str] = {
            enrichment.unit_id: enrichment.heading for enrichment in material.heading_enrichments
        }

        # §5.4.1 rank=0 (body) + §5.4.2 quota 32 + §5.4.3 overflow drop
        # (drop highest order_index units; no descriptor substitution).
        units = sorted(scope.units, key=lambda u: u.order_index)
        body_sources: list[ArticleMapEntrySource] = []
        for unit in units:
            if not unit.text:
                continue
            heading = heading_by_unit_id.get(unit.unit_id)
            body_sources.append(ArticleMapEntrySource(heading=heading, window_text=unit.text))
            if len(body_sources) >= _MAX_MAP_ENTRY_SOURCES:
                break

        # §5.4.1 rank=1 (descriptor) — provider already sorts by §5.4.1
        # key and caps at 8 (§5.4.2 frozen contract). Defensive cap
        # enforces the invariant if the contract is ever violated.
        descriptor_sources = list(material.descriptor_sources[:_MAX_DESCRIPTOR_MAP_ENTRY_SOURCES])

        # §5.4.1: body (rank=0) before descriptor (rank=1).
        return body_sources + descriptor_sources

    # ------------------------------------------------------------------
    # Public: tools
    # ------------------------------------------------------------------

    def expand_evidence(self, pointer: str) -> MeteredToolReturn:
        """Expand selection- or map-scope evidence by opaque pointer."""
        started = time.perf_counter()
        if not isinstance(pointer, str):
            pointer = ""

        outcome = None
        record = self.ledger.lookup(pointer) if is_expand_pointer_shape(pointer) else None
        if record is not None and record.binding.scope_kind == "map":
            if self._map_expander is not None:
                outcome = self._map_expander.expand(pointer=pointer)
            else:
                outcome = metered_expand_error_outcome(
                    renderer=self.renderer,
                    budget=self.budget,
                    status="stale_evidence",
                    summary=(
                        "Expansion pointer does not match this turn's "
                        "verified context. No text was added."
                    ),
                )
        elif self._selection_session is not None:
            outcome = self._selection_session.expand(pointer=pointer)
        elif record is not None:
            # Known pointer (e.g. prior-turn selection) with no session.
            outcome = metered_expand_error_outcome(
                renderer=self.renderer,
                budget=self.budget,
                status="stale_evidence",
                summary=(
                    "Expansion pointer does not match this turn's "
                    "verified context. No text was added."
                ),
            )
        else:
            outcome = metered_expand_error_outcome(
                renderer=self.renderer,
                budget=self.budget,
                status="invalid_cursor",
                summary=(
                    "Unknown, malformed, or already-used expansion pointer. No text was added."
                ),
            )

        duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        if outcome.kind == "budget_exhausted" or not outcome.model_visible:
            return self._tool_budget_exhausted(started=started)
        assert outcome.rendered_tool_view is not None
        handle_ids: tuple[str, ...] = ()
        if outcome.evidence_handle_id:
            handle_ids = (outcome.evidence_handle_id,)
        return MeteredToolReturn(
            text=outcome.rendered_tool_view.text,
            status=outcome.kind,
            summary=self._tool_view_summary(outcome.rendered_tool_view),
            evidence_handle_ids=handle_ids,
            duration_ms=duration_ms,
        )

    async def search_current_article(
        self,
        query: str,
        limit: int | None = None,
    ) -> MeteredToolReturn:
        """RAG search via ArticleRagSearchPort + assemble_rag_model_view."""
        started = time.perf_counter()
        effective_limit = 5 if limit is None else max(1, min(int(limit), 10))

        # Call-limit (consume even on fence failure after the call is made).
        if self.search_current_article_calls >= self.max_search_current_article_calls:
            return await self._rag_safe_unavailable(
                started=started,
                detail="call_limit",
            )

        # Pre-generation fence.
        fence_result = await self._run_fence()
        if not fence_result.ok:
            return await self._rag_safe_unavailable(started=started, detail="fence_pre")

        # Port None / missing stable document → no I/O.
        if self.article_rag is None or self.envelope.stable_document_id is None:
            self.search_current_article_calls += 1
            return await self._rag_safe_unavailable(
                started=started, detail="port_or_document_missing"
            )

        outcome = await self.article_rag.search_current_article(
            user_id=self.envelope.user_id,
            reading_record_id=self.envelope.reading_record_id,
            base_id=self.envelope.base_id,
            record_generation=self.envelope.record_generation,
            stable_document_id=self.envelope.stable_document_id,
            query=query if isinstance(query, str) else "",
            limit=effective_limit,
        )
        self.search_current_article_calls += 1

        # Post-generation fence.
        fence_after = await self._run_fence()
        if not fence_after.ok:
            # Fail-soft: safe unavailable view (no hit registration).
            from app.services.reader_record_ask.article_rag_port import (
                ArticleRagSearchOutcome,
            )

            outcome = ArticleRagSearchOutcome(
                status="unavailable",
                summary="Article search is unavailable for this article.",
                detail_code="fence_post",
            )

        rag_identity = RagEnvelopeIdentity(
            envelope_fingerprint=self.envelope.envelope_fingerprint,
            reading_record_id=self.envelope.reading_record_id,
            base_id=self.envelope.base_id,
            record_generation=self.envelope.record_generation,
            stable_document_id=self.envelope.stable_document_id,
        )
        result = assemble_rag_model_view(
            outcome=outcome,
            envelope_identity=rag_identity,
            registry=self.registry,
            budget=self.budget,
            renderer=self.renderer,
        )
        duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        if result.kind == "budget_denied" or not result.model_visible:
            return self._tool_budget_exhausted(started=started)
        assert result.rendered_tool_view is not None
        handle_ids = tuple(ref.handle_id for ref in result.evidence_handles)
        return MeteredToolReturn(
            text=result.rendered_tool_view.text,
            status=result.kind,
            summary=self._tool_view_summary(result.rendered_tool_view),
            evidence_handle_ids=handle_ids,
            duration_ms=duration_ms,
        )

    # ------------------------------------------------------------------
    # Public: tools — web search (G1-b5)
    # ------------------------------------------------------------------

    async def search_web(
        self,
        query: str,
        max_results: int | None = None,
    ) -> MeteredToolReturn:
        """Execute the frozen, serial Web Search lifecycle.

        The lock is intentional: model tool calls may be scheduled together,
        but provider attempt two is legal only after attempt one has completed
        with ``no_results``. The host never stores query text; it keeps only
        an in-memory keyed HMAC signature set for the rewrite gate.
        """
        async with self._web_search_lock:
            tool_requests_before = self.web_search_tool_requests
            try:
                return await self._search_web_locked(query, max_results)
            finally:
                # ``total_duration_ms`` is Web Search lifecycle time, not
                # later model composition/validation latency. A rejected
                # late call does not extend it because it did not start a
                # new allowed tool request.
                if self.web_search_tool_requests > tool_requests_before:
                    self._web_search_finished_at = self._web_search_clock()

    async def _search_web_locked(
        self,
        query: str,
        max_results: int | None,
    ) -> MeteredToolReturn:
        started = self._web_search_clock()
        if self._web_search_started_at is None:
            self._web_search_started_at = started

        # A late/malicious invocation cannot make another provider call or
        # downgrade an earlier successful outcome.
        if not self.can_offer_web_search:
            detail = (
                "deadline_exhausted"
                if self._web_search_deadline_exhausted
                and self._last_web_search_outcome == "no_results"
                else "call_limit"
            )
            self._last_web_search_attempt_outcome = "unavailable"
            if self._last_web_search_outcome is None:
                self._record_web_search_outcome(
                    "unavailable", detail_code=detail
                )
            return await self._web_safe_unavailable(
                started=started,
                detail=detail,
            )

        self.web_search_tool_requests += 1
        if self._remaining_web_search_deadline_seconds() <= 0:
            self._web_search_deadline_exhausted = True
            if self._last_web_search_outcome == "no_results":
                # A narrow race can occur after tool preparation. Keep the
                # first real no-results result as the turn truth; no second
                # provider call happened, but terminal telemetry still records
                # why the retry was not available.
                self._web_search_final_detail_code = "deadline_exhausted"
                self._last_web_search_attempt_outcome = "unavailable"
            else:
                self._record_web_search_outcome(
                    "timeout", detail_code="deadline_exhausted"
                )
            return await self._web_safe_unavailable(
                started=started,
                detail="deadline_exhausted",
            )

        # Clamp query before any port call. The original query remains local
        # to this function and never enters logs, events, DTOs, or storage.
        if not isinstance(query, str):
            query = ""
        clamped_query = query[:WEB_QUERY_MAX_LEN] if query else ""

        # The only possible second tool request follows the first real
        # no-results provider attempt. Block exact, near-duplicate, and
        # unsafe-to-compare forms locally without another provider call.
        if self._last_web_search_outcome == "no_results":
            rewrite_decision = self._web_query_reformulation_decision(
                clamped_query
            )
            changed = rewrite_decision == "changed"
            self._web_search_second_query_changed = changed
            if not changed:
                self._record_web_search_outcome(
                    "no_results", detail_code=rewrite_decision
                )
                return await self._web_emit_empty_view(
                    started=started,
                    detail=rewrite_decision,
                )

        capability_max_results = WEB_MAX_RESULTS_PER_CALL
        if self.web_search_capability is not None:
            capability_max_results = max(
                1,
                min(
                    self.web_search_capability.max_results_per_call,
                    WEB_MAX_RESULTS_PER_CALL,
                ),
            )
        if max_results is None:
            effective_max_results = capability_max_results
        else:
            try:
                requested_max_results = int(max_results)
            except (TypeError, ValueError):
                requested_max_results = capability_max_results
            effective_max_results = max(
                1, min(requested_max_results, capability_max_results)
            )

        # Pre-generation fence is a host refusal, not a provider retry.
        fence_result = await self._run_fence()
        if not fence_result.ok:
            self._record_web_search_outcome("unavailable", detail_code="fence_pre")
            return await self._web_safe_unavailable(
                started=started, detail="fence_pre"
            )

        # Capability/backend absence is terminal for this turn and performs
        # no I/O. It is not counted as a provider attempt.
        if (
            self.web_search_capability is None
            or not self.web_search_capability.enabled_for_turn
            or self.web_search_backend is None
        ):
            self._record_web_search_outcome(
                "unavailable", detail_code="capability_or_backend_missing"
            )
            return await self._web_safe_unavailable(
                started=started, detail="capability_or_backend_missing"
            )

        remaining_seconds = self._remaining_web_search_deadline_seconds()
        if remaining_seconds <= 0:
            self._web_search_deadline_exhausted = True
            self._record_web_search_outcome(
                "timeout", detail_code="deadline_exhausted"
            )
            return await self._web_safe_unavailable(
                started=started, detail="deadline_exhausted"
            )

        provider_timeout = min(
            WEB_SEARCH_PROVIDER_TIMEOUT_SECONDS,
            remaining_seconds,
        )
        self.web_search_calls += 1
        try:
            outcome = await asyncio.wait_for(
                self.web_search_backend.search_web(
                    query=clamped_query,
                    max_results=effective_max_results,
                ),
                timeout=provider_timeout,
            )
        except TimeoutError:
            deadline_exhausted = (
                remaining_seconds <= WEB_SEARCH_PROVIDER_TIMEOUT_SECONDS
            )
            detail_code = (
                "deadline_exhausted" if deadline_exhausted else "provider_timeout"
            )
            self._web_search_deadline_exhausted = (
                self._web_search_deadline_exhausted or deadline_exhausted
            )
            self._record_web_search_outcome("timeout", detail_code=detail_code)
            return await self._web_safe_unavailable(
                started=started, detail=detail_code
            )
        except Exception:
            # Provider raised — fail soft; host and provider retry budgets are
            # intentionally separate, and this path never retries the port.
            self._record_web_search_outcome(
                "failed", detail_code="backend_exception"
            )
            return await self._web_safe_unavailable(
                started=started, detail="backend_exception"
            )

        # A provider that returned just as the global deadline elapsed cannot
        # surface a partial success; fail soft and retire the tool instead.
        if self._remaining_web_search_deadline_seconds() <= 0:
            self._web_search_deadline_exhausted = True
            self._record_web_search_outcome(
                "timeout", detail_code="deadline_exhausted"
            )
            return await self._web_safe_unavailable(
                started=started, detail="deadline_exhausted"
            )

        # Post-generation fence.
        fence_after = await self._run_fence()
        if not fence_after.ok:
            self._record_web_search_outcome("unavailable", detail_code="fence_post")
            return await self._web_safe_unavailable(
                started=started, detail="fence_post"
            )

        metered = await self._register_web_search_outcome(
            outcome=outcome,
            started=started,
            max_results=effective_max_results,
        )
        if (
            metered.status == "empty"
            and self.web_search_calls == 1
            and self._last_web_search_outcome == "no_results"
        ):
            self._first_no_results_query_signature = self._web_query_signature(
                clamped_query
            )
        return metered

    async def _register_web_search_outcome(
        self,
        *,
        outcome: WebSearchResult,
        started: float,
        max_results: int,
    ) -> MeteredToolReturn:
        """Translate a port outcome into a metered tool view + registry.

        Host-side invariants enforced here:

        - Every URL is re-canonicalized via :func:`canonicalize_url`
          before :class:`WebEvidence` construction. A URL that fails
          canonicalization is dropped (the hit is not registered).
        - :func:`compute_web_source_fingerprint` is recomputed from the
          canonical URL + a server-recorded ``retrieved_at`` — never
          from provider-supplied text. The fingerprint is verified by
          the :class:`WebEvidence` model validator.
        - ``internal_handle_id`` is server-minted (``evh_<32 hex>``)
          and never derived from provider text.
        """
        from app.services.reader_record_ask.tool_contracts import (
            SearchWebToolView,
        )

        # Map port outcome to public outcome + tool status.
        if outcome.status == "unavailable":
            detail = _safe_web_search_detail_code(
                outcome.detail_code,
                fallback="port_unavailable",
            )
            self._record_web_search_outcome(
                "unavailable",
                detail_code=detail,
            )
            return await self._web_safe_unavailable(
                started=started,
                detail=detail,
            )
        if outcome.status == "failed":
            detail = _safe_web_search_detail_code(
                outcome.detail_code,
                fallback="port_failed",
            )
            self._record_web_search_outcome(
                "failed",
                detail_code=detail,
            )
            return await self._web_safe_unavailable(
                started=started,
                detail=detail,
                tool_status="failed",
            )
        if outcome.status == "timeout":
            detail = _safe_web_search_detail_code(
                outcome.detail_code,
                fallback="provider_timeout",
            )
            self._record_web_search_outcome(
                "timeout",
                detail_code=detail,
            )
            return await self._web_safe_unavailable(
                started=started,
                detail=detail,
            )
        # ``ok`` with zero hits and ``empty`` both map to ``no_results``.
        if outcome.status == "empty" or (
            outcome.status == "ok" and not outcome.hits
        ):
            self._record_web_search_outcome("no_results", detail_code="empty")
            return await self._web_emit_empty_view(started=started, detail="empty")

        # ``ok`` with hits → register evidence + emit ok view.
        assert outcome.status == "ok" and outcome.hits

        retrieved_at = _web_retrieved_at_iso()
        handle_ids: list[str] = []
        web_source_blocks: list[str] = []
        # Canonical URL dedup happens before ordering. Then emit first hits
        # from independent PSL registrable domains ahead of later results from
        # the same registrable domain, while retaining every valid candidate
        # below the diversity boundary. A hostname without a safe registrable
        # key is never fabricated into an independent domain. Provider
        # rank/score/raw payload never reach this host path.
        seen_canonical_urls: set[str] = set()
        seen_registrable_domains: set[str] = set()
        independent_domain_candidates: list[tuple[Any, str, str]] = []
        same_domain_candidates: list[tuple[Any, str, str]] = []
        unclassified_domain_candidates: list[tuple[Any, str, str]] = []
        for hit in outcome.hits:
            try:
                canonical = canonicalize_url(hit.raw_url)
            except (ValueError, TypeError):
                # Drop malformed / disallowed scheme hits silently —
                # never raise from provider text.
                continue
            display_domain = display_domain_from_canonical_url(canonical)
            if not display_domain or canonical in seen_canonical_urls:
                continue
            seen_canonical_urls.add(canonical)
            candidate = (hit, canonical, display_domain)
            registrable_domain = registrable_domain_from_canonical_url(canonical)
            if registrable_domain is None:
                unclassified_domain_candidates.append(candidate)
            elif registrable_domain in seen_registrable_domains:
                same_domain_candidates.append(candidate)
            else:
                seen_registrable_domains.add(registrable_domain)
                independent_domain_candidates.append(candidate)

        ordered_candidates = (
            independent_domain_candidates
            + same_domain_candidates
            + unclassified_domain_candidates
        )[:max_results]
        for hit, canonical, display_domain in ordered_candidates:
            source_fingerprint = compute_web_source_fingerprint(
                canonical_url=canonical,
                retrieved_at=retrieved_at,
            )
            handle_id = _mint_web_evidence_handle_id()
            try:
                evidence = WebEvidence(
                    internal_handle_id=handle_id,
                    canonical_url=canonical,
                    display_domain=display_domain,
                    title=hit.title or None,
                    description=hit.description or None,
                    retrieved_at=retrieved_at,
                    provider_result_ref=hit.provider_result_ref,
                    source_fingerprint=source_fingerprint,
                    # Strict date normalization is enforced by WebEvidence.
                    # Raw page_age stays server-internal and is never rendered
                    # to public DTO/SSE/UI surfaces.
                    published_at=hit.published_at,
                    page_age=hit.page_age,
                )
            except (ValueError, TypeError):
                # Drop malformed hits — never raise from provider text.
                continue
            try:
                self.web_evidence_registry.register(evidence)
            except ValueError:
                # Duplicate handle id (defensive — should not happen with
                # a fresh mint) — drop the hit silently.
                continue
            handle_ids.append(handle_id)
            web_source_blocks.append(
                _render_web_source_block(
                    canonical_url=canonical,
                    title=hit.title or "",
                    description=hit.description or "",
                    published_at=evidence.published_at,
                )
            )

        if not handle_ids:
            # All hits dropped (URL canonicalization / fingerprint /
            # registry rejection) — surface ``no_results`` to the model.
            self._record_web_search_outcome("no_results", detail_code="empty")
            return await self._web_emit_empty_view(started=started, detail="empty")

        self._record_web_search_outcome("completed", detail_code="ok")
        tool_view = SearchWebToolView(
            status="ok",
            summary=(
                f"Web search returned {len(handle_ids)} "
                f"source{'s' if len(handle_ids) != 1 else ''}."
            ),
            evidence_handles=[
                {"handle_id": handle_id} for handle_id in handle_ids
            ],
            web_source_blocks=tuple(web_source_blocks),
        )
        rendered = self.renderer.render_tool_view(tool_view.model_dump(mode="json"))
        if not self.budget.can_charge("rag", rendered):
            # Registered handles remain internal and unbound because the
            # model never saw them. The finalizer ignores them.
            return self._tool_budget_exhausted(started=started, detail_code="budget_exhausted")
        self.budget.charge("rag", rendered)
        return MeteredToolReturn(
            text=rendered.text,
            status="ok",
            summary=tool_view.summary,
            evidence_handle_ids=tuple(handle_ids),
            duration_ms=self._web_elapsed_ms(started),
            detail_code="ok",
        )

    async def _web_safe_unavailable(
        self,
        *,
        started: float,
        detail: str,
        tool_status: str = "unavailable",
    ) -> MeteredToolReturn:
        """Emit a fail-soft web search safe view with no handles / blocks."""
        from app.services.reader_record_ask.tool_contracts import (
            SearchWebToolView,
        )

        summary = "Web search is unavailable for this turn."
        tool_view = SearchWebToolView(
            status=tool_status,
            summary=summary,
        )
        rendered = self.renderer.render_tool_view(tool_view.model_dump(mode="json"))
        if not self.budget.can_charge("rag", rendered):
            return self._tool_budget_exhausted(started=started, detail_code="budget_exhausted")
        self.budget.charge("rag", rendered)
        return MeteredToolReturn(
            text=rendered.text,
            status=tool_status,
            summary=summary,
            duration_ms=self._web_elapsed_ms(started),
            detail_code=_safe_web_search_detail_code(
                detail,
                fallback="unknown",
            ),
        )

    async def _web_emit_empty_view(
        self,
        *,
        started: float,
        detail: str = "empty",
    ) -> MeteredToolReturn:
        """Emit a no-results web search view (no handles / blocks)."""
        from app.services.reader_record_ask.tool_contracts import (
            SearchWebToolView,
        )

        summary = "Web search returned no results."
        tool_view = SearchWebToolView(
            status="empty",
            summary=summary,
        )
        rendered = self.renderer.render_tool_view(tool_view.model_dump(mode="json"))
        if not self.budget.can_charge("rag", rendered):
            return self._tool_budget_exhausted(started=started, detail_code="budget_exhausted")
        self.budget.charge("rag", rendered)
        return MeteredToolReturn(
            text=rendered.text,
            status="empty",
            summary=summary,
            duration_ms=self._web_elapsed_ms(started),
            detail_code=_safe_web_search_detail_code(
                detail,
                fallback="empty",
            ),
        )

    def _tool_budget_exhausted(
        self,
        *,
        started: float,
        detail_code: str = "budget_exhausted",
    ) -> MeteredToolReturn:
        """Return a bounded control view, hard-aborting only if it cannot fit."""
        tool_view = ToolBudgetExhaustedView()
        rendered = self.renderer.render_tool_view(tool_view.model_dump(mode="json"))
        duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        if not self.budget.can_charge("control", rendered):
            return MeteredToolReturn(
                text="",
                status="budget_exhausted",
                summary="",
                duration_ms=duration_ms,
                host_budget_abort=True,
                detail_code=_safe_web_search_detail_code(
                    detail_code,
                    fallback="budget_exhausted",
                ),
            )
        self.budget.charge("control", rendered)
        return MeteredToolReturn(
            text=rendered.text,
            status="budget_exhausted",
            summary=tool_view.summary,
            duration_ms=duration_ms,
            detail_code=_safe_web_search_detail_code(
                detail_code,
                fallback="budget_exhausted",
            ),
        )

    @property
    def web_search_outcome(self) -> WebSearchOutcome | None:
        """Translate the per-turn web search state into a public outcome.

        - ``None`` when ``search_web`` was never invoked this turn (no
          capability, or capability enabled but no call made).
        - ``completed`` when at least one call returned hits (even if
          some hits were dropped on canonicalization).
        - ``no_results`` when at least one call returned ``empty`` /
          ``ok`` with zero hits, and no call returned hits.
        - ``timeout`` when the provider cap or the turn-level web-search
          deadline elapsed before a usable result was registered.
        - ``unavailable`` when every call returned ``unavailable``.
        - ``failed`` when every call returned ``failed``.

        The finalizer reads this to set ``web_search_summary`` on the
        completed DTO. Mixing outcomes within a turn is conservative:
        ``completed`` wins over ``timeout`` wins over ``no_results`` wins
        over ``unavailable`` wins over ``failed``.
        """
        return self._last_web_search_outcome

    @property
    def web_search_last_attempt_outcome(self) -> WebSearchOutcome | None:
        """Typed outcome for the latest tool invocation, never raw provider data."""
        return self._last_web_search_attempt_outcome

    @property
    def web_search_next_call_sequence(self) -> int:
        """One-based host tool-invocation sequence for safe progress events."""
        return self.web_search_tool_requests + 1

    @property
    def can_offer_web_search(self) -> bool:
        """Whether ``search_web`` may be exposed on the next agent request.

        The only non-terminal transition is exactly one provider result of
        ``no_results`` followed by one further host tool request. The query
        rewrite gate runs inside :meth:`search_web`, because tool preparing
        intentionally never sees or stores model query text. It does, however,
        retire the tool if the remaining Web Search deadline is exhausted.
        """
        if self.web_search_tool_requests >= self.max_web_search_calls:
            return False
        outcome = self._last_web_search_outcome
        if outcome is None:
            return self.web_search_tool_requests == 0
        if outcome != "no_results":
            return False
        may_offer_retry = (
            self.web_search_tool_requests == 1
            and self.web_search_calls == 1
            and self.max_web_search_calls >= 2
        )
        if not may_offer_retry:
            return False
        if self._remaining_web_search_deadline_seconds() <= 0:
            # Do not manufacture a provider result or alter the already-real
            # ``no_results`` outcome. This is a tool-prepare-only terminal
            # marker, consumed by the terminal aggregate observation.
            self._web_search_deadline_exhausted = True
            self._web_search_final_detail_code = "deadline_exhausted"
            return False
        return True

    @property
    def web_search_deadline_exhausted(self) -> bool:
        return self._web_search_deadline_exhausted

    def web_search_turn_observation(
        self,
        *,
        cited_source_count: int,
        distinct_domain_count: int,
    ) -> WebSearchTurnObservation:
        """Build the one terminal-only aggregate without content fields."""
        total_duration_ms: int | None = None
        if (
            self._web_search_started_at is not None
            and self._web_search_finished_at is not None
        ):
            total_duration_ms = max(
                0,
                int(
                    (
                        self._web_search_finished_at
                        - self._web_search_started_at
                    )
                    * 1000
                ),
            )
        return WebSearchTurnObservation(
            attempt_count=self.web_search_calls,
            final_outcome=self._last_web_search_outcome,
            total_duration_ms=total_duration_ms,
            cited_source_count=max(0, int(cited_source_count)),
            distinct_domain_count=max(0, int(distinct_domain_count)),
            deadline_exhausted=self._web_search_deadline_exhausted,
            second_query_changed=self._web_search_second_query_changed,
            final_detail_code=self._web_search_final_detail_code,
        )

    # ASK-WEB-R4: executable-capability properties used by the runtime to
    # decide whether to mount ``expand_evidence`` and
    # ``search_current_article``. When ``False``, the tool is NOT
    # registered on the agent and the model never sees it — no
    # ``unavailable`` activity is produced for a non-executable tool.
    @property
    def has_expand_pointer(self) -> bool:
        """True when a real expansion pointer exists this turn.

        - selection session created (injected + expandable + selected_text), OR
        - article map expander created (map_result.is_ok).

        When ``False``, ``expand_evidence`` would always return a safe
        ``invalid_cursor`` / ``stale_evidence`` view — so the tool is
        not mounted and no ``unavailable`` activity is produced.
        """
        return (
            self._selection_session is not None
            or self._map_expander is not None
        )

    @property
    def has_executable_article_rag(self) -> bool:
        """True when ``search_current_article`` can perform real I/O.

        Requires a non-None ``ArticleRagSearchPort`` AND a non-None
        ``stable_document_id`` on the envelope. When ``False``, the
        tool would always return a safe ``port_or_document_missing``
        view — so the tool is not mounted and no ``unavailable``
        activity is produced.
        """
        return (
            self.article_rag is not None
            and self.envelope.stable_document_id is not None
        )

    def _record_web_search_outcome(
        self,
        outcome: WebSearchOutcome,
        *,
        detail_code: str | None = None,
    ) -> None:
        """Keep the strongest user-visible outcome across all attempts.

        A successful search must not be downgraded when the agent makes a
        follow-up call after the per-turn limit has already been consumed.
        """
        priority: dict[WebSearchOutcome, int] = {
            "failed": 0,
            "unavailable": 1,
            "no_results": 2,
            "timeout": 3,
            "completed": 4,
        }
        safe_detail_code = (
            _safe_web_search_detail_code(
                detail_code,
                fallback="unknown",
            )
            if detail_code is not None
            else None
        )
        self._last_web_search_attempt_outcome = outcome
        current = self._last_web_search_outcome
        if current is None or priority[outcome] >= priority[current]:
            self._last_web_search_outcome = outcome
            self._web_search_final_detail_code = safe_detail_code

    def _remaining_web_search_deadline_seconds(self) -> float:
        if self._web_search_started_at is None:
            return self.web_search_deadline_seconds
        return self.web_search_deadline_seconds - (
            self._web_search_clock() - self._web_search_started_at
        )

    def _web_elapsed_ms(self, started: float) -> int:
        return max(0, int((self._web_search_clock() - started) * 1000))

    def _normalize_web_query_for_comparison(self, query: str) -> str:
        """Return the private NFKC/casefold/punctuation-space comparison form.

        The result is used only as a local input to HMAC during this method's
        caller; it is never placed in coordinator state, events, logs, DTOs,
        or persistence.
        """
        return "".join(
            character
            for character in unicodedata.normalize("NFKC", query).casefold()
            if not character.isspace()
            and not unicodedata.category(character).startswith("P")
        )

    @staticmethod
    def _is_cjk_query_character(character: str) -> bool:
        """Whether a character must remain its own lexical unit.

        In particular, a continuous Chinese phrase is deliberately not treated
        as one giant token. Han extensions and compatibility ideographs are
        included so the behavior stays stable for common Chinese input.
        """
        return (
            "\u3400" <= character <= "\u4dbf"
            or "\u4e00" <= character <= "\u9fff"
            or "\uf900" <= character <= "\ufaff"
            or "\U00020000" <= character <= "\U0002ebef"
        )

    def _web_query_lexical_units(self, query: str) -> tuple[str, ...]:
        """Build ephemeral CJK-character / alphanumeric-token units.

        Punctuation and whitespace are boundaries and are omitted. The tuple
        is immediately HMACed by :meth:`_web_query_signature`; it is never
        retained on the coordinator or emitted outside this stack frame.
        """
        units: list[str] = []
        latin_or_number: list[str] = []

        def flush_latin_or_number() -> None:
            if latin_or_number:
                units.append("".join(latin_or_number))
                latin_or_number.clear()

        for character in unicodedata.normalize("NFKC", query).casefold():
            if (
                character.isspace()
                or unicodedata.category(character).startswith("P")
            ):
                flush_latin_or_number()
            elif self._is_cjk_query_character(character):
                flush_latin_or_number()
                units.append(character)
            elif character.isalnum():
                latin_or_number.append(character)
            else:
                # Symbols are neither comparison tokens nor token glue.
                flush_latin_or_number()
        flush_latin_or_number()
        return tuple(units)

    def _web_query_hmac(self, material: str) -> bytes:
        return hmac.digest(
            self._web_search_query_digest_key,
            material.encode("utf-8"),
            "sha256",
        )

    def _web_query_signature(
        self,
        query: str,
    ) -> _WebQuerySimilaritySignature | None:
        """Build opaque HMAC token/shingle features for one local query.

        Individual lexical units are token features. Consecutive two- and
        three-unit windows are shingle features. The actual units and shingles
        exist only in this function until HMACed; the returned signature stores
        only bytes and a length needed for the light edge-append safeguard.
        """
        normalized = self._normalize_web_query_for_comparison(query)
        if not normalized:
            return None
        units = self._web_query_lexical_units(query)
        if not units or "".join(units) != normalized:
            # Unsupported symbols would make the unit boundary comparison
            # ambiguous. A missing signature causes the retry to fail closed.
            return None

        feature_materials = {f"token:{unit}" for unit in units}
        for shingle_size in (2, 3):
            for start_index in range(len(units) - shingle_size + 1):
                feature_materials.add(
                    f"shingle:{shingle_size}:"
                    + "\x1f".join(
                        units[start_index : start_index + shingle_size]
                    )
                )
        feature_digests = frozenset(
            self._web_query_hmac(material) for material in feature_materials
        )
        if not feature_digests:
            return None

        edge_trim_digests: set[bytes] = set()
        for delta in range(
            1,
            min(_WEB_QUERY_LIGHT_EDGE_DELTA_MAX_CHARS, len(normalized) - 1)
            + 1,
        ):
            edge_trim_digests.add(
                self._web_query_hmac(f"full:{normalized[delta:]}")
            )
            edge_trim_digests.add(
                self._web_query_hmac(f"full:{normalized[:-delta]}")
            )
        edge_unit_trim_digests: set[bytes] = set()
        if len(units) >= 2:
            edge_unit_trim_digests.add(
                self._web_query_hmac(f"full:{''.join(units[:-1])}")
            )
            edge_unit_trim_digests.add(
                self._web_query_hmac(f"full:{''.join(units[1:])}")
            )
        return _WebQuerySimilaritySignature(
            full_digest=self._web_query_hmac(f"full:{normalized}"),
            feature_digests=feature_digests,
            normalized_length=len(normalized),
            edge_trim_digests=frozenset(edge_trim_digests),
            edge_unit_trim_digests=frozenset(edge_unit_trim_digests),
        )

    def _is_light_web_query_edge_extension(
        self,
        *,
        query: str,
        first: _WebQuerySimilaritySignature,
        candidate: _WebQuerySimilaritySignature,
    ) -> bool:
        """Reject a small edge change or one lexical-unit edge change.

        The stored first-query state is HMAC-only. For a longer candidate we
        HMAC its temporary matching edge and compare it with the stored full
        digest. For a shorter candidate we compare its full digest with the
        first query's precomputed, HMAC-only character and unit edge trims.
        """
        length_delta = candidate.normalized_length - first.normalized_length
        if length_delta == 0:
            return False
        if length_delta < 0:
            candidate_matches = first.edge_unit_trim_digests
            if abs(length_delta) <= _WEB_QUERY_LIGHT_EDGE_DELTA_MAX_CHARS:
                candidate_matches = (
                    candidate_matches | first.edge_trim_digests
                )
            return any(
                hmac.compare_digest(candidate.full_digest, digest)
                for digest in candidate_matches
            )

        normalized = self._normalize_web_query_for_comparison(query)
        if len(normalized) != candidate.normalized_length:
            # Defensive fail closed if the local material cannot be reproduced
            # exactly for comparison.
            return True
        original_length = first.normalized_length
        if abs(length_delta) <= _WEB_QUERY_LIGHT_EDGE_DELTA_MAX_CHARS:
            for edge in (
                normalized[:original_length],
                normalized[-original_length:],
            ):
                if hmac.compare_digest(
                    first.full_digest,
                    self._web_query_hmac(f"full:{edge}"),
                ):
                    return True
        units = self._web_query_lexical_units(query)
        if "".join(units) != normalized:
            return True
        if len(units) >= 2:
            for edge_units in (units[:-1], units[1:]):
                if hmac.compare_digest(
                    first.full_digest,
                    self._web_query_hmac(f"full:{''.join(edge_units)}"),
                ):
                    return True
        return False

    def _web_query_reformulation_decision(
        self,
        query: str,
    ) -> Literal[
        "changed",
        "equivalent_query",
        "insufficient_rewrite",
        "unsafe_query_comparison",
    ]:
        """Return the safe host decision for the sole potential retry.

        The Jaccard score is calculated over opaque HMAC digest sets. A score
        at or above 0.8 is near-duplicate and fails closed. A short direct
        edge extension also fails closed even if a small query's set Jaccard
        falls below the threshold; this prevents appending a date or one word
        from spending the retry. No score, token, shingle, or normalized form
        is retained after this method returns.
        """
        first = self._first_no_results_query_signature
        candidate = self._web_query_signature(query)
        if first is None or candidate is None:
            return "unsafe_query_comparison"
        # Normalization equivalence is an absolute Host refusal, independent
        # of lexical-boundary differences caused by removed punctuation (for
        # example ``foo,bar`` versus ``foobar``).
        if hmac.compare_digest(first.full_digest, candidate.full_digest):
            return "equivalent_query"
        union = first.feature_digests | candidate.feature_digests
        if not union:
            return "unsafe_query_comparison"
        jaccard = len(first.feature_digests & candidate.feature_digests) / len(
            union
        )
        if jaccard >= _WEB_QUERY_SIGNIFICANT_REWRITE_JACCARD_THRESHOLD:
            return "equivalent_query"
        if self._is_light_web_query_edge_extension(
            query=query,
            first=first,
            candidate=candidate,
        ):
            return "insufficient_rewrite"
        return "changed"

    async def _rag_safe_unavailable(self, *, started: float, detail: str) -> MeteredToolReturn:
        from app.services.reader_record_ask.article_rag_port import (
            ArticleRagSearchOutcome,
        )

        # stable_document_id required by RagEnvelopeIdentity — when missing
        # charge a minimal safe RAG tool-view without identity construction.
        if self.envelope.stable_document_id is None:
            from app.services.reader_record_ask.tool_contracts import (
                RagSearchToolView,
            )

            rag_view = RagSearchToolView(
                status="unavailable",
                summary="Article search is unavailable for this article.",
            )
            rendered = self.renderer.render_tool_view(rag_view.model_dump(mode="json"))
            if not self.budget.can_charge("rag", rendered):
                return self._tool_budget_exhausted(started=started)
            self.budget.charge("rag", rendered)
            return MeteredToolReturn(
                text=rendered.text,
                status="unavailable",
                summary=rag_view.summary,
                duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
            )

        outcome = ArticleRagSearchOutcome(
            status="unavailable",
            summary="Article search is unavailable for this article.",
            detail_code=detail,
        )
        rag_identity = RagEnvelopeIdentity(
            envelope_fingerprint=self.envelope.envelope_fingerprint,
            reading_record_id=self.envelope.reading_record_id,
            base_id=self.envelope.base_id,
            record_generation=self.envelope.record_generation,
            stable_document_id=self.envelope.stable_document_id,
        )
        result = assemble_rag_model_view(
            outcome=outcome,
            envelope_identity=rag_identity,
            registry=self.registry,
            budget=self.budget,
            renderer=self.renderer,
        )
        duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        if result.kind == "budget_denied" or not result.model_visible:
            return self._tool_budget_exhausted(started=started)
        assert result.rendered_tool_view is not None
        return MeteredToolReturn(
            text=result.rendered_tool_view.text,
            status=result.kind,
            summary=self._tool_view_summary(result.rendered_tool_view),
            duration_ms=duration_ms,
        )

    async def _run_fence(self) -> Any:
        result = self.fence(self.envelope)
        if hasattr(result, "__await__"):
            return await result
        return result

    @staticmethod
    def _tool_view_summary(rendered: RenderedModelView) -> str:
        try:
            payload = json.loads(rendered.text)
            if isinstance(payload, dict):
                summary = payload.get("summary")
                if isinstance(summary, str):
                    return summary
        except Exception:  # noqa: BLE001
            pass
        return "ok"


# ---------------------------------------------------------------------------
# G1-b5 module-private web search helpers
# ---------------------------------------------------------------------------
#
# Server-only utilities for the ``search_web`` tool path. Kept at module
# scope so they can be patched in tests (e.g. deterministic handle ids /
# timestamps) without exposing them on the coordinator's public surface.


def _mint_web_evidence_handle_id() -> str:
    """Mint a fresh ``evh_<32 hex>`` handle id for one web evidence entry.

    Uses :func:`secrets.token_hex` so the handle is cryptographically
    random and unpredictable — provider text can never influence the id.
    """
    return f"evh_{_secrets.token_hex(16)}"


def _web_retrieved_at_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Used as the server-recorded ``retrieved_at`` timestamp on
    :class:`WebEvidence`. The host owns this timestamp — never the
    provider — so :func:`compute_web_source_fingerprint` is stable
    across provider text drift.
    """
    return (
        _datetime.datetime.now(_datetime.UTC)
        .replace(microsecond=0)
        .isoformat()
    )


def _render_web_source_block(
    *,
    canonical_url: str,
    title: str,
    description: str,
    published_at: str | None = None,
) -> str:
    """Render one ``<untrusted_web_source>`` XML block for the model view.

    Mirrors the article ``<untrusted_article_text>`` discipline: every
    field is XML-escaped so provider text cannot inject markup. The
    block is the *only* place the model sees web source text — it never
    sees ``provider_result_ref`` or raw provider payload.

    Only a strict provider-supplied ``published_at`` date may be projected.
    Raw relative ``page_age`` hints never enter the model or public surface,
    so they cannot be mistaken for a verifiable publication date.
    """
    title_attr = _xml_escape_web(title, {'"': "&quot;"}) if title else ""
    desc_attr = (
        _xml_escape_web(description, {'"': "&quot;"}) if description else ""
    )
    url_attr = _xml_escape_web(canonical_url, {'"': "&quot;"})
    parts = [
        '<untrusted_web_source role="search_web"',
        f' url="{url_attr}"',
    ]
    if title_attr:
        parts.append(f' title="{title_attr}"')
    if desc_attr:
        parts.append(f' description="{desc_attr}"')
    if published_at:
        date_attr = _xml_escape_web(published_at, {'"': "&quot;"})
        parts.append(f' published_at="{date_attr}"')
    parts.append("/>")
    return "".join(parts)


__all__ = [
    "DEFAULT_MAX_SEARCH_CURRENT_ARTICLE_CALLS",
    "HostBudgetExhausted",
    "MeteredToolReturn",
    "TurnAssembly",
    "TurnCoordinator",
]
