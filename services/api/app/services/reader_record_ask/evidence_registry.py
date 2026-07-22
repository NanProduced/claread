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
from typing import Literal

from app.services.reader_record_ask.evidence import (
    EvidenceHandleRef,
    ServerEvidenceObservation,
)

_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# Result of :meth:`EvidenceRegistry.discard_if_matches` — narrow rollback seam.
DiscardMatchResult = Literal["discarded", "absent", "mismatch"]


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

    def discard_if_matches(
        self,
        *,
        handle_id: str,
        expected: ServerEvidenceObservation,
    ) -> DiscardMatchResult:
        """Conditionally discard one observation for transaction rollback.

        Deletes **only** when ``handle_id`` currently maps to an observation
        that equals ``expected`` (full frozen-model equality, including the
        handle identity embedded in the observation).

        Returns
        -------
        ``"discarded"``
            Entry removed; it matched ``expected``.
        ``"absent"``
            No entry under ``handle_id`` (register never wrote, or already gone).
        ``"mismatch"``
            An entry exists but is **not** ``expected`` — left untouched so
            pre-existing / foreign observations are never deleted.

        This is a narrow host-side compensation API for co-located Ask seams
        (selection inject). It is **not** a general public delete-by-handle
        capability.
        """
        if expected.handle.handle_id != handle_id:
            # Call-site must pass the observation's own handle — refuse broad
            # "delete any handle if some other observation equals".
            return "mismatch"
        current = self._observations.get(handle_id)
        if current is None:
            return "absent"
        if current != expected:
            return "mismatch"
        del self._observations[handle_id]
        return "discarded"

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
