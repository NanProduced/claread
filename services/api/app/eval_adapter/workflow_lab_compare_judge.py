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
JUDGE_MAX_COMPLETION_TOKENS: Final[int] = 320
PROMPT_MAX_INLINE_MARKS: Final[int] = 6
PROMPT_MAX_SENTENCE_ENTRIES: Final[int] = 4
PROMPT_MAX_WARNINGS: Final[int] = 4
PROMPT_MAX_DROP_LOG: Final[int] = 3
PROMPT_MAX_SENTENCE_TEXT_CHARS: Final[int] = 240
PROMPT_MAX_TRANSLATION_CHARS: Final[int] = 240
PROMPT_MAX_MARK_TEXT_CHARS: Final[int] = 80
PROMPT_MAX_ENTRY_TEXT_CHARS: Final[int] = 180
PROMPT_MAX_WARNING_CHARS: Final[int] = 200
PROMPT_MAX_DROP_REASON_CHARS: Final[int] = 160


def _truncate_prompt_text(value: Any, max_chars: int) -> str:
    text = str(value or "")
    return " ".join(text.split())[:max_chars]


def _compact_inline_marks(raw_marks: Any) -> list[dict[str, str]]:
    if not isinstance(raw_marks, list):
        return []
    compact: list[dict[str, str]] = []
    for item in raw_marks[:PROMPT_MAX_INLINE_MARKS]:
        if not isinstance(item, dict):
            continue
        entry = {
            "anchor": _truncate_prompt_text(
                (item.get("anchor") if isinstance(item.get("anchor"), dict) else {}).get("anchor_text")
                or item.get("anchor_text")
                or (item.get("anchor") if isinstance(item.get("anchor"), str) else "")
                or item.get("text")
                or "",
                PROMPT_MAX_MARK_TEXT_CHARS,
            ),
            "type": _truncate_prompt_text(
                item.get("annotation_type")
                or item.get("type")
                or item.get("visual_tone")
                or "",
                40,
            ),
            "extra": _truncate_prompt_text(
                (item.get("glossary") if isinstance(item.get("glossary"), dict) else {}).get("zh")
                or (item.get("glossary") if isinstance(item.get("glossary"), dict) else {}).get("gloss")
                or item.get("extra")
                or item.get("zh")
                or item.get("gloss")
                or item.get("label")
                or "",
                PROMPT_MAX_MARK_TEXT_CHARS,
            ),
        }
        compact.append({key: value for key, value in entry.items() if value})
    return compact


def _compact_sentence_entries(raw_entries: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_entries, list):
        return []
    compact: list[dict[str, Any]] = []
    for item in raw_entries[:PROMPT_MAX_SENTENCE_ENTRIES]:
        if not isinstance(item, dict):
            continue
        entry: dict[str, Any] = {}
        for source_key, target_key, limit in (
            ("type", "type", 40),
            ("entry_type", "type", 40),
            ("label", "label", 80),
            ("title", "label", 80),
            ("summary", "summary", PROMPT_MAX_ENTRY_TEXT_CHARS),
            ("content", "summary", PROMPT_MAX_ENTRY_TEXT_CHARS),
            ("description", "summary", PROMPT_MAX_ENTRY_TEXT_CHARS),
            ("explanation", "summary", PROMPT_MAX_ENTRY_TEXT_CHARS),
            ("source_text", "source_text", 120),
            ("anchor_text", "anchor_text", 120),
        ):
            if target_key in entry:
                continue
            value = _truncate_prompt_text(item.get(source_key), limit)
            if value:
                entry[target_key] = value
        raw_chunks = item.get("chunks")
        if isinstance(raw_chunks, list):
            chunks: list[dict[str, str]] = []
            for chunk in raw_chunks[:2]:
                if not isinstance(chunk, dict):
                    continue
                chunk_text = _truncate_prompt_text(chunk.get("text"), 80)
                chunk_label = _truncate_prompt_text(chunk.get("label") or chunk.get("type"), 40)
                if not chunk_text and not chunk_label:
                    continue
                chunks.append(
                    {
                        key: value
                        for key, value in {"label": chunk_label, "text": chunk_text}.items()
                        if value
                    }
                )
            if chunks:
                entry["chunks"] = chunks
        if entry:
            compact.append(entry)
    return compact


def _compact_drop_log(raw_drop_log: Any) -> list[dict[str, str]]:
    if not isinstance(raw_drop_log, list):
        return []
    compact: list[dict[str, str]] = []
    for item in raw_drop_log[:PROMPT_MAX_DROP_LOG]:
        if isinstance(item, dict):
            entry = {
                "code": _truncate_prompt_text(item.get("code"), 40),
                "sentence_id": _truncate_prompt_text(item.get("sentence_id"), 40),
                "reason": _truncate_prompt_text(
                    item.get("reason") or item.get("message") or "",
                    PROMPT_MAX_DROP_REASON_CHARS,
                ),
            }
            compact.append({key: value for key, value in entry.items() if value})
            continue
        text = _truncate_prompt_text(item, PROMPT_MAX_DROP_REASON_CHARS)
        if text:
            compact.append({"reason": text})
    return compact


