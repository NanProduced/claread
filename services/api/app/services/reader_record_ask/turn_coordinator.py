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

import datetime as _datetime
import json
import secrets as _secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
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
    ResolvedWebSearchCapability,
    WebEvidence,
    WebSearchOutcome,
    canonicalize_url,
    compute_web_source_fingerprint,
    display_domain_from_canonical_url,
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
# resolver default (3 as of ASK-WEB-R4). The actual limit comes from the
# resolved capability's ``max_calls`` field; this constant is only used
# when the coordinator is constructed without an explicit
# ``max_web_search_calls`` AND the capability is unavailable (defensive
# fail-soft path).
_DEFAULT_MAX_WEB_SEARCH_CALLS: int = 3


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
        self.web_search_calls: int = 0
        # G1-b5: last translated public outcome for ``web_search_outcome``
        # property / finalizer. ``None`` until the first call sets it.
        self._last_web_search_outcome: WebSearchOutcome | None = None
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
        """Provider-neutral web search via :class:`WebSearchBackend` port.

        Mirrors :meth:`search_current_article` discipline:

        - pre-call fence (fail-soft ``unavailable`` on stale envelope);
        - call-limit consumption (even on fence failure after the call
          is made);
        - host re-canonicalizes provider URLs and recomputes
          :class:`WebEvidence.source_fingerprint` before any host-side
          registry mutation;
        - metered budget charge via the ``rag`` account (web evidence
          shares the existing model-visible budget pool);
        - host-only ``budget_exhausted`` abort when even a minimal safe
          view cannot be charged.

        ``query`` is clamped to :data:`WEB_QUERY_MAX_LEN` before the
        port call. ``max_results`` is clamped to the resolved
        capability's ``max_results_per_call`` (or
        :data:`WEB_MAX_RESULTS_PER_CALL` when capability is ``None``).

        Fail-soft safe views (``unavailable`` / ``failed`` / ``empty``)
        carry no evidence handles and no web source blocks.
        """
        started = time.perf_counter()

        # Clamp query length BEFORE any port call (defensive fail-soft).
        if not isinstance(query, str):
            query = ""
        clamped_query = query[:WEB_QUERY_MAX_LEN] if query else ""

        # Resolve effective max_results from capability / default.
        if max_results is not None:
            effective_max_results = max(
                1, min(int(max_results), WEB_MAX_RESULTS_PER_CALL)
            )
        elif self.web_search_capability is not None:
            effective_max_results = max(
                1,
                min(
                    self.web_search_capability.max_results_per_call,
                    WEB_MAX_RESULTS_PER_CALL,
                ),
            )
        else:
            effective_max_results = WEB_MAX_RESULTS_PER_CALL

        # Call-limit (consume even on fence failure after the call is made).
        if self.web_search_calls >= self.max_web_search_calls:
            self._record_web_search_outcome("unavailable")
            return await self._web_safe_unavailable(
                started=started,
                detail="call_limit",
            )

        # Pre-generation fence.
        fence_result = await self._run_fence()
        if not fence_result.ok:
            self._record_web_search_outcome("unavailable")
            return await self._web_safe_unavailable(
                started=started, detail="fence_pre"
            )

        # Capability not enabled OR backend port None → no I/O.
        if (
            self.web_search_capability is None
            or not self.web_search_capability.enabled_for_turn
            or self.web_search_backend is None
        ):
            self.web_search_calls += 1
            self._record_web_search_outcome("unavailable")
            return await self._web_safe_unavailable(
                started=started, detail="capability_or_backend_missing"
            )

        # Backend port call (provider-neutral). The host re-canonicalizes
        # every URL and recomputes source_fingerprint AFTER the call.
        outcome: WebSearchResult
        try:
            outcome = await self.web_search_backend.search_web(
                query=clamped_query,
                max_results=effective_max_results,
            )
        except Exception:
            # Provider raised — fail-soft safe view; never ModelRetry.
            self.web_search_calls += 1
            self._record_web_search_outcome("failed")
            return await self._web_safe_unavailable(
                started=started, detail="backend_exception"
            )
        self.web_search_calls += 1

        # Post-generation fence.
        fence_after = await self._run_fence()
        if not fence_after.ok:
            self._record_web_search_outcome("unavailable")
            return await self._web_safe_unavailable(
                started=started, detail="fence_post"
            )

        # Translate port outcome → host model view + evidence registration.
        return await self._register_web_search_outcome(
            outcome=outcome,
            started=started,
        )

    async def _register_web_search_outcome(
        self,
        *,
        outcome: WebSearchResult,
        started: float,
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
            self._record_web_search_outcome("unavailable")
            return await self._web_safe_unavailable(
                started=started,
                detail=outcome.detail_code or "port_unavailable",
            )
        if outcome.status == "failed":
            self._record_web_search_outcome("failed")
            return await self._web_safe_unavailable(
                started=started,
                detail=outcome.detail_code or "port_failed",
                tool_status="failed",
            )
        # ``ok`` with zero hits and ``empty`` both map to ``no_results``.
        if outcome.status == "empty" or (
            outcome.status == "ok" and not outcome.hits
        ):
            self._record_web_search_outcome("no_results")
            return await self._web_emit_empty_view(started=started)

        # ``ok`` with hits → register evidence + emit ok view.
        assert outcome.status == "ok" and outcome.hits

        retrieved_at = _web_retrieved_at_iso()
        handle_ids: list[str] = []
        web_source_blocks: list[str] = []
        for hit in outcome.hits:
            try:
                canonical = canonicalize_url(hit.raw_url)
            except (ValueError, TypeError):
                # Drop malformed / disallowed scheme hits silently —
                # never raise from provider text.
                continue
            source_fingerprint = compute_web_source_fingerprint(
                canonical_url=canonical,
                retrieved_at=retrieved_at,
            )
            display_domain = display_domain_from_canonical_url(canonical)
            if not display_domain:
                continue
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
                    # ASK-WEB-R4: propagate optional provider-supplied
                    # freshness hints. Untrusted provider text — the
                    # host never treats them as authoritative.
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
                    page_age=hit.page_age,
                )
            )

        if not handle_ids:
            # All hits dropped (URL canonicalization / fingerprint /
            # registry rejection) — surface ``no_results`` to the model.
            self._record_web_search_outcome("no_results")
            return await self._web_emit_empty_view(started=started)

        self._record_web_search_outcome("completed")
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
            duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
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
            duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
            detail_code=detail,
        )

    async def _web_emit_empty_view(self, *, started: float) -> MeteredToolReturn:
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
            duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
            detail_code="empty",
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
                detail_code=detail_code,
            )
        self.budget.charge("control", rendered)
        return MeteredToolReturn(
            text=rendered.text,
            status="budget_exhausted",
            summary=tool_view.summary,
            duration_ms=duration_ms,
            detail_code=detail_code,
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
        - ``unavailable`` when every call returned ``unavailable``.
        - ``failed`` when every call returned ``failed``.

        The finalizer reads this to set ``web_search_summary`` on the
        completed DTO. Mixing outcomes within a turn is conservative:
        ``completed`` wins over ``no_results`` wins over ``unavailable``
        wins over ``failed``.
        """
        if self.web_search_calls == 0:
            return None
        return self._last_web_search_outcome

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

    def _record_web_search_outcome(self, outcome: WebSearchOutcome) -> None:
        """Keep the strongest user-visible outcome across all attempts.

        A successful search must not be downgraded when the agent makes a
        follow-up call after the per-turn limit has already been consumed.
        """
        priority: dict[WebSearchOutcome, int] = {
            "failed": 0,
            "unavailable": 1,
            "no_results": 2,
            "completed": 3,
        }
        current = self._last_web_search_outcome
        if current is None or priority[outcome] > priority[current]:
            self._last_web_search_outcome = outcome

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
    page_age: str | None = None,
) -> str:
    """Render one ``<untrusted_web_source>`` XML block for the model view.

    Mirrors the article ``<untrusted_article_text>`` discipline: every
    field is XML-escaped so provider text cannot inject markup. The
    block is the *only* place the model sees web source text — it never
    sees ``provider_result_ref`` or raw provider payload.

    ASK-WEB-R4: ``page_age`` is an optional untrusted provider-supplied
    freshness hint (e.g. "2 days ago"). Included as a ``page_age``
    attribute so the model can prefer newer sources for time-sensitive
    questions. Never authoritative — the model must not claim a fact is
    confirmed 'as of today' based solely on this hint.
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
    if page_age:
        age_attr = _xml_escape_web(page_age, {'"': "&quot;"})
        parts.append(f' page_age="{age_attr}"')
    parts.append("/>")
    return "".join(parts)


__all__ = [
    "DEFAULT_MAX_SEARCH_CURRENT_ARTICLE_CALLS",
    "HostBudgetExhausted",
    "MeteredToolReturn",
    "TurnAssembly",
    "TurnCoordinator",
]
