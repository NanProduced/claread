"""Daily Reader Pipeline orchestrator.

Coordinates the four-layer pipeline: Discovery → Extraction → Scoring,
then selects diverse candidates and runs the Daily Reader Workflow.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from time import perf_counter

import httpx
import orjson

from app.config.settings import get_settings
from app.database import connection as db_connection
from app.llm.router import resolve_model_config
from app.llm.routes import (
    MODEL_ROUTE_DAILY_ANALYSIS,
    MODEL_ROUTE_DAILY_ANNOTATION,
    MODEL_ROUTE_DAILY_REVIEW,
)
from app.services.ai_usage import (
    BILLING_MODE_INTERNAL_ONLY,
    CAPABILITY_DAILY_READER_PIPELINE,
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_SUCCEEDED,
    USAGE_SCOPE_SYSTEM_INTERNAL,
    AIUsageEventCreate,
    record_ai_usage_event,
    resolve_model_metadata,
)
from app.services.daily_reader.cover_download import probe_cover_eligible, process_article_covers
from app.services.daily_reader.discovery import (
    DiscoveredArticle,
    discover_guardian,
    discover_rss_sources,
)
from app.services.daily_reader.extraction import (
    apply_extraction_to_article,
    extract_with_trafilatura,
)
from app.services.daily_reader.pipeline_tracker import PipelineRunTracker
from app.services.daily_reader.scoring import (
    SCORE_THRESHOLD,
    ArticleScore,
    deduplicate,
    filter_by_word_count,
    score_article,
)
from app.services.daily_reader.service import business_today
from app.services.prompting.daily_prompt_strategy import resolve_refined_difficulty
from app.services.prompting.prompt_loader import get_prompt_version

logger = logging.getLogger(__name__)

SOURCE_ROTATION_POLICY = {
    "max_same_source_per_day": 2,
    "topic_diversity": True,
}

SCORING_MAX_CANDIDATES = 15
DAILY_READER_WORKFLOW_NAME = "daily_reader"
DAILY_READER_WORKFLOW_VERSION = "2.0.0"
DAILY_READER_SCHEMA_VERSION = "1.0.0"


@dataclass
class PipelineResult:
    articles: list[dict] = field(default_factory=list)
    candidates_found: int = 0
    candidates_extracted: int = 0
    candidates_scored: int = 0
    candidates_selected: int = 0
    errors: list[str] = field(default_factory=list)
    rejections: list[str] = field(default_factory=list)


ALERT_ZERO_OUTPUT = "zero_output"
ALERT_WORKFLOW_FAILURE = "workflow_failure"
ALERT_ALL_CANDIDATES_FILTERED = "all_candidates_filtered"


def collect_pipeline_alert_reasons(result: PipelineResult) -> list[str]:
    reasons: list[str] = []
    if not result.articles:
        reasons.append(ALERT_ZERO_OUTPUT)
    if result.errors:
        reasons.append(ALERT_WORKFLOW_FAILURE)
    if result.candidates_found > 0 and result.candidates_scored == 0:
        reasons.append(ALERT_ALL_CANDIDATES_FILTERED)
    return reasons


async def emit_pipeline_alerts(
    result: PipelineResult,
    run_id: str | None = None,
) -> None:
    reasons = collect_pipeline_alert_reasons(result)
    if not reasons:
        return

    payload = {
        "run_id": run_id,
        "reasons": reasons,
        "articles_generated": len(result.articles),
        "candidates_found": result.candidates_found,
        "candidates_extracted": result.candidates_extracted,
        "candidates_scored": result.candidates_scored,
        "workflow_errors": result.errors,
        "rejections": result.rejections,
    }
    logger.error(
        "Daily reader pipeline alert run_id=%s reasons=%s",
        run_id,
        reasons,
    )

    webhook_url = get_settings().daily_reader_alert_webhook_url
    if not webhook_url:
        return

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(webhook_url, json=payload)
    except Exception as exc:
        logger.error(
            "Daily reader alert webhook failed run_id=%s: %s",
            run_id,
            exc,
        )


def _resolve_daily_workflow_model_metadata() -> tuple[dict[str, str | None], dict[str, dict[str, str]]]:
    settings = get_settings()
    resolved_models: dict[str, dict[str, str]] = {}
    primary_model_metadata = {
        "model_route": MODEL_ROUTE_DAILY_ANALYSIS,
        "model_profile": None,
        "model_provider": None,
        "model_name": None,
    }

    for route in (
        MODEL_ROUTE_DAILY_ANNOTATION,
        MODEL_ROUTE_DAILY_ANALYSIS,
        MODEL_ROUTE_DAILY_REVIEW,
    ):
        metadata = resolve_model_metadata(settings, route)
        if metadata["model_profile"] is None:
            continue
        resolved_models[route] = {
            "profile": metadata["model_profile"] or "",
            "provider": metadata["model_provider"] or "",
            "model_name": metadata["model_name"] or "",
        }
        if route == MODEL_ROUTE_DAILY_ANALYSIS:
            primary_model_metadata = metadata

    return primary_model_metadata, resolved_models


async def _record_daily_pipeline_event(
    *,
    request_id: str,
    status: str,
    usage_data: dict | None,
    latency_ms: int,
    daily_reader_article_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    metadata_json: dict | None = None,
) -> None:
    primary_model_metadata, resolved_models = _resolve_daily_workflow_model_metadata()
    payload_metadata = dict(metadata_json or {})
    payload_metadata.setdefault("resolved_models", resolved_models)

    await record_ai_usage_event(
        AIUsageEventCreate(
            usage_scope=USAGE_SCOPE_SYSTEM_INTERNAL,
            capability_code=CAPABILITY_DAILY_READER_PIPELINE,
            billing_mode=BILLING_MODE_INTERNAL_ONLY,
            status=status,
            request_id=request_id,
            daily_reader_article_id=daily_reader_article_id,
            workflow_name=DAILY_READER_WORKFLOW_NAME,
            workflow_version=DAILY_READER_WORKFLOW_VERSION,
            schema_version=DAILY_READER_SCHEMA_VERSION,
            prompt_version=get_prompt_version(),
            usage_data=usage_data,
            latency_ms=latency_ms,
            error_code=error_code,
            error_message=error_message,
            metadata_json=payload_metadata,
            **primary_model_metadata,
        )
    )


async def run_daily_pipeline(
    max_count: int = 3,
    force: bool = False,
    tracker: PipelineRunTracker | None = None,
) -> PipelineResult:
    result = PipelineResult()

    # Layer 1: Discovery (concurrent)
    if tracker:
        await tracker.update_stage("discovery")
    guardian_articles, rss_articles = await asyncio.gather(
        discover_guardian(),
        discover_rss_sources(),
    )
    candidates = guardian_articles + rss_articles
    result.candidates_found = len(candidates)
    logger.info("Pipeline discovery: %d candidates", len(candidates))

    # Layer 2: Extraction (concurrent for RSS-sourced articles)
    if tracker:
        await tracker.update_stage("extraction", candidates_found=len(candidates))
    async def _extract_one(article: DiscoveredArticle) -> None:
        if article.needs_extraction:
            extraction = await extract_with_trafilatura(article.url)
            if extraction and extraction.rejection_reason:
                article.text = ""
                rejection_msg = (
                    f"Rejected candidate '{article.title[:60]}' "
                    f"({article.source}): {extraction.rejection_reason}"
                )
                logger.warning("Pipeline extraction rejection: %s", rejection_msg)
                result.rejections.append(rejection_msg)
                if tracker:
                    await tracker.add_error("extraction_rejected", rejection_msg)
            elif extraction:
                apply_extraction_to_article(article, extraction)
            else:
                article.text = ""

    await asyncio.gather(*[_extract_one(a) for a in candidates])

    candidates = [a for a in candidates if a.text]
    result.candidates_extracted = len(candidates)
    logger.info("Pipeline extraction: %d articles with text", len(candidates))

    # Deduplication
    existing_hashes = await _get_existing_text_hashes()
    candidates = deduplicate(candidates, existing_hashes=existing_hashes)

    # Length filter
    candidates = filter_by_word_count(candidates)
    logger.info("Pipeline word count filter: %d articles in range", len(candidates))

    # B-1: cover eligibility probe — real pixel validation of the primary
    # candidate feeds the has_cover signal (previously mere URL presence).
    # B-2 owns the sort priority; this only makes the signal truthful.
    cover_sem = asyncio.Semaphore(8)

    async def _probe_cover(article: DiscoveredArticle) -> None:
        async with cover_sem:
            article.has_qualified_cover = await probe_cover_eligible(article)

    await asyncio.gather(*[_probe_cover(a) for a in candidates])

    # Heuristic pre-filter: only LLM-score articles that pass heuristic threshold
    from app.services.daily_reader.scoring import HEURISTIC_THRESHOLD, heuristic_score
    pre_filtered: list[DiscoveredArticle] = []
    for a in candidates:
        h_score = heuristic_score(a)
        if h_score.score >= HEURISTIC_THRESHOLD:
            pre_filtered.append(a)
    logger.info("Pipeline heuristic pre-filter: %d / %d articles passed (threshold=%.1f)",
                len(pre_filtered), len(candidates), HEURISTIC_THRESHOLD)

    if len(pre_filtered) > SCORING_MAX_CANDIDATES:
        pre_filtered.sort(key=lambda a: heuristic_score(a).score, reverse=True)
        pre_filtered = pre_filtered[:SCORING_MAX_CANDIDATES]
        logger.info("Pipeline scoring cap: trimmed to %d candidates (SCORING_MAX_CANDIDATES=%d)",
                     len(pre_filtered), SCORING_MAX_CANDIDATES)

    # Layer 3: AI Scoring (concurrent, capped)
    if tracker:
        await tracker.update_stage("scoring", candidates_extracted=len(pre_filtered))
    sem = asyncio.Semaphore(10)

    async def _score_one(
        article: DiscoveredArticle,
    ) -> tuple[DiscoveredArticle, ArticleScore | None]:
        async with sem:
            score = await score_article(article)
        return article, score

    score_results = await asyncio.gather(*[_score_one(a) for a in pre_filtered])

    scored: list[tuple[DiscoveredArticle, ArticleScore]] = []
    for article, score in score_results:
        if score and score.score >= SCORE_THRESHOLD:
            scored.append((article, score))

    scored.sort(key=lambda x: (x[0].has_qualified_cover, x[1].score), reverse=True)
    result.candidates_scored = len(scored)
    logger.info("Pipeline scoring: %d articles passed threshold", len(scored))

    # Select diverse candidates (oversample to allow for workflow failures)
    if tracker:
        await tracker.update_stage("selection", candidates_scored=len(scored))
    selected = select_diverse_candidates(scored, max_count=max_count + 2)
    result.candidates_selected = len(selected)
    logger.info("Pipeline selection: %d candidates selected (target: %d)", len(selected), max_count)

    # Execute workflow for each candidate until we have enough
    if tracker:
        await tracker.update_stage("workflow")
    success_count = 0
    for article, score in selected:
        if success_count >= max_count:
            break
        try:
            payload = await _run_workflow_and_store(article, score, tracker=tracker)
            if payload is not None:
                result.articles.append(payload)
                success_count += 1
        except Exception as e:
            error_msg = f"Workflow failed for '{article.title[:30]}': {e}"
            logger.error(error_msg)
            result.errors.append(error_msg)
            if tracker:
                await tracker.add_error("workflow", error_msg)

    if success_count < max_count and len(scored) > len(selected):
        remaining = [s for s in scored if s not in selected]
        for article, score in remaining:
            if success_count >= max_count:
                break
            try:
                payload = await _run_workflow_and_store(article, score, tracker=tracker)
                if payload is not None:
                    result.articles.append(payload)
                    success_count += 1
            except Exception as e:
                error_msg = f"Workflow failed for '{article.title[:30]}': {e}"
                logger.error(error_msg)
                result.errors.append(error_msg)
                if tracker:
                    await tracker.add_error("workflow", error_msg)

    if tracker:
        await tracker.complete(success_count)
    await emit_pipeline_alerts(result, run_id=tracker.run_id if tracker else None)
    return result


def select_diverse_candidates(
    scored: list[tuple[DiscoveredArticle, ArticleScore]],
    max_count: int = 3,
    max_same_source: int = 2,
) -> list[tuple[DiscoveredArticle, ArticleScore]]:
    selected: list[tuple[DiscoveredArticle, ArticleScore]] = []
    source_counts: Counter[str] = Counter()
    selected_topics: list[str] = []

    for article, score in scored:
        if len(selected) >= max_count:
            break

        source = article.source
        if source_counts[source] >= max_same_source:
            continue

        if SOURCE_ROTATION_POLICY["topic_diversity"] and selected_topics:
            article_topics = set(article.tags)
            overlap = sum(1 for t in selected_topics if t in article_topics)
            if overlap >= len(selected_topics) and len(selected) >= 2:
                continue

        selected.append((article, score))
        source_counts[source] += 1
        selected_topics.extend(article.tags)

    return selected


async def _run_workflow_and_store(
    article: DiscoveredArticle, score: ArticleScore, tracker: PipelineRunTracker | None = None
) -> dict | None:
    from app.observability.workflow_tracing import (
        build_workflow_root_metadata,
        build_workflow_root_tags,
    )
    from app.services.daily_reader.workflow import (
        WORKFLOW_NAME,
        WORKFLOW_VERSION,
        build_daily_reader_graph,
    )

    graph = build_daily_reader_graph()

    input_state = {
        "original_text": article.text,
        "title": article.title,
        "subtitle": article.description,
        "source": article.source,
        "source_url": article.url,
        "cover_image_url": article.cover_image_url,
        "tags": article.tags,
        "difficulty": score.difficulty,
        "read_time_minutes": max(1, article.word_count // 200),
        "pipeline_source": article.source,
        "pipeline_meta": {
            "score": score.score,
            "score_details": {
                "language_richness": score.language_richness,
                "topic_interest": score.topic_interest,
                "structure_clarity": score.structure_clarity,
                "cultural_value": score.cultural_value,
            },
        },
    }

    logger.info("Workflow starting for: %s", article.title[:60])
    started_at = perf_counter()
    try:
        final_state = await graph.ainvoke(
            input_state,
            config={
                "run_name": WORKFLOW_NAME,
                "tags": build_workflow_root_tags(
                    WORKFLOW_NAME, surface="daily_reader_pipeline"
                ),
                "metadata": build_workflow_root_metadata(
                    workflow_name=WORKFLOW_NAME,
                    workflow_version=WORKFLOW_VERSION,
                    schema_version="1.0.0",
                    request_id=article.url,
                    source_type="pipeline",
                    reading_goal="daily_reading",
                    reading_variant="standard",
                    profile_id="daily_reader",
                    surface="daily_reader_pipeline",
                    extra={
                        "article_title": article.title[:80],
                        "article_source": article.source,
                        "article_word_count": article.word_count,
                    },
                ),
            },
        )
    except Exception as e:
        logger.error("Daily Reader Workflow execution failed: %s", e)
        if tracker:
            await tracker.add_error("workflow", f"Workflow failed: {article.title[:40]}: {e}")
        await _record_daily_pipeline_event(
            request_id=article.url,
            status=STATUS_FAILED,
            usage_data=None,
            latency_ms=int((perf_counter() - started_at) * 1000),
            error_code=type(e).__name__,
            error_message=str(e),
            metadata_json={
                "entrypoint": "daily_reader_pipeline",
                "article_title": article.title[:80],
                "article_source": article.source,
                "article_word_count": article.word_count,
                "pipeline_score": score.score,
            },
        )
        return None

    if final_state.get("abort"):
        review = final_state.get("review_result", {})
        abort_reason = review.get("reason", "quality_review_rejected")
        logger.info("Workflow aborted for: %s (reason: %s)", article.title[:50], abort_reason)
        if tracker:
            await tracker.add_error("workflow_abort", f"Aborted: {article.title[:40]}: {abort_reason}")
        await _record_daily_pipeline_event(
            request_id=article.url,
            status=STATUS_SKIPPED,
            usage_data=final_state.get("usage_summary"),
            latency_ms=int((perf_counter() - started_at) * 1000),
            error_code="workflow_abort",
            error_message=str(abort_reason),
            metadata_json={
                "entrypoint": "daily_reader_pipeline",
                "article_title": article.title[:80],
                "article_source": article.source,
                "article_word_count": article.word_count,
                "pipeline_score": score.score,
                "pipeline_meta": final_state.get("pipeline_meta", {}),
            },
        )
        return None

    paragraph_notes = final_state.get("paragraph_notes_json", {})
    takeaways = final_state.get("takeaways_json", {})
    logger.info("Workflow final state: paragraph_notes keys=%s, takeaways keys=%s",
                list(paragraph_notes.keys()) if isinstance(paragraph_notes, dict) else type(paragraph_notes),
                list(takeaways.keys()) if isinstance(takeaways, dict) else type(takeaways))

    try:
        if tracker:
            await tracker.update_stage("cover_download")
        # B-1: multi-candidate validation + LLM selection + storage. Never
        # raises for image failures — fallback is cover_url=None + tracker
        # errors + pipeline_meta.cover (no silent failures).
        cover_outcome = await process_article_covers(article, tracker=tracker)

        if tracker:
            await tracker.update_stage("storing")
        payload = await _assemble_payload(article, score, final_state, cover_outcome.cover_url)
        # B-1: null when no qualified candidate (editorial theme fallback);
        # _assemble_payload would otherwise leak the raw remote URL.
        payload["cover_image_url"] = cover_outcome.cover_url
        if cover_outcome.image_blocks:
            payload["body_json"] = {**payload["body_json"], "images": cover_outcome.image_blocks}
        payload["pipeline_meta"] = {
            **(payload.get("pipeline_meta") or {}),
            "cover": cover_outcome.meta,
        }
        await _store_daily_reader(payload)
    except Exception as e:
        await _record_daily_pipeline_event(
            request_id=article.url,
            status=STATUS_FAILED,
            usage_data=final_state.get("usage_summary"),
            latency_ms=int((perf_counter() - started_at) * 1000),
            error_code=type(e).__name__,
            error_message=str(e),
            metadata_json={
                "entrypoint": "daily_reader_pipeline",
                "article_title": article.title[:80],
                "article_source": article.source,
                "article_word_count": article.word_count,
                "pipeline_score": score.score,
            },
        )
        raise

    await _record_daily_pipeline_event(
        request_id=article.url,
        daily_reader_article_id=payload["id"],
        status=STATUS_SUCCEEDED,
        usage_data=final_state.get("usage_summary"),
        latency_ms=int((perf_counter() - started_at) * 1000),
        metadata_json={
            "entrypoint": "daily_reader_pipeline",
            "article_title": article.title[:80],
            "article_source": article.source,
            "article_word_count": article.word_count,
            "pipeline_score": score.score,
            "stored_article_id": payload["id"],
            "stored_status": payload["status"],
        },
    )
    logger.info("Article stored: %s (cover=%s)", article.title[:50], bool(cover_outcome.cover_url))
    return payload


async def run_workflow_only(article_id: str) -> dict | None:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM daily_readers WHERE id = $1",
            article_id,
        )
    if row is None:
        return None

    original_text = row.get("original_text")
    if not original_text:
        raise ValueError(f"Article {article_id} has no original_text stored; retry not possible")

    from app.observability.workflow_tracing import (
        build_workflow_root_metadata,
        build_workflow_root_tags,
    )
    from app.services.daily_reader.workflow import (
        WORKFLOW_NAME,
        WORKFLOW_VERSION,
        build_daily_reader_graph,
    )

    graph = build_daily_reader_graph()

    # A-3: title now stores the Chinese headline; the English source
    # headline lives in original_title. Workflow prompts (takeaways
    # title_zh, highlight context) must keep seeing the English original.
    english_title = row.get("original_title") or row["title"]

    input_state = {
        "original_text": original_text,
        "title": english_title,
        "subtitle": row["subtitle"],
        "source": row["source"],
        "source_url": row["source_url"],
        "cover_image_url": row["cover_image_url"],
        "tags": _decode_jsonb(row["tags"], []),
        "difficulty": row["difficulty"],
        "read_time_minutes": row["read_time_minutes"],
        "pipeline_source": row.get("pipeline_source", row["source"]),
        "pipeline_meta": _decode_jsonb(row["pipeline_meta"], {}),
    }

    started_at = perf_counter()
    try:
        final_state = await graph.ainvoke(
            input_state,
            config={
                "run_name": WORKFLOW_NAME,
                "tags": build_workflow_root_tags(
                    WORKFLOW_NAME, surface="daily_reader_pipeline"
                ),
                "metadata": build_workflow_root_metadata(
                    workflow_name=WORKFLOW_NAME,
                    workflow_version=WORKFLOW_VERSION,
                    schema_version="1.0.0",
                    request_id=article_id,
                    source_type="retry",
                    reading_goal="daily_reading",
                    reading_variant="standard",
                    profile_id="daily_reader",
                    surface="daily_reader_pipeline",
                ),
            },
        )
    except Exception as e:
        logger.error("Retry workflow execution failed for %s: %s", article_id, e)
        await _record_daily_pipeline_event(
            request_id=article_id,
            daily_reader_article_id=article_id,
            status=STATUS_FAILED,
            usage_data=None,
            latency_ms=int((perf_counter() - started_at) * 1000),
            error_code=type(e).__name__,
            error_message=str(e),
            metadata_json={
                "entrypoint": "daily_reader_retry",
                "article_id": article_id,
            },
        )
        raise

    if final_state.get("abort"):
        logger.info("Retry workflow aborted for: %s", article_id)
        await _record_daily_pipeline_event(
            request_id=article_id,
            daily_reader_article_id=article_id,
            status=STATUS_SKIPPED,
            usage_data=final_state.get("usage_summary"),
            latency_ms=int((perf_counter() - started_at) * 1000),
            error_code="workflow_abort",
            error_message="quality_review_rejected",
            metadata_json={
                "entrypoint": "daily_reader_retry",
                "article_id": article_id,
            },
        )
        return None

    async with pool.acquire() as conn:
        # A-3: retry refreshes the localized headline columns alongside the
        # analysis payload. Missing takeaways fields keep the stored values.
        retry_takeaways = final_state.get("takeaways_json") or {}
        retry_title = (retry_takeaways.get("title_zh") or "").strip() or row["title"]
        retry_subtitle_zh = (retry_takeaways.get("subtitle_zh") or "").strip() or None
        retry_tags = retry_takeaways.get("tags_zh") or _decode_jsonb(row["tags"], [])
        await conn.execute(
            """
            UPDATE daily_readers
            SET body_json = $1, highlights_json = $2, paragraph_notes_json = $3,
                takeaways_json = $4, difficulty = $5,
                title = $6, original_title = $7, subtitle_zh = $8, tags = $9,
                status = 'draft', published_at = NULL,
                review_status = 'pending', updated_at = NOW()
            WHERE id = $10
            """,
            final_state.get("body_json", {"paragraphs": []}),
            final_state.get("highlights_json", []),
            final_state.get("paragraph_notes_json", {}),
            final_state.get("takeaways_json", {}),
            # A-2: refined whole-text grade overrides the stored coarse grade.
            resolve_refined_difficulty(final_state.get("paragraph_notes_json"))
            or row["difficulty"],
            retry_title,
            english_title,
            retry_subtitle_zh,
            retry_tags,
            article_id,
        )

    await _record_daily_pipeline_event(
        request_id=article_id,
        daily_reader_article_id=article_id,
        status=STATUS_SUCCEEDED,
        usage_data=final_state.get("usage_summary"),
        latency_ms=int((perf_counter() - started_at) * 1000),
        metadata_json={
            "entrypoint": "daily_reader_retry",
            "article_id": article_id,
            "body_updated": True,
            "highlights_updated": True,
            "paragraph_notes_updated": True,
            "takeaways_updated": True,
        },
    )

    return {
        "id": article_id,
        "status": "retry_completed",
        "body_updated": True,
        "highlights_updated": True,
        "paragraph_notes_updated": True,
        "takeaways_updated": True,
    }


async def _assemble_payload(
    article: DiscoveredArticle, score: ArticleScore, state: dict, local_cover_url: str | None = None
) -> dict:
    today = business_today()
    nnn = await _next_sequence_number(today)
    takeaways = state.get("takeaways_json") or {}
    # A-3: the Chinese editorial headline from takeaways is the stored
    # title; the English source headline moves to original_title.
    # takeaways may be empty when the takeaways node failed — then keep
    # the English headline so the row stays renderable.
    title_zh = (takeaways.get("title_zh") or "").strip() or article.title
    subtitle_zh = (takeaways.get("subtitle_zh") or "").strip() or None
    # A-3 tags verdict: takeaways tags_zh wins; score.tags stays in
    # pipeline_meta as candidate-selection reference only.
    tags_zh = takeaways.get("tags_zh") or article.tags
    pipeline_meta = dict(state.get("pipeline_meta") or {})
    pipeline_meta.setdefault("score_tags", score.tags)
    return {
        "id": f"daily_{today.strftime('%Y')}_{today.strftime('%m')}_{today.strftime('%d')}_{nnn:03d}",
        "title": title_zh,
        "subtitle": article.description,
        "original_title": article.title,
        "subtitle_zh": subtitle_zh,
        "source": article.source,
        "source_url": article.url,
        "publish_date": today,
        # A-2: scorer coarse grade is only for candidate selection; the
        # paragraph-notes refined whole-text grade wins at projection.
        "difficulty": resolve_refined_difficulty(state.get("paragraph_notes_json"))
        or score.difficulty,
        "read_time_minutes": max(1, article.word_count // 200),
        "tags": tags_zh,
        "cover_image_url": local_cover_url or article.cover_image_url,
        "cover_theme": "editorial_warm",
        "body_json": state.get("body_json", {"paragraphs": []}),
        "highlights_json": state.get("highlights_json", []),
        "footer_analysis_json": {},
        "paragraph_notes_json": state.get("paragraph_notes_json", {}),
        "takeaways_json": state.get("takeaways_json", {}),
        "status": "draft",
        "score": score.score,
        "original_text_hash": hashlib.sha256(article.text.encode()).hexdigest(),
        "original_text": article.text,
        "pipeline_source": article.source,
        "pipeline_meta": pipeline_meta,
    }


async def _get_existing_text_hashes() -> set[str]:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT original_text_hash FROM daily_readers WHERE original_text_hash IS NOT NULL"
            )
            return {row["original_text_hash"] for row in rows}
    except Exception as e:
        logger.warning("Failed to fetch existing text hashes: %s", e)
        return set()


def _decode_jsonb(value: object, default: object) -> object:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (str, bytes)):
        try:
            decoded = orjson.loads(value)
        except (orjson.JSONDecodeError, ValueError):
            return default
        if isinstance(decoded, (dict, list)):
            return decoded
        if isinstance(decoded, str):
            try:
                return orjson.loads(decoded)
            except (orjson.JSONDecodeError, ValueError):
                return default
        return default
    return value


async def _next_sequence_number(publish_date: date) -> int:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt FROM daily_readers WHERE publish_date = $1",
                publish_date,
            )
            count = row["cnt"] if row else 0
            return count + 1
    except Exception as e:
        logger.warning("Failed to query sequence number for %s: %s", publish_date, e)
        return 1


async def _store_daily_reader(payload: dict) -> None:
    pool = db_connection.DB_POOL
    if pool is None:
        raise RuntimeError("Database pool not initialized")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO daily_readers (
                id, title, subtitle, original_title, subtitle_zh, source, source_url, publish_date,
                difficulty, read_time_minutes, tags, cover_image_url, cover_theme,
                body_json, highlights_json, footer_analysis_json, paragraph_notes_json, takeaways_json,
                status, score, original_text_hash, original_text,
                pipeline_source, pipeline_meta
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                      $12, $13, $14, $15, $16, $17, $18, $19, $20, $21,
                      $22, $23, $24)
            """,
            payload["id"],
            payload["title"],
            payload["subtitle"],
            payload.get("original_title"),
            payload.get("subtitle_zh"),
            payload["source"],
            payload["source_url"],
            payload["publish_date"],
            payload["difficulty"],
            payload["read_time_minutes"],
            payload["tags"],
            payload["cover_image_url"],
            payload["cover_theme"],
            payload["body_json"],
            payload["highlights_json"],
            payload["footer_analysis_json"],
            payload["paragraph_notes_json"],
            payload["takeaways_json"],
            payload["status"],
            payload["score"],
            payload["original_text_hash"],
            payload.get("original_text"),
            payload["pipeline_source"],
            payload["pipeline_meta"],
        )