def _compact_side_for_prompt(side: Any) -> dict[str, Any]:
    data = side.model_dump(mode="json") if hasattr(side, "model_dump") else dict(side or {})
    return {
        "user_facing_state": _truncate_prompt_text(data.get("user_facing_state"), 40) or None,
        "sentence_id": _truncate_prompt_text(data.get("sentence_id"), 40) or None,
        "sentence_text": _truncate_prompt_text(
            data.get("sentence_text"),
            PROMPT_MAX_SENTENCE_TEXT_CHARS,
        ),
        "translation": _truncate_prompt_text(
            data.get("translation"),
            PROMPT_MAX_TRANSLATION_CHARS,
        )
        or None,
        "inline_marks": _compact_inline_marks(data.get("inline_marks")),
        "sentence_entries": _compact_sentence_entries(data.get("sentence_entries")),
        "warnings": [
            value
            for item in (data.get("warnings") or [])[:PROMPT_MAX_WARNINGS]
            if (value := _truncate_prompt_text(item, PROMPT_MAX_WARNING_CHARS))
        ],
        "drop_log": _compact_drop_log(data.get("drop_log")),
    }


def _packet_for_prompt(packet: WorkflowLabCompareJudgePacket) -> dict[str, Any]:
    return {
        "compare_id": packet.compare_id,
        "case_id": packet.case_id,
        "sentence_id": packet.sentence_id,
        "sentence_text": _truncate_prompt_text(
            packet.sentence_text,
            PROMPT_MAX_SENTENCE_TEXT_CHARS,
        ),
        "reading_goal": packet.reading_goal,
        "reading_variant": packet.reading_variant,
        "baseline": _compact_side_for_prompt(packet.baseline),
        "candidate": _compact_side_for_prompt(packet.candidate),
    }


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
            "packet": _packet_for_prompt(packet),
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
            max_tokens=JUDGE_MAX_COMPLETION_TOKENS,
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
            usage_summary=None,
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
        usage_summary=result.usage,
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
        usage_summary=None,
        error=WorkflowLabCompareJudgeCaseError(code=code, message=message),
    )


def _aggregate_usage(results: list[WorkflowLabCompareJudgeCaseResult]) -> tuple[int | None, int | None, int | None]:
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    seen_usage = False
    for result in results:
        usage = result.usage_summary
        if not isinstance(usage, dict):
            continue
        prompt_tokens += int(usage.get("prompt_tokens") or 0)
        completion_tokens += int(usage.get("completion_tokens") or 0)
        total_tokens += int(usage.get("total_tokens") or 0)
        seen_usage = True
    if not seen_usage:
        return None, None, None
    return prompt_tokens, completion_tokens, total_tokens


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

    ``asyncio.gather`` is called with ``return_exceptions=True`` so a
    non-``StructuredCompletionError`` exception in one packet cannot
    cancel the rest of the batch. The per-packet exception is captured
    into the same ``_short_circuited_case`` shape used by the timeout
    short-circuit, so callers always get a fully-populated result list
    — one entry per input packet.
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

    outcomes = await asyncio.gather(
        *(_run_one(index, packet) for index, packet in enumerate(packets)),
        return_exceptions=True,
    )
    # Map any per-packet exception that escaped ``_judge_single_case``'s
    # ``StructuredCompletionError`` catch into the same case-error shape.
    # This keeps the contract: every input packet produces exactly one
    # result entry, and a single bad packet never aborts the batch.
    for index, outcome in enumerate(outcomes):
        if isinstance(outcome, BaseException):
            packet = packets[index]
            results[index] = _short_circuited_case(
                packet,
                code="WORKFLOW_COMPARE_JUDGE_PACKET_EXCEPTION",
                message=(
                    f"{type(outcome).__name__}: {outcome}"
                ),
            )
    # All slots are now guaranteed to be filled.
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
    started_at = time.monotonic()
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
            latency_seconds=max(0.0, time.monotonic() - started_at),
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
            latency_seconds=max(0.0, time.monotonic() - started_at),
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
    input_tokens, output_tokens, total_tokens = _aggregate_usage(results)

    return WorkflowLabCompareJudgeResult(
        judge_run_id=request.judge_run_id,
        compare_id=request.compare_id,
        rubric_id=request.rubric_id,
        judge_model_profile=request.judge_model_profile,
        model_name=config.model_name,
        profile_name=config.profile_name,
        provider=config.provider,
        base_url=config.base_url,
        latency_seconds=max(0.0, time.monotonic() - started_at),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
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
