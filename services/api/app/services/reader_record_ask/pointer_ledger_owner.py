"""Process-scoped ExpansionPointerLedger owner (R4-A5-7 / A5-7R2 / R3).

Production default obtains one shared :class:`ExpansionPointerLedger`
instance for the process so a new turn can **recognize** pointers minted
under an earlier turn and answer with metered ``stale_evidence`` instead
of ``invalid_cursor``.

Retention / capacity boundary (explicit, non-persistent)
--------------------------------------------------------
- **Scope**: single OS process only. There is **no** cross-worker or
  cross-host sharing and **no** durable store.
- **Lifetime**: entries live until process exit, optional capacity
  eviction, or an explicit test reset. Process restart clears all
  knowledge — expired/unknown pointers safely degrade to
  ``invalid_cursor`` (never falsely claimed as cross-process stale).
- **Capacity**: soft cap ``DEFAULT_LEDGER_CAPACITY`` on distinct pointer
  tokens. The capacity queue stores ``token → expected_issue_marker``
  (the marker remembered at issuance). Eviction calls
  :meth:`ExpansionPointerLedger.discard_token_for_capacity` with that
  pair: only matching issue markers delete ledger state; a **mismatch**
  (foreign marker now owns the token) drops the local queue entry only
  and never deletes the current ledger record.

Transition capacity timing (A5-7R3)
-----------------------------------
:meth:`CapacityAwarePointerLedger.transition_pointers` **suppresses**
capacity registration and eviction for the whole base-class transition
(issue-new + consume-old). Only after a successful return does it
register the newly issued cursor once. A failed transition therefore
leaves no capacity-queue entry for the provisional cursor. Direct
:meth:`issue` still registers immediately (unchanged).

Tests may inject a shared fake/real ledger via
:func:`run_reading_record_ask` / coordinator constructor arguments and
must not rely on the process default when isolation is required.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

from app.services.reader_record_ask.evidence_expansion import (
    ExpansionPointerLedger,
)

# Soft capacity on distinct pointer tokens in the process-default ledger.
DEFAULT_LEDGER_CAPACITY: int = 4096

_lock = threading.RLock()
_process_ledger: ExpansionPointerLedger | None = None
# Insertion-ordered capacity queue: token → expected_issue_marker.
_token_order: OrderedDict[str, str] = OrderedDict()


class CapacityAwarePointerLedger(ExpansionPointerLedger):
    """Ledger that records token+marker for process-owner capacity eviction.

    Public behavior matches :class:`ExpansionPointerLedger` except that
    capacity tracking is deferred across
    :meth:`transition_pointers` (see module docstring).
    """

    def __init__(self) -> None:
        super().__init__()
        # When True, issue() must not touch the capacity queue (set for the
        # duration of transition_pointers so mid-transition issue cannot
        # capacity-evict the pointer about to be mark_consumed).
        self._suppress_capacity_tracking: bool = False

    def issue(self, **kwargs):  # type: ignore[no-untyped-def]
        receipt = super().issue(**kwargs)
        # Only track *this* write when not mid-transition. Idempotent
        # re-issue of a pre-existing same-binding token does not own
        # capacity (newly_issued=False).
        if (
            not self._suppress_capacity_tracking
            and receipt.newly_issued
            and isinstance(receipt.token, str)
        ):
            _remember_token(receipt.token, receipt.marker)
        return receipt

    def transition_pointers(self, **kwargs):  # type: ignore[no-untyped-def]
        """Run base transition with capacity tracking fully suppressed.

        Base ``transition_pointers`` may call ``self.issue`` then
        ``mark_consumed``. Registering capacity on that nested issue can
        capacity-evict the consume target before ``mark_consumed`` runs.
        Suppress for the whole call; on success only, register the new
        cursor once. On exception, leave no capacity-queue residue for
        the provisional issue (it was never remembered).
        """
        previous = self._suppress_capacity_tracking
        self._suppress_capacity_tracking = True
        try:
            receipt = super().transition_pointers(**kwargs)
        finally:
            self._suppress_capacity_tracking = previous
        # Reached only when the base transition returned successfully —
        # failed transitions never registered capacity for the provisional
        # issue (suppressed), so marker rollback leaves no queue residue.
        issued = receipt.issued_token
        marker = receipt.marker
        if isinstance(issued, str) and isinstance(marker, str):
            _remember_token(issued, marker)
        return receipt


def _remember_token(token: str, issue_marker: str) -> None:
    with _lock:
        if token in _token_order:
            _token_order.move_to_end(token)
            _token_order[token] = issue_marker
        else:
            _token_order[token] = issue_marker
        _enforce_capacity_unlocked()


def _enforce_capacity_unlocked() -> None:
    global _process_ledger
    if _process_ledger is None:
        return
    capacity = DEFAULT_LEDGER_CAPACITY
    while len(_token_order) > capacity:
        # Drop oldest half when over capacity (amortized).
        drop_n = max(1, len(_token_order) // 2)
        for _ in range(drop_n):
            if not _token_order:
                break
            old_token, expected_marker = _token_order.popitem(last=False)
            # Marker-gated public seam: mismatch forgets queue only.
            _process_ledger.discard_token_for_capacity(
                old_token, expected_marker
            )


def get_process_pointer_ledger() -> ExpansionPointerLedger:
    """Return the process-scoped production ledger (create on first use)."""
    global _process_ledger
    with _lock:
        if _process_ledger is None:
            _process_ledger = CapacityAwarePointerLedger()
            _token_order.clear()
        return _process_ledger


def reset_process_pointer_ledger_for_tests() -> None:
    """Drop the process ledger (tests only). Not used by production paths."""
    global _process_ledger
    with _lock:
        _process_ledger = None
        _token_order.clear()


__all__ = [
    "DEFAULT_LEDGER_CAPACITY",
    "CapacityAwarePointerLedger",
    "get_process_pointer_ledger",
    "reset_process_pointer_ledger_for_tests",
]
