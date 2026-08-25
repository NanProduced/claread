"""The 12 deterministic hard gates of the Daily Reader teaching contract v2.

Contract: prompt-p2-eval-v2.md lines 144-161 (mirrors P-1 §9.1). Every gate
is a pure function ``(case, artifact) -> {"passed": bool|None, "detail": ...}``
(``passed=None`` == n/a, same convention as v1 checks).

Since P-5A the nine gold-free gates are single-sourced in
``app.services.daily_reader.teaching.gates``; this module composes them
with the three gold-dependent gates that stay eval-only.
"""

from __future__ import annotations

from typing import Any

# shared gold-free implementation (single source of truth)
from app.services.daily_reader.teaching.gates import (  # noqa: F401  (helpers reused below)
    GateFn,
    _artifact_surface_texts,
    _bp,
    _is_reject_run,
    _na,
    _pkg,
    gate_anchors_resolve,
    gate_checkpoint_evidence_valid,
    gate_counts_in_bounds,
    gate_expression_explained_once,
    gate_legacy_fields_not_required,
    gate_no_empty_placeholders,
    gate_refinement_bounded,
    gate_sentence_map_translation_reuse,
    gate_source_caption_preserved,
)

from claread_eval.daily_reader.checks import normalize_text
from claread_eval.daily_reader.teaching_v2.schema import substantive_unit_ids

# ---------------------------------------------------------------------------
# 1. no boilerplate / transcript skeleton / source UI residue (gold)
# ---------------------------------------------------------------------------


def gate_no_boilerplate_residue(case: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    if _is_reject_run(case, artifact):
        return _na("rejected run carries no teaching surfaces")
    fragments = (case.get("gold") or {}).get("dirty_fragments") or []
    haystack = normalize_text("\n".join(_artifact_surface_texts(artifact)))
    hits = [f for f in fragments if normalize_text(f) and normalize_text(f) in haystack]
    return {"passed": not hits, "detail": {"fragments_checked": len(fragments), "hits": hits}}


# ---------------------------------------------------------------------------
# 10. outcome matches gold: cleaned_publish completes, reject rejects
# ---------------------------------------------------------------------------


def gate_outcome_matches_gold(case: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    expected = (case.get("gold") or {}).get("expected_outcome")
    meta = artifact.get("run_meta") or {}
    outcome = meta.get("outcome")
    if expected == "cleaned_publish":
        ok = (
            outcome == "cleaned_publish"
            and not meta.get("abort")
            and bool(artifact.get("learning_package"))
        )
        return {
            "passed": ok,
            "detail": {"expected": expected, "outcome": outcome, "abort": bool(meta.get("abort"))},
        }
    ok = outcome == "reject" and bool(meta.get("abort"))
    return {
        "passed": ok,
        "detail": {
            "expected": expected,
            "outcome": outcome,
            "abort": bool(meta.get("abort")),
            "rejection_reason": meta.get("rejection_reason"),
        },
    }


# ---------------------------------------------------------------------------
# 11. translation coverage dispatched by gold policy
# ---------------------------------------------------------------------------


def _coverage(case: dict[str, Any]) -> dict[str, Any]:
    return case.get("gold", {}).get("expected_translation_coverage") or {}


def gate_translation_coverage_policy(
    case: dict[str, Any], artifact: dict[str, Any]
) -> dict[str, Any]:
    if _is_reject_run(case, artifact):
        return _na("rejected run has no translations")
    gold = case.get("gold") or {}
    cov = _coverage(case)
    policy = cov.get("policy")
    required = set(cov.get("required_paragraph_ids") or [])
    allowed = set(cov.get("allowed_paragraph_ids") or [])
    keys = set(_pkg(artifact).get("translations_by_paragraph_id") or {})

    outside_allowed = sorted(keys - allowed)
    missing_required = sorted(required - keys)
    problems: list[str] = []
    if outside_allowed:
        problems.append(f"translations outside gold allowed set: {outside_allowed}")
    if missing_required:
        problems.append(f"missing required translations: {missing_required}")

    pkg = _pkg(artifact)
    if policy == "all_units":
        # P-4C-B-R: B1 must cover every substantive (non-pure-dirty) unit
        missing_units = sorted(substantive_unit_ids(case) - keys)
        if missing_units:
            problems.append(f"all_units policy missing unit translations: {missing_units}")
    elif policy == "selected_units":
        difficulty = gold.get("expected_difficulty")
        if difficulty == "B2":
            associated: set[str] = set()
            for cp in pkg.get("comprehension_checkpoints") or []:
                associated |= set(cp.get("evidence_paragraph_ids") or [])
            associated |= {lt.get("paragraph_id") for lt in pkg.get("language_targets") or []}
            associated |= {sm.get("paragraph_id") for sm in pkg.get("sentence_maps") or []}
            associated.discard(None)
            missing_assoc = sorted(associated - keys)
            if missing_assoc:
                problems.append(f"B2 associated units without translation: {missing_assoc}")
        elif difficulty == "C1":
            # only blueprint/gold explicitly selected hard units need
            # translations; plain checkpoint evidence is NOT forced.
            selected = set(_bp(artifact).get("selected_paragraph_ids") or []) | required
            missing_sel = sorted(selected - keys)
            if missing_sel:
                problems.append(f"C1 selected hard units without translation: {missing_sel}")
        else:
            problems.append(f"selected_units policy with unexpected difficulty {difficulty}")
    else:
        problems.append(f"unknown coverage policy {policy!r}")
    return {
        "passed": not problems,
        "detail": {
            "policy": policy,
            "difficulty": gold.get("expected_difficulty"),
            "translated_ids": sorted(keys),
            "problems": problems,
        },
    }


HARD_GATES: dict[str, GateFn] = {
    "no_boilerplate_residue": gate_no_boilerplate_residue,
    "anchors_resolve": gate_anchors_resolve,
    "expression_explained_once": gate_expression_explained_once,
    "counts_in_bounds": gate_counts_in_bounds,
    "no_empty_placeholders": gate_no_empty_placeholders,
    "checkpoint_evidence_valid": gate_checkpoint_evidence_valid,
    "sentence_map_translation_reuse": gate_sentence_map_translation_reuse,
    "source_caption_preserved": gate_source_caption_preserved,
    "refinement_bounded": gate_refinement_bounded,
    "outcome_matches_gold": gate_outcome_matches_gold,
    "translation_coverage_policy": gate_translation_coverage_policy,
    "legacy_fields_not_required": gate_legacy_fields_not_required,
}


def run_hard_gates(case: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    gates = {name: fn(case, artifact) for name, fn in HARD_GATES.items()}
    scored = [g for g in gates.values() if g["passed"] is not None]
    return {
        "gates": gates,
        "passed_count": sum(1 for g in scored if g["passed"]),
        "scored_count": len(scored),
        "all_passed": all(g["passed"] for g in scored),
    }
