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
REVIEW_KINDS = ("checkpoint", "language_target", "sentence_map", "transfer_task")
KEEP_MINOR_MIN_RATIO = 0.85
HEAVY_EDIT_MAX_RATIO = 0.15


def evaluate_review(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Acceptance thresholds for one reviewed case. Pure, never raises."""
    problems: list[str] = []
    n = len(items)
    factual = [it.get("item_id") for it in items
               if isinstance(it, dict) and it.get("factual_major_error") is True]
    if factual:
        problems.append(f"factual_major_error on items: {factual}")
    unknown = [it.get("item_id") for it in items
               if it.get("decision") not in DECISIONS]
    if unknown:
        # fail-closed: unknown decisions must never dilute the ratios
        problems.append(f"unknown decision on items: {unknown}")
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


def expected_review_item_ids(artifact: dict[str, Any]) -> list[str]:
    """Stable ids of every teaching point that must be reviewed 1:1:
    checkpoints, language targets, sentence maps and the transfer task."""
    pkg = artifact.get("learning_package") or {}
    ids = [f"checkpoint:{i}"
           for i in range(len(pkg.get("comprehension_checkpoints") or []))]
    ids += [f"language_target:{i}"
            for i in range(len(pkg.get("language_targets") or []))]
    ids += [f"sentence_map:{i}"
            for i in range(len(pkg.get("sentence_maps") or []))]
    if pkg.get("transfer_task"):
        ids.append("transfer_task:0")
    return ids


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _item_problems(item: Any) -> list[str]:
    if not isinstance(item, dict):
        return ["item is not an object"]
    problems: list[str] = []
    item_id = item.get("item_id")
    if not _nonempty_str(item_id):
        problems.append("item_id is empty")
        prefix = None
    else:
        prefix = str(item_id).split(":")[0]
    kind = item.get("kind")
    if kind not in REVIEW_KINDS:
        problems.append(f"kind {kind!r} is not one of {REVIEW_KINDS}")
    elif prefix is not None and kind != prefix:
        problems.append(f"kind {kind!r} does not match item_id prefix {prefix!r}")
    if item.get("decision") not in DECISIONS:
        problems.append(f"decision {item.get('decision')!r} is not one of {DECISIONS}")
    if not _nonempty_str(item.get("reviewer")):
        problems.append("reviewer is empty")
    if not _nonempty_str(item.get("reviewed_at")):
        problems.append("reviewed_at is empty")
    if not _nonempty_str(item.get("reason")):
        problems.append("reason is empty")
    if not isinstance(item.get("factual_major_error"), bool):
        problems.append("factual_major_error must be bool")
    suggested = item.get("suggested_edit")
    if suggested is not None and not isinstance(suggested, str):
        problems.append("suggested_edit must be a string or null")
    return problems


def review_status(case: dict[str, Any], artifact: dict[str, Any],
                  review_doc: dict[str, Any] | None) -> dict[str, Any]:
    """Status of the human gate for one case.

    Rejected runs carry no teaching points: the review gate is recorded
    as ``not_applicable_reject`` and counts as satisfied. Anything else
    without a completed review document stays HUMAN_REVIEW_PENDING; a
    reviewed document that is not a true 1:1 (unique item_id, matching
    count/case_id, legal item fields) is REVIEW_INCOMPLETE and never
    accepted.
    """
    rejected = (case.get("gold", {}).get("expected_outcome") == "reject"
                or (artifact.get("run_meta") or {}).get("outcome") == "reject")
    if rejected:
        return {"status": "not_applicable_reject", "accepted": True}
    if not isinstance(review_doc, dict) or review_doc.get("status") != "reviewed" \
            or not review_doc.get("items"):
        return {"status": HUMAN_REVIEW_PENDING, "accepted": False}
    expected = expected_review_item_ids(artifact)
    items = review_doc.get("items")
    problems: list[str] = []
    if review_doc.get("case_id") != case.get("case_id"):
        problems.append("case_id mismatch")
    if not isinstance(items, list):
        return {"status": "REVIEW_INCOMPLETE", "accepted": False,
                "problems": problems + ["items must be a list"]}
    got_ids = [str(it.get("item_id")) if isinstance(it, dict) else "" for it in items]
    if len(got_ids) != len(expected):
        problems.append(f"item count {len(got_ids)} != expected {len(expected)}")
    if len(set(got_ids)) != len(got_ids):
        problems.append("duplicate item_id")
    missing = sorted(set(expected) - set(got_ids))
    extra = sorted(set(got_ids) - set(expected))
    if missing or extra:
        problems.append(f"item_id set mismatch missing={missing} extra={extra}")
    for it in items:
        problems.extend(_item_problems(it))
    if problems:
        return {"status": "REVIEW_INCOMPLETE", "accepted": False,
                "problems": problems,
                "missing_items": missing, "extra_items": extra}
    verdict = evaluate_review(items)
    return {"status": "REVIEWED", "accepted": verdict["accepted"], **verdict}


def strata_all_accepted(strata: dict[str, dict[str, Any]]) -> bool:
    """Every stratum must independently be accepted — no averaging."""
    return bool(strata) and all(s.get("accepted") for s in strata.values())
