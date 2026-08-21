"""Eight-dimension LLM judge contract for teaching v2 (offline in P-2).

``build_judge_messages_v2`` renders the prompt from the rubric YAML
(rubric-as-single-source, same convention as v1) with the FULL original
text (no truncation), gold key evidence / forbidden facts, effective
difficulty, article type and the complete teaching package — one call
scores all eight dimensions.

``parse_judge_output`` is fail-closed: exactly 8 dimensions (no missing,
no extra), integer scores 1-5 (no clamp, no floats), non-empty rationale,
JSON/provider errors become status=error. This package implements NO
network path; judge execution lands in P-3.
"""

from __future__ import annotations

import json
import re
from typing import Any

SEMANTIC_NOT_RUN = "SEMANTIC_NOT_RUN"


def build_judge_messages_v2(rubric: dict[str, Any], case: dict[str, Any],
                            artifact: dict[str, Any]) -> list[dict[str, str]]:
    j = rubric["judge"]
    gold = case.get("gold", {})
    bp = artifact.get("lesson_blueprint") or {}
    difficulty = str(bp.get("effective_difficulty")
                     or gold.get("expected_difficulty") or "").upper()
    dims_lines = [
        f"- {d['id']}（{d['label']}，{d['score_min']}-{d['score_max']} 分，"
        f"pass≥{d['pass_score']}）：{d['criteria'].strip()}"
        for d in j["dimensions"]
    ]
    system = (
        f"{j['system_role'].strip()}\n\n评分维度（必须恰好逐项评分，不得增减）：\n"
        + "\n".join(dims_lines)
        + "\n\n本案例难度校准（按 effective_difficulty）：\n"
        + str(j.get("difficulty_calibration", {}).get(difficulty, "")).strip()
        + "\n\n输出必须是单个 JSON 对象，无其他文字，schema："
        + json.dumps(j["output"]["schema"], ensure_ascii=False)
    )
    payload = {
        "article_type": bp.get("article_type") or gold.get("article_type"),
        "effective_difficulty": bp.get("effective_difficulty")
                                or gold.get("expected_difficulty"),
        "gold_key_evidence": gold.get("key_evidence", []),
        "gold_forbidden_facts": gold.get("forbidden_facts", []),
        "gold_core_expressions": gold.get("core_expressions", []),
        "gold_acceptable_transfer_directions":
            gold.get("acceptable_transfer_directions", []),
        "lesson_blueprint": bp,
        "learning_package": artifact.get("learning_package") or {},
        "source_assets": artifact.get("source_assets") or {},
    }
    original_text = case.get("input", {}).get("original_text", "")
    # original text goes in verbatim (no JSON escaping, no truncation) so the
    # judge sees byte-identical source; everything else is one JSON block.
    user = ("以下是完整原文与完整教学包（未截断）。请依据 gold 关键证据与禁出事实，"
            "对教学包一次性评全部八个维度：\n\n[original_text]\n"
            + original_text
            + "\n\n[gold_and_artifact]\n"
            + json.dumps(payload, ensure_ascii=False, indent=1))
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _extract_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        m = re.search(r"\{.*\}", text or "", re.S)
        if m:
            return json.loads(m.group(0))
        raise


def validate_judge_payload(rubric: dict[str, Any], payload: Any) -> dict[str, Any]:
    """Strict eight-dimension contract. Never trusts caller ``status``.

    Used by both ``parse_judge_output`` and ``score_case`` so parse and
    verdict cannot drift.
    """
    expected = {d["id"]: (d["score_min"], d["score_max"])
                for d in rubric["judge"]["dimensions"]}
    dims = payload.get("dimensions") if isinstance(payload, dict) else None
    if not isinstance(dims, dict):
        return {"status": "error", "reason": "missing 'dimensions' object"}
    missing = sorted(set(expected) - set(dims))
    extra = sorted(set(dims) - set(expected))
    if missing or extra:
        return {"status": "error",
                "reason": f"dimension set mismatch: missing={missing} extra={extra}"}
    valid: dict[str, Any] = {}
    for dim_id, (lo, hi) in expected.items():
        entry = dims.get(dim_id)
        if not isinstance(entry, dict):
            return {"status": "error",
                    "reason": f"dimension {dim_id}: entry {entry!r} is not an object"}
        score = entry.get("score")
        rationale = entry.get("rationale")
        # bool is an int subclass -> reject explicitly; floats rejected (no clamp)
        if isinstance(score, bool) or not isinstance(score, int) or not lo <= score <= hi:
            return {"status": "error",
                    "reason": f"dimension {dim_id}: score {score!r} is not an int in "
                              f"{lo}-{hi} (clamping forbidden)"}
        # null/true/number rationales must not stringify into a pass ("None")
        if not isinstance(rationale, str) or not rationale.strip():
            return {"status": "error",
                    "reason": f"dimension {dim_id}: rationale {rationale!r} is not a "
                              f"non-empty string"}
        valid[dim_id] = {"score": score, "rationale": rationale.strip()}
    return {"status": "ok", "dimensions": valid}


def parse_judge_output(rubric: dict[str, Any], raw_text: str) -> dict[str, Any]:
    """Fail-closed parse. Never raises; returns status ok|error."""
    try:
        parsed = _extract_json(raw_text)
    except (json.JSONDecodeError, ValueError) as exc:
        return {"status": "error", "reason": f"invalid JSON: {exc}"}
    return validate_judge_payload(rubric, parsed)


def judge_mean_v2(judge_result: dict[str, Any]) -> float | None:
    if judge_result.get("status") != "ok":
        return None
    scores = [d["score"] for d in judge_result.get("dimensions", {}).values()]
    return sum(scores) / len(scores) if scores else None
