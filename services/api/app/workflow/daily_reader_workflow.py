"""Daily Reader Workflow - LangGraph StateGraph.

Redesigned per redesign-tracker.tmp.md:
  light_normalize
  → highlight_by_paragraph_batches
  → paragraph_guides_and_translations
  → close_reading_takeaways
  → quality_review
  → (conditional) refinement
  → daily_projection

Key changes from v1:
- Highlights generated per-paragraph-batch to ensure full-coverage (fixes front-half bias)
- footer_analysis replaced by paragraph_guides_and_translations (ParagraphNotesDraft)
- full_interpretation replaced by close_reading_takeaways (CloseReadingTakeaways)
- Coverage check in projection node
- Refinement targets new schema fields
"""

from __future__ import annotations

import json
import logging
import math
import re
from html import unescape
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import traceable

from app.agents.daily_footer_agent import (
    DailyFooterAgentDeps,
    build_daily_footer_prompt,
    get_daily_footer_agent,
)
from app.agents.daily_interpretation_agent import (
    DailyInterpretationAgentDeps,
    build_daily_interpretation_prompt,
    get_daily_interpretation_agent,
)
from app.agents.daily_refinement_agent import (
    DailyRefinementAgentDeps,
    build_daily_refinement_prompt,
    get_daily_refinement_agent,
)
from app.agents.daily_review_agent import (
    DailyReviewAgentDeps,
    build_daily_review_prompt,
    get_daily_review_agent,
)
from app.agents.daily_vocab_agent import (
    DailyVocabAgentDeps,
    build_daily_vocab_prompt,
    get_daily_vocab_agent,
)
from app.config.settings import get_settings
from app.llm.agent_runner import extract_run_usage, run_agent_with_route
from app.llm.router import resolve_model_config
from app.llm.routes import (
    MODEL_ROUTE_DAILY_ANALYSIS,
    MODEL_ROUTE_DAILY_ANNOTATION,
    MODEL_ROUTE_DAILY_REVIEW,
    ModelRoute,
)
from app.workflow.tracing import build_llm_trace_metadata

logger = logging.getLogger(__name__)

WORKFLOW_NAME = "daily_reader"
WORKFLOW_VERSION = "2.0.0"

HIGHLIGHT_BATCH_SIZE = 3
MAX_PARAGRAPH_CHARS = 900
MAX_PARAGRAPH_SENTENCES = 8
MIN_REQUIRED_HIGHLIGHT_CHARS = 80


class DailyReaderState(TypedDict, total=False):
    original_text: str
    title: str
    subtitle: str
    source: str
    source_url: str
    cover_image_url: str | None
    tags: list[str]
    difficulty: str
    read_time_minutes: int
    pipeline_source: str
    pipeline_meta: dict

    normalized_paragraphs: list[dict]
    vocab_draft: dict | None
    highlights_json: list[dict]
    highlight_retry_exhausted: bool
    highlight_retry_missing_paragraph_ids: list[str]
    paragraph_notes_json: dict
    takeaways_json: dict
    review_result: dict | None
    refinement_result: dict | None
    abort: bool

    body_json: dict
    content_sec_check: dict

    usage_summary: dict | None


def _aggregate_usage(state: DailyReaderState) -> dict[str, Any]:
    per_agent: dict[str, dict[str, object]] = {}
    for key in (
        "vocab_usage", "phrase_gloss_usage", "paragraph_notes_usage",
        "takeaways_usage", "review_usage", "refinement_usage",
    ):
        usage = state.get(key)
        if usage and isinstance(usage, dict):
            per_agent[key.replace("_usage", "")] = usage

    if not per_agent:
        return {
            "available": False,
            "per_agent": {},
            "aggregate": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
        }

    def _sum(field: str) -> int:
        return sum(int(u.get(field, 0) or 0) for u in per_agent.values())

    return {
        "available": True,
        "per_agent": per_agent,
        "aggregate": {
            "input_tokens": _sum("input_tokens"),
            "output_tokens": _sum("output_tokens"),
            "total_tokens": _sum("total_tokens"),
        },
    }


