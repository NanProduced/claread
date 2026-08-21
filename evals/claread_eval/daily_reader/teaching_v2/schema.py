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
TRANSFER_TASK_KINDS = ("retell", "rewrite", "counter", "explain")
GOLD_REQUIRED_FIELDS = (
    "annotation_status", "expected_outcome", "expected_difficulty", "article_type",
    "dirty_fragments", "rejection_reasons", "key_evidence", "core_expressions",
    "forbidden_facts", "acceptable_transfer_directions", "expected_translation_coverage",
)
_GOLD_ITEM_FIELDS = {
    "key_evidence": ("source_quote", "acceptable_answer_points_zh", "paragraph_ids"),
    "core_expressions": ("expression", "source_quote", "meaning_zh", "teaching_value",
                         "paragraph_ids"),
    "forbidden_facts": ("claim_zh", "reason"),
    "acceptable_transfer_directions": (
        "task_kind", "required_learning_target", "acceptable_direction_zh"),
}

SHORT_ARTICLE_MAX_WORDS = 800   # execution default: short < 800 English words
LONG_ARTICLE_MIN_WORDS = 1500   # execution default: long >= 1500 English words
UNIT_ID_RE = re.compile(r"^u\d{2,3}$")


def english_word_count(text: Any) -> int:
    """Deterministic word count: whitespace split (the single source of truth
    for the short/long article thresholds). Non-strings count as 0."""
    if not isinstance(text, str):
        return 0
    return len(text.split())


def collapse_whitespace(s: str) -> str:
    """Whitespace-only normalization (case preserved) for verbatim checks."""
    return re.sub(r"\s+", " ", s or "").strip()


def _as_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _as_list(value: Any) -> list[Any] | None:
    return value if isinstance(value, list) else None


def _nonempty_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def unit_ids(case: dict[str, Any]) -> set[str]:
    inp = _as_dict(case.get("input")) or {}
    units = _as_list(inp.get("reading_units")) or []
    return {u["id"] for u in units if isinstance(u, dict) and isinstance(u.get("id"), str)}


def _anchors(case: dict[str, Any], ids: Any, where: str, errs: list[str]) -> None:
    if not isinstance(ids, list):
        errs.append(f"{where}: paragraph_ids must be a list")
        return
    valid = unit_ids(case)
    if not ids:
        errs.append(f"{where}: paragraph_ids must not be empty")
        return
    for pid in ids:
        if not isinstance(pid, str) or pid not in valid:
            errs.append(f"{where}: anchor '{pid}' does not resolve to a reading unit")


def _quote_substring(case: dict[str, Any], quote: Any, where: str, errs: list[str]) -> None:
    if not isinstance(quote, str):
        errs.append(f"{where}: source_quote must be a string")
        return
    normalized = collapse_whitespace(quote)
    if not normalized:
        errs.append(f"{where}: source_quote is empty after whitespace normalization")
        return
    inp = _as_dict(case.get("input")) or {}
    original = collapse_whitespace(inp.get("original_text") if isinstance(
        inp.get("original_text"), str) else "")
    if normalized not in original:
        errs.append(f"{where}: source_quote is not a verbatim (whitespace-normalized) "
                    f"substring of original_text: {str(quote)[:80]!r}")


def _quote_spans_declared_units(case: dict[str, Any], quote: Any, paragraph_ids: Any,
                                where: str, errs: list[str]) -> None:
    """P-2G-C gold gate: a source_quote must be a contiguous (whitespace-
    normalized) substring of at least one of the declared paragraph_ids'
    reading units. A quote that only exists in other paragraphs is a
    schema error, not just an anchor misalignment. Malformed inputs are
    already reported by ``_quote_substring``/_anchors, so this is skipped
    for them — this helper never raises."""
    if not isinstance(quote, str) or not collapse_whitespace(quote):
        return  # covered by _quote_substring
    if not isinstance(paragraph_ids, list) or not paragraph_ids:
        return  # covered by _anchors
    inp = _as_dict(case.get("input")) or {}
    units = _as_list(inp.get("reading_units")) or []
    by_id = {u.get("id"): u for u in units if isinstance(u, dict)}
    normalized = collapse_whitespace(quote)
    declared_texts: list[str] = []
    for pid in paragraph_ids:
        u = by_id.get(pid)
        if not isinstance(u, dict):
            continue
        text = u.get("text")
        if isinstance(text, str):
            declared_texts.append(collapse_whitespace(text))
    if declared_texts and not any(normalized in t for t in declared_texts):
        errs.append(f"{where}: source_quote must be a contiguous substring of at "
                    f"least one declared paragraph unit: {str(quote)[:80]!r}")


