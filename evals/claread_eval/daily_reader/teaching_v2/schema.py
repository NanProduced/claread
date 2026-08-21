"""Schema-2 validation for the Daily Reader teaching-contract v2 dataset.

Plain-dict validators (no pydantic models) for case/gold/artifact shapes
and the dataset coverage matrix, per prompt-p2-eval-v2.md §Case 与 gold
最小语义 / §样本合同. All functions are pure and offline.
"""

from __future__ import annotations

import re
from typing import Any

ARTICLE_TYPES = ("news_report", "opinion_commentary", "explainer", "narrative_profile")
DIFFICULTIES = ("B1", "B2", "C1")  # A2 is legacy-compat only: never built here
EXPECTED_OUTCOMES = ("cleaned_publish", "reject")
COVERAGE_POLICIES = ("all_units", "selected_units")
ANNOTATION_STATUSES = ("DRAFT_PM_REVIEW",)  # gold drafted here; approval is human work
CHECKPOINT_SKILLS = ("fact_location", "sequence", "main_idea", "inference", "causality",
                     "source_attribution", "claim_evidence", "stance", "structure")

SHORT_ARTICLE_MAX_WORDS = 800   # execution default: short < 800 English words
LONG_ARTICLE_MIN_WORDS = 1500   # execution default: long >= 1500 English words
UNIT_ID_RE = re.compile(r"^u\d{2,3}$")


def english_word_count(text: str) -> int:
    """Deterministic word count: whitespace split (the single source of truth
    for the short/long article thresholds)."""
    return len((text or "").split())


def collapse_whitespace(s: str) -> str:
    """Whitespace-only normalization (case preserved) for verbatim checks."""
    return re.sub(r"\s+", " ", s or "").strip()


def unit_ids(case: dict[str, Any]) -> set[str]:
    return {u["id"] for u in case.get("input", {}).get("reading_units", []) or []}


def _anchors(case: dict[str, Any], ids: list[str], where: str, errs: list[str]) -> None:
    valid = unit_ids(case)
    for pid in ids or []:
        if pid not in valid:
            errs.append(f"{where}: anchor '{pid}' does not resolve to a reading unit")


def _quote_substring(case: dict[str, Any], quote: str, where: str, errs: list[str]) -> None:
    original = collapse_whitespace(case.get("input", {}).get("original_text", ""))
    if collapse_whitespace(quote) not in original:
        errs.append(f"{where}: source_quote is not a verbatim (whitespace-normalized) "
                    f"substring of original_text: {str(quote)[:80]!r}")


def validate_case(case: dict[str, Any]) -> list[str]:
    """Validate one schema-2 case incl. gold semantics. [] == valid."""
    errs: list[str] = []
    if case.get("schema_version") != 2:
        errs.append("schema_version must be 2")
    if case.get("dataset_id") != "daily-reader-teaching-v2":
        errs.append("dataset_id must be daily-reader-teaching-v2")

    origin = case.get("origin", {})
    if origin.get("frozen_real_article") is not True:
        errs.append("origin.frozen_real_article must be true (synthetic cases forbidden)")
    for field in ("source", "source_url", "captured_at"):
        if not origin.get(field):
            errs.append(f"origin.{field} is required")

    inp = case.get("input", {})
    for field in ("title", "source", "source_url", "original_text"):
        if not inp.get(field):
            errs.append(f"input.{field} is required")

    units = inp.get("reading_units") or []
    if not units:
        errs.append("input.reading_units must not be empty")
    seen_ids: set[str] = set()
    for u in units:
        uid = u.get("id", "")
        if not UNIT_ID_RE.match(uid):
            errs.append(f"reading unit id '{uid}' is not a stable uNN id")
        if uid in seen_ids:
            errs.append(f"duplicate reading unit id '{uid}'")
        seen_ids.add(uid)
        if not (u.get("text") or "").strip():
            errs.append(f"reading unit '{uid}' has empty text")

    gold = case.get("gold", {})
    if gold.get("annotation_status") not in ANNOTATION_STATUSES:
        errs.append(f"gold.annotation_status must be one of {ANNOTATION_STATUSES}")
    if gold.get("expected_outcome") not in EXPECTED_OUTCOMES:
        errs.append(f"gold.expected_outcome must be one of {EXPECTED_OUTCOMES}")
    if gold.get("expected_difficulty") not in DIFFICULTIES:
        errs.append(f"gold.expected_difficulty must be one of {DIFFICULTIES} (no A2)")
    if gold.get("article_type") not in ARTICLE_TYPES:
        errs.append(f"gold.article_type must be one of {ARTICLE_TYPES}")

    for i, ev in enumerate(gold.get("key_evidence") or []):
        _anchors(case, ev.get("paragraph_ids"), f"gold.key_evidence[{i}]", errs)
        _quote_substring(case, ev.get("source_quote", ""), f"gold.key_evidence[{i}]", errs)
    for i, ex in enumerate(gold.get("core_expressions") or []):
        _anchors(case, ex.get("paragraph_ids"), f"gold.core_expressions[{i}]", errs)
        _quote_substring(case, ex.get("source_quote", ""),
                         f"gold.core_expressions[{i}]", errs)

    cov = gold.get("expected_translation_coverage") or {}
    if cov.get("policy") not in COVERAGE_POLICIES:
        errs.append(f"gold.expected_translation_coverage.policy must be one of "
                    f"{COVERAGE_POLICIES}")
    else:
        _anchors(case, cov.get("required_paragraph_ids"), "translation_coverage.required", errs)
        _anchors(case, cov.get("allowed_paragraph_ids"), "translation_coverage.allowed", errs)
        req = set(cov.get("required_paragraph_ids") or [])
        allowed = set(cov.get("allowed_paragraph_ids") or [])
        if not req <= allowed:
            errs.append("translation_coverage.required must be a subset of allowed")
        diff = gold.get("expected_difficulty")
        if diff == "B1" and cov.get("policy") != "all_units":
            errs.append("B1 gold must use policy=all_units")
        if diff in ("B2", "C1") and cov.get("policy") != "selected_units":
            errs.append("B2/C1 gold must use policy=selected_units")
        if cov.get("policy") == "all_units" and req != seen_ids:
            errs.append("policy=all_units must require exactly all reading units")
    return errs


