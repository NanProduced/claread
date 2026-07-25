"""Reading Record Ask — contracts, independent agent runtime, route facade.

Contract modules (round 1):
- ``context_envelope`` / ``tool_contracts`` / ``evidence``

Independent runtime (round 2+):
- document access, read_range, initial_anchor, agent, finalizer
- Article RAG port/adapter + ``search_current_article`` (round 3)
- Baseline context assembler + ``article_seed`` evidence (round 4-A1)

The production HTTP path still goes through ``service`` → ``ask_runtime``
and is **not** connected to the new agent loop yet.
"""

from app.services.reader_record_ask.article_rag_port import (
    ALLOWED_ASK_RAG_SOURCE_SCOPES,
    ArticleRagHitView,
    ArticleRagSearchOutcome,
    FakeArticleRagSearchPort,
)
from app.services.reader_record_ask.baseline_context import (
    BaselineAgentContext,
    BaselineContextAssembler,
    BaselineStatus,
    ModelContextChunk,
)
from app.services.reader_record_ask.context_envelope import (
    ENVELOPE_VERSION,
    SERVER_OWNED_SCOPE_FIELDS,
    AgentInitialSelectionLocator,
    EnvelopeCapabilityState,
    EnvelopeInitialAnchor,
    EnvelopeVisibleRange,
    ReadingRecordAskAgentContextProjection,
    ReadingRecordAskContextEnvelope,
    VerifiedEnvelopeInput,
    build_context_envelope,
    compute_envelope_fingerprint,
)
from app.services.reader_record_ask.document_access import (
    AnchorSegmentView,
    DocumentScopeSnapshot,
    InMemoryDocumentAccess,
    ReadingUnitView,
    build_document_scope,
    scope_identity_mismatch_reason,
)
from app.services.reader_record_ask.evidence import (
    LEGAL_EVIDENCE_KIND_SOURCE,
    ArticleRagCitationEvidence,
    EvidenceHandleRef,
    EvidenceKind,
    EvidenceOrigin,
    EvidenceSourceTool,
    ServerEvidenceHandle,
    ServerEvidenceObservation,
    assert_legal_evidence_kind_source,
    build_server_evidence_observation,
    is_valid_evidence_handle_id,
    mint_evidence_handle_id,
    mint_server_evidence_handle,
    parse_evidence_handle_ref,
)
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.fence import (
    FenceCheckResult,
    SequenceGenerationFence,
    StaticGenerationFence,
)
from app.services.reader_record_ask.finalizer import (
    FinalizedAskResult,
    PublicAnswerBlock,
    PublicCitation,
    finalize_agent_answer,
)
from app.services.reader_record_ask.initial_anchor_evidence import (
    register_initial_anchor_evidence,
)
from app.services.reader_record_ask.read_range_executor import (
    DEFAULT_MAX_READ_RANGE_CALLS,
    MAX_UNIT_ORDER_SPAN_UNITS,
    MAX_UNIT_ORDER_SPAN_WIDTH,
    SERVER_READ_RANGE_MAX_CHARS,
    effective_max_chars,
    execute_read_range,
)
from app.services.reader_record_ask.runtime import (
    ReadingRecordAskRunResult,
    run_reading_record_ask,
)
from app.services.reader_record_ask.search_current_article_executor import (
    DEFAULT_MAX_SEARCH_CURRENT_ARTICLE_CALLS,
    execute_search_current_article,
)
from app.services.reader_record_ask.tool_contracts import (
    READ_RANGE_OFFSET_UNIT,
    TOOL_READ_RANGE,
    TOOL_SEARCH_CURRENT_ARTICLE,
    ReaderRecordAskToolResult,
    ReaderRecordAskToolStatus,
    ReadRangeLocator,
    ReadRangeToolInput,
    SearchCurrentArticleToolInput,
    assert_no_server_owned_fields,
)

__all__ = [
    "ALLOWED_ASK_RAG_SOURCE_SCOPES",
    "AgentInitialSelectionLocator",
    "AnchorSegmentView",
    "ArticleRagCitationEvidence",
    "ArticleRagHitView",
    "ArticleRagSearchOutcome",
    "BaselineAgentContext",
    "BaselineContextAssembler",
    "BaselineStatus",
    "DEFAULT_MAX_READ_RANGE_CALLS",
    "DEFAULT_MAX_SEARCH_CURRENT_ARTICLE_CALLS",
    "DocumentScopeSnapshot",
    "ENVELOPE_VERSION",
    "EvidenceHandleRef",
    "EvidenceKind",
    "EvidenceOrigin",
    "EvidenceRegistry",
    "EvidenceSourceTool",
    "EnvelopeCapabilityState",
    "EnvelopeInitialAnchor",
    "EnvelopeVisibleRange",
    "FakeArticleRagSearchPort",
    "FenceCheckResult",
    "FinalizedAskResult",
    "PublicAnswerBlock",
    "PublicCitation",
    "InMemoryDocumentAccess",
    "LEGAL_EVIDENCE_KIND_SOURCE",
    "MAX_UNIT_ORDER_SPAN_UNITS",
    "MAX_UNIT_ORDER_SPAN_WIDTH",
    "ModelContextChunk",
    "READ_RANGE_OFFSET_UNIT",
    "ReadRangeLocator",
    "ReadRangeToolInput",
    "ReadingRecordAskAgentContextProjection",
    "ReadingRecordAskContextEnvelope",
    "ReadingRecordAskRunResult",
    "ReadingUnitView",
    "ReaderRecordAskToolResult",
    "ReaderRecordAskToolStatus",
    "SERVER_OWNED_SCOPE_FIELDS",
    "SERVER_READ_RANGE_MAX_CHARS",
    "SearchCurrentArticleToolInput",
    "SequenceGenerationFence",
    "ServerEvidenceHandle",
    "ServerEvidenceObservation",
    "StaticGenerationFence",
    "TOOL_READ_RANGE",
    "TOOL_SEARCH_CURRENT_ARTICLE",
    "VerifiedEnvelopeInput",
    "assert_legal_evidence_kind_source",
    "assert_no_server_owned_fields",
    "build_context_envelope",
    "build_document_scope",
    "build_server_evidence_observation",
    "compute_envelope_fingerprint",
    "effective_max_chars",
    "execute_read_range",
    "execute_search_current_article",
    "finalize_agent_answer",
    "is_valid_evidence_handle_id",
    "mint_evidence_handle_id",
    "mint_server_evidence_handle",
    "parse_evidence_handle_ref",
    "register_initial_anchor_evidence",
    "run_reading_record_ask",
    "scope_identity_mismatch_reason",
]