def _require_str_list(value: Any, where: str, errs: list[str], *, allow_empty: bool) -> None:
    if not isinstance(value, list):
        errs.append(f"{where} must be a list")
        return
    if not allow_empty and not value:
        errs.append(f"{where} must not be empty")
    for i, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errs.append(f"{where}[{i}] must be a non-empty string")


def _require_gold_items(gold: dict[str, Any], field: str, required_keys: tuple[str, ...],
                        case: dict[str, Any], errs: list[str], *, allow_empty: bool) -> None:
    items = gold.get(field)
    if not isinstance(items, list):
        errs.append(f"gold.{field} must be a list")
        return
    if not allow_empty and not items:
        errs.append(f"gold.{field} must not be empty")
        return
    for i, item in enumerate(items):
        where = f"gold.{field}[{i}]"
        if not isinstance(item, dict):
            errs.append(f"{where} must be an object")
            continue
        for key in required_keys:
            if key not in item:
                errs.append(f"{where}.{key} is required")
        if "source_quote" in required_keys:
            _quote_substring(case, item.get("source_quote"), where, errs)
        if "paragraph_ids" in required_keys:
            _anchors(case, item.get("paragraph_ids"), where, errs)
        if "source_quote" in required_keys and "paragraph_ids" in required_keys:
            _quote_spans_declared_units(case, item.get("source_quote"),
                                        item.get("paragraph_ids"), where, errs)
        if "acceptable_answer_points_zh" in required_keys:
            _require_str_list(item.get("acceptable_answer_points_zh"),
                              f"{where}.acceptable_answer_points_zh", errs, allow_empty=False)
        for key in ("expression", "meaning_zh", "teaching_value", "claim_zh", "reason",
                    "required_learning_target", "acceptable_direction_zh"):
            if key in required_keys and _nonempty_str(item.get(key)) is None:
                errs.append(f"{where}.{key} must be a non-empty string")
        if "task_kind" in required_keys and item.get("task_kind") not in TRANSFER_TASK_KINDS:
            errs.append(f"{where}.task_kind must be one of {TRANSFER_TASK_KINDS}")