def _build_daily_llm_metadata(
    state: DailyReaderState,
    *,
    node_name: str,
    route: ModelRoute,
    extra: dict[str, Any] | None = None,
) -> dict[str, object]:
    model_config = resolve_model_config(get_settings(), route)
    return build_llm_trace_metadata(
        workflow_name=WORKFLOW_NAME,
        workflow_version=WORKFLOW_VERSION,
        request_id=state.get("source_url", "") or state.get("title", ""),
        source_type=state.get("pipeline_source", "pipeline"),
        reading_goal="daily_reading",
        reading_variant="standard",
        profile_id="daily_reader",
        model_name=model_config.model_name if model_config else "unconfigured",
        model_provider=model_config.provider if model_config else "unconfigured",
        extra={
            "node": node_name,
            "model_profile": model_config.profile_name if model_config else "unconfigured",
            "article_title": state.get("title", "")[:80],
            **(extra or {}),
        },
    )


@traceable(name="daily_highlight_llm_call", run_type="llm")
async def _run_daily_highlight_llm_span(
    *,
    deps: DailyVocabAgentDeps,
    prompt: str,
    metadata: dict[str, object],
) -> dict[str, Any]:
    result = await run_agent_with_route(
        agent=get_daily_vocab_agent(),
        prompt=prompt,
        deps=deps,
        route=MODEL_ROUTE_DAILY_ANNOTATION,
    )
    usage = extract_run_usage(result)
    return {"output": result.output if hasattr(result, "output") else result, "usage_metadata": usage}


@traceable(name="daily_paragraph_notes_llm_call", run_type="llm")
async def _run_daily_paragraph_notes_llm_span(
    *,
    deps: DailyFooterAgentDeps,
    prompt: str,
    metadata: dict[str, object],
) -> dict[str, Any]:
    result = await run_agent_with_route(
        agent=get_daily_footer_agent(),
        prompt=prompt,
        deps=deps,
        route=MODEL_ROUTE_DAILY_ANALYSIS,
    )
    usage = extract_run_usage(result)
    return {"output": result.output if hasattr(result, "output") else result, "usage_metadata": usage}


@traceable(name="daily_takeaways_llm_call", run_type="llm")
async def _run_daily_takeaways_llm_span(
    *,
    deps: DailyInterpretationAgentDeps,
    prompt: str,
    metadata: dict[str, object],
) -> dict[str, Any]:
    result = await run_agent_with_route(
        agent=get_daily_interpretation_agent(),
        prompt=prompt,
        deps=deps,
        route=MODEL_ROUTE_DAILY_ANALYSIS,
    )
    usage = extract_run_usage(result)
    return {"output": result.output if hasattr(result, "output") else result, "usage_metadata": usage}


@traceable(name="daily_review_llm_call", run_type="llm")
async def _run_daily_review_llm_span(
    *,
    deps: DailyReviewAgentDeps,
    prompt: str,
    metadata: dict[str, object],
) -> dict[str, Any]:
    result = await run_agent_with_route(
        agent=get_daily_review_agent(),
        prompt=prompt,
        deps=deps,
        route=MODEL_ROUTE_DAILY_REVIEW,
    )
    usage = extract_run_usage(result)
    return {"output": result.output if hasattr(result, "output") else result, "usage_metadata": usage}


@traceable(name="daily_refinement_llm_call", run_type="llm")
async def _run_daily_refinement_llm_span(
    *,
    deps: DailyRefinementAgentDeps,
    prompt: str,
    metadata: dict[str, object],
) -> dict[str, Any]:
    result = await run_agent_with_route(
        agent=get_daily_refinement_agent(),
        prompt=prompt,
        deps=deps,
        route=MODEL_ROUTE_DAILY_REVIEW,
    )
    usage = extract_run_usage(result)
    return {"output": result.output if hasattr(result, "output") else result, "usage_metadata": usage}


def light_normalize_node(state: DailyReaderState) -> dict:
    text = state.get("original_text", "")
    paragraphs = _split_into_paragraphs(text)
    normalized = []
    for i, para in enumerate(paragraphs):
        cleaned = _clean_paragraph(para)
        if cleaned:
            normalized.append({"paragraph_id": f"p_{i}", "text": cleaned})
    return {"normalized_paragraphs": normalized}


