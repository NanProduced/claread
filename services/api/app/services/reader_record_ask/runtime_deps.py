"""Deps object for the Reading Record Ask agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field

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
from app.services.reader_record_ask.runtime_events import RuntimeEvent
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
