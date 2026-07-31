"""Allowlist validation for thread memory snapshots.

Implements the Host side of R0.1 §4.2(d) ten-step algorithm. The model
compactor receives an allowlist at input-assembly time; this module
validates the model's output (or emergency output) against the same
allowlist. Bindings are never created here — they are Host-derived
upstream via ``derive_source_bindings`` (H6) — so any binding_id in the
snapshot that the Host did not pre-derive triggers whole-snapshot
rejection.

R1.6 P0-2: ``validate_snapshot`` now receives a Host binding **map**
(``binding_id -> SourceBinding``) instead of a bare id set. This lets
the Host detect tampering: a snapshot binding whose
source_type/source_id/fence_type/fence_values/validity_check differ
from the Host-derived canonical copy triggers whole-snapshot rejection.
After validation, episode bindings are back-filled from the Host map so
no model/snapshot-provided binding fields survive.

Steps implemented here:
  5. strict schema (Pydantic ``extra='forbid'`` does this at parse time)
  6. per-fact source_ids ⊆ ALLOW; article facts must reference a Host
     article binding
  7. fence re-check (caller pre-computes ``fence_results``)
  8. if stripped/total > 0.20 → whole-snapshot reject
  9. watermark CAS (caller compares via :func:`compute_watermark`)
  10. atomic write (caller's responsibility)
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.services.reader_record_ask.thread_memory.mapping import (
    derive_source_bindings,
)
from app.services.reader_record_ask.thread_memory.schema import (
    Episode,
    SourceBinding,
    StructuredFact,
    ThreadMemorySnapshot,
)

# R0.1 §4.2(d) step 8: if more than 20% of facts are stripped due to
# allowlist violation, the whole snapshot is rejected.
_ALLOWLIST_VIOLATION_REJECT_RATIO: float = 0.20


def build_allowlist(
    thread_messages: list[dict[str, Any]],
    ok_turn_runs: list[dict[str, Any]],
) -> set[str]:
    """Compute the Host allowlist (R0.1 §4.2(d) steps 1–3).

    ``ALLOW = A_msg ∪ A_cit ∪ A_bind`` where:
    - ``A_msg`` = every canonical message id in the thread (user + ok
      assistant)
    - ``A_cit`` = every public ``citation_id`` attached to ok assistant
      messages
    - ``A_bind`` = every Host-derived ``binding_id`` from ok turn runs'
      ``resolved_evidence_json`` / ``citation_bindings``

    Web-citation hints produced by ``degrade_web_citation_to_hint`` are
    deliberately NOT in ``A_bind`` (H7): degraded hints are free-text
    and cannot anchor citation-truth facts.
    """
    allow: set[str] = set()

    # A_msg
    for msg in thread_messages or []:
        if not isinstance(msg, dict):
            continue
        msg_id = str(msg.get("id") or msg.get("message_id") or "")
        if msg_id:
            allow.add(msg_id)

    # A_cit (caller is expected to pass canonical messages; emergency
    # also tolerates a public-citations shape). Both ``citations`` and
    # ``public_citations`` keys are inspected for resilience.
    for msg in thread_messages or []:
        if not isinstance(msg, dict):
            continue
        if str(msg.get("role") or "").lower() != "assistant":
            continue
        for cit in (
            msg.get("citations")
            or msg.get("public_citations")
            or []
        ):
            if not isinstance(cit, dict):
                continue
            cit_id = str(cit.get("citation_id") or "")
            if cit_id:
                allow.add(cit_id)

    # A_bind (Host-derived only)
    for run in ok_turn_runs or []:
        if not isinstance(run, dict):
            continue
        raw_bindings = (
            run.get("citation_bindings")
            or run.get("resolved_evidence_json")
            or []
        )
        if not isinstance(raw_bindings, list):
            continue
        for binding in derive_source_bindings(raw_bindings):
            allow.add(binding.binding_id)

    return allow


def build_host_bindings(
    ok_turn_runs: list[dict[str, Any]],
) -> dict[str, SourceBinding]:
    """R1.6 P0-2: derive the Host binding map from canonical ok turn runs.

    Returns ``{binding_id: SourceBinding}``. This map is the single
    source of truth for binding content — the compactor (model or
    emergency) NEVER has the power to create, modify, or reuse Host
    bindings. ``validate_snapshot`` uses this map to detect tampering.

    Only the LATEST canonical ok run per assistant message contributes
    bindings (R1.6 P0-3). The caller is responsible for passing only
    canonical ok runs (``list_ok_turn_runs_with_bindings`` already
    applies ``DISTINCT ON (message_id)``).
    """
    host_map: dict[str, SourceBinding] = {}
    for run in ok_turn_runs or []:
        if not isinstance(run, dict):
            continue
        raw_bindings = (
            run.get("citation_bindings")
            or run.get("resolved_evidence_json")
            or []
        )
        if not isinstance(raw_bindings, list):
            continue
        for binding in derive_source_bindings(raw_bindings):
            host_map[binding.binding_id] = binding
    return host_map


def _binding_matches_host(
    snapshot_binding: SourceBinding,
    host_binding: SourceBinding,
) -> bool:
    """R1.6 P0-2: check whether a snapshot binding matches the Host copy.

    Compares source_type, source_id, fence_type, and fence_values. Any
    mismatch → tampering detected → reject. The comparison is exact (no
    fuzzy matching) because Host bindings are deterministically derived
    from canonical evidence.

    R1.6.1 P0-2: ``validity_check`` is NOT compared. It is a runtime-
    computed field owned by the Host fence layer (fence.py), not a
    binding identity field. After fence runs, ``validity_check`` is
    updated (e.g. from ``'unchecked'`` to ``'invalid'``); comparing it
    against the Host's pre-fence ``'unchecked'`` would falsely trigger
    rejection. The model's ``validity_check`` is always overwritten by
    Host materialization + fence, so it cannot bypass fence by setting
    ``validity_check='valid'``.
    """
    return (
        snapshot_binding.source_type == host_binding.source_type
        and snapshot_binding.source_id == host_binding.source_id
        and snapshot_binding.fence_type == host_binding.fence_type
        and snapshot_binding.fence_values == host_binding.fence_values
    )


def _article_fact_has_host_article_binding(
    fact: StructuredFact, host_bindings: dict[str, SourceBinding]
) -> bool:
    """R0.1 §4.2(d) step 6 + R1.6 P0-2: article facts must reference a
    Host binding with ``source_type='article'``.

    A message id, web binding, or pseudo-binding cannot satisfy article
    provenance — only a Host-derived article binding can.
    """
    if not fact.source_ids:
        return False
    for sid in fact.source_ids:
        host_b = host_bindings.get(sid)
        if host_b is not None and host_b.source_type == "article":
            return True
    return False


def _materialize_host_bindings_for_episode(
    facts: list[StructuredFact],
    host_bindings: dict[str, SourceBinding],
    fence_results: dict[str, Any] | None,
) -> list[SourceBinding]:
    """R1.6.1 P0-2: materialize Host ``SourceBinding`` for an episode.

    The model/snapshot's ``source_bindings`` is NEVER the authority —
    only the Host map is. This function derives the authoritative
    binding list from the KEPT facts' ``source_ids ∩ host_bindings``:
      - Complete: every Host binding referenced by any kept fact is
        included.
      - Deduplicated: each ``binding_id`` appears at most once.
      - Stably sorted: by ``binding_id`` for determinism.
      - Host objects: the returned bindings are Host ``SourceBinding``
        instances (never the model's), with ``validity_check`` updated
        from ``fence_results`` when available.

    Bindings not referenced by any fact are excluded (R1.6.1 P0-2:
    "未被任何 fact 引用的 binding 不进入 episode").
    """
    fence_results = fence_results or {}
    referenced_ids: set[str] = set()
    for fact in facts:
        for sid in fact.source_ids:
            if sid in host_bindings:
                referenced_ids.add(sid)
    materialized: list[SourceBinding] = []
    for binding_id in sorted(referenced_ids):
        host_b = host_bindings[binding_id]
        fence_vc = fence_results.get(binding_id)
        if isinstance(fence_vc, dict):
            materialized.append(
                host_b.model_copy(update={"validity_check": fence_vc})
            )
        else:
            materialized.append(host_b)
    return materialized


# ---------------------------------------------------------------------------
# R1.6 P0-1: canonical revision watermark
# ---------------------------------------------------------------------------


def _message_revision_digest(message: dict[str, Any]) -> str:
    """Per-message revision digest (no raw content leaks).

    R1.6 P0-1: the digest is a SHA-256 hash — the raw text/query/provider
    payload is NEVER written to the watermark, logs, DTO, or DB. Only the
    final hash is retained.

    R1.6.1 P0-1 fixes:
      - Use ``canonical_turn_run_id`` (from repository LATERAL JOIN of the
        latest ok turn_run) instead of the message row's
        ``current_turn_run_id`` (which may point to a failed retry).
      - Replace the separator-based ``f"{run_id}|\\n".join(safe_texts)``
        with a structured JSON serialization. The old form lost the run_id
        when ``safe_texts`` had exactly one element (join only inserts the
        separator BETWEEN elements, so a single-element list produced just
        the text with no run_id).
      - The digest input now explicitly includes:
        (a) canonical_turn_run_id
        (b) ordered safe answer block texts
        (c) web_search outcome
        serialized as a deterministic JSON object.

    - **user**: SHA-256(content_md). Content changes (edits) → digest
      changes → watermark changes.
    - **assistant**: SHA-256(JSON of canonical_turn_run_id + safe-visible
      answer_blocks texts + web_search outcome). Successful regenerate →
      new canonical ok turn_run_id → digest changes. Failed/cancelled
      retry → old ok run stays canonical (LATERAL JOIN picks latest ok) →
      canonical_turn_run_id unchanged → digest unchanged.
    """
    role = str(message.get("role") or "").lower()
    if role == "user":
        content = str(
            message.get("content_md")
            or message.get("text")
            or message.get("user_message")
            or ""
        )
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
    if role == "assistant":
        # R1.6.1 P0-1: use canonical_turn_run_id (LATERAL JOIN of latest ok
        # turn_run), NOT the message row's current_turn_run_id (which may
        # point to a failed retry). Fall back to current_turn_run_id only
        # for legacy callers that haven't been updated yet.
        canonical_run_id = str(
            message.get("canonical_turn_run_id")
            or message.get("current_turn_run_id")
            or ""
        )
        # Safe-visible answer text (no reasoning / tool_trace / raw payload).
        blocks = message.get("answer_blocks")
        if not isinstance(blocks, list):
            blocks = []
        safe_texts: list[str] = []
        for b in blocks:
            if isinstance(b, dict):
                t = str(b.get("text") or "").strip()
                if t:
                    safe_texts.append(t)
        # Safe-visible web search outcome (PublicWebSearchSummary.outcome).
        web = message.get("web_search_summary")
        web_outcome = ""
        if isinstance(web, dict):
            web_outcome = str(web.get("outcome") or "").strip()
        # R1.6.1 P0-1: structured serialization (no separator-based join).
        # This fixes the single-element join bug and makes the digest
        # input unambiguous: canonical_run_id, ordered answer texts, and
        # web outcome are serialized as a deterministic JSON object.
        digest_input = json.dumps(
            {
                "run_id": canonical_run_id,
                "answer_texts": safe_texts,
                "web_outcome": web_outcome,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    # system / unknown roles: empty digest (stable, content-independent).
    return hashlib.sha256(b"".encode("utf-8")).hexdigest()


def compute_watermark(canonical_messages: list[dict[str, Any]]) -> str:
    """SHA-256 over deterministic (message_id, role, revision_digest) pairs.

    R1.6 P0-1: watermark follows canonical revision, not just message_id.

    - **assistant**: includes the latest canonical ok turn_run identity
      and a safe-visible output revision digest. Successful regenerate
      (new ok run replaces old) → turn_run_id changes → watermark
      changes. Failed/cancelled retry (old ok run stays canonical) →
      turn_run_id unchanged → watermark unchanged.
    - **user**: includes a content revision digest so edits invalidate
      CAS.
    - **No raw text, query, or provider payload** is written to the
      watermark, logs, DTO, or DB — only the final SHA-256 hash.

    The serialization is ``[(message_id, role, revision_digest), ...]``
    sorted by key for determinism.
    """
    triples = [
        (
            str(m.get("id") or m.get("message_id") or ""),
            str(m.get("role") or ""),
            _message_revision_digest(m),
        )
        for m in canonical_messages
        if isinstance(m, dict)
    ]
    serialized = json.dumps(
        triples, sort_keys=True, ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def validate_snapshot(
    snapshot: ThreadMemorySnapshot,
    host_bindings: dict[str, SourceBinding],
    allowlist: set[str],
    fence_results: dict[str, Any] | None,
) -> tuple[ThreadMemorySnapshot, dict[str, Any]]:
    """Validate a snapshot against the Host binding map + allowlist.

    R1.6 P0-2: ``host_bindings`` is the Host-derived
    ``binding_id -> SourceBinding`` map. It is the single source of
    truth for binding content. Any binding in the snapshot that is not
    in the map, or whose content differs from the Host copy, triggers
    whole-snapshot rejection.

    R1.6.1 P0-2: after the tampering check, episode ``source_bindings``
    are **materialized** from the KEPT facts' ``source_ids ∩
    host_bindings`` — not back-filled from the snapshot's own bindings.
    This closes the hole where an article fact references a real Host
    binding ID but the episode omits ``source_bindings=[]``, causing
    fence to never run on that binding. The materialized list is:
      - complete (every referenced Host binding is included)
      - deduplicated (by binding_id)
      - stably sorted (by binding_id)
      - Host objects (never the model's), with ``validity_check``
        updated from ``fence_results`` when available.

    Bindings not referenced by any kept fact are excluded.

    Returns ``(validated_snapshot, metrics)``. The validated snapshot
    has violating facts stripped and bindings materialized from Host.
    Fence-invalidated bindings are NOT stripped here — fact降级 to
    ``prior_mention`` happens at render time so episodes stay
    reconstructable from canonical messages.

    ``metrics`` shape::

        {
            "total_facts": int,
            "allowlist_violation": int,
            "stripped_facts": int,
            "binding_violation": int,
            "fence_invalid_bindings": int,
            "binding_tampering": int,
            "rejected": bool,
            "reject_reason": str | None,
        }
    """
    metrics: dict[str, Any] = {
        "total_facts": 0,
        "allowlist_violation": 0,
        "stripped_facts": 0,
        "binding_violation": 0,
        "fence_invalid_bindings": 0,
        "binding_tampering": 0,
        "rejected": False,
        "reject_reason": None,
    }

    fence_results = fence_results or {}
    fence_invalid_ids = {
        binding_id
        for binding_id, result in fence_results.items()
        if isinstance(result, dict)
        and result.get("status") == "invalid"
    }
    metrics["fence_invalid_bindings"] = len(fence_invalid_ids)

    # R1.6 P0-2: check every snapshot binding against the Host map.
    # Any unknown binding or content mismatch → whole-snapshot reject.
    # R1.6.1 P0-2: validity_check is NOT compared (see _binding_matches_host).
    for episode in snapshot.episodes:
        for sb in episode.source_bindings:
            host_b = host_bindings.get(sb.binding_id)
            if host_b is None:
                # Unknown binding_id (forged or stale) → reject.
                metrics["binding_tampering"] += 1
                metrics["rejected"] = True
                metrics["reject_reason"] = (
                    f"unknown_binding:{sb.binding_id}"
                )
                continue
            if not _binding_matches_host(sb, host_b):
                # Same id but content tampered → reject.
                metrics["binding_tampering"] += 1
                metrics["rejected"] = True
                metrics["reject_reason"] = (
                    f"binding_tampered:{sb.binding_id}"
                )

    if metrics["rejected"]:
        return snapshot, metrics

    new_episodes: list[Episode] = []
    for episode in snapshot.episodes:
        kept_facts: list[StructuredFact] = []
        for fact in episode.structured_facts:
            metrics["total_facts"] += 1
            stripped = False
            # Step 6a: source_ids ⊆ ALLOW
            if fact.source_ids and not all(
                sid in allowlist for sid in fact.source_ids
            ):
                metrics["allowlist_violation"] += 1
                metrics["stripped_facts"] += 1
                stripped = True
            # Step 6b + R1.6 P0-2: article fact must reference a Host
            # article binding (message id / web binding / pseudo-binding
            # cannot satisfy article provenance).
            elif (
                fact.source_type == "article"
                and not _article_fact_has_host_article_binding(
                    fact, host_bindings
                )
            ):
                metrics["binding_violation"] += 1
                metrics["stripped_facts"] += 1
                stripped = True
            if not stripped:
                kept_facts.append(fact)
        # R1.6.1 P0-2: materialize Host bindings from KEPT facts'
        # source_ids ∩ host_bindings. This replaces the old "back-fill
        # from episode.source_bindings" which failed to fence-check
        # bindings that the model omitted. The materialized list is
        # complete, deduplicated, stably sorted, and consists of Host
        # objects with fence_results applied.
        materialized_bindings = _materialize_host_bindings_for_episode(
            kept_facts, host_bindings, fence_results
        )
        new_episodes.append(
            episode.model_copy(
                update={
                    "structured_facts": kept_facts,
                    "source_bindings": materialized_bindings,
                }
            )
        )

    validated = snapshot.model_copy(update={"episodes": new_episodes})

    # Step 8: >20% stripped → whole-snapshot reject
    if (
        not metrics["rejected"]
        and metrics["total_facts"] > 0
    ):
        ratio = metrics["stripped_facts"] / metrics["total_facts"]
        if ratio > _ALLOWLIST_VIOLATION_REJECT_RATIO:
            metrics["rejected"] = True
            metrics["reject_reason"] = "allowlist_violation_exceeded_20pct"

    return validated, metrics
