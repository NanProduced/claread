"""The 12 deterministic hard gates of the Daily Reader teaching contract v2.

Contract: prompt-p2-eval-v2.md lines 144-161 (mirrors P-1 §9.1). Every gate
is a pure function ``(case, artifact) -> {"passed": bool|None, "detail": ...}``
(``passed=None`` == n/a, same convention as v1 checks). The deterministic
layer only verifies anchors, structure and declared relations; semantic
judgement belongs to gold + Judge + human review.

v1 helpers ``normalize_text``/``normalize_expression`` are imported and
reused unchanged — v1 files are never modified.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from typing import Any

from claread_eval.daily_reader.checks import normalize_expression, normalize_text

GateFn = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]

_PLACEHOLDER_MARKERS = ("{{", "}}", "todo", "tbd", "placeholder", "待补充", "占位")


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _is_reject_run(case: dict[str, Any], artifact: dict[str, Any]) -> bool:
    return (case.get("gold", {}).get("expected_outcome") == "reject"
            or (artifact.get("run_meta") or {}).get("outcome") == "reject")


def _na(note: str) -> dict[str, Any]:
    return {"passed": None, "detail": {"note": note}}


def _bp(artifact: dict[str, Any]) -> dict[str, Any]:
    return artifact.get("lesson_blueprint") or {}


def _pkg(artifact: dict[str, Any]) -> dict[str, Any]:
    return artifact.get("learning_package") or {}


def _unit_ids(case: dict[str, Any]) -> set[str]:
    return {u["id"] for u in case.get("input", {}).get("reading_units", []) or []}


def _unit_texts(case: dict[str, Any]) -> dict[str, str]:
    """id -> raw unit text for every dict reading unit with a string text."""
    texts: dict[str, str] = {}
    for u in case.get("input", {}).get("reading_units", []) or []:
        if isinstance(u, dict) and isinstance(u.get("text"), str):
            texts[u.get("id", "")] = u["text"]
    return texts


def _coverage(case: dict[str, Any]) -> dict[str, Any]:
    return (case.get("gold", {}).get("expected_translation_coverage") or {})


def _artifact_surface_texts(artifact: dict[str, Any]) -> list[str]:
    """Every user-visible text surface of a v2 artifact."""
    texts: list[str] = []
    bp = _bp(artifact)
    texts += [bp.get("reading_mission", "")]
    texts += list(bp.get("learning_objectives") or [])
    for node in bp.get("structure_map") or []:
        texts.append(node.get("label", ""))
        texts.append(node.get("function", ""))
    pkg = _pkg(artifact)
    for cp in pkg.get("comprehension_checkpoints") or []:
        texts += [cp.get("prompt", ""), cp.get("reference_answer", ""),
                  cp.get("explanation_zh", "")]
    for lt in pkg.get("language_targets") or []:
        texts += [lt.get("expression", ""), lt.get("meaning_zh", ""),
                  lt.get("usage_note", ""), lt.get("reusable_pattern", "")]
    for sm in pkg.get("sentence_maps") or []:
        texts += [sm.get("sentence", ""), sm.get("structure_zh", ""),
                  sm.get("translation", "")]
    texts += list((pkg.get("translations_by_paragraph_id") or {}).values())
    texts.append(pkg.get("post_read_summary", ""))
    tt = pkg.get("transfer_task") or {}
    texts += [tt.get("prompt", ""), tt.get("scaffold", "")]
    texts += list(tt.get("reference_points") or [])
    texts.append((artifact.get("source_assets") or {}).get("source_caption") or "")
    return texts


# ---------------------------------------------------------------------------
# 1. no boilerplate / transcript skeleton / source UI residue
# ---------------------------------------------------------------------------


def gate_no_boilerplate_residue(case: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    if _is_reject_run(case, artifact):
        return _na("rejected run carries no teaching surfaces")
    fragments = (case.get("gold") or {}).get("dirty_fragments") or []
    haystack = normalize_text("\n".join(_artifact_surface_texts(artifact)))
    hits = [f for f in fragments if normalize_text(f) and normalize_text(f) in haystack]
    return {"passed": not hits, "detail": {"fragments_checked": len(fragments), "hits": hits}}


# ---------------------------------------------------------------------------
# 2. every paragraph/span anchor resolves to a reading unit
# ---------------------------------------------------------------------------


def gate_anchors_resolve(case: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    if _is_reject_run(case, artifact):
        return _na("rejected run carries no anchors")
    valid = _unit_ids(case)
    unit_texts = _unit_texts(case)
    anchors: list[tuple[str, str]] = []
    bp = _bp(artifact)
    for pid in bp.get("selected_paragraph_ids") or []:
        anchors.append(("lesson_blueprint.selected_paragraph_ids", pid))
    for i, node in enumerate(bp.get("structure_map") or []):
        for pid in node.get("paragraph_ids") or []:
            anchors.append((f"structure_map[{i}]", pid))
    pkg = _pkg(artifact)
    for i, cp in enumerate(pkg.get("comprehension_checkpoints") or []):
        for key in ("evidence_paragraph_ids", "answer_evidence_paragraph_ids"):
            for pid in cp.get(key) or []:
                anchors.append((f"checkpoint[{i}].{key}", pid))
    for i, lt in enumerate(pkg.get("language_targets") or []):
        if isinstance(lt, dict):
            anchors.append((f"language_target[{i}]", lt.get("paragraph_id", "")))
    for i, sm in enumerate(pkg.get("sentence_maps") or []):
        if isinstance(sm, dict):
            anchors.append((f"sentence_map[{i}]", sm.get("paragraph_id", "")))
    for pid in (pkg.get("translations_by_paragraph_id") or {}):
        anchors.append(("translations_by_paragraph_id", pid))
    unresolved = [{"where": w, "anchor": p} for w, p in anchors if p not in valid]

    # P-2G-C: a source-backed surface must actually be a contiguous substring
    # of the unit it is anchored to (whitespace-normalized, case-insensitive).
    text_problems: list[dict[str, str]] = []
    for i, lt in enumerate(pkg.get("language_targets") or []):
        if not isinstance(lt, dict):
            continue
        pid = lt.get("paragraph_id", "")
        expr = lt.get("expression", "")
        hay = normalize_text(unit_texts.get(pid, ""))
        if isinstance(expr, str) and normalize_text(expr) and \
                normalize_text(expr) not in hay:
            text_problems.append({"kind": "language_target", "index": i,
                                  "paragraph_id": pid,
                                  "text": expr[:120]})
    for i, sm in enumerate(pkg.get("sentence_maps") or []):
        if not isinstance(sm, dict):
            continue
        pid = sm.get("paragraph_id", "")
        sentence = sm.get("sentence", "")
        hay = _squash(unit_texts.get(pid, ""))
        if isinstance(sentence, str) and _squash(sentence) and \
                _squash(sentence) not in hay:
            text_problems.append({"kind": "sentence_map", "index": i,
                                  "paragraph_id": pid,
                                  "text": sentence[:120]})

    # P-2G-C: structure_map / selected paragraphs must not reference a pure
    # dirty unit (unit whose full text equals a declared dirty fragment).
    norm_frags = {normalize_text(f) for f in (case.get("gold") or {}).get(
        "dirty_fragments") or [] if isinstance(f, str) and normalize_text(f)}
    pure_dirty = {pid for pid, txt in unit_texts.items()
                  if norm_frags and normalize_text(txt) in norm_frags}
    dirty_refs: list[dict[str, str]] = []
    for i, node in enumerate(bp.get("structure_map") or []):
        if not isinstance(node, dict):
            continue
        for pid in node.get("paragraph_ids") or []:
            if pid in pure_dirty:
                dirty_refs.append({"where": f"structure_map[{i}]", "paragraph_id": pid})
    for pid in bp.get("selected_paragraph_ids") or []:
        if pid in pure_dirty:
            dirty_refs.append({"where": "selected_paragraph_ids", "paragraph_id": pid})

    problems = unresolved or text_problems or dirty_refs
    return {"passed": not problems,
            "detail": {"anchors_checked": len(anchors), "unresolved": unresolved[:20],
                       "text_mismatches": text_problems[:20],
                       "pure_dirty_refs": dirty_refs[:20]}}


# ---------------------------------------------------------------------------
# 3. one normalized expression is explained exactly once
# ---------------------------------------------------------------------------


def gate_expression_explained_once(case: dict[str, Any],
                                   artifact: dict[str, Any]) -> dict[str, Any]:
    if _is_reject_run(case, artifact):
        return _na("rejected run has no language targets")
    keys = [normalize_expression(lt.get("expression", ""))
            for lt in _pkg(artifact).get("language_targets") or []]
    keys = [k for k in keys if k]
    dupes = {k: c for k, c in Counter(keys).items() if c > 1}
    return {"passed": not dupes, "detail": {"duplicate_keys": dupes}}


# ---------------------------------------------------------------------------
# 4. counts within P-1 bounds, transfer task exactly 1
# ---------------------------------------------------------------------------

_BOUNDS = (
    ("comprehension_checkpoints", 2, 4),
    ("language_targets", 3, 5),
    ("sentence_maps", 1, 2),
)


def gate_counts_in_bounds(case: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    if _is_reject_run(case, artifact):
        return _na("rejected run carries no teaching package")
    bp, pkg = _bp(artifact), _pkg(artifact)
    violations: list[str] = []
    for key, lo, hi in _BOUNDS:
        n = len(pkg.get(key) or [])
        if not lo <= n <= hi:
            violations.append(f"{key}={n} outside {lo}-{hi}")
    n = len(bp.get("learning_objectives") or [])
    if not 1 <= n <= 2:
        violations.append(f"learning_objectives={n} outside 1-2")
    n = len(bp.get("structure_map") or [])
    if not 2 <= n <= 6:
        violations.append(f"structure_map={n} outside 2-6")
    if not isinstance(pkg.get("transfer_task"), dict):
        violations.append("transfer_task must be exactly 1 object")
    return {"passed": not violations, "detail": {"violations": violations}}


# ---------------------------------------------------------------------------
# 5. no empty strings / containers / placeholders
# ---------------------------------------------------------------------------


def _walk_empty(node: Any, path: str, problems: list[str]) -> None:
    if isinstance(node, str):
        if not node.strip():
            problems.append(f"{path}: empty string")
        elif normalize_text(node) in _PLACEHOLDER_MARKERS or "{{" in node:
            problems.append(f"{path}: placeholder {node[:40]!r}")
    elif isinstance(node, dict):
        if not node:
            problems.append(f"{path}: empty object")
        for k, v in node.items():
            _walk_empty(v, f"{path}.{k}", problems)
    elif isinstance(node, list):
        if not node:
            problems.append(f"{path}: empty list")
        for i, v in enumerate(node):
            _walk_empty(v, f"{path}[{i}]", problems)


def gate_no_empty_placeholders(case: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    if _is_reject_run(case, artifact):
        return _na("rejected run carries no teaching package")
    problems: list[str] = []
    for key in ("lesson_blueprint", "learning_package"):
        _walk_empty(artifact.get(key) or {}, key, problems)
    return {"passed": not problems, "detail": {"problems": problems[:30]}}


# ---------------------------------------------------------------------------
# 6. checkpoint evidence non-empty; answer evidence is a non-empty subset
# ---------------------------------------------------------------------------


def gate_checkpoint_evidence_valid(case: dict[str, Any],
                                   artifact: dict[str, Any]) -> dict[str, Any]:
    if _is_reject_run(case, artifact):
        return _na("rejected run has no checkpoints")
    problems: list[str] = []
    for i, cp in enumerate(_pkg(artifact).get("comprehension_checkpoints") or []):
        ev = cp.get("evidence_paragraph_ids") or []
        ans = cp.get("answer_evidence_paragraph_ids") or []
        if not ev:
            problems.append(f"checkpoint[{i}]: evidence_paragraph_ids empty")
            continue
        if not ans:
            problems.append(f"checkpoint[{i}]: answer_evidence_paragraph_ids empty")
        elif not set(ans) <= set(ev):
            problems.append(
                f"checkpoint[{i}]: answer evidence {ans} not a subset of evidence {ev}")
    return {"passed": not problems, "detail": {"problems": problems}}


# ---------------------------------------------------------------------------
# 7. sentence map translation reuses the same paragraph's shared translation
# ---------------------------------------------------------------------------


def gate_sentence_map_translation_reuse(case: dict[str, Any],
                                        artifact: dict[str, Any]) -> dict[str, Any]:
    if _is_reject_run(case, artifact):
        return _na("rejected run has no sentence maps")
    pkg = _pkg(artifact)
    shared = pkg.get("translations_by_paragraph_id") or {}
    diffs: list[dict[str, str]] = []
    for sm in pkg.get("sentence_maps") or []:
        pid = sm.get("paragraph_id", "")
        map_tr = _squash(sm.get("translation", ""))
        para_tr = _squash(shared.get(pid, ""))
        if not map_tr or not para_tr or map_tr not in para_tr:
            diffs.append({"paragraph_id": pid,
                          "sentence": (sm.get("sentence") or "")[:120],
                          "translation": (sm.get("translation") or "")[:120]})
    return {"passed": not diffs, "detail": {"sentence_maps_checked": len(
        pkg.get("sentence_maps") or []), "diffs": diffs}}


# ---------------------------------------------------------------------------
# 8. source caption preserved; empty source caption stays empty
# ---------------------------------------------------------------------------


def gate_source_caption_preserved(case: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    src = (case.get("input") or {}).get("source_caption") or ""
    art = (artifact.get("source_assets") or {}).get("source_caption") or ""
    if not src:
        ok = not art
        return {"passed": ok,
                "detail": {"note": "source caption empty -> artifact must stay empty",
                           "artifact_caption": art[:120]}}
    return {"passed": art == src,
            "detail": {"expected": src[:200], "actual": art[:200]}}


# ---------------------------------------------------------------------------
# 9. refinement count <= 1
# ---------------------------------------------------------------------------


def gate_refinement_bounded(case: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    count = (artifact.get("run_meta") or {}).get("refinement_count", 0)
    ok = (isinstance(count, int) and not isinstance(count, bool)
          and 0 <= count <= 1)
    return {"passed": ok, "detail": {"refinement_count": count}}


# ---------------------------------------------------------------------------
# 10. outcome matches gold: cleaned_publish completes, reject rejects
# ---------------------------------------------------------------------------


def gate_outcome_matches_gold(case: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    expected = (case.get("gold") or {}).get("expected_outcome")
    meta = artifact.get("run_meta") or {}
    outcome = meta.get("outcome")
    if expected == "cleaned_publish":
        ok = (outcome == "cleaned_publish" and not meta.get("abort")
              and bool(artifact.get("learning_package")))
        return {"passed": ok, "detail": {"expected": expected, "outcome": outcome,
                                         "abort": bool(meta.get("abort"))}}
    ok = outcome == "reject" and bool(meta.get("abort"))
    return {"passed": ok, "detail": {"expected": expected, "outcome": outcome,
                                     "abort": bool(meta.get("abort")),
                                     "rejection_reason": meta.get("rejection_reason")}}


# ---------------------------------------------------------------------------
# 11. translation coverage dispatched by gold policy
# ---------------------------------------------------------------------------


def gate_translation_coverage_policy(case: dict[str, Any],
                                     artifact: dict[str, Any]) -> dict[str, Any]:
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
        # B1: every substantial unit gets exactly one shared translation
        # (dict keys are unique by construction).
        missing_units = sorted(_unit_ids(case) - keys)
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
                problems.append(
                    f"B2 associated units without translation: {missing_assoc}")
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
    return {"passed": not problems,
            "detail": {"policy": policy, "difficulty": gold.get("expected_difficulty"),
                       "translated_ids": sorted(keys), "problems": problems}}


# ---------------------------------------------------------------------------
# 12. a legal v2 artifact without legacy v1 fields must not fail
# ---------------------------------------------------------------------------

_LEGACY_FIELDS = ("focus_question", "micro_summary", "discussion_questions")


def gate_legacy_fields_not_required(case: dict[str, Any],
                                    artifact: dict[str, Any]) -> dict[str, Any]:
    dump = str(artifact)
    found = [f for f in _LEGACY_FIELDS if f in dump]
    return {"passed": True,
            "detail": {"legacy_fields_found": found,
                       "note": "v2 gates never require per-paragraph focus_question/"
                               "micro_summary or discussion_questions"}}


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
    return {"gates": gates,
            "passed_count": sum(1 for g in scored if g["passed"]),
            "scored_count": len(scored),
            "all_passed": all(g["passed"] for g in scored)}
