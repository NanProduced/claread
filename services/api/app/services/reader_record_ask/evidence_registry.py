"""In-turn evidence observation registry for Reading Record Ask.

Tool executors mint and register :class:`ServerEvidenceObservation` entries.
The model only receives :class:`EvidenceHandleRef` values.  Finalizer
resolution is deferred to a later slice.

The registry is bound to a single envelope fingerprint at construction time.
Observations from any other envelope are rejected — first-line defence
against cross-turn / cross-generation registry reuse.
"""

from __future__ import annotations

import re

from app.services.reader_record_ask.evidence import (
    EvidenceHandleRef,
    ServerEvidenceObservation,
)

_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class EvidenceRegistry:
    """Server-side observation registry for one agent run / turn.

    Parameters
    ----------
    envelope_fingerprint:
        Fingerprint of the turn envelope.  Every registered observation's
        ``handle.envelope_fingerprint`` must match this value.
    """

    def __init__(self, envelope_fingerprint: str) -> None:
        if not _FINGERPRINT_PATTERN.match(envelope_fingerprint):
            raise ValueError(
                "envelope_fingerprint must be a 64-char lowercase hex SHA-256 digest"
            )
        self._envelope_fingerprint = envelope_fingerprint
        self._observations: dict[str, ServerEvidenceObservation] = {}

    @property
    def envelope_fingerprint(self) -> str:
        return self._envelope_fingerprint

    def register(self, observation: ServerEvidenceObservation) -> EvidenceHandleRef:
        handle_fp = observation.handle.envelope_fingerprint
        if handle_fp != self._envelope_fingerprint:
            raise ValueError(
                "observation envelope_fingerprint does not match registry binding: "
                f"observation={handle_fp}, registry={self._envelope_fingerprint}"
            )
        handle_id = observation.handle.handle_id
        if handle_id in self._observations:
            raise ValueError(f"duplicate evidence handle_id: {handle_id}")
        self._observations[handle_id] = observation
        return EvidenceHandleRef(handle_id=handle_id)

    def get(self, handle_id: str) -> ServerEvidenceObservation | None:
        return self._observations.get(handle_id)

    def list_observations(self) -> tuple[ServerEvidenceObservation, ...]:
        return tuple(self._observations.values())

    def list_handle_refs(self) -> tuple[EvidenceHandleRef, ...]:
        return tuple(
            EvidenceHandleRef(handle_id=handle_id)
            for handle_id in self._observations
        )

    def __len__(self) -> int:
        return len(self._observations)