def validate_case(case: dict[str, Any]) -> list[str]:
    """Validate one schema-2 case incl. gold semantics. [] == valid.
    Never raises: malformed nested dict/list/item become schema errors."""
    if not isinstance(case, dict):
        return ["case must be an object"]
    errs: list[str] = []
    if _nonempty_str(case.get("case_id")) is None:
        errs.append("case_id is required")
    if case.get("schema_version") != 2:
        errs.append("schema_version must be 2")
    if case.get("dataset_id") != "daily-reader-teaching-v2":
        errs.append("dataset_id must be daily-reader-teaching-v2")

    origin = _as_dict(case.get("origin"))
    if origin is None:
        errs.append("origin must be an object")
        origin = {}
    if origin.get("frozen_real_article") is not True:
        errs.append("origin.frozen_real_article must be true (synthetic cases forbidden)")
    for field in ("source", "source_url", "captured_at"):
        if not origin.get(field):
            errs.append(f"origin.{field} is required")

    inp = _as_dict(case.get("input"))
    if inp is None:
        errs.append("input must be an object")
        inp = {}
    for field in ("title", "source", "source_url", "original_text"):
        if not inp.get(field) or not isinstance(inp.get(field), str):
            errs.append(f"input.{field} is required")

    units = _as_list(inp.get("reading_units"))
    if not units:
        errs.append("input.reading_units must not be empty")
        units = []
    seen_ids: set[str] = set()
    for i, u in enumerate(units):
        if not isinstance(u, dict):
            errs.append(f"reading_units[{i}] must be an object")
            continue
        uid = u.get("id", "")
        if not isinstance(uid, str) or not UNIT_ID_RE.match(uid):
            errs.append(f"reading unit id '{uid}' is not a stable uNN id")
        if uid in seen_ids:
            errs.append(f"duplicate reading unit id '{uid}'")
        seen_ids.add(uid)
        if not isinstance(u.get("text"), str) or not u.get("text", "").strip():
            errs.append(f"reading unit '{uid}' has empty text")

    gold = _as_dict(case.get("gold"))
    if gold is None:
        errs.append("gold must be an object")
        return errs
    for field in GOLD_REQUIRED_FIELDS:
        if field not in gold:
            errs.append(f"gold.{field} is required")
    if gold.get("annotation_status") not in ANNOTATION_STATUSES:
        errs.append(f"gold.annotation_status must be one of {ANNOTATION_STATUSES}")
    if gold.get("expected_outcome") not in EXPECTED_OUTCOMES:
        errs.append(f"gold.expected_outcome must be one of {EXPECTED_OUTCOMES}")
    if gold.get("expected_difficulty") not in DIFFICULTIES:
        errs.append(f"gold.expected_difficulty must be one of {DIFFICULTIES} (no A2)")
    if gold.get("article_type") not in ARTICLE_TYPES:
        errs.append(f"gold.article_type must be one of {ARTICLE_TYPES}")

    publish = gold.get("expected_outcome") == "cleaned_publish"
    _require_str_list(gold.get("dirty_fragments"), "gold.dirty_fragments", errs,
                      allow_empty=True)
    _require_str_list(gold.get("rejection_reasons"), "gold.rejection_reasons", errs,
                      allow_empty=not (gold.get("expected_outcome") == "reject"))
    for field, keys in _GOLD_ITEM_FIELDS.items():
        allow_empty = not publish or field == "forbidden_facts"
        if field in gold or not allow_empty:
            _require_gold_items(gold, field, keys, case, errs, allow_empty=allow_empty)

    cov = _as_dict(gold.get("expected_translation_coverage"))
    if cov is None:
        errs.append("gold.expected_translation_coverage must be an object")
    elif cov.get("policy") not in COVERAGE_POLICIES:
        errs.append(f"gold.expected_translation_coverage.policy must be one of "
                    f"{COVERAGE_POLICIES}")
    else:
        _anchors(case, cov.get("required_paragraph_ids"), "translation_coverage.required", errs)
        _anchors(case, cov.get("allowed_paragraph_ids"), "translation_coverage.allowed", errs)
        req = set(pid for pid in (cov.get("required_paragraph_ids") or [])
                  if isinstance(pid, str))
        allowed = set(pid for pid in (cov.get("allowed_paragraph_ids") or [])
                      if isinstance(pid, str))
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
    """Minimal v2 artifact shape validation. [] == valid. Never raises."""
    errs: list[str] = []
    if not isinstance(artifact, dict):
        return ["artifact must be an object"]
    case = case if isinstance(case, dict) else {}
    case_id = _nonempty_str(case.get("case_id"))
    art_id = artifact.get("case_id")
    if _nonempty_str(art_id) is None:
        errs.append("artifact.case_id is required")
    elif case_id is not None and art_id != case_id:
        errs.append(f"artifact.case_id {art_id!r} != case.case_id {case_id!r}")

    meta = _as_dict(artifact.get("run_meta")) or {}
    if artifact.get("run_meta") is not None and _as_dict(artifact.get("run_meta")) is None:
        errs.append("run_meta must be an object")
    if meta.get("outcome") not in EXPECTED_OUTCOMES:
        errs.append(f"run_meta.outcome must be one of {EXPECTED_OUTCOMES}")
    rc = meta.get("refinement_count", 0)
    if not isinstance(rc, int) or isinstance(rc, bool) or rc < 0:
        errs.append("run_meta.refinement_count must be a non-negative int (bool forbidden)")
    if meta.get("usage") is not None and _as_dict(meta.get("usage")) is None:
        errs.append("run_meta.usage must be an object")
    if artifact.get("source_assets") is not None and _as_dict(
            artifact.get("source_assets")) is None:
        errs.append("source_assets must be an object")
    if meta.get("outcome") == "reject":
        return errs  # rejected runs carry no teaching package

    gold = _as_dict(case.get("gold")) or {}
    bp = _as_dict(artifact.get("lesson_blueprint")) or {}
    if artifact.get("lesson_blueprint") is not None and _as_dict(
            artifact.get("lesson_blueprint")) is None:
        errs.append("lesson_blueprint must be an object")
    if bp.get("article_type") not in ARTICLE_TYPES:
        errs.append(f"lesson_blueprint.article_type {bp.get('article_type')!r} "
                    f"must be one of {ARTICLE_TYPES}")
    elif bp.get("article_type") != gold.get("article_type"):
        errs.append(f"lesson_blueprint.article_type {bp.get('article_type')!r} "
                    f"must equal gold.article_type {gold.get('article_type')!r}")
    if bp.get("effective_difficulty") not in DIFFICULTIES:
        errs.append(f"lesson_blueprint.effective_difficulty "
                    f"{bp.get('effective_difficulty')!r} must be one of {DIFFICULTIES}")
    elif bp.get("effective_difficulty") != gold.get("expected_difficulty"):
        errs.append(f"lesson_blueprint.effective_difficulty "
                    f"{bp.get('effective_difficulty')!r} must equal "
                    f"gold.expected_difficulty {gold.get('expected_difficulty')!r}")
    pkg = _as_dict(artifact.get("learning_package")) or {}
    if artifact.get("learning_package") is not None and _as_dict(
            artifact.get("learning_package")) is None:
        errs.append("learning_package must be an object")
        return errs
    for name in ("comprehension_checkpoints", "language_targets", "sentence_maps"):
        items = pkg.get(name)
        if items is None:
            continue
        if not isinstance(items, list):
            errs.append(f"learning_package.{name} must be a list")
            continue
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                errs.append(f"learning_package.{name}[{i}] must be an object")
                continue
            if name == "comprehension_checkpoints" and item.get("skill") not in CHECKPOINT_SKILLS:
                errs.append(f"checkpoint skill '{item.get('skill')}' is not a P-1 §3.3 skill")
    tt = pkg.get("transfer_task")
    if isinstance(tt, dict) and tt.get("task_kind") not in TRANSFER_TASK_KINDS:
        errs.append(f"transfer_task.task_kind must be one of {TRANSFER_TASK_KINDS}")
    elif tt is not None and not isinstance(tt, dict):
        errs.append("transfer_task must be an object")
    return errs