async def highlight_by_paragraph_batches_node(state: DailyReaderState) -> dict:
    paragraphs = state.get("normalized_paragraphs", [])
    if not paragraphs:
        return {"vocab_draft": None, "highlights_json": []}

    all_highlights: list[dict] = []
    all_para_drafts: list[dict] = []
    total_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    batches = _make_batches(paragraphs, HIGHLIGHT_BATCH_SIZE)

    for batch_idx, batch in enumerate(batches):
        try:
            deps = DailyVocabAgentDeps(
                paragraphs=batch,
                batch_index=batch_idx,
                total_batches=len(batches),
            )
            prompt = build_daily_vocab_prompt(deps)
            metadata = _build_daily_llm_metadata(
                state,
                node_name="highlight_by_paragraph_batches",
                route=MODEL_ROUTE_DAILY_ANNOTATION,
                extra={
                    "batch_index": batch_idx,
                    "total_batches": len(batches),
                    "paragraph_count": len(batch),
                },
            )
            result = await _run_daily_highlight_llm_span(
                deps=deps,
                prompt=prompt,
                metadata=metadata,
                langsmith_extra={"metadata": metadata},
            )
            draft = result.get("output")
            usage = result.get("usage_metadata")

            if draft:
                batch_highlights = _extract_highlights_from_vocab_draft(draft)
                all_highlights.extend(batch_highlights)
                for para in getattr(draft, "paragraphs", []):
                    all_para_drafts.append(para.model_dump() if hasattr(para, "model_dump") else para)

            if usage:
                for k in total_usage:
                    total_usage[k] += int(usage.get(k, 0) or 0)

            logger.info(
                "highlight batch %d/%d: %d paragraphs, %d highlights",
                batch_idx + 1, len(batches), len(batch), len(batch_highlights) if draft else 0,
            )
        except Exception as e:
            logger.error("highlight batch %d/%d failed: %s", batch_idx + 1, len(batches), e, exc_info=True)

    missing_required = _paragraphs_requiring_highlight(paragraphs, all_highlights)
    if missing_required:
        try:
            deps = DailyVocabAgentDeps(
                paragraphs=missing_required,
                batch_index=len(batches),
                total_batches=len(batches) + 1,
            )
            prompt = build_daily_vocab_prompt(deps)
            metadata = _build_daily_llm_metadata(
                state,
                node_name="highlight_required_retry",
                route=MODEL_ROUTE_DAILY_ANNOTATION,
                extra={
                    "paragraph_count": len(missing_required),
                    "missing_paragraph_ids": [p.get("paragraph_id") for p in missing_required],
                },
            )
            result = await _run_daily_highlight_llm_span(
                deps=deps,
                prompt=prompt,
                metadata=metadata,
                langsmith_extra={"metadata": metadata},
            )
            retry_draft = result.get("output")
            retry_usage = result.get("usage_metadata")
            if retry_draft:
                retry_highlights = _extract_highlights_from_vocab_draft(retry_draft)
                all_highlights.extend(retry_highlights)
                logger.info(
                    "highlight required retry: %d paragraphs, %d highlights",
                    len(missing_required), len(retry_highlights),
                )
            if retry_usage:
                for k in total_usage:
                    total_usage[k] += int(retry_usage.get(k, 0) or 0)
        except Exception as e:
            logger.error("highlight required retry failed: %s", e, exc_info=True)

    missing_after_retry = _paragraphs_requiring_highlight(paragraphs, all_highlights)
    coverage = _check_highlight_coverage(paragraphs, all_highlights)
    logger.info("highlight coverage: %s", coverage)

    updates: dict[str, Any] = {
        "vocab_draft": {"paragraphs": all_para_drafts},
        "highlights_json": all_highlights,
        "highlight_retry_exhausted": bool(missing_after_retry),
        "highlight_retry_missing_paragraph_ids": [
            p.get("paragraph_id", "") for p in missing_after_retry
        ],
    }
    if total_usage["total_tokens"] > 0:
        updates["vocab_usage"] = total_usage
    return updates


