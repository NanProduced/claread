"""Reading Record Ask — new-path contracts and route service.

Public contract modules (this slice):

- ``context_envelope``: server-owned Context Envelope + agent projection
- ``tool_contracts``: schema-first ``read_range`` / ``search_current_article`` IO
- ``evidence``: server observation / evidence handle contracts

The production stream still goes through ``service`` → ask_runtime and is
**not** wired to these contracts yet.
"""

from app.services.reader_record_ask.context_envelope import (
    ENVELOPE_VERSION,
    SERVER_OWNED_SCOPE_FIELDS,
    EnvelopeCapabilityState,
    EnvelopeInitialAnchor,
    EnvelopeVisibleRange,
    ReadingRecordAskAgentContextProjection,
    ReadingRecordAskContextEnvelope,
    VerifiedEnvelopeInput,
    build_context_envelope,
    compute_envelope_fingerprint,
)
from app.services.reader_record_ask.evidence import (
    LEGAL_EVIDENCE_KIND_SOURCE,
    EvidenceHandleRef,
    ServerEvidenceHandle,
    ServerEvidenceObservation,
    assert_legal_evidence_kind_source,
    build_server_evidence_observation,
    is_valid_evidence_handle_id,
    mint_evidence_handle_id,
    mint_server_evidence_handle,
    parse_evidence_handle_ref,
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
    "ENVELOPE_VERSION",
    "LEGAL_EVIDENCE_KIND_SOURCE",
    "READ_RANGE_OFFSET_UNIT",
    "SERVER_OWNED_SCOPE_FIELDS",
    "EnvelopeCapabilityState",
    "EnvelopeInitialAnchor",
    "EnvelopeVisibleRange",
    "EvidenceHandleRef",
    "ReadRangeLocator",
    "ReadRangeToolInput",
    "ReadingRecordAskAgentContextProjection",
    "ReadingRecordAskContextEnvelope",
    "ReaderRecordAskToolResult",
    "ReaderRecordAskToolStatus",
    "SearchCurrentArticleToolInput",
    "ServerEvidenceHandle",
    "ServerEvidenceObservation",
    "TOOL_READ_RANGE",
    "TOOL_SEARCH_CURRENT_ARTICLE",
    "VerifiedEnvelopeInput",
    "assert_legal_evidence_kind_source",
    "assert_no_server_owned_fields",
    "build_context_envelope",
    "build_server_evidence_observation",
    "compute_envelope_fingerprint",
    "is_valid_evidence_handle_id",
    "mint_evidence_handle_id",
    "mint_server_evidence_handle",
    "parse_evidence_handle_ref",
]