def validate_dataset_coverage(cases: Any) -> list[str]:
    """Dataset coverage matrix per the sample contract. [] == valid.
    Malformed cases (missing gold/input) land in the error list — this
    function never raises."""
    if not isinstance(cases, list):
        return [f"dataset must be a list of cases, got {type(cases).__name__}"]
    errs: list[str] = []
    n = len(cases)
    if not 8 <= n <= 12:
        errs.append(f"dataset must contain 8-12 frozen real articles, got {n}")

    golds: list[dict[str, Any]] = []
    inputs: list[dict[str, Any]] = []
    for i, c in enumerate(cases):
        if not isinstance(c, dict):
            errs.append(f"cases[{i}] must be an object")
            golds.append({})
            inputs.append({})
            continue
        gold = _as_dict(c.get("gold"))
        inp = _as_dict(c.get("input"))
        if gold is None:
            errs.append(f"cases[{i}].gold must be an object")
            gold = {}
        if inp is None:
            errs.append(f"cases[{i}].input must be an object")
            inp = {}
        golds.append(gold)
        inputs.append(inp)
    producible = [g for g in golds if g.get("expected_outcome") == "cleaned_publish"]
    for t in ARTICLE_TYPES:
        if sum(1 for g in producible if g.get("article_type") == t) < 2:
            errs.append(f"article_type {t} needs >=2 producible cases "
                        f"(reject cases do not count toward quotas)")
    for d in DIFFICULTIES:
        n = sum(1 for g in producible if g.get("expected_difficulty") == d)
        minimum = 3 if d in ("B1", "B2") else 2
        if n < minimum:
            errs.append(f"difficulty {d} below minimum ({minimum} cleaned_publish)")
    if any(g.get("expected_difficulty") == "A2" for g in golds):
        errs.append("A2 cases are forbidden in the v2 dataset")

    sources = {inp.get("source") for inp in inputs}
    if len(sources) < 2:
        errs.append(f"need >=2 sources (bbc/guardian/npr), got {sorted(sources, key=str)}")
    outside = sources - {"bbc", "guardian", "npr"}
    if outside:
        errs.append(f"sources outside the bbc/guardian/npr allowlist: "
                    f"{sorted(outside, key=str)}")

    if not any(g.get("dirty_fragments") or g.get("expected_outcome") == "reject"
               for g in golds):
        errs.append("need >=1 dirty-data case declaring expected_outcome")
    counts = [english_word_count(inp.get("original_text", "")) for inp in inputs]
    if not any(w < SHORT_ARTICLE_MAX_WORDS for w in counts):
        errs.append(f"need >=1 short article (<{SHORT_ARTICLE_MAX_WORDS} words)")
    if not any(w >= LONG_ARTICLE_MIN_WORDS for w in counts):
        errs.append(f"need >=1 long article (>={LONG_ARTICLE_MIN_WORDS} words)")
    if any(g.get("annotation_status") != "DRAFT_PM_REVIEW" for g in golds):
        errs.append("all golds must be DRAFT_PM_REVIEW")
    for case in cases:
        if isinstance(case, dict):
            errs.extend(validate_case(case))
    return errs
