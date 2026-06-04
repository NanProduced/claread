"""API-side Workflow compare-level LLM judge execution.

This module is the single place where the Workflow compare LLM judge should be
invoked. The Directus extension proxies to this module (via the
``/eval/article-analysis/workflow-lab/compare-judge`` route) and never talks to
an OpenAI-compatible endpoint directly. Reusing
:mod:`app.llm.structured_completion` keeps the model_profile -> base_url /
api_key / model_name negotiation identical to the rest of the eval surface.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Final

from app.config.settings import Settings
from app.eval_adapter.schemas import (
    VALID_COMPARE_JUDGE_VERDICTS,
    WorkflowLabCompareJudgeCaseError,
    WorkflowLabCompareJudgeCaseResult,
    WorkflowLabCompareJudgePacket,
    WorkflowLabCompareJudgeRequest,
    WorkflowLabCompareJudgeResult,
)
from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION
from app.llm.structured_completion import (
    StructuredCompletionError,
    run_structured_completion,
)
from app.llm.types import ModelSelection, RouteModelSelection

JUDGE_SYSTEM_PROMPT: Final[str] = (
    "You are a pairwise evaluator for Claread workflow compare outputs. "
    "Return strict JSON only."
)

# Cap on the number of characters of the LLM summary we propagate to Directus.
# Matches the existing Directus-side slice.
SUMMARY_MAX_CHARS: Final[int] = 1000
REASON_MAX_CHARS: Final[int] = 300
DEFAULT_PER_PACKET_TIMEOUT_SECONDS: Final[float] = 30.0
DEFAULT_TOTAL_TIMEOUT_SECONDS: Final[float] = 600.0
DEFAULT_CONCURRENCY: Final[int] = 1
MAX_CONCURRENCY: Final[int] = 8


def _build_user_prompt(packet: WorkflowLabCompareJudgePacket) -> str:
    return json.dumps(
        {
            "instructions": {
                "verdict": "candidate_preferred|baseline_preferred|tie|needs_review",
                "summary": "one concise Chinese sentence",
                "reasons": ["short reasons"],
                "overall_score": (
                    "0~1 where 1 means candidate clearly preferred, "
                    "0 means baseline clearly preferred, 0.5 means tie"
                ),
            },
            "packet": packet.model_dump(mode="json", exclude_none=True),
        },
        ensure_ascii=False,
    )


def _normalize_verdict(raw: Any) -> str:
    if isinstance(raw, str) and raw in VALID_COMPARE_JUDGE_VERDICTS:
        return raw
    return "needs_review"


def _normalize_score(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        score = float(raw)
    except (TypeError, ValueError):
        return None
    if not (score == score):  # NaN check
        return None
    return max(0.0, min(1.0, score))


def _preferred_side_from_verdict(verdict: str) -> str | None:
    if verdict == "candidate_preferred":
        return "candidate"
    if verdict == "baseline_preferred":
        return "baseline"
    return None


def _default_score_for_verdict(verdict: str) -> float:
    # Mirrors Directus compareJudgeCaseScore fallbacks.
    if verdict == "candidate_preferred":
        return 0.75
    if verdict == "baseline_preferred":
        return 0.25
    if verdict == "tie":
        return 0.5
    return 0.5  # needs_review: 0.5 (Directus uses 0.5 by default)


async def _judge_single_case(
    *,
    settings: Settings,
    selection: ModelSelection,
    packet: WorkflowLabCompareJudgePacket,
    timeout_seconds: float,
) -> WorkflowLabCompareJudgeCaseResult:
    try:
        result = await run_structured_completion(
            settings=settings,
            route=MODEL_ROUTE_ANNOTATION_GENERATION,
            selection=selection,
            system_prompt=JUDGE_SYSTEM_PROMPT,
            user_prompt=_build_user_prompt(packet),
            timeout_seconds=timeout_seconds,
            temperature=0.0,
        )
    except StructuredCompletionError as exc:
        return WorkflowLabCompareJudgeCaseResult(
            case_id=packet.case_id,
            status="error",
            verdict="needs_review",
            preferred_side=None,
            overall_score=None,
            summary="LLM judge execution failed.",
            reasons=[str(exc).strip() or "LLM judge execution failed."],
            error=WorkflowLabCompareJudgeCaseError(
                code="WORKFLOW_COMPARE_JUDGE_LLM_ERROR",
                message=str(exc).strip() or "LLM judge execution failed.",
            ),
        )

    parsed = result.parsed
    verdict = _normalize_verdict(parsed.get("verdict"))
    score = _normalize_score(parsed.get("overall_score"))
    if score is None:
        score = _default_score_for_verdict(verdict)
    summary_text = str(parsed.get("summary") or "").strip() or "LLM judge returned no summary."
    raw_reasons = parsed.get("reasons")
    reason_list: list[str] = []
    if isinstance(raw_reasons, list):
        for item in raw_reasons:
            if item is None:
                continue
            reason_list.append(str(item).strip()[:REASON_MAX_CHARS])

    return WorkflowLabCompareJudgeCaseResult(
        case_id=packet.case_id,
        status="succeeded",
        verdict=verdict,
        preferred_side=_preferred_side_from_verdict(verdict),
        overall_score=score,
        summary=summary_text[:SUMMARY_MAX_CHARS],
        reasons=reason_list,
    )


def _selection_for_profile(profile: str) -> ModelSelection:
    return ModelSelection(
        default_profile=profile,
        routes={MODEL_ROUTE_ANNOTATION_GENERATION: RouteModelSelection(profile=profile)},
    )


def _short_circuited_case(
    packet: WorkflowLabCompareJudgePacket,
    *,
    code: str,
    message: str,
) -> WorkflowLabCompareJudgeCaseResult:
    return WorkflowLabCompareJudgeCaseResult(
        case_id=packet.case_id,
        status="error",
        verdict="needs_review",
        preferred_side=None,
        overall_score=None,
        summary=message,
        reasons=[],
        error=WorkflowLabCompareJudgeCaseError(code=code, message=message),
    )


def _resolve_execution_params(
    request: WorkflowLabCompareJudgeRequest,
) -> tuple[float, float, int]:
    """Return ``(per_packet_timeout, total_timeout, concurrency)``."""
    per_packet = (
        float(request.timeout_seconds)
        if request.timeout_seconds
        else DEFAULT_PER_PACKET_TIMEOUT_SECONDS
    )
    total = (
        float(request.total_timeout_seconds)
        if request.total_timeout_seconds
        else DEFAULT_TOTAL_TIMEOUT_SECONDS
    )
    # The per-packet timeout must always be at least the total budget makes
    # no sense; clamp it to keep error messages sane.
    per_packet = max(1.0, min(per_packet, total))
    concurrency = request.concurrency or DEFAULT_CONCURRENCY
    concurrency = max(1, min(concurrency, MAX_CONCURRENCY))
    return per_packet, total, concurrency


async def _run_with_bounded_concurrency(
    *,
    settings: Settings,
    selection: ModelSelection,
    packets: list[WorkflowLabCompareJudgePacket],
    per_packet_timeout: float,
    total_timeout: float,
    concurrency: int,
) -> list[WorkflowLabCompareJudgeCaseResult]:
    """Execute packet judges with bounded concurrency + total time budget.

    The total budget is measured wall-clock from the start of the first
    packet. Once exceeded, pending packets are short-circuited with
    ``WORKFLOW_COMPARE_JUDGE_TOTAL_TIMEOUT`` instead of starting a new LLM
    call that would just overrun. Per-packet timeouts remain as a final
    safety net for individual slow requests.
    """
    if not packets:
        return []

    results: list[WorkflowLabCompareJudgeCaseResult | None] = [None] * len(packets)
    semaphore = asyncio.Semaphore(concurrency)
    deadline = time.monotonic() + total_timeout

    async def _run_one(
        index: int,
        packet: WorkflowLabCompareJudgePacket,
    ) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            results[index] = _short_circuited_case(
                packet,
                code="WORKFLOW_COMPARE_JUDGE_TOTAL_TIMEOUT",
                message=(
                    f"Compare judge total timeout ({total_timeout:.0f}s) "
                    "exhausted before this case could be scheduled."
                ),
            )
            return
        per_case_timeout = min(per_packet_timeout, remaining)
        async with semaphore:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                results[index] = _short_circuited_case(
                    packet,
                    code="WORKFLOW_COMPARE_JUDGE_TOTAL_TIMEOUT",
                    message=(
                        f"Compare judge total timeout ({total_timeout:.0f}s) "
                        "exhausted while waiting for a concurrency slot."
                    ),
                )
                return
            results[index] = await _judge_single_case(
                settings=settings,
                selection=selection,
                packet=packet,
                timeout_seconds=per_case_timeout,
            )

    await asyncio.gather(
        *(_run_one(index, packet) for index, packet in enumerate(packets))
    )
    # ``asyncio.gather`` propagates exceptions, so all slots are filled here.
    return [result for result in results if result is not None]


async def run_workflow_lab_compare_judge(
    request: WorkflowLabCompareJudgeRequest,
    *,
    settings: Settings,
) -> WorkflowLabCompareJudgeResult:
    """Run a sentence-level compare LLM judge for a Workflow Lab compare.

    Caller is responsible for persistence (artifact, control-plane row). This
    function only performs model resolution + LLM execution + result shaping.
    """
    selection = _selection_for_profile(request.judge_model_profile)
    per_packet_timeout, total_timeout, concurrency = _resolve_execution_params(request)

    # Validate model profile up-front so callers can return a configuration
    # error without iterating packets.
    try:
        from app.llm.router import resolve_model_config

        config = resolve_model_config(
            settings,
            MODEL_ROUTE_ANNOTATION_GENERATION,
            selection,
        )
    except Exception as exc:  # noqa: BLE001 - propagate as config error
        message = "LLM judge is not configured for the requested model profile."
        return WorkflowLabCompareJudgeResult(
            judge_run_id=request.judge_run_id,
            compare_id=request.compare_id,
            rubric_id=request.rubric_id,
            judge_model_profile=request.judge_model_profile,
            results=[
                _short_circuited_case(
                    packet,
                    code="WORKFLOW_COMPARE_JUDGE_LLM_NOT_CONFIGURED",
                    message=f"{message} ({exc})",
                )
                for packet in request.packets
            ],
        )

    if config is None:
        message = "LLM judge is not configured for the requested model profile."
        return WorkflowLabCompareJudgeResult(
            judge_run_id=request.judge_run_id,
            compare_id=request.compare_id,
            rubric_id=request.rubric_id,
            judge_model_profile=request.judge_model_profile,
            results=[
                _short_circuited_case(
                    packet,
                    code="WORKFLOW_COMPARE_JUDGE_LLM_NOT_CONFIGURED",
                    message=(
                        f"{message} Profile '{request.judge_model_profile}' "
                        "is not configured for the annotation_generation route."
                    ),
                )
                for packet in request.packets
            ],
        )

    results = await _run_with_bounded_concurrency(
        settings=settings,
        selection=selection,
        packets=request.packets,
        per_packet_timeout=per_packet_timeout,
        total_timeout=total_timeout,
        concurrency=concurrency,
    )

    return WorkflowLabCompareJudgeResult(
        judge_run_id=request.judge_run_id,
        compare_id=request.compare_id,
        rubric_id=request.rubric_id,
        judge_model_profile=request.judge_model_profile,
        model_name=config.model_name,
        profile_name=config.profile_name,
        provider=config.provider,
        base_url=config.base_url,
        results=results,
    )


__all__ = [
    "DEFAULT_CONCURRENCY",
    "DEFAULT_PER_PACKET_TIMEOUT_SECONDS",
    "DEFAULT_TOTAL_TIMEOUT_SECONDS",
    "JUDGE_SYSTEM_PROMPT",
    "MAX_CONCURRENCY",
    "run_workflow_lab_compare_judge",
]