def validate_artifact(case: dict[str, Any], artifact: dict[str, Any]) -> list[str]:
    """Minimal v2 artifact shape validation. [] == valid."""
    errs: list[str] = []
    meta = artifact.get("run_meta") or {}
    if meta.get("outcome") not in EXPECTED_OUTCOMES:
        errs.append(f"run_meta.outcome must be one of {EXPECTED_OUTCOMES}")
    if not isinstance(meta.get("refinement_count", 0), int) or meta.get(
            "refinement_count", 0) < 0:
        errs.append("run_meta.refinement_count must be a non-negative int")
    if meta.get("outcome") == "reject":
        return errs  # rejected runs carry no teaching package

    bp = artifact.get("lesson_blueprint") or {}
    if bp.get("article_type") not in ARTICLE_TYPES:
        errs.append(f"lesson_blueprint.article_type must be one of {ARTICLE_TYPES}")
    if bp.get("effective_difficulty") not in DIFFICULTIES:
        errs.append(f"lesson_blueprint.effective_difficulty must be one of {DIFFICULTIES}")
    pkg = artifact.get("learning_package") or {}
    for cp in pkg.get("comprehension_checkpoints") or []:
        if cp.get("skill") not in CHECKPOINT_SKILLS:
            errs.append(f"checkpoint skill '{cp.get('skill')}' is not a P-1 §3.3 skill")
    return errs


def validate_dataset_coverage(cases: list[dict[str, Any]]) -> list[str]:
    """Dataset coverage matrix per the sample contract. [] == valid."""
    errs: list[str] = []
    n = len(cases)
    if not 8 <= n <= 12:
        errs.append(f"dataset must contain 8-12 frozen real articles, got {n}")

    producible = [c for c in cases
                  if c["gold"].get("expected_outcome") == "cleaned_publish"]
    for t in ARTICLE_TYPES:
        if sum(1 for c in producible if c["gold"].get("article_type") == t) < 2:
            errs.append(f"article_type {t} needs >=2 producible cases "
                        f"(reject cases do not count toward quotas)")
    for d in DIFFICULTIES:
        if sum(1 for c in cases if c["gold"].get("expected_difficulty") == d) < (
                3 if d in ("B1", "B2") else 2):
            errs.append(f"difficulty {d} below minimum ({'3' if d in ('B1', 'B2') else '2'})")
    if any(c["gold"].get("expected_difficulty") == "A2" for c in cases):
        errs.append("A2 cases are forbidden in the v2 dataset")

    sources = {c["input"].get("source") for c in cases}
    if len(sources) < 2:
        errs.append(f"need >=2 sources (bbc/guardian/npr), got {sorted(sources)}")

    if not any(c["gold"].get("dirty_fragments") or c["gold"].get("expected_outcome") == "reject"
               for c in cases):
        errs.append("need >=1 dirty-data case declaring expected_outcome")
    counts = [english_word_count(c["input"].get("original_text", "")) for c in cases]
    if not any(w < SHORT_ARTICLE_MAX_WORDS for w in counts):
        errs.append(f"need >=1 short article (<{SHORT_ARTICLE_MAX_WORDS} words)")
    if not any(w >= LONG_ARTICLE_MIN_WORDS for w in counts):
        errs.append(f"need >=1 long article (>={LONG_ARTICLE_MIN_WORDS} words)")
    if any(c["gold"].get("annotation_status") != "DRAFT_PM_REVIEW" for c in cases):
        errs.append("all golds must be DRAFT_PM_REVIEW")
    for case in cases:
        errs.extend(validate_case(case))
    return errs
