"""Deps object for the Reading Record Ask agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.reader_record_ask.answer_correctness_policy import (
    AnswerCorrectnessPolicy,
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


@dataclass(slots=True)
class ReaderRecordAskDeps:
    """Server-owned dependencies injected into every tool call.

    The model never supplies these fields; they come only from the runtime.
    """

    envelope: ReadingRecordAskContextEnvelope
    document_access: DocumentAccess
    fence: FenceFn
    evidence_registry: EvidenceRegistry
    article_rag: ArticleRagSearchPort | None = None
    read_range_calls: int = 0
    max_read_range_calls: int = DEFAULT_MAX_READ_RANGE_CALLS
    search_current_article_calls: int = 0
    max_search_current_article_calls: int = DEFAULT_MAX_SEARCH_CURRENT_ARTICLE_CALLS
    events: list[RuntimeEvent] = field(default_factory=list)
    event_sink: RuntimeEventSink | None = None
    # Set by runtime after baseline assembly. The grounding output_validator
    # reads this to decide whether ``response_kind="unavailable"`` is
    # permitted (only allowed when baseline is NOT available). Internal-only;
    # never serialised, never enters public DTO or persistence.
    baseline_available: bool = False
    # R4-A4-1B: write-once — set by runtime after baseline assembly, read
    # only by grounding_validator. ``None`` on the fail-closed path (baseline
    # not injected) and in tests that do not exercise the policy. Internal-only;
    # never serialised, never enters public DTO or persistence.
    answer_correctness_policy: AnswerCorrectnessPolicy | None = None

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
