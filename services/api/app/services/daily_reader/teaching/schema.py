"""Shared artifact-shape validation (gold-free half of the evals schema).

Enum/shape checks only; the gold-equality branches of the evals
``validate_artifact`` intentionally stay on the eval side where gold
lives. All functions are pure and offline.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.daily_reader.teaching.normalize import normalize_text

ARTICLE_TYPES = ("news_report", "opinion_commentary", "explainer", "narrative_profile")
DIFFICULTIES = ("B1", "B2", "C1")  # A2 is legacy-compat only: never built here
EXPECTED_OUTCOMES = ("cleaned_publish", "reject")
CHECKPOINT_SKILLS = (
    "fact_location",
    "sequence",
    "main_idea",
    "inference",
    "causality",
    "source_attribution",
    "claim_evidence",
    "stance",
    "structure",
)
TRANSFER_TASK_KINDS = ("retell", "rewrite", "counter", "explain")
UNIT_ID_RE = re.compile(r"^u\d{2,3}$")

# P-5A title contract (blueprint title_zh/subtitle_zh/tags_zh):
# title_zh 8-18 字, subtitle_zh ≤30 字, tags_zh = 2-4 个全中文标签.
# Length bounds are enforced by the counts_in_bounds gate; shape/purity
# checks live here.
TITLE_ZH_MIN_LEN = 8
TITLE_ZH_MAX_LEN = 18
SUBTITLE_ZH_MAX_LEN = 30
TAGS_ZH_MIN_COUNT = 2
TAGS_ZH_MAX_COUNT = 4

_ASCII_ALNUM_RE = re.compile(r"[A-Za-z0-9]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _as_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _nonempty_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def substantive_unit_ids(case: Any) -> set[str]:
    """Return IDs of substantive (non-pure-dirty) reading units.

    A unit is pure dirty iff its full text after ``normalize_text`` exactly
    equals some non-empty dirty fragment's ``normalize_text``. Substring
    containment is not enough. Malformed input is fail-closed (no traceback).
    When no dirty_fragments are declared, all unit IDs are substantive —
    which is the production shape (runtime carries no gold).
    """
    try:
        if not isinstance(case, dict):
            return set()
        inp = case.get("input")
        if not isinstance(inp, dict):
            return set()
        units = inp.get("reading_units")
        if not isinstance(units, list):
            return set()
        unit_ids_set: set[str] = set()
        unit_texts: dict[str, str] = {}
        for u in units:
            if not isinstance(u, dict):
                continue
            uid = u.get("id")
            txt = u.get("text")
            if isinstance(uid, str) and UNIT_ID_RE.match(uid):
                unit_ids_set.add(uid)
                if isinstance(txt, str):
                    unit_texts[uid] = txt
        gold = case.get("gold")
        if not isinstance(gold, dict):
            return unit_ids_set
        dirty = gold.get("dirty_fragments")
        if not isinstance(dirty, list):
            return unit_ids_set
        norm_frags: set[str] = set()
        for f in dirty:
            if isinstance(f, str):
                n = normalize_text(f)
                if n:
                    norm_frags.add(n)
        if not norm_frags:
            return unit_ids_set
        pure_dirty = {pid for pid, txt in unit_texts.items() if normalize_text(txt) in norm_frags}
        return unit_ids_set - pure_dirty
    except Exception:
        return set()


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
    if (
        artifact.get("source_assets") is not None
        and _as_dict(artifact.get("source_assets")) is None
    ):
        errs.append("source_assets must be an object")
    if meta.get("outcome") == "reject":
        return errs  # rejected runs carry no teaching package

    bp = _as_dict(artifact.get("lesson_blueprint")) or {}
    if (
        artifact.get("lesson_blueprint") is not None
        and _as_dict(artifact.get("lesson_blueprint")) is None
    ):
        errs.append("lesson_blueprint must be an object")
    if bp.get("article_type") not in ARTICLE_TYPES:
        errs.append(
            f"lesson_blueprint.article_type {bp.get('article_type')!r} "
            f"must be one of {ARTICLE_TYPES}"
        )
    if bp.get("effective_difficulty") not in DIFFICULTIES:
        errs.append(
            f"lesson_blueprint.effective_difficulty "
            f"{bp.get('effective_difficulty')!r} must be one of {DIFFICULTIES}"
        )
    pkg = _as_dict(artifact.get("learning_package")) or {}
    if (
        artifact.get("learning_package") is not None
        and _as_dict(artifact.get("learning_package")) is None
    ):
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

    # P-5A title contract: shape/purity of the Chinese headline fields
    # (presence itself is enforced by the eval-side dataset contract).
    title = bp.get("title_zh")
    if title is not None and (not isinstance(title, str) or not title.strip()):
        errs.append(f"lesson_blueprint.title_zh must be a non-empty string, got {title!r}")
    subtitle = bp.get("subtitle_zh")
    if subtitle is not None and (not isinstance(subtitle, str) or not subtitle.strip()):
        errs.append(f"lesson_blueprint.subtitle_zh must be a non-empty string, got {subtitle!r}")
    tags = bp.get("tags_zh")
    if tags is not None:
        if not isinstance(tags, list):
            errs.append(f"lesson_blueprint.tags_zh must be a list, got {tags!r}")
        else:
            for i, tag in enumerate(tags):
                if not isinstance(tag, str) or not tag.strip():
                    errs.append(f"lesson_blueprint.tags_zh[{i}] must be a non-empty string")
                elif _ASCII_ALNUM_RE.search(tag) or not _CJK_RE.search(tag):
                    errs.append(
                        f"lesson_blueprint.tags_zh[{i}] must be an all-Chinese label: {tag!r}"
                    )
    return errs