async def paragraph_guides_and_translations_node(state: DailyReaderState) -> dict:
    paragraphs = state.get("normalized_paragraphs", [])
    title = state.get("title", "")
    highlights = state.get("highlights_json", [])

    if not paragraphs:
        return {"paragraph_notes_json": {}}

    try:
        highlights_summary = ""
        if highlights:
            hl_texts = [h.get("text", "") for h in highlights[:15]]
            highlights_summary = f"已标注的关键词和短语：{', '.join(hl_texts)}"

        paragraphs_info = _build_paragraphs_info(paragraphs)

        deps = DailyFooterAgentDeps(
            full_text=_reconstruct_numbered_full_text(paragraphs),
            title=title,
            highlights_summary=highlights_summary,
            paragraphs_info=paragraphs_info,
        )
        prompt = build_daily_footer_prompt(deps)
        metadata = _build_daily_llm_metadata(
            state,
            node_name="paragraph_guides_and_translations",
            route=MODEL_ROUTE_DAILY_ANALYSIS,
            extra={"paragraph_count": len(paragraphs), "highlight_count": len(highlights)},
        )

        result = await _run_daily_paragraph_notes_llm_span(
            deps=deps,
            prompt=prompt,
            metadata=metadata,
            langsmith_extra={"metadata": metadata},
        )
        notes_draft = result.get("output")
        usage = result.get("usage_metadata")

        notes_dict = notes_draft.model_dump() if notes_draft else {}
        notes_dict = _align_paragraph_notes(notes_dict, paragraphs)

        updates: dict[str, Any] = {"paragraph_notes_json": notes_dict}
        if usage:
            updates["paragraph_notes_usage"] = usage
        return updates
    except Exception as e:
        logger.error("paragraph_guides_and_translations_node failed: %s", e, exc_info=True)
        return {"paragraph_notes_json": {}}


async def close_reading_takeaways_node(state: DailyReaderState) -> dict:
    paragraphs = state.get("normalized_paragraphs", [])
    title = state.get("title", "")
    highlights = state.get("highlights_json", [])
    paragraph_notes = state.get("paragraph_notes_json", {})

    try:
        highlights_summary = ""
        if highlights:
            hl_texts = [h.get("text", "") for h in highlights[:15]]
            highlights_summary = f"已标注的关键词和短语：{', '.join(hl_texts)}"

        notes_summary = ""
        if paragraph_notes:
            notes_summary = json.dumps(paragraph_notes, ensure_ascii=False)[:1500]

        deps = DailyInterpretationAgentDeps(
            full_text=_reconstruct_numbered_full_text(paragraphs),
            title=title,
            highlights_summary=highlights_summary,
            notes_summary=notes_summary,
        )
        prompt = build_daily_interpretation_prompt(deps)
        metadata = _build_daily_llm_metadata(
            state,
            node_name="close_reading_takeaways",
            route=MODEL_ROUTE_DAILY_ANALYSIS,
            extra={"paragraph_count": len(paragraphs), "highlight_count": len(highlights)},
        )

        result = await _run_daily_takeaways_llm_span(
            deps=deps,
            prompt=prompt,
            metadata=metadata,
            langsmith_extra={"metadata": metadata},
        )
        takeaways_draft = result.get("output")
        usage = result.get("usage_metadata")

        takeaways_dict = takeaways_draft.model_dump() if takeaways_draft else {}

        updates: dict[str, Any] = {"takeaways_json": takeaways_dict}
        if usage:
            updates["takeaways_usage"] = usage
        return updates
    except Exception as e:
        logger.error("close_reading_takeaways_node failed: %s", e, exc_info=True)
        return {"takeaways_json": {}}


