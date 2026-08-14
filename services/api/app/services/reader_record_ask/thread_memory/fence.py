"""Provenance fence re-check for thread memory source bindings.

Implements §8.1 article claim validity rules:

    valid(binding) ⟺ reading_record 仍存在
                    ∧ stable_document_id 仍可解析
                    ∧ base_id 与当前 base 一致
                    ∧ record_generation 与当前 generation 一致

H6 boundary: ``validity_check`` is computed by the Host (this module).
The compactor (model or emergency) NEVER touches
``InternalCitationBinding`` raw fields and never computes validity —
it only consumes Host-pre-derived :class:`SourceBinding` instances.

On failure, the binding's ``validity_check.status`` flips to
``'invalid'`` with a typed ``invalidation_reason``. The associated
fact is NOT stripped here (episodes are immutable in spirit); the
caller renders the fact as ``prior_mention`` at injection time and
drops it from ``citation_ids`` (§8.1).

Web bindings have no article fence — they are always 'unchecked' here
and rely on the upstream ``degrade_web_citation_to_hint`` path (H7).
"""

from __future__ import annotations

from typing import Any, Literal

from app.services.reader_record_ask.thread_memory.schema import SourceBinding

InvalidationReason = Literal[
    "generation_changed",
    "generation_missing",
    "base_changed",
    "base_missing",
    "document_missing",
    "record_missing",
]


def _fence_values(binding: SourceBinding) -> dict[str, Any]:
    fv = binding.fence_values or {}
    return fv if isinstance(fv, dict) else {}


def check_binding_validity(
    binding: SourceBinding,
    *,
    reading_record_id: str,
    current_generation: int,
    current_base_id: str,
) -> SourceBinding:
    """Re-validate one binding against the live fence (§8.1).

    ``reading_record_id`` is the live record id; if the record was
    deleted upstream, the caller passes ``record_missing=True`` via a
    separate sentinel (we treat empty ``reading_record_id`` as missing).

    Returns a NEW :class:`SourceBinding` (frozen model, ``model_copy``
    with update) carrying the updated ``validity_check``. Web bindings
    skip the article fence and are returned with ``status='unchecked'``.
    """
    if binding.source_type != "article":
        return binding.model_copy(
            update={
                "validity_check": {
                    "status": "unchecked",
                    "last_validated_turn": binding.validity_check.get(
                        "last_validated_turn", 0
                    )
                    if isinstance(binding.validity_check, dict)
                    else 0,
                }
            }
        )

    fence = _fence_values(binding)
    binding_record_id = str(fence.get("reading_record_id") or "")
    binding_stable = str(fence.get("stable_document_id") or "")
    binding_base = str(fence.get("base_id") or "")
    binding_gen_raw = fence.get("record_generation")

    # Article binding 缺少任一必需 fence 值（record、stable
    # document、base、generation）即 invalid。此前 record_id / base /
    # generation 缺失时被静默跳过，可能把不完整 binding 当作有效。
    # record_missing: live record absent OR binding has no record_id
    # OR binding's record_id does not match live.
    if not reading_record_id or not binding_record_id:
        return _invalidate(binding, reason="record_missing")
    if binding_record_id != reading_record_id:
        return _invalidate(binding, reason="record_missing")

    # document_missing: binding has no stable_document_id pointer
    if not binding_stable:
        return _invalidate(binding, reason="document_missing")

    # base_missing: binding has no base_id
    if not binding_base:
        return _invalidate(binding, reason="base_missing")

    # base_changed: live base differs from binding's stored base
    if current_base_id and binding_base != current_base_id:
        return _invalidate(binding, reason="base_changed")

    # generation_missing: binding has no record_generation
    if binding_gen_raw is None:
        return _invalidate(binding, reason="generation_missing")

    # generation_changed: live generation differs from binding's stored
    try:
        binding_gen = int(binding_gen_raw)
    except (TypeError, ValueError):
        return _invalidate(binding, reason="generation_missing")
    if binding_gen != current_generation:
        return _invalidate(binding, reason="generation_changed")

    # All four fence checks passed.
    last_validated = (
        binding.validity_check.get("last_validated_turn", 0)
        if isinstance(binding.validity_check, dict)
        else 0
    )
    return binding.model_copy(
        update={
            "validity_check": {
                "status": "valid",
                "last_validated_turn": last_validated,
            }
        }
    )


def _invalidate(
    binding: SourceBinding, *, reason: InvalidationReason
) -> SourceBinding:
    last_validated = (
        binding.validity_check.get("last_validated_turn", 0)
        if isinstance(binding.validity_check, dict)
        else 0
    )
    return binding.model_copy(
        update={
            "validity_check": {
                "status": "invalid",
                "last_validated_turn": last_validated,
                "invalidation_reason": reason,
            }
        }
    )


async def check_all_bindings(
    bindings: list[SourceBinding],
    context: dict[str, Any],
) -> list[SourceBinding]:
    """Batch-validate bindings against one shared live-fence context.

    ``context`` shape::

        {
            "reading_record_id": str,
            "current_generation": int,
            "current_base_id": str,
        }

    Returns a new list of SourceBindings with updated
    ``validity_check``. Fence-invalidated bindings stay in the list —
    episode immutability forbids deletion. Fact降级 to
    ``prior_mention`` happens at render time (§4.2(d) step 7).

     if this function raises, the caller MUST NOT reuse the
    old bindings (they may carry a stale ``validity_check='valid'``).
    The caller must skip memory injection entirely — return ``None``
    so the Ask pipeline continues without memory. This function does
    NOT swallow exceptions; it lets them propagate so the caller can
    distinguish "fence ran and returned invalid bindings" from "fence
    crashed and we have no validity information at all".
    """
    reading_record_id = str(context.get("reading_record_id") or "")
    current_generation_raw = context.get("current_generation")
    try:
        current_generation = (
            int(current_generation_raw) if current_generation_raw is not None else 0
        )
    except (TypeError, ValueError):
        current_generation = 0
    current_base_id = str(context.get("current_base_id") or "")

    return [
        check_binding_validity(
            binding,
            reading_record_id=reading_record_id,
            current_generation=current_generation,
            current_base_id=current_base_id,
        )
        for binding in bindings
    ]
