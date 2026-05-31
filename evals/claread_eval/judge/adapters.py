from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from claread_eval.schemas.judge import (
    JudgeCaseResult,
    JudgeCriterionResult,
    JudgeVerdict,
)
from claread_eval.schemas.rubric import RubricCaseInput, RubricCriterion


class JudgeAdapterError(RuntimeError):
    pass


class JudgeAdapterConfigError(JudgeAdapterError):
    pass


class JudgeAdapterClient(Protocol):
    adapter_kind: str

    async def judge_case(self, packet: RubricCaseInput) -> JudgeCaseResult:
        ...


@dataclass
class FakeJudgeAdapterClient:
    adapter_kind: str = "fake"

    async def judge_case(self, packet: RubricCaseInput) -> JudgeCaseResult:
        output = packet.output_excerpt or {}
        warning_count = _list_count(output.get("warnings"))
        drop_count = _list_count(output.get("drop_log"))
        output_count = (
            _list_count(output.get("sentence_entries"))
            + _list_count(output.get("translations"))
            + _list_count(output.get("inline_marks"))
        )
        criteria = [
            _fake_criterion_result(
                criterion,
                output_count=output_count,
                issue_count=warning_count + drop_count,
            )
            for criterion in packet.criteria
        ]
        overall_score = _weighted_average(criteria, packet.criteria)
        threshold = _weighted_threshold(packet.criteria)
        verdict = _verdict_from_criteria(criteria, threshold)
        return JudgeCaseResult(
            case_id=packet.case_id,
            run_id=packet.run_id,
            status="succeeded",
            verdict=verdict,
            overall_score=overall_score,
            pass_threshold=threshold,
            criteria=criteria,
            summary=_fake_summary(verdict, warning_count=warning_count, drop_count=drop_count),
            judge_adapter_kind=self.adapter_kind,
        )


@dataclass
class OpenAICompatibleJudgeAdapterClient:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 60.0
    adapter_kind: str = "llm"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> OpenAICompatibleJudgeAdapterClient:
        values = env or os.environ
        base_url = values.get("CLAREAD_EVAL_JUDGE_BASE_URL", "").strip()
        api_key = values.get("CLAREAD_EVAL_JUDGE_API_KEY", "").strip()
        model = values.get("CLAREAD_EVAL_JUDGE_MODEL", "").strip()
        timeout_raw = values.get("CLAREAD_EVAL_JUDGE_TIMEOUT_SECONDS", "60").strip()
        missing = [
            key
            for key, value in (
                ("CLAREAD_EVAL_JUDGE_BASE_URL", base_url),
                ("CLAREAD_EVAL_JUDGE_API_KEY", api_key),
                ("CLAREAD_EVAL_JUDGE_MODEL", model),
            )
            if not value
        ]
        if missing:
            raise JudgeAdapterConfigError(
                f"Missing required judge LLM environment variables: {', '.join(missing)}"
            )
        try:
            timeout_seconds = float(timeout_raw)
        except ValueError:
            timeout_seconds = 60.0
        if timeout_seconds <= 0:
            timeout_seconds = 60.0
        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
        )

    async def judge_case(self, packet: RubricCaseInput) -> JudgeCaseResult:
        return await asyncio.to_thread(self._judge_case_sync, packet)

    def _judge_case_sync(self, packet: RubricCaseInput) -> JudgeCaseResult:
        request = urllib.request.Request(
            _chat_completions_url(self.base_url),
            data=json.dumps(self._request_payload(packet)).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:500]
            raise JudgeAdapterError(f"Judge LLM HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise JudgeAdapterError(f"Judge LLM request failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise JudgeAdapterError("Judge LLM response was not valid JSON.") from exc

        content = _extract_message_content(payload)
        try:
            raw_result = _parse_json_object(content)
        except ValueError as exc:
            raise JudgeAdapterError(str(exc)) from exc
        return normalize_judge_case_result(
            raw_result,
            packet=packet,
            adapter_kind=self.adapter_kind,
        )

    def _request_payload(self, packet: RubricCaseInput) -> dict[str, Any]:
        return {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an evaluator for Claread article analysis outputs. "
                        "Score only the provided bounded packet. Return strict JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "instructions": {
                                "return_shape": {
                                    "verdict": "pass|fail|needs_review",
                                    "overall_score": "weighted score on the rubric scale",
                                    "summary": "short reason",
                                    "criteria": [
                                        {
                                            "criterion_id": "string",
                                            "score": "number",
                                            "passed": "boolean",
                                            "reason": "short reason",
                                            "evidence": ["short quotes or observations"],
                                        }
                                    ],
                                },
                                "safety": (
                                    "Do not infer hidden data. "
                                    "If evidence is insufficient, use needs_review."
                                ),
                            },
                            "packet": packet.model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }


def create_judge_adapter(
    adapter_kind: str,
    *,
    env: dict[str, str] | None = None,
) -> JudgeAdapterClient:
    if adapter_kind == "fake":
        return FakeJudgeAdapterClient()
    if adapter_kind in {"llm", "openai_compatible"}:
        return OpenAICompatibleJudgeAdapterClient.from_env(env)
    raise JudgeAdapterConfigError(
        f"Unsupported judge_adapter_kind: {adapter_kind}. Expected fake or llm."
    )