async def quality_review_node(state: DailyReaderState) -> dict:
    original_text = state.get("original_text", "")
    paragraphs = state.get("normalized_paragraphs", [])
    highlights = state.get("highlights_json", [])
    paragraph_notes = state.get("paragraph_notes_json", {})
    takeaways = state.get("takeaways_json", {})

    try:
        coverage_report = _check_highlight_coverage(paragraphs, highlights)
        coverage_report["retry_exhausted"] = bool(state.get("highlight_retry_exhausted", False))
        coverage_report["retry_missing_paragraph_ids"] = state.get("highlight_retry_missing_paragraph_ids", [])
        paragraph_notes_report = _check_paragraph_notes_coverage(paragraphs, paragraph_notes)

        deps = DailyReviewAgentDeps(
            original_text=original_text,
            highlights_json=json.dumps(highlights, ensure_ascii=False),
            paragraph_notes_json=json.dumps(paragraph_notes, ensure_ascii=False),
            takeaways_json=json.dumps(takeaways, ensure_ascii=False),
            coverage_report=json.dumps(coverage_report, ensure_ascii=False),
            paragraph_notes_report=json.dumps(paragraph_notes_report, ensure_ascii=False),
        )
        prompt = build_daily_review_prompt(deps)
        metadata = _build_daily_llm_metadata(
            state,
            node_name="quality_review",
            route=MODEL_ROUTE_DAILY_REVIEW,
            extra={
                "paragraph_count": len(paragraphs),
                "highlight_count": len(highlights),
                "coverage_ratio": coverage_report.get("coverage_ratio"),
                "missing_note_ids": paragraph_notes_report.get("missing_paragraph_ids"),
            },
        )

        result = await _run_daily_review_llm_span(
            deps=deps,
            prompt=prompt,
            metadata=metadata,
            langsmith_extra={"metadata": metadata},
        )
        review = result.get("output")
        usage = result.get("usage_metadata")

        review_dict = review.model_dump() if review else {}
        updates: dict[str, Any] = {"review_result": review_dict}
        if usage:
            updates["review_usage"] = usage
        return updates
    except Exception as e:
        logger.error("quality_review_node failed: %s", e, exc_info=True)
        return {"review_result": {"passed": True}}


async def refinement_node(state: DailyReaderState) -> dict:
    original_text = state.get("original_text", "")
    paragraphs = state.get("normalized_paragraphs", [])
    review = state.get("review_result", {})
    highlights = state.get("highlights_json", [])
    paragraph_notes = state.get("paragraph_notes_json", {})
    takeaways = state.get("takeaways_json", {})

    try:
        issues_text = json.dumps(review.get("issues", []), ensure_ascii=False)

        deps = DailyRefinementAgentDeps(
            original_text=original_text,
            review_issues=issues_text,
            current_highlights=json.dumps(highlights, ensure_ascii=False),
            current_paragraph_notes=json.dumps(paragraph_notes, ensure_ascii=False),
            current_takeaways=json.dumps(takeaways, ensure_ascii=False),
        )
        prompt = build_daily_refinement_prompt(deps)
        metadata = _build_daily_llm_metadata(
            state,
            node_name="refinement",
            route=MODEL_ROUTE_DAILY_REVIEW,
            extra={
                "issue_count": len(review.get("issues", [])) if isinstance(review, dict) else 0,
                "highlight_count": len(highlights),
            },
        )

        result = await _run_daily_refinement_llm_span(
            deps=deps,
            prompt=prompt,
            metadata=metadata,
            langsmith_extra={"metadata": metadata},
        )
        refinement = result.get("output")
        usage = result.get("usage_metadata")

        refinement_dict = refinement.model_dump() if refinement else {}
        updates: dict[str, Any] = {"refinement_result": refinement_dict}

        if refinement and refinement.abort:
            updates["abort"] = True
            if usage:
                updates["refinement_usage"] = usage
            return updates

        if refinement:
            if refinement.refined_highlights is not None:
                refined_highlights = [h.model_dump() for h in refinement.refined_highlights]
                updates["highlights_json"] = _choose_refined_highlights(
                    paragraphs=paragraphs,
                    current=highlights,
                    refined=refined_highlights,
                )
            if refinement.refined_paragraph_notes is not None:
                updates["paragraph_notes_json"] = refinement.refined_paragraph_notes.model_dump()
            if refinement.refined_takeaways is not None:
                updates["takeaways_json"] = refinement.refined_takeaways.model_dump()

        if usage:
            updates["refinement_usage"] = usage
        return updates
    except Exception as e:
        logger.error("refinement_node failed: %s", e, exc_info=True)
        return {"refinement_result": {}}


