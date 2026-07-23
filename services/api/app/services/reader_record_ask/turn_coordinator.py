"""Turn coordinator — single turn state center for Ask model-views (R4-A5-7).

Narrow public surface
---------------------
- :meth:`TurnCoordinator.assemble_turn` → :class:`TurnAssembly`
- :meth:`TurnCoordinator.expand_evidence` → :class:`MeteredToolReturn`
- :meth:`TurnCoordinator.search_current_article` → :class:`MeteredToolReturn`

Owns registry, six-account budget, renderer, fence, pointer ledger,
selection expansion session, map expander, RAG identity, and search
counter. Outer initial-assembly transaction is plan-then-commit with
host-only rollback receipts; once ``agent.run`` begins the assembly is
committed and is never refunded because the model already saw content.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from app.services.reader_record_ask.answer_correctness_policy import (
    AnswerCorrectnessPolicy,
    build_answer_correctness_policy,
)
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

# Default search call limit (mirrors legacy executor).
DEFAULT_MAX_SEARCH_CURRENT_ARTICLE_CALLS = 1

# Max map entry sources from document units (content policy; map budget fits).
_MAX_MAP_ENTRY_SOURCES = 32


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


@dataclass(frozen=True, slots=True)
class TurnAssembly:
    """Committed initial turn assembly ready for agent.run."""

    turn_id: str
    user_prompt: str
    system_instructions: str
    turn_frame: TurnFramePromptCapability
    projection: TurnCapabilityProjection
    baseline_context: BaselineAgentContext
    answer_correctness_policy: AnswerCorrectnessPolicy | None
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
    # Map assembly issues cursors under the shared ledger; on rollback we
    # rely on assemble_article_map's own compensation when it fails mid-way.
    # If a later step fails after map ok, we must revoke map cursors + refund.
    map_cursors: tuple[str, ...] = ()


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
        max_search_current_article_calls: int = (
            DEFAULT_MAX_SEARCH_CURRENT_ARTICLE_CALLS
        ),
        product_search_enabled: bool = True,
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
            if (
                evidence_registry.envelope_fingerprint
                != envelope.envelope_fingerprint
            ):
                raise ValueError(
                    "evidence_registry envelope_fingerprint does not match "
                    "the turn envelope"
                )
            self.registry = evidence_registry
        else:
            self.registry = EvidenceRegistry(envelope.envelope_fingerprint)
        # Never default to a fresh ExpansionPointerLedger() — production
        # and multi-turn tests must share recognition via an explicit owner.
        self.ledger = (
            pointer_ledger
            if pointer_ledger is not None
            else get_process_pointer_ledger()
        )
        self.budget = budget if budget is not None else ModelVisibleTurnBudget()
        self.renderer = (
            renderer if renderer is not None else ModelViewRenderer()
        )
        self.max_search_current_article_calls = max_search_current_article_calls
        self.product_search_enabled = product_search_enabled

        self.turn_id: str = mint_turn_id()
        self.search_current_article_calls: int = 0
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
            raise RuntimeError(
                "turn coordinator requires a fresh budget before assemble"
            )
        if len(self.registry) != 0:
            raise RuntimeError(
                "turn coordinator requires an empty registry before assemble"
            )

        # ---- outer commit (selection → baseline → map → request_frame) ----
        receipt = _OuterTxnReceipt()
        try:
            assembly = self._commit_assembly(scope=scope, receipt=receipt)
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
            answer_correctness_policy=None,
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
        receipt: _OuterTxnReceipt,
    ) -> TurnAssembly:
        envelope = self.envelope
        fp = envelope.envelope_fingerprint

        # 1) Selection inject (may be absent / budget_denied / injected).
        selected_text = (
            envelope.initial_anchor.selected_text
            if envelope.initial_anchor is not None
            else None
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
        map_sources = self._map_sources_from_scope(scope)
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
            receipt.map_cursors = tuple(e.cursor for e in map_result.entries)
            self._map_expander = map_result.expander

        # 4) Projection (turn_id server-minted; same value everywhere).
        sel_view = selection.selection
        map_present = map_result.is_ok and map_result.entry_count > 0
        can_expand = bool(
            (selection.status == "injected" and sel_view.expandable)
            or map_present
        )
        # budget_denied selection: can_read_range=False, has_visible_range=False
        if selection.status == "budget_denied":
            can_read_range = False
            has_visible_range = False
        else:
            can_read_range = can_expand
            has_visible_range = bool(
                sel_view.present and sel_view.visible_char_count > 0
            )

        projection = build_turn_capability_projection(
            article_rag_port=self.article_rag,
            stable_document_id=envelope.stable_document_id,
            product_search_enabled=self.product_search_enabled,
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

        # 5) Correctness policy from baseline chunk texts.
        policy = build_answer_correctness_policy(
            user_message=self.user_message,
            model_visible_chunk_texts=tuple(
                c.text for c in baseline.model_context_chunks
            ),
            baseline_is_complete=baseline.is_complete,
        )
        correctness_block = policy.render_prompt_block()

        # 6) Handles listing (selection + baseline seeds).
        handle_ids: list[str] = []
        if sel_view.handle_id:
            handle_ids.append(sel_view.handle_id)
        handle_ids.extend(baseline.available_seed_handle_ids)
        handles_block = render_handles_listing(handle_ids)

        # 7) Request-frame charge (full question; fail closed on excess).
        selection_prompt: SelectionPromptCapability | None = (
            selection.prompt_capability
            if selection.status == "injected"
            else None
        )
        baseline_prompt: BaselinePromptCapability | None = (
            baseline.prompt_capability
        )
        map_prompt: ArticleMapPromptCapability | None = (
            map_result.prompt_capability if map_result.is_ok else None
        )

        try:
            turn_frame = mint_turn_frame_prompt_capability(
                system_instructions=self.system_instructions,
                projection_json=projection_json,
                handles_block=handles_block,
                baseline_is_complete=baseline.is_complete,
                correctness_block=correctness_block,
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
        if (
            selection.status == "injected"
            and selection.selection.expandable
            and selected_text
        ):
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
            answer_correctness_policy=policy,
            selection_result=selection,
            baseline_result=baseline,
            map_result=map_result,
            available_handle_ids=tuple(handle_ids),
        )
        return assembly

    def _rollback_outer(self, receipt: _OuterTxnReceipt) -> None:
        """Reverse-order cleanup of this transaction's writes only."""
        # Request-frame refund.
        if receipt.request_frame_charge > 0:
            try:
                spent = self.budget.spent("request_frame")
                if spent >= receipt.request_frame_charge:
                    self.budget._refund_chars(  # noqa: SLF001
                        "request_frame", receipt.request_frame_charge
                    )
            except Exception:  # noqa: BLE001
                raise RuntimeError(
                    "turn_assembly_rollback_failed code=request_frame_refund"
                ) from None

        # Map: refund + delete only this assembly's cursors.
        if receipt.map_result is not None and receipt.map_charge > 0:
            for cursor in receipt.map_cursors:
                records = getattr(self.ledger, "_records", None)
                if isinstance(records, dict):
                    records.pop(cursor, None)
            try:
                if self.budget.spent("map") >= receipt.map_charge:
                    self.budget._refund_chars("map", receipt.map_charge)  # noqa: SLF001
            except Exception:  # noqa: BLE001
                raise RuntimeError(
                    "turn_assembly_rollback_failed code=map_refund"
                ) from None

        # Baseline.
        if (
            receipt.baseline_result is not None
            and receipt.baseline_result.status == "injected"
        ):
            try:
                rollback_baseline_inject(
                    budget=self.budget,
                    registry=self.registry,
                    result=receipt.baseline_result,
                )
            except Exception:  # noqa: BLE001
                raise RuntimeError(
                    "turn_assembly_rollback_failed code=baseline_refund"
                ) from None

        # Selection.
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
                raise RuntimeError(
                    "turn_assembly_rollback_failed code=selection_refund"
                ) from None

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

    @staticmethod
    def _map_sources_from_scope(
        scope: DocumentScopeSnapshot,
    ) -> list[ArticleMapEntrySource]:
        units = sorted(scope.units, key=lambda u: u.order_index)
        sources: list[ArticleMapEntrySource] = []
        for unit in units:
            if not unit.text:
                continue
            sources.append(ArticleMapEntrySource(window_text=unit.text))
            if len(sources) >= _MAX_MAP_ENTRY_SOURCES:
                break
        return sources

    # ------------------------------------------------------------------
    # Public: tools
    # ------------------------------------------------------------------

    def expand_evidence(self, pointer: str) -> MeteredToolReturn:
        """Expand selection- or map-scope evidence by opaque pointer."""
        started = time.perf_counter()
        if not isinstance(pointer, str):
            pointer = ""

        outcome = None
        record = (
            self.ledger.lookup(pointer)
            if is_expand_pointer_shape(pointer)
            else None
        )
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
                    "Unknown, malformed, or already-used expansion "
                    "pointer. No text was added."
                ),
            )

        duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        if outcome.kind == "budget_exhausted" or not outcome.model_visible:
            return MeteredToolReturn(
                text="",
                status="budget_exhausted",
                summary="",
                duration_ms=duration_ms,
                host_budget_abort=True,
            )
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
            return await self._rag_safe_unavailable(
                started=started, detail="fence_pre"
            )

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
            return MeteredToolReturn(
                text="",
                status="budget_exhausted",
                summary="",
                duration_ms=duration_ms,
                host_budget_abort=True,
            )
        assert result.rendered_tool_view is not None
        handle_ids = tuple(ref.handle_id for ref in result.evidence_handles)
        return MeteredToolReturn(
            text=result.rendered_tool_view.text,
            status=result.kind,
            summary=self._tool_view_summary(result.rendered_tool_view),
            evidence_handle_ids=handle_ids,
            duration_ms=duration_ms,
        )

    async def _rag_safe_unavailable(
        self, *, started: float, detail: str
    ) -> MeteredToolReturn:
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
            rendered = self.renderer.render_tool_view(
                rag_view.model_dump(mode="json")
            )
            if not self.budget.can_charge("rag", rendered):
                return MeteredToolReturn(
                    text="",
                    status="budget_exhausted",
                    summary="",
                    duration_ms=max(
                        0, int((time.perf_counter() - started) * 1000)
                    ),
                    host_budget_abort=True,
                )
            self.budget.charge("rag", rendered)
            return MeteredToolReturn(
                text=rendered.text,
                status="unavailable",
                summary=rag_view.summary,
                duration_ms=max(
                    0, int((time.perf_counter() - started) * 1000)
                ),
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
            return MeteredToolReturn(
                text="",
                status="budget_exhausted",
                summary="",
                duration_ms=duration_ms,
                host_budget_abort=True,
            )
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
            return await result  # type: ignore[misc]
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


__all__ = [
    "DEFAULT_MAX_SEARCH_CURRENT_ARTICLE_CALLS",
    "HostBudgetExhausted",
    "MeteredToolReturn",
    "TurnAssembly",
    "TurnCoordinator",
]
