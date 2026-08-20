"""Deterministic checks for the daily-reader regression rubric.

Pure functions over plain dicts (dataset case + workflow/DB artifact).
The artifact shape is the SAME for both eval modes:

- baseline: a ``daily_readers`` row fetched from the local DB;
- workflow: the ``daily_reader`` LangGraph final state dumped by the
  harness (no DB write).

Both expose: title, difficulty, original_text, body_json,
highlights_json, paragraph_notes_json, takeaways_json.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize_text(s: str) -> str:
    """Casefold + collapse all whitespace runs to single spaces."""
    return re.sub(r"\s+", " ", s or "").strip().casefold()


def normalize_expression(s: str) -> str:
    """normalize_text + naive morphology: plural / simple tense suffixes.

    ponytail: suffix strip only (no lemmatizer). Mirrors services/api
    ``_normalize_highlight_key`` so the highlight_dedup gate matches
    production (manifestos→manifesto, initiating→initiate).
    """
    words = []
    for w in normalize_text(s).split(" "):
        if not w:
            continue
        if len(w) > 5 and w.endswith("ing"):
            w = w[:-3]
            if len(w) > 2 and w[-1] == w[-2] and w[-1] not in "aeiouy":
                w = w[:-1]
        elif len(w) > 4 and w.endswith("ied"):
            w = w[:-3] + "y"
        elif len(w) > 4 and w.endswith("ed"):
            w = w[:-2]
            if len(w) > 2 and w[-1] == w[-2] and w[-1] not in "aeiouy":
                w = w[:-1]
        elif len(w) > 4 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 4 and w.endswith("es"):
            w = w[:-2]
        elif w.endswith("s") and not w.endswith("ss") and len(w) > 3:
            w = w[:-1]
        if len(w) > 4 and w.endswith("e"):
            w = w[:-1]
        words.append(w)
    return " ".join(words)


def _squash(s: str) -> str:
    """Remove ALL whitespace — used for CJK substring containment."""
    return re.sub(r"\s+", "", s or "")


# ---------------------------------------------------------------------------
# Artifact text extraction
# ---------------------------------------------------------------------------


def artifact_surface_texts(artifact: dict[str, Any]) -> list[str]:
    """Every user-visible text surface a dirty fragment could leak into."""
    texts: list[str] = []
    for para in (artifact.get("body_json") or {}).get("paragraphs", []):
        texts.append(para.get("text", ""))
        note = para.get("reading_note") or {}
        for k in ("focus_question", "micro_summary", "translation"):
            texts.append(note.get(k, ""))
    notes = artifact.get("paragraph_notes_json") or {}
    texts.append(notes.get("article_summary", ""))
    texts.extend(notes.get("reading_focus", []) or [])
    for n in notes.get("notes", []) or []:
        for k in ("focus_question", "micro_summary", "translation"):
            texts.append(n.get(k, ""))
    for h in artifact.get("highlights_json") or []:
        texts.append(h.get("text", ""))
        texts.append(h.get("gloss", ""))
    takeaways = artifact.get("takeaways_json") or {}
    texts.append(takeaways.get("article_takeaway", ""))
    for e in takeaways.get("key_expressions", []) or []:
        texts.extend([e.get("expression", ""), e.get("gloss", ""),
                      e.get("context_sentence", ""), e.get("usage_note", "")])
    for sn in takeaways.get("sentence_notes", []) or []:
        texts.extend([sn.get("sentence", ""), sn.get("translation", ""),
                      sn.get("breakdown", ""), sn.get("takeaway", "")])
    for wm in takeaways.get("writing_moves", []) or []:
        texts.extend([wm.get("anchor", ""), wm.get("move_type", ""),
                      wm.get("explanation", ""), wm.get("reusable_pattern", "") or ""])
    texts.extend(takeaways.get("discussion_questions", []) or [])
    texts.append(artifact.get("title", ""))
    return texts


# ---------------------------------------------------------------------------
# Checks — each returns {"passed": bool, "detail": ...}
# ---------------------------------------------------------------------------


def check_no_boilerplate(case: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    fragments = (case.get("gold") or {}).get("dirty_fragments", [])
    haystack = normalize_text("\n".join(artifact_surface_texts(artifact)))
    hits = [f for f in fragments if normalize_text(f) and normalize_text(f) in haystack]
    return {"passed": not hits, "detail": {"fragments_checked": len(fragments), "hits": hits}}


def check_highlight_dedup(case: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    seen: dict[str, int] = {}
    for h in artifact.get("highlights_json") or []:
        key = normalize_expression(h.get("text", ""))
        if key:
            seen[key] = seen.get(key, 0) + 1
    dupes = {k: c for k, c in seen.items() if c > 1}
    return {"passed": not dupes, "detail": {"duplicate_keys": dupes}}


def check_translation_consistency(case: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    notes = (artifact.get("paragraph_notes_json") or {}).get("notes", []) or []
    trans_by_pid = {n.get("paragraph_id", ""): n.get("translation", "") for n in notes}
    diffs: list[dict[str, str]] = []
    sentence_notes = (artifact.get("takeaways_json") or {}).get("sentence_notes", []) or []
    for sn in sentence_notes:
        para_tr = _squash(trans_by_pid.get(sn.get("paragraph_id", ""), ""))
        sent_tr = _squash(sn.get("translation", ""))
        if not sent_tr or sent_tr not in para_tr:
            para_id = sn.get("paragraph_id", "")
            diffs.append({
                "paragraph_id": para_id,
                "sentence": sn.get("sentence", "")[:160],
                "sentence_translation": sn.get("translation", ""),
                "paragraph_translation_excerpt": trans_by_pid.get(para_id, "")[:200],
            })
    return {
        "passed": not diffs,
        "detail": {"sentence_notes_checked": len(sentence_notes), "diffs": diffs},
    }


def _phrase_in(needle: str, haystack: str) -> bool:
    """Word-boundary phrase containment on normalized text."""
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


def check_gold_expression_coverage(
    case: dict[str, Any], artifact: dict[str, Any]
) -> dict[str, Any]:
    # A-1 leftover ③: a rejected (aborted) candidate produces no artifacts,
    # so there is nothing to cover. Mark the check n/a instead of failing on
    # coverage=0 — rejection with clean boilerplate is the desired behavior.
    if artifact.get("abort"):
        return {"passed": None, "detail": {"coverage": None,
                "note": "aborted run — no artifacts to evaluate"}}
    expected = (case.get("gold") or {}).get("expected_expressions", [])
    if not expected:
        return {"passed": True, "detail": {"coverage": 1.0, "note": "no gold expressions"}}
    candidates = [
        normalize_expression(h.get("text", ""))
        for h in artifact.get("highlights_json") or []
    ]
    candidates += [
        normalize_expression(e.get("expression", ""))
        for e in (artifact.get("takeaways_json") or {}).get("key_expressions", []) or []
    ]
    candidates = [c for c in candidates if c]

    def covered(expr: str) -> bool:
        key = normalize_expression(expr)
        return any(key == c or _phrase_in(key, c) or _phrase_in(c, key) for c in candidates)

    hits = [e for e in expected if covered(e)]
    coverage = len(hits) / len(expected)
    return {
        "passed": coverage >= 0.5,
        "detail": {"coverage": round(coverage, 3), "hits": hits,
                   "missing": [e for e in expected if e not in hits]},
    }


DETERMINISTIC_CHECKS = {
    "no_boilerplate": check_no_boilerplate,
    "highlight_dedup": check_highlight_dedup,
    "translation_consistency": check_translation_consistency,
    "gold_expression_coverage": check_gold_expression_coverage,
}


def run_deterministic_checks(case: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    results = {name: fn(case, artifact) for name, fn in DETERMINISTIC_CHECKS.items()}
    # passed=None marks a check as n/a (nothing to evaluate); it is excluded
    # from both the numerator and the denominator.
    scored = [r for r in results.values() if r["passed"] is not None]
    passed = sum(1 for r in scored if r["passed"])
    return {"checks": results, "passed": passed, "total": len(scored),
            "pass_ratio": passed / len(scored) if scored else 0.0}
