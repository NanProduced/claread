"""In-turn Web evidence registry for Reading Record Ask (G1-b2).

Mirrors the article :class:`EvidenceRegistry` pattern but for
:class:`WebEvidence` entries produced by the host-owned ``search_web``
function tool (G1 vertical slice).

Key invariants
--------------
- Bound to a single envelope fingerprint at construction time. Web
  evidence from any other envelope is rejected — first-line defence
  against cross-turn / cross-generation registry reuse.
- ``internal_handle_id`` (``evh_`` shape) is the registry key. The
  model only receives this opaque token; all other WebEvidence fields
  are server-side registry material.
- ``source_fingerprint`` is recomputed from ``canonical_url`` +
  ``retrieved_at`` on every :meth:`get` read so provider text drift
  cannot silently replace a source. A mismatch raises ``ValueError``
  (fail-closed).
- ``provider_result_ref`` is internal-only and never exposed to the
  model surface.
"""

from __future__ import annotations

import re

from app.services.reader_record_ask.evidence import EvidenceHandleRef
from app.services.reader_record_ask.web_search_contracts import (
    WebEvidence,
    compute_web_source_fingerprint,
)

_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_HANDLE_ID_PATTERN = re.compile(r"^evh_[0-9a-f]{32}$")


class WebEvidenceRegistry:
    """Server-side web evidence registry for one agent run / turn.

    Parameters
    ----------
    envelope_fingerprint:
        Fingerprint of the turn envelope. Every registered
        :class:`WebEvidence` is bound to this fingerprint via the
        turn-scoped handle id space; cross-envelope reuse is rejected
        at the finalizer fence, not here.
    """

    def __init__(self, envelope_fingerprint: str) -> None:
        if not _FINGERPRINT_PATTERN.match(envelope_fingerprint):
            raise ValueError(
                "envelope_fingerprint must be a 64-char lowercase hex SHA-256 digest"
            )
        self._envelope_fingerprint = envelope_fingerprint
        self._evidence: dict[str, WebEvidence] = {}

    @property
    def envelope_fingerprint(self) -> str:
        return self._envelope_fingerprint

    def register(self, evidence: WebEvidence) -> EvidenceHandleRef:
        """Register one web evidence entry; return its opaque handle ref.

        Rejects duplicate handle ids. The handle id space is shared with
        the article :class:`EvidenceRegistry` (both use ``evh_<32 hex>``),
        so callers must mint distinct ids across both registries.
        """
        handle_id = evidence.internal_handle_id
        if not _HANDLE_ID_PATTERN.match(handle_id):
            raise ValueError(
                "internal_handle_id must be a server-minted token matching "
                "evh_<32 hex chars>"
            )
        if handle_id in self._evidence:
            raise ValueError(f"duplicate web evidence handle_id: {handle_id}")
        self._evidence[handle_id] = evidence
        return EvidenceHandleRef(handle_id=handle_id)

    def get(self, handle_id: str) -> WebEvidence | None:
        """Return the web evidence for ``handle_id``, re-verifying identity.

        Returns ``None`` when the handle is unknown. Raises ``ValueError``
        when the stored entry's ``source_fingerprint`` no longer matches
        ``canonical_url + retrieved_at`` — this is fail-closed defence
        against provider text drift replacing a source.
        """
        evidence = self._evidence.get(handle_id)
        if evidence is None:
            return None
        expected = compute_web_source_fingerprint(
            canonical_url=evidence.canonical_url,
            retrieved_at=evidence.retrieved_at,
        )
        if expected != evidence.source_fingerprint:
            raise ValueError(
                "web evidence source_fingerprint mismatch on read; "
                "provider text drift detected"
            )
        return evidence

    def list_evidence(self) -> tuple[WebEvidence, ...]:
        """Return all registered web evidence in insertion order."""
        return tuple(self._evidence.values())

    def list_handle_refs(self) -> tuple[EvidenceHandleRef, ...]:
        return tuple(
            EvidenceHandleRef(handle_id=handle_id)
            for handle_id in self._evidence
        )

    def __len__(self) -> int:
        return len(self._evidence)


__all__ = ["WebEvidenceRegistry"]
