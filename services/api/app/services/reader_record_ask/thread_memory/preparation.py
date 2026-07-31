"""Host-owned preparation of a thread-memory snapshot for model injection.

This module is the single seam for the security-sensitive ordering contract:

1. validate snapshot facts and materialize every referenced binding from the
   Host map;
2. run the live identity fence over those materialized Host bindings;
3. apply the fresh fence results and revalidate the final snapshot.

Callers must never fence ``episode.source_bindings`` before Host
materialization.  A model-authored or stale snapshot may omit that list while
facts still reference real Host binding IDs.
"""

from __future__ import annotations

from typing import Any

from app.services.reader_record_ask.thread_memory.allowlist import (
    validate_snapshot,
)
from app.services.reader_record_ask.thread_memory.fence import (
    check_all_bindings,
)
from app.services.reader_record_ask.thread_memory.schema import (
    SourceBinding,
    ThreadMemorySnapshot,
)


async def prepare_snapshot_for_model(
    snapshot: ThreadMemorySnapshot,
    *,
    host_bindings: dict[str, SourceBinding],
    allowlist: set[str],
    fence_context: dict[str, Any],
) -> tuple[ThreadMemorySnapshot, dict[str, Any]]:
    """Return a Host-materialized, freshly fenced snapshot.

    ``host_bindings`` is the only source of binding truth.  The first
    validation pass rejects tampering, filters unsupported facts, and
    materializes the Host bindings referenced by the surviving facts.  Only
    then is the live fence run.  The second pass writes those fresh validity
    results into the final snapshot.

    Fence exceptions deliberately propagate.  The outer Ask pipeline treats
    them as a fail-soft "no memory" outcome and must not reuse stale validity
    markers from storage.
    """

    materialized, materialize_metrics = validate_snapshot(
        snapshot,
        host_bindings,
        allowlist,
        fence_results=None,
    )
    if materialize_metrics.get("rejected"):
        return materialized, materialize_metrics

    binding_by_id: dict[str, SourceBinding] = {}
    for episode in materialized.episodes:
        for binding in episode.source_bindings:
            binding_by_id[binding.binding_id] = binding

    if not binding_by_id:
        return materialized, materialize_metrics

    checked = await check_all_bindings(
        [binding_by_id[binding_id] for binding_id in sorted(binding_by_id)],
        context=fence_context,
    )
    fence_results = {
        binding.binding_id: binding.validity_check
        for binding in checked
        if isinstance(binding.validity_check, dict)
    }
    prepared, final_metrics = validate_snapshot(
        materialized,
        host_bindings,
        allowlist,
        fence_results=fence_results,
    )
    if final_metrics.get("rejected"):
        return prepared, final_metrics

    # Preserve first-pass audit counts.  The second pass should normally be a
    # pure validity materialization, but merging keeps diagnostics honest if
    # future validation adds another deterministic filter.
    merged_metrics = dict(materialize_metrics)
    for key in ("total_facts", "stripped_facts", "binding_violation"):
        merged_metrics[key] = max(
            int(materialize_metrics.get(key) or 0),
            int(final_metrics.get(key) or 0),
        )
    merged_metrics["rejected"] = False
    merged_metrics["reject_reason"] = None
    return prepared, merged_metrics


__all__ = ["prepare_snapshot_for_model"]
