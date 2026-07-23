"""Process-scoped ExpansionPointerLedger owner (R4-A5-7).

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
  tokens. When exceeded, the oldest half of tokens is dropped. After
  eviction, a previously known pointer may surface as
  ``invalid_cursor`` rather than ``stale_evidence``; that is an accepted
  degradation, not a cross-process guarantee.

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
# Insertion-ordered token index for capacity eviction (token -> None).
_token_order: OrderedDict[str, None] = OrderedDict()


class CapacityAwarePointerLedger(ExpansionPointerLedger):
    """Ledger that records token order for process-owner capacity eviction.

    Public behavior matches :class:`ExpansionPointerLedger`; capacity is
    enforced by the owner after mutations, not inside expand paths.
    """

    def issue(self, **kwargs):  # type: ignore[no-untyped-def]
        receipt = super().issue(**kwargs)
        token = kwargs.get("token")
        if isinstance(token, str):
            _remember_token(token)
        return receipt

    def transition_pointers(self, **kwargs):  # type: ignore[no-untyped-def]
        receipt = super().transition_pointers(**kwargs)
        issued = receipt.issued_token
        if isinstance(issued, str):
            _remember_token(issued)
        return receipt


def _remember_token(token: str) -> None:
    with _lock:
        if token in _token_order:
            _token_order.move_to_end(token)
        else:
            _token_order[token] = None
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
            old, _ = _token_order.popitem(last=False)
            # Public retention seam only — never touch private _records.
            _process_ledger.discard_token_for_capacity(old)


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
