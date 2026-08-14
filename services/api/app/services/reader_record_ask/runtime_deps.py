"""Deps object for the Reading Record Ask agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from app.services.reader_record_ask.answer_block_provenance import (
    ArticleScope,
    ValidatedAnswerBlocks,
)
from app.services.reader_record_ask.article_rag_port import ArticleRagSearchPort
from app.services.reader_record_ask.context_envelope import (
    ReadingRecordAskContextEnvelope,
)
from app.services.reader_record_ask.document_access import DocumentAccess
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.fence import FenceFn
from app.services.reader_record_ask.read_range_executor import (
    DEFAULT_MAX_READ_RANGE_CALLS,
)
from app.services.reader_record_ask.runtime_events import (
    RuntimeEvent,
    RuntimeEventSink,
)
from app.services.reader_record_ask.search_current_article_executor import (
    DEFAULT_MAX_SEARCH_CURRENT_ARTICLE_CALLS,
)
from app.services.reader_record_ask.web_search_contracts import (
    WEB_MAX_CALLS_PER_TURN,
    ResolvedWebSearchCapability,
)
from app.services.reader_record_ask.web_search_port import WebSearchBackend

if TYPE_CHECKING:
    from app.services.reader_record_ask.baseline_context import BaselineAgentContext
    from app.services.reader_record_ask.turn_coordinator import TurnCoordinator
    from app.services.reader_record_ask.web_evidence_registry import (
        WebEvidenceRegistry,
    )

# Issue #1: typed execution-stage evidence. This Literal is
# the single source of truth for the runtime's execution-stage state
# machine. The runtime writes ``RuntimeObservation.execution_stage`` at
# each transition point; the harness classifier reads it to distinguish
# ``ValidationError`` raised DURING ``agent.run`` (where the output
# validator fires) from ``ValidationError`` raised AFTER ``agent.run``
# returned (e.g. in the finalizer). Only the former is classified as
# ``agent_output_invalid``; the latter is conservatively classified as
# ``runtime_exception``. See :class:`RuntimeObservation` docstring for
# the full state machine.
ExecutionStage = Literal[
    # Currently in baseline assembly (``assemble_baseline``).
    "baseline_assembly",
    # Currently somewhere inside ``agent.run`` but outside our final-mode
    # grounding validator. Errors here are conservatively runtime failures.
    "agent_run",
    # Currently inside the final-mode grounding output validator. This is
    # the only stage that proves a ``ValidationError`` originated from our
    # output-validation seam.
    "output_validation",
    # ``agent.run`` returned successfully; the finalizer has NOT yet
    # started. A ``ValidationError`` raised AFTER this transition did
    # NOT come from the output validator — it came from the finalizer
    # or later code, and must be classified as ``runtime_exception``.
    "agent_run_completed",
    # Currently in ``finalize_agent_answer``.
    "finalizer",
]


@dataclass(slots=True)
class RuntimeObservation:
    """internal-only typed observation seam
    with PRECISE retry evidence AND typed execution-stage evidence.

    A mutable container written by :func:`run_reading_record_ask` at key
    instrumentation points and read by the test harness on BOTH the
    success path and the exception path. Production callers pass
    ``None`` (default) — no observation is recorded, no overhead.

    Design choice (Design-it-twice):

    - Design A (callable ``baseline_observer: Callable[[BaselineAgentContext], None]``):
      Pro — explicit callback; Con — only captures baseline, needs a
      separate mechanism for retry evidence; callback failure requires
      explicit try/except handling.
    - Design B (selected — mutable container): Pro — captures baseline
      AND precise retry evidence in one seam; no failure path (plain
      dataclass field writes cannot raise); each call creates its own
      container so concurrency-safe by construction; the run result is
      structurally unaffected because the runtime never READS from the
      container.
    - Design B was selected because it satisfies (baseline
      capture) and (precise retry evidence) with a single
      internal-only seam, and the "observer failure must not silently
      change the answer" requirement is trivially satisfied: there is
      no callback to fail.

    NOT part of any public DTO, SSE, Web, database, or persistence
    contract. Never serialised. Never returned from
    :func:`run_reading_record_ask` on the result object.

     The single ``output_validation_attempts`` counter
    has been SPLIT into two PRECISE counters:

    - ``output_validation_final_attempts``: incremented ONLY when the
      grounding validator is called in FINAL mode (``partial_output=False``).
      Partial-mode calls do NOT touch this counter. This counts how
      many times the final output validator was invoked, regardless of
      whether each call raised ``ModelRetry`` or passed.
    - ``output_validation_retry_requests``: incremented ONLY when the
      grounding validator in FINAL mode actually RAISES
      :class:`pydantic_ai.exceptions.ModelRetry`. A normal pass does
      NOT increment this counter. This is the precise "retry request"
      count — it proves the model was asked to retry.

    The taxonomy classifier (:func:`_classify_exception_safe_code`)
    requires BOTH counters to equal ``DEFAULT_OUTPUT_RETRIES + 1`` (3)
    to classify as ``output_retry_exhausted``. This prevents
    mis-classification when:

    - The validator was called 3 times but only 2 raised ModelRetry
      (the 3rd passed, but a subsequent non-validator UMB occurred).
    - The validator was called fewer than 3 times (pydantic-ai
      internal error before retry budget exhausted).
    - Partial-mode calls inflated the old single counter.

     Issue #1: typed execution-stage state machine. The
    ``execution_stage`` field is written by the runtime at each
    transition point and read by the harness classifier to distinguish
    ``ValidationError`` raised DURING ``agent.run`` (where the output
    validator fires) from ``ValidationError`` raised AFTER ``agent.run``
    returned (e.g. in the finalizer). The full state machine:

    1. ``baseline_assembly`` — set BEFORE ``assemble_baseline()`` runs.
       A ``ValidationError`` raised here is conservatively classified
       as ``runtime_exception`` (NOT ``agent_output_invalid``) because
       it did not come from the output validator.
    2. ``agent_run`` — the broad agent loop outside the final-mode
       grounding validator. Errors in this broad stage are handled
       conservatively. The validator temporarily narrows the stage to
       ``output_validation`` while its final-mode body executes.
       Only that nested stage may classify ``agent_output_invalid``.
    3. ``agent_run_completed`` — set AFTER ``agent.run()`` returns
       successfully and BEFORE the finalizer starts. A
       ``ValidationError`` raised AFTER this transition did NOT come
       from the output validator — it came from the finalizer or later
       code, and MUST be classified as ``runtime_exception``.
    4. ``finalizer`` — set BEFORE ``finalize_agent_answer()`` is
       awaited. A ``ValidationError`` raised here is conservatively
       classified as ``runtime_exception``.

    The classifier (:func:`_classify_exception_safe_code`) only returns
    ``agent_output_invalid`` when ``execution_stage == "output_validation"``
    AND the exception is a ``ValidationError``. Any other
    ``execution_stage`` value (or ``None``, e.g. legacy observation
    container or assembly-phase failure) falls back to
    ``runtime_exception`` — fail-closed. The classifier does NOT parse
    exception text.

    Fields:
        baseline_context: the :class:`BaselineAgentContext` produced by
            :meth:`BaselineContextAssembler.assemble_baseline`. ``None``
            when assembly has not run yet or raised. Written by the
            runtime immediately after ``assemble_baseline()`` succeeds,
            BEFORE the ``is_injected`` check — so both ``captured``
            and ``unavailable`` states are observable. When assembly
            raises, this stays ``None`` (``failed`` state).
        output_validation_final_attempts: number of times the grounding
            output validator was invoked in FINAL mode. ``0`` when the
            agent didn't run, the validator was never reached, or only
            partial-mode calls occurred. Incremented by
            :func:`grounding_validator` BEFORE any final-mode
            validation logic.
        output_validation_retry_requests: number of times the grounding
            output validator in FINAL mode raised
            :class:`ModelRetry`. ``0`` when the validator passed on
            every call or was never reached. Incremented by
            :func:`grounding_validator` via a try/except wrapper
            around ALL final-mode validation branches — covers every
            raise site without scattering increments.
        execution_stage: typed execution-stage evidence.
            ``None`` until the runtime writes the first transition
            (legacy / pre-agent-run). The harness reads this on the
            exception path to disambiguate ``ValidationError`` source.
            See the state-machine section above.
    """

    baseline_context: BaselineAgentContext | None = None
    output_validation_final_attempts: int = 0
    output_validation_retry_requests: int = 0
    validated_artifacts_published: int = 0
    validated_artifacts_consumed: int = 0
    agent_event_topology: list[str] = field(default_factory=list)
    output_validation_object_ids: list[int] = field(default_factory=list)
    validated_artifact_object_ids: list[int] = field(default_factory=list)
    transport_final_output_object_id: int | None = None
    # Issue #1: typed execution-stage evidence. Written by
    # the runtime at each transition point (see class docstring). Read
    # by the harness classifier on the exception path. ``None`` means
    # either a legacy observation container or an assembly-phase
    # failure — the classifier treats both as ``runtime_exception``.
    execution_stage: ExecutionStage | None = None


@dataclass(slots=True)
class ReaderRecordAskDeps:
    """Server-owned dependencies injected into every tool call.

    The model never supplies these fields; they come only from the runtime.
    """

    envelope: ReadingRecordAskContextEnvelope
    document_access: DocumentAccess
    fence: FenceFn
    evidence_registry: EvidenceRegistry
    confirmed_article_scopes: frozenset[ArticleScope] = frozenset()
    article_rag: ArticleRagSearchPort | None = None
    read_range_calls: int = 0
    max_read_range_calls: int = DEFAULT_MAX_READ_RANGE_CALLS
    search_current_article_calls: int = 0
    max_search_current_article_calls: int = DEFAULT_MAX_SEARCH_CURRENT_ARTICLE_CALLS
    events: list[RuntimeEvent] = field(default_factory=list)
    event_sink: RuntimeEventSink | None = None
    # 2: internal-only observation seam. ``None`` in
    # production and in tests that do not opt into observation. When
    # non-None, the runtime writes ``baseline_context`` after assembly
    # and the grounding output_validator increments
    # ``output_validation_final_attempts`` (on every final-mode call)
    # and ``output_validation_retry_requests`` (only when ModelRetry is
    # raised in final mode). The harness reads all three fields on the
    # success path and the exception path. Never serialised, never
    # enters public DTO or persistence.
    observation: RuntimeObservation | None = None
    # Production turn coordinator (expand / RAG tool paths).
    # ``None`` only for legacy offline tests that never call tools.
    turn_coordinator: TurnCoordinator | None = None
    # G0-b7: web search capability + port + registry + call counter.
    # ``web_search_capability`` is the resolved execution truth for one
    # turn; the runtime reads ``enabled_for_turn`` to decide whether to
    # mount the ``search_web`` tool (G1-b4). The model never reads this.
    # ``web_search_backend`` is the provider-neutral port; ``None`` means
    # the capability is disabled even when ``enabled_for_turn=True``
    # (defensive fail-soft — the search_web tool returns ``unavailable``).
    # ``web_evidence_registry`` is the in-turn web evidence registry
    # used by the finalizer (G0-b3) to resolve web handles to public
    # citations. ``web_search_calls`` / ``max_web_search_calls`` mirror
    # the RAG call counters for observability / RunFinishedEvent.
    web_search_capability: ResolvedWebSearchCapability | None = None
    web_search_backend: WebSearchBackend | None = None
    web_evidence_registry: WebEvidenceRegistry | None = None
    web_search_calls: int = 0
    max_web_search_calls: int = WEB_MAX_CALLS_PER_TURN
    _validated_answer_blocks: ValidatedAnswerBlocks | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def publish_validated_answer_blocks(
        self,
        validated: ValidatedAnswerBlocks,
    ) -> None:
        """Publish the single accepted grounding artifact for this run."""

        if self._validated_answer_blocks is not None:
            raise RuntimeError("grounding validation artifact already published")
        self._validated_answer_blocks = validated
        if self.observation is not None:
            self.observation.validated_artifacts_published += 1

    def consume_validated_answer_blocks(self) -> ValidatedAnswerBlocks | None:
        """Consume the accepted grounding artifact at most once."""

        validated = self._validated_answer_blocks
        self._validated_answer_blocks = None
        if validated is not None and self.observation is not None:
            self.observation.validated_artifacts_consumed += 1
        return validated

    def emit_event(self, event: RuntimeEvent) -> None:
        """Append an internal event and optionally notify a live sink.

        The sink must never raise into tool execution. Production stream uses
        it for concurrent progress projection; tests can leave it unset.
        """
        self.events.append(event)
        sink = self.event_sink
        if sink is None:
            return
        try:
            sink(event)
        except Exception:  # noqa: BLE001 — observation must not break the agent
            return