def normalize_judge_case_result(
    raw: dict[str, Any],
    *,
    packet: RubricCaseInput,
    adapter_kind: str,
) -> JudgeCaseResult:
    criteria_payload = raw.get("criteria") or raw.get("criterion_results") or []
    criteria_by_id = {
        str(item.get("criterion_id") or item.get("id")): item
        for item in criteria_payload
        if isinstance(item, dict) and (item.get("criterion_id") or item.get("id"))
    }
    criteria = []
    for criterion in packet.criteria:
        item = criteria_by_id.get(criterion.id, {})
        score = _coerce_score(item.get("score"), criterion)
        passed = item.get("passed")
        if not isinstance(passed, bool):
            passed = score is not None and score >= criterion.pass_score
        criteria.append(
            JudgeCriterionResult(
                criterion_id=criterion.id,
                label=criterion.label,
                score=score,
                passed=passed,
                reason=str(item.get("reason") or item.get("feedback") or "")[:1000],
                evidence=_evidence_list(item.get("evidence")),
            )
        )

    threshold = _weighted_threshold(packet.criteria)
    overall_score = _coerce_float(raw.get("overall_score"))
    if overall_score is None:
        overall_score = _weighted_average(criteria, packet.criteria)
    verdict = _normalize_verdict(raw.get("verdict"), criteria, threshold)
    return JudgeCaseResult(
        case_id=packet.case_id,
        run_id=packet.run_id,
        status="succeeded",
        verdict=verdict,
        overall_score=overall_score,
        pass_threshold=threshold,
        criteria=criteria,
        summary=str(raw.get("summary") or raw.get("reason") or "")[:1000],
        judge_adapter_kind=adapter_kind,
    )


def error_case_result(
    *,
    packet: RubricCaseInput,
    adapter_kind: str,
    exc: Exception,
) -> JudgeCaseResult:
    message = str(exc)
    return JudgeCaseResult(
        case_id=packet.case_id,
        run_id=packet.run_id,
        status="error",
        verdict="error",
        criteria=[],
        summary="Judge case execution failed.",
        error={"code": type(exc).__name__, "message": message[:500]},
        judge_adapter_kind=adapter_kind,
    )


def _chat_completions_url(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/chat/completions"):
        return trimmed
    return f"{trimmed}/chat/completions"


def _extract_message_content(payload: dict[str, Any]) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise JudgeAdapterError(
            "Judge LLM response did not include choices[0].message.content."
        ) from exc
    if not isinstance(content, str) or not content.strip():
        raise JudgeAdapterError("Judge LLM response content was empty.")
    return content


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Judge LLM message content was not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Judge LLM message content must be a JSON object.")
    return parsed


def _fake_criterion_result(
    criterion: RubricCriterion,
    *,
    output_count: int,
    issue_count: int,
) -> JudgeCriterionResult:
    if output_count <= 0:
        score = criterion.score_min
        reason = "No user-facing analysis content was present in the bounded packet."
    elif issue_count > 0:
        score = max(criterion.score_min, criterion.pass_score - 1)
        reason = "The bounded packet includes warnings or dropped content."
    else:
        score = criterion.score_max
        reason = "The bounded packet contains user-facing analysis content without warnings."
    return JudgeCriterionResult(
        criterion_id=criterion.id,
        label=criterion.label,
        score=float(score),
        passed=score >= criterion.pass_score,
        reason=reason,
        evidence=[],
    )


def _fake_summary(verdict: JudgeVerdict, *, warning_count: int, drop_count: int) -> str:
    if verdict == "pass":
        return "Fake judge found no bounded-packet quality issues."
    if warning_count or drop_count:
        return f"Fake judge flagged {warning_count} warnings and {drop_count} dropped items."
    return "Fake judge could not find enough output content to pass the case."


def _verdict_from_criteria(
    criteria: list[JudgeCriterionResult],
    threshold: float | None,
) -> JudgeVerdict:
    if not criteria:
        return "needs_review"
    if any(item.passed is False for item in criteria):
        return "fail"
    average = _plain_average(criteria)
    if threshold is not None and average is not None and average < threshold:
        return "fail"
    return "pass"


def _normalize_verdict(
    value: Any,
    criteria: list[JudgeCriterionResult],
    threshold: float | None,
) -> JudgeVerdict:
    verdict = str(value or "").strip().lower()
    if verdict in {"pass", "fail", "needs_review", "error"}:
        return verdict  # type: ignore[return-value]
    return _verdict_from_criteria(criteria, threshold)


def _weighted_threshold(criteria: list[RubricCriterion]) -> float | None:
    total_weight = sum(criterion.weight for criterion in criteria)
    if total_weight <= 0:
        return None
    return sum(criterion.pass_score * criterion.weight for criterion in criteria) / total_weight


def _weighted_average(
    results: list[JudgeCriterionResult],
    criteria: list[RubricCriterion],
) -> float | None:
    by_id = {result.criterion_id: result for result in results}
    total_weight = 0.0
    weighted = 0.0
    for criterion in criteria:
        result = by_id.get(criterion.id)
        if result is None or result.score is None:
            continue
        total_weight += criterion.weight
        weighted += result.score * criterion.weight
    if total_weight <= 0:
        return None
    return round(weighted / total_weight, 4)


def _plain_average(results: list[JudgeCriterionResult]) -> float | None:
    scores = [item.score for item in results if item.score is not None]
    if not scores:
        return None
    return sum(scores) / len(scores)


def _coerce_score(value: Any, criterion: RubricCriterion) -> float | None:
    score = _coerce_float(value)
    if score is None:
        return None
    return min(max(score, float(criterion.score_min)), float(criterion.score_max))


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _evidence_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item)[:500] for item in value[:5]]
    if isinstance(value, str) and value:
        return [value[:500]]
    return []


def _list_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0
