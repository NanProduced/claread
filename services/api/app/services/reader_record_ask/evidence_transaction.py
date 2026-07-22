"""Host-only compensation seam shared by Ask model-view transactions (R4-A5-3).

Selection inject (A5-2) and evidence expand (A5-3) commit one charged
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

from app.services.reader_record_ask.evidence import ServerEvidenceObservation
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.model_view_budget import (
    BudgetAccountName,
    ModelVisibleTurnBudget,
)

__all__ = ["rollback_charged_observation"]


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
