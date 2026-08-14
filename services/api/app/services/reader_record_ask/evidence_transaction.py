"""Host-only compensation seam shared by Ask model-view transactions.

Selection inject and evidence expand commit one charged
observation through the same two mutable host stores:

1. :class:`EvidenceRegistry` — the turn's observation set;
2. :class:`ModelVisibleTurnBudget` — the six-account char ledger.

Both transactions need the identical post-charge rollback:

- conditionally discard **only this transaction's** registry entry
  (:meth:`EvidenceRegistry.discard_if_matches` — never foreign entries);
- refund this transaction's charge to its budget account
  (:meth:`ModelVisibleTurnBudget._refund_chars` — private integer refund,
  the established host-only compensation path; never a public debit).

This module is that single compensation implementation, parameterized by
budget account and failure-domain code prefix. It exists so selection and
expand cannot drift into two separately maintained rollback logics.

Fail-closed contract
--------------------
If compensation cannot be proven complete (registry discard raised, left a
mismatching foreign entry, left this observation behind, or the budget
refund failed), raise a ``RuntimeError`` whose message is exactly::

    {failure_domain}_rollback_failed code={stable_code}

Stable codes: ``registry_and_budget``, ``registry_discard``,
``registry_mismatch_and_budget``, ``registry_mismatch``,
``registry_residual_and_budget``, ``registry_residual``, ``budget_refund``.

The message never embeds observation bodies, snippets, object reprs, or
raw exception text. No model-retry control flow is ever raised.

Scope: offline compensation primitive only. No runtime / agent / SSE /
RAG / DB wiring.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.services.reader_record_ask.evidence import ServerEvidenceObservation
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.model_view_budget import (
    BudgetAccountName,
    ModelVisibleTurnBudget,
)

__all__ = [
    "compensate_ledger_transition_and_observation",
    "rollback_charged_observation",
    "rollback_charged_observations_batch",
]


def rollback_charged_observation(
    *,
    budget: ModelVisibleTurnBudget,
    account: BudgetAccountName,
    charge_cost: int,
    registry: EvidenceRegistry,
    observation: ServerEvidenceObservation,
    failure_domain: str,
) -> None:
    """Roll back one charged observation after a failed host transaction.

    Must be called **only** after ``budget.charge(account, view)`` succeeded
    for this attempt. Restores budget spend and removes this transaction's
    registry entry when still present and equal; pre-existing / foreign
    observations are never deleted.

    Parameters
    ----------
    failure_domain:
        Stable code prefix namespace, e.g. ``"selection_inject"`` or
        ``"expand_evidence"``. Incomplete compensation raises
        ``RuntimeError("{failure_domain}_rollback_failed code=...")``.
    """
    if not failure_domain or not isinstance(failure_domain, str):
        raise ValueError("failure_domain must be a non-empty string")
    prefix = f"{failure_domain}_rollback_failed code="
    handle_id = observation.handle.handle_id

    try:
        discard_outcome = registry.discard_if_matches(
            handle_id=handle_id,
            expected=observation,
        )
    except Exception:
        # Attempt budget refund anyway; still report dual failure without
        # chaining raw exception text that might carry probe payloads.
        try:
            budget._refund_chars(account, charge_cost)
        except Exception:
            raise RuntimeError(f"{prefix}registry_and_budget") from None
        raise RuntimeError(f"{prefix}registry_discard") from None

    if discard_outcome == "mismatch":
        # Foreign entry under our handle — must not delete; still refund budget.
        try:
            budget._refund_chars(account, charge_cost)
        except Exception:
            raise RuntimeError(
                f"{prefix}registry_mismatch_and_budget"
            ) from None
        raise RuntimeError(f"{prefix}registry_mismatch") from None

    # discarded | absent: no residual for *this* observation under handle_id.
    residual = registry.get(handle_id)
    if residual is not None and residual == observation:
        try:
            budget._refund_chars(account, charge_cost)
        except Exception:
            raise RuntimeError(
                f"{prefix}registry_residual_and_budget"
            ) from None
        raise RuntimeError(f"{prefix}registry_residual") from None

    try:
        budget._refund_chars(account, charge_cost)
    except Exception:
        raise RuntimeError(f"{prefix}budget_refund") from None


def _discard_one_observation(
    registry: EvidenceRegistry,
    observation: ServerEvidenceObservation,
) -> bool:
    """Conditional single-observation cleanup.

    Returns True iff the registry is proven clean of this observation:
    ``discard_if_matches`` reported discarded/absent **and** no residual
    equal to this observation remains. A mismatch (foreign entry under
    the handle) or any raise leaves the entry untouched and reports
    False — never deletes foreign state, never raises.
    """
    handle_id = observation.handle.handle_id
    try:
        discard_outcome = registry.discard_if_matches(
            handle_id=handle_id,
            expected=observation,
        )
    except Exception:
        return False
    if discard_outcome == "mismatch":
        return False
    try:
        residual = registry.get(handle_id)
    except Exception:
        return False
    return not (residual is not None and residual == observation)


def rollback_charged_observations_batch(
    *,
    budget: ModelVisibleTurnBudget,
    account: BudgetAccountName,
    charge_cost: int,
    registry: EvidenceRegistry,
    observations: Sequence[ServerEvidenceObservation],
    failure_domain: str,
) -> None:
    """Best-effort **complete** compensation for one charged transaction.

    Unlike :func:`rollback_charged_observation` (single observation,
    fail-closed on first problem), this seam is built for multi-
    observation transactions (e.g. the RAG ok path):

    - every attempted observation receives its conditional cleanup even
      if earlier cleanups returned mismatch / residual or raised — the
      loop never short-circuits, so later observations of the same
      transaction are never stranded;
    - foreign entries are never deleted (conditional discard only);
    - the charge is refunded exactly once, after ALL registry cleanup
      attempts (never per-observation cost slicing);
    - always raises one aggregate stable verdict code
      ``{failure_domain}_rollback_failed code=...``:

      ``batch_complete``
          all cleanups proven complete and the refund succeeded (the
          failed transaction left no provable residue);
      ``batch_partial``
          at least one cleanup could not be proven, refund succeeded;
      ``batch_refund``
          cleanups complete but the refund failed;
      ``batch_partial_and_refund``
          both unproven.

    The message never embeds bodies, reprs, handle ids, identity, or
    raw exception text.
    """
    registry_complete = True
    for observation in observations:
        if not _discard_one_observation(registry, observation):
            registry_complete = False

    refund_ok = True
    try:
        budget._refund_chars(account, charge_cost)
    except Exception:
        refund_ok = False

    prefix = f"{failure_domain}_rollback_failed code="
    if registry_complete and refund_ok:
        raise RuntimeError(f"{prefix}batch_complete") from None
    if registry_complete:
        raise RuntimeError(f"{prefix}batch_refund") from None
    if refund_ok:
        raise RuntimeError(f"{prefix}batch_partial") from None
    raise RuntimeError(f"{prefix}batch_partial_and_refund") from None


class LedgerTransitionRollbackLike(Protocol):
    """Structural seam: marker-scoped pointer transition rollback.

    Implemented by ``ExpansionPointerLedger``; typed here as a Protocol so
    this module never imports the expansion module (no cycle).
    """

    def rollback_transition_by_marker(self, marker: str) -> str: ...


def compensate_ledger_transition_and_observation(
    *,
    budget: ModelVisibleTurnBudget,
    account: BudgetAccountName,
    charge_cost: int,
    registry: EvidenceRegistry,
    observation: ServerEvidenceObservation,
    ledger: LedgerTransitionRollbackLike,
    marker: str,
    failure_domain: str,
) -> None:
    """Shared compensation after a failed ledger-transition commit.

    Used by selection-scope and map-scope expanders alike so the
    transition-failure semantics cannot drift between seams:

    1. marker-scoped ledger rollback — a rollback that returns incomplete
       **or raises** is treated as unproven (raw exception text is never
       propagated);
    2. shared charged-observation rollback (conditional registry discard +
       budget refund).

    Returns normally **only** when the ledger rollback is proven complete
    and registry+budget compensation succeeded — callers then raise their
    own stable transition-failure code. Raises
    ``{failure_domain}_rollback_failed code=ledger_transition`` when the
    ledger cannot be proven clean but registry+budget were compensated, or
    ``code=ledger_transition_and_registry`` when both are unproven.
    Never embeds body, repr, pointer, identity, or raw exception text.
    """
    try:
        rollback_status = ledger.rollback_transition_by_marker(marker)
    except Exception:
        rollback_status = "incomplete"
    ledger_complete = rollback_status == "rolled_back"
    try:
        rollback_charged_observation(
            budget=budget,
            account=account,
            charge_cost=charge_cost,
            registry=registry,
            observation=observation,
            failure_domain=failure_domain,
        )
    except RuntimeError:
        if not ledger_complete:
            raise RuntimeError(
                f"{failure_domain}_rollback_failed "
                "code=ledger_transition_and_registry"
            ) from None
        raise
    if not ledger_complete:
        raise RuntimeError(
            f"{failure_domain}_rollback_failed code=ledger_transition"
        ) from None