def daily_projection_node(state: DailyReaderState) -> dict:
    paragraphs = state.get("normalized_paragraphs", [])
    highlights = state.get("highlights_json", [])
    paragraph_notes = state.get("paragraph_notes_json", {})
    takeaways = state.get("takeaways_json", {})

    logger.info(
        "daily_projection_node: paragraphs=%d, highlights=%d, notes_keys=%s, takeaways_keys=%s",
        len(paragraphs), len(highlights),
        list(paragraph_notes.keys()) if isinstance(paragraph_notes, dict) else type(paragraph_notes),
        list(takeaways.keys()) if isinstance(takeaways, dict) else type(takeaways),
    )

    corrected = _reconcile_highlights(paragraphs, highlights)

    coverage = _check_highlight_coverage(paragraphs, corrected)
    logger.info("daily_projection_node: highlight coverage=%s", coverage)

    notes_map = _build_notes_map(paragraph_notes)

    body_paragraphs = []
    for para in paragraphs:
        pid = para.get("paragraph_id", "")
        text = para.get("text", "")
        para_highlights = [h for h in corrected if h.get("paragraph_id") == pid]
        para_note = notes_map.get(pid)
        body_paragraphs.append({
            "id": pid,
            "text": text,
            "highlights": para_highlights,
            "reading_note": para_note,
        })

    usage_summary = _aggregate_usage(state)

    return {
        "body_json": {"paragraphs": body_paragraphs},
        "highlights_json": corrected,
        "paragraph_notes_json": paragraph_notes,
        "takeaways_json": takeaways,
        "usage_summary": usage_summary,
    }


def _should_refine(state: DailyReaderState) -> bool:
    review = state.get("review_result")
    if review is None:
        return False
    return not review.get("passed", True)


def build_daily_reader_graph() -> Any:
    graph = StateGraph(DailyReaderState)

    graph.add_node("light_normalize", light_normalize_node)
    graph.add_node("highlight_by_paragraph_batches", highlight_by_paragraph_batches_node)
    graph.add_node("paragraph_guides_and_translations", paragraph_guides_and_translations_node)
    graph.add_node("close_reading_takeaways", close_reading_takeaways_node)
    graph.add_node("quality_review", quality_review_node)
    graph.add_node("refinement", refinement_node)
    graph.add_node("daily_projection", daily_projection_node)

    graph.add_edge(START, "light_normalize")
    graph.add_edge("light_normalize", "highlight_by_paragraph_batches")
    graph.add_edge("highlight_by_paragraph_batches", "paragraph_guides_and_translations")
    graph.add_edge("paragraph_guides_and_translations", "close_reading_takeaways")
    graph.add_edge("close_reading_takeaways", "quality_review")
    graph.add_conditional_edges(
        "quality_review",
        _should_refine,
        {True: "refinement", False: "daily_projection"},
    )
    graph.add_edge("refinement", "daily_projection")
    graph.add_edge("daily_projection", END)

    return graph.compile()


def _split_into_paragraphs(text: str) -> list[str]:
    if re.search(r"\n\s*\n", text):
        parts = re.split(r"\n\s*\n", text)
    elif "\n" in text:
        parts = re.split(r"\n", text)
    else:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        parts: list[str] = []
        chunk: list[str] = []
        for s in sentences:
            chunk.append(s)
            if len(chunk) >= 3 or len(" ".join(chunk)) > 300:
                parts.append(" ".join(chunk))
                chunk = []
        if chunk:
            parts.append(" ".join(chunk))

    expanded: list[str] = []
    for part in parts:
        cleaned = part.strip()
        if cleaned:
            expanded.extend(_split_long_paragraph(cleaned))
    return expanded


def _split_long_paragraph(text: str) -> list[str]:
    if len(text) <= MAX_PARAGRAPH_CHARS:
        return [text]

    sentences = re.split(r"(?<=[.!?])\s+", text)
    if len(sentences) <= 1:
        return [
            text[i : i + MAX_PARAGRAPH_CHARS].strip()
            for i in range(0, len(text), MAX_PARAGRAPH_CHARS)
            if text[i : i + MAX_PARAGRAPH_CHARS].strip()
        ]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        would_len = current_len + len(sentence) + (1 if current else 0)
        if current and (
            would_len > MAX_PARAGRAPH_CHARS
            or (
                len(current) >= MAX_PARAGRAPH_SENTENCES
                and current_len >= int(MAX_PARAGRAPH_CHARS * 0.55)
            )
        ):
            chunks.append(" ".join(current))
            current = [sentence]
            current_len = len(sentence)
        else:
            current.append(sentence)
            current_len = would_len
    if current:
        chunks.append(" ".join(current))
    return chunks


