"""Per-teaching-point human review contract (P-1 §9.3) for teaching v2.

Every checkpoint / language target / sentence map / transfer task gets a
decision ``keep | minor_edit | major_edit | delete`` plus reviewer,
reviewed_at, factual_major_error, reason and suggested_edit. Acceptance
is a pure function: factual major errors == 0, keep+minor_edit >= 85%,
per-case major_edit+delete <= 15%. Strata (article_type / difficulty /
source) are judged independently — a failing stratum is never averaged
away. Unreviewed runs stay ``HUMAN_REVIEW_PENDING``; this package never
writes ``human_approved``.
"""

from __future__ import annotations

from typing import Any

HUMAN_REVIEW_PENDING = "HUMAN_REVIEW_PENDING"

DECISIONS = ("keep", "minor_edit", "major_edit", "delete")
KEEP_MINOR_MIN_RATIO = 0.85
HEAVY_EDIT_MAX_RATIO = 0.15


def evaluate_review(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Acceptance thresholds for one reviewed case. Pure, never raises."""
    problems: list[str] = []
    n = len(items)
    factual = [it.get("item_id") for it in items if it.get("factual_major_error")]
    if factual:
        problems.append(f"factual_major_error on items: {factual}")
    if n == 0:
        problems.append("no reviewed items")
        keep_minor_ratio = 0.0
        heavy_ratio = 1.0
    else:
        keep_minor = sum(1 for it in items
                         if it.get("decision") in ("keep", "minor_edit"))
        heavy = sum(1 for it in items
                    if it.get("decision") in ("major_edit", "delete"))
        keep_minor_ratio = keep_minor / n
        heavy_ratio = heavy / n
        if keep_minor_ratio < KEEP_MINOR_MIN_RATIO:
            problems.append(
                f"keep+minor_edit ratio {keep_minor_ratio:.2%} < 85%")
        if heavy_ratio > HEAVY_EDIT_MAX_RATIO:
            problems.append(
                f"major_edit+delete ratio {heavy_ratio:.2%} > 15%")
    return {"accepted": not problems, "problems": problems,
            "item_count": n,
            "keep_minor_ratio": round(keep_minor_ratio, 4),
            "heavy_edit_ratio": round(heavy_ratio, 4),
            "factual_major_errors": len(factual)}


def review_status(case: dict[str, Any], artifact: dict[str, Any],
                  review_doc: dict[str, Any] | None) -> dict[str, Any]:
    """Status of the human gate for one case.

    Rejected runs carry no teaching points: the review gate is recorded
    as ``not_applicable_reject`` and counts as satisfied. Anything else
    without a completed review document stays HUMAN_REVIEW_PENDING.
    """
    rejected = (case.get("gold", {}).get("expected_outcome") == "reject"
                or (artifact.get("run_meta") or {}).get("outcome") == "reject")
    if rejected:
        return {"status": "not_applicable_reject", "accepted": True}
    if not review_doc or review_doc.get("status") != "reviewed" \
            or not review_doc.get("items"):
        return {"status": HUMAN_REVIEW_PENDING, "accepted": False}
    verdict = evaluate_review(review_doc.get("items") or [])
    return {"status": "REVIEWED", "accepted": verdict["accepted"], **verdict}


def strata_all_accepted(strata: dict[str, dict[str, Any]]) -> bool:
    """Every stratum must independently be accepted — no averaging."""
    return bool(strata) and all(s.get("accepted") for s in strata.values())
