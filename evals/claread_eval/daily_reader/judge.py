"""LLM judge for the daily-reader regression rubric.

Stdlib-only OpenAI-compatible chat client (urllib). Fail-closed: when
the paid-call gate or credentials are missing, the judge returns
``skipped`` instead of erroring — deterministic checks still run.

Prompt contract lives in ``evals/rubrics/daily-reader-regression-v1.yaml``
(``judge:`` section) — this module only renders it, matching the evals
convention where rubric YAML is the single judge prompt source.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

ORIGINAL_TEXT_JUDGE_CHARS = 4000


def resolve_judge_config(rubric: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve judge endpoint config from env; None => judge skipped."""
    j = rubric.get("judge", {})
    if os.environ.get(j.get("gate_env", "CLAREAD_ALLOW_REAL_LLM_TESTS")) != "1":
        return None
    api_key = os.environ.get(j.get("api_key_env", "")) or os.environ.get(
        j.get("fallback_api_key_env", ""), ""
    )
    if not api_key:
        return None
    base_url = os.environ.get(j.get("endpoint_env", "")) or j.get(
        "fallback_base_url", "https://api.deepseek.com/v1"
    )
    model = os.environ.get(j.get("model_env", "")) or j.get("fallback_model", "deepseek-chat")
    return {"base_url": base_url.rstrip("/"), "api_key": api_key, "model": model,
            "temperature": float(j.get("temperature", 0.0))}


def build_judge_messages(rubric: dict[str, Any], case: dict[str, Any],
                         artifact: dict[str, Any]) -> list[dict[str, str]]:
    j = rubric["judge"]
    gold = case.get("gold", {})
    expected_difficulty = str(gold.get("expected_difficulty") or "").upper()
    dims_lines = []
    for d in j["dimensions"]:
        dims_lines.append(
            f"- {d['id']}（{d['label']}，{d['score_min']}-{d['score_max']} 分，"
            f"pass≥{d['pass_score']}）：{d['criteria'].strip()}"
        )
    system = (
        f"{j['system_role'].strip()}\n\n评分维度：\n" + "\n".join(dims_lines)
        + "\n\n本案例难度分档：\n"
        + str(j.get("difficulty_calibration", {}).get(expected_difficulty, "")).strip()
        + "\n\n输出必须是单个 JSON 对象，无其他文字，schema："
        + json.dumps(j["output"]["schema"], ensure_ascii=False)
    )

    evidence = {
        "article_title": artifact.get("title", ""),
        "expected_difficulty": gold.get("expected_difficulty"),
        "original_text_excerpt": (artifact.get("original_text") or "")[:ORIGINAL_TEXT_JUDGE_CHARS],
        "highlights": [
            {"text": h.get("text"), "gloss": h.get("gloss")}
            for h in artifact.get("highlights_json") or []
        ],
        "key_expressions": (artifact.get("takeaways_json") or {}).get("key_expressions", []),
        "sentence_notes": (artifact.get("takeaways_json") or {}).get("sentence_notes", []),
        "writing_moves": (artifact.get("takeaways_json") or {}).get("writing_moves", []),
        "discussion_questions": (artifact.get("takeaways_json") or {})
        .get("discussion_questions", []),
        "article_summary": (artifact.get("paragraph_notes_json") or {}).get("article_summary", ""),
        # A-3 adds title_zh/subtitle_zh/tags_zh to takeaways; absent before that.
        "title_zh": (artifact.get("takeaways_json") or {}).get("title_zh"),
        "subtitle_zh": (artifact.get("takeaways_json") or {}).get("subtitle_zh"),
    }
    user = (
        "以下是待评解析产物的证据（JSON）：\n"
        + json.dumps(evidence, ensure_ascii=False, indent=1)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _extract_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def run_judge(rubric: dict[str, Any], case: dict[str, Any],
              artifact: dict[str, Any], timeout_s: int = 120) -> dict[str, Any]:
    """Returns {"status": "ok"|"skipped"|"error", ...} — never raises."""
    cfg = resolve_judge_config(rubric)
    if cfg is None:
        return {"status": "skipped",
                "reason": "judge gate/credentials not configured "
                          "(CLAREAD_ALLOW_REAL_LLM_TESTS=1 + judge api key)"}
    messages = build_judge_messages(rubric, case, artifact)
    payload = json.dumps({
        "model": cfg["model"],
        "messages": messages,
        "temperature": cfg["temperature"],
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{cfg['base_url']}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg['api_key']}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        parsed = _extract_json(content)
        dims = parsed.get("dimensions", {})
        # clamp scores to rubric bounds
        valid: dict[str, Any] = {}
        bounds = {d["id"]: (d["score_min"], d["score_max"]) for d in rubric["judge"]["dimensions"]}
        for dim_id, (lo, hi) in bounds.items():
            entry = dims.get(dim_id) or {}
            score = entry.get("score")
            valid[dim_id] = {
                "score": max(lo, min(hi, int(score))) if isinstance(score, int | float) else None,
                "rationale": str(entry.get("rationale", ""))[:300],
            }
        usage = body.get("usage") or {}
        return {"status": "ok", "model": cfg["model"], "dimensions": valid,
                "usage": {"prompt_tokens": usage.get("prompt_tokens"),
                          "completion_tokens": usage.get("completion_tokens")}}
    except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError,
            ValueError) as exc:
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}


def judge_mean_score(judge_result: dict[str, Any]) -> float | None:
    if judge_result.get("status") != "ok":
        return None
    scores = [d["score"] for d in judge_result["dimensions"].values()
              if isinstance(d.get("score"), int)]
    return sum(scores) / len(scores) if scores else None