def _clean_paragraph(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).replace("\u00A0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _make_batches(paragraphs: list[dict], batch_size: int) -> list[list[dict]]:
    if not paragraphs:
        return []
    return [
        paragraphs[i : i + batch_size]
        for i in range(0, len(paragraphs), batch_size)
    ]


def _extract_highlights_from_vocab_draft(draft: Any) -> list[dict]:
    if draft is None:
        return []
    highlights = []
    for para in getattr(draft, "paragraphs", []):
        pid = getattr(para, "paragraph_id", "")
        para_idx = int(pid.split("_")[1]) if "_" in pid else 0
        hl_counter = 0
        for hl in getattr(para, "highlights", []):
            hl_counter += 1
            highlights.append({
                "id": f"hl_p{para_idx:02d}_{hl_counter:02d}",
                "type": getattr(hl, "type", "vocab_highlight"),
                "text": getattr(hl, "anchor", ""),
                "gloss": getattr(hl, "gloss", ""),
                "paragraph_id": pid,
                "start": getattr(hl, "start", 0),
                "end": getattr(hl, "end", 0),
            })
    return highlights


def _check_highlight_coverage(
    paragraphs: list[dict],
    highlights: list[dict],
) -> dict[str, Any]:
    if not paragraphs:
        return {"total_paragraphs": 0, "covered_paragraphs": 0, "coverage_ratio": 0.0}

    para_ids = [p.get("paragraph_id", "") for p in paragraphs]
    covered_pids = set()
    for hl in highlights:
        pid = hl.get("paragraph_id", "")
        if pid in para_ids:
            covered_pids.add(pid)

    mid = len(para_ids) // 2
    first_half = para_ids[:mid] if mid > 0 else para_ids
    second_half = para_ids[mid:]

    first_covered = sum(1 for pid in first_half if pid in covered_pids)
    second_covered = sum(1 for pid in second_half if pid in covered_pids)

    uncovered = [pid for pid in para_ids if pid not in covered_pids]

    para_map = {p.get("paragraph_id", ""): p.get("text", "") for p in paragraphs}

    return {
        "total_paragraphs": len(para_ids),
        "covered_paragraphs": len(covered_pids),
        "coverage_ratio": len(covered_pids) / len(para_ids),
        "first_half_coverage": first_covered / len(first_half) if first_half else 0.0,
        "second_half_coverage": second_covered / len(second_half) if second_half else 0.0,
        "uncovered_paragraph_ids": uncovered,
        "uncovered_required_paragraph_ids": [
            pid for pid in uncovered
            if len(para_map.get(pid, "")) >= MIN_REQUIRED_HIGHLIGHT_CHARS
        ],
    }


def _paragraphs_requiring_highlight(
    paragraphs: list[dict],
    highlights: list[dict],
) -> list[dict]:
    highlighted_ids = {
        h.get("paragraph_id", "")
        for h in highlights
        if isinstance(h, dict)
    }
    return [
        p for p in paragraphs
        if p.get("paragraph_id", "") not in highlighted_ids
        and len(p.get("text", "")) >= MIN_REQUIRED_HIGHLIGHT_CHARS
    ]


def _check_paragraph_notes_coverage(
    paragraphs: list[dict],
    paragraph_notes: dict,
) -> dict[str, Any]:
    para_ids = [p.get("paragraph_id", "") for p in paragraphs]
    notes = paragraph_notes.get("notes", []) if isinstance(paragraph_notes, dict) else []
    note_ids = {
        n.get("paragraph_id", "")
        for n in notes
        if isinstance(n, dict)
        and n.get("focus_question")
        and n.get("micro_summary")
        and n.get("translation")
    }
    missing = [pid for pid in para_ids if pid not in note_ids]
    return {
        "total_paragraphs": len(para_ids),
        "noted_paragraphs": len(para_ids) - len(missing),
        "coverage_ratio": (len(para_ids) - len(missing)) / len(para_ids) if para_ids else 0.0,
        "missing_paragraph_ids": missing,
    }


def _reconstruct_full_text(paragraphs: list[dict]) -> str:
    return "\n\n".join(p.get("text", "") for p in paragraphs)


def _reconstruct_numbered_full_text(paragraphs: list[dict]) -> str:
    return "\n\n".join(
        f"{p.get('paragraph_id', '')}: {p.get('text', '')}"
        for p in paragraphs
    )


def _build_paragraphs_info(paragraphs: list[dict]) -> str:
    if not paragraphs:
        return ""
    lines = []
    for p in paragraphs:
        pid = p.get("paragraph_id", "")
        text_preview = p.get("text", "")[:100]
        lines.append(f"{pid}: {text_preview}...")
    return "\n".join(lines)


def _build_notes_map(paragraph_notes: dict) -> dict[str, dict]:
    notes_map: dict[str, dict] = {}
    if not isinstance(paragraph_notes, dict):
        return notes_map
    for note in paragraph_notes.get("notes", []):
        if isinstance(note, dict):
            pid = note.get("paragraph_id", "")
            if pid:
                notes_map[pid] = note
    return notes_map


def _align_paragraph_notes(paragraph_notes: dict, paragraphs: list[dict]) -> dict:
    if not isinstance(paragraph_notes, dict):
        return {}

    valid_ids = {p.get("paragraph_id", "") for p in paragraphs}
    aligned_notes = [
        note
        for note in paragraph_notes.get("notes", [])
        if isinstance(note, dict) and note.get("paragraph_id") in valid_ids
    ]
    dropped = len(paragraph_notes.get("notes", [])) - len(aligned_notes)
    if dropped:
        logger.warning("Dropped %d paragraph notes with unknown paragraph_id", dropped)

    return {
        **paragraph_notes,
        "notes": aligned_notes,
    }


def _reconcile_highlights(
    paragraphs: list[dict],
    highlights: list[dict],
) -> list[dict]:
    if not paragraphs or not highlights:
        return highlights

    para_map: dict[str, str] = {}
    for p in paragraphs:
        pid = p.get("paragraph_id", "")
        text = p.get("text", "")
        if pid and text:
            para_map[pid] = text

    corrected = []
    fix_count = 0
    for hl in highlights:
        hl_text = hl.get("text", "")
        assigned_pid = hl.get("paragraph_id", "")
        assigned_para = para_map.get(assigned_pid, "")

        if hl_text and assigned_para and hl_text in assigned_para:
            start = assigned_para.index(hl_text)
            new_hl = {**hl, "start": start, "end": start + len(hl_text)}
            corrected.append(new_hl)
            continue

        found = False
        for pid, para_text in para_map.items():
            if hl_text and hl_text in para_text:
                start = para_text.index(hl_text)
                new_hl = {**hl, "paragraph_id": pid, "start": start, "end": start + len(hl_text)}
                corrected.append(new_hl)
                found = True
                fix_count += 1
                break

        if not found:
            logger.warning(
                "Dropping highlight: text=%r not found in any paragraph",
                hl_text[:50] if hl_text else "",
            )

    if fix_count:
        logger.info("_reconcile_highlights: fixed %d highlight positions", fix_count)
    return corrected


def _choose_refined_highlights(
    *,
    paragraphs: list[dict] | list[str],
    current: list[dict],
    refined: list[dict],
) -> list[dict]:
    normalized_paragraphs = [
        p if isinstance(p, dict) else {"paragraph_id": f"p_{idx}", "text": p}
        for idx, p in enumerate(paragraphs)
    ]
    if not refined:
        logger.warning("Ignoring empty refined_highlights; keeping current highlights")
        return current

    current_coverage = _check_highlight_coverage(normalized_paragraphs, current)
    refined_coverage = _check_highlight_coverage(normalized_paragraphs, refined)

    current_ratio = float(current_coverage.get("coverage_ratio", 0) or 0)
    refined_ratio = float(refined_coverage.get("coverage_ratio", 0) or 0)
    current_second = float(current_coverage.get("second_half_coverage", 0) or 0)
    refined_second = float(refined_coverage.get("second_half_coverage", 0) or 0)

    if refined_ratio < current_ratio or refined_second < current_second:
        logger.warning(
            "Ignoring refined_highlights because coverage regressed: current=%s refined=%s",
            current_coverage,
            refined_coverage,
        )
        return current
    return refined
