from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from uuid import UUID

from app.services.reader_ask import planner
from app.services.reader_ask import repository as repo
from app.services.reader_ask import utils


def _normalize_title(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


def _normalize_title_for_matching(value: str | None) -> str:
    """Normalize title for fuzzy matching: strip articles, punctuation, and lowercase."""
    text = _normalize_title(value)
    # Remove common English articles
    for article in ("the ", "a ", "an "):
        if text.startswith(article):
            text = text[len(article):]
            break
    # Remove punctuation for matching
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


# Common Chinese→English keyword mappings for cross-language title matching.
# These are high-confidence, domain-agnostic mappings that help bridge the gap
# when users describe an English-titled article in Chinese.
_CROSS_LANG_MAP: dict[str, list[str]] = {
    "气候": ["climate"],
    "环境": ["environment", "environmental"],
    "经济": ["economy", "economic", "economics"],
    "政治": ["politics", "political", "policy"],
    "科技": ["technology", "tech", "technological"],
    "教育": ["education", "educational"],
    "健康": ["health", "healthcare"],
    "历史": ["history", "historical"],
    "文化": ["culture", "cultural"],
    "社会": ["society", "social"],
    "人工智能": ["artificial intelligence", "ai"],
    "机器学习": ["machine learning", "ml"],
    "深度学习": ["deep learning"],
    "自然语言": ["natural language", "nlp"],
    "数据": ["data"],
    "算法": ["algorithm", "algorithms"],
    "网络": ["network", "networks", "internet", "web"],
    "安全": ["security", "safety"],
    "能源": ["energy"],
    "食品": ["food"],
    "医学": ["medicine", "medical"],
    "心理": ["psychology", "psychological"],
    "法律": ["law", "legal"],
    "商业": ["business", "commerce"],
    "金融": ["finance", "financial"],
    "市场": ["market", "marketing"],
    "管理": ["management", "managerial"],
    "设计": ["design"],
    "艺术": ["art", "arts", "artistic"],
    "音乐": ["music", "musical"],
    "电影": ["film", "films", "movie", "movies", "cinema"],
    "文学": ["literature", "literary"],
    "哲学": ["philosophy", "philosophical"],
    "数学": ["mathematics", "math"],
    "物理": ["physics"],
    "化学": ["chemistry", "chemical"],
    "生物": ["biology", "biological"],
    "地理": ["geography", "geographical"],
    "太空": ["space", "astronautics"],
    "宇宙": ["universe", "cosmos", "cosmic"],
    "全球": ["global", "world"],
    "中国": ["china", "chinese"],
    "美国": ["america", "american", "usa"],
    "欧洲": ["europe", "european"],
    "日本": ["japan", "japanese"],
    "印度": ["india", "indian"],
    "非洲": ["africa", "african"],
    "发展": ["development", "developing", "growth"],
    "变化": ["change", "changing"],
    "影响": ["impact", "effect", "influence"],
    "问题": ["problem", "problems", "issue", "issues"],
    "未来": ["future"],
    "创新": ["innovation", "innovative"],
    "可持续": ["sustainable", "sustainability"],
    "改革": ["reform"],
    "革命": ["revolution", "revolutionary"],
}

# Reverse map: English → Chinese (for English query matching Chinese title)
_EN_TO_ZH_MAP: dict[str, str] = {}
for zh, en_list in _CROSS_LANG_MAP.items():
    for en in en_list:
        _EN_TO_ZH_MAP[en] = zh


def _extract_english_tokens(text: str) -> list[str]:
    """Extract English word tokens from a mixed-language string."""
    return [token.lower() for token in re.findall(r"[a-zA-Z]+", text) if len(token) >= 2]


def _extract_chinese_segments(text: str) -> list[str]:
    """Extract continuous Chinese character segments from a string."""
    segments = re.findall(r"[\u4e00-\u9fff]+", text)
    return segments


def _token_match(en_word: str, title_tokens: list[str], title_lower: str) -> bool:
    """Check if an English word/phrase matches at token level.

    Multi-word mappings (e.g. "artificial intelligence") require ALL tokens
    to appear in the title. Single-word mappings (e.g. "ai") require an exact
    token match — prevents "ai" from matching "asia", "aid", "rail", etc.
    """
    word_tokens = en_word.split()
    if len(word_tokens) > 1:
        # Multi-word: every token must appear as a token in the title
        return all(wt in title_tokens for wt in word_tokens)
    # Single word: exact token match to avoid substring false positives
    return en_word in title_tokens


def _cross_lang_score(query: str, title: str) -> int:
    """Score cross-language matches between query and title.

    Handles:
    - Chinese query describing an English-titled article (e.g. "气候" ↔ "Climate")
    - English query describing a Chinese-titled article (e.g. "AI" ↔ "人工智能")
    - Mixed language queries
    """
    score = 0

    # Chinese query → English title
    zh_segments = _extract_chinese_segments(query)
    title_en_tokens = _extract_english_tokens(title)

    for segment in zh_segments:
        # Check if the segment or any prefix of it is in the cross-lang map
        matched = False
        # Try full segment first, then progressively shorter prefixes
        for i in range(len(segment)):
            sub = segment[i:]
            if sub in _CROSS_LANG_MAP:
                en_words = _CROSS_LANG_MAP[sub]
                for en_word in en_words:
                    if _token_match(en_word, title_en_tokens, ""):
                        score = max(score, 55)
                        matched = True
                        break
                if matched:
                    break
            # Also try from start up to each position
            sub_prefix = segment[:len(segment) - i] if i > 0 else segment
            if sub_prefix in _CROSS_LANG_MAP and sub_prefix != sub:
                en_words = _CROSS_LANG_MAP[sub_prefix]
                for en_word in en_words:
                    if _token_match(en_word, title_en_tokens, ""):
                        score = max(score, 50)
                        matched = True
                        break
                if matched:
                    break

    # English query → Chinese title
    query_en_tokens = _extract_english_tokens(query)
    title_zh_segments = _extract_chinese_segments(title)

    # Check single tokens first (e.g. "ai" → "人工智能")
    for en_token in query_en_tokens:
        if en_token in _EN_TO_ZH_MAP:
            zh_word = _EN_TO_ZH_MAP[en_token]
            for zh_seg in title_zh_segments:
                if zh_word in zh_seg:
                    score = max(score, 55)
                    break

    # Check consecutive n-grams for multi-word phrases
    # (e.g. "artificial intelligence" → "人工智能", "machine learning" → "机器学习")
    if not score and len(query_en_tokens) >= 2:
        for n in range(min(len(query_en_tokens), 3), 1, -1):
            for start in range(len(query_en_tokens) - n + 1):
                phrase = " ".join(query_en_tokens[start:start + n])
                if phrase in _EN_TO_ZH_MAP:
                    zh_word = _EN_TO_ZH_MAP[phrase]
                    for zh_seg in title_zh_segments:
                        if zh_word in zh_seg:
                            score = max(score, 55)
                            break
                    if score:
                        break
            if score:
                break

    # English tokens in query matching English tokens in title (even when
    # ILIKE failed because the query also contains Chinese characters).
    # Use exact token equality to avoid substring false positives.
    if query_en_tokens and title_en_tokens:
        en_hit_count = sum(
            1 for qt in query_en_tokens if qt in title_en_tokens
        )
        if en_hit_count > 0:
            ratio = en_hit_count / len(query_en_tokens)
            if ratio >= 0.5:
                score = max(score, 55)
            elif ratio > 0:
                score = max(score, 40)

    return score


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


def _score_title_match(query: str, title: str) -> int:
    normalized_query = _normalize_title(query)
    normalized_title = _normalize_title(title)
    if not normalized_query or not normalized_title:
        return 0
    if normalized_query == normalized_title:
        return 100
    if normalized_title.startswith(normalized_query):
        return 90
    if normalized_query in normalized_title:
        return 80
    query_tokens = [token for token in re.split(r"[\s\-:]+", normalized_query) if token]
    if query_tokens and all(token in normalized_title for token in query_tokens):
        return 70
    # Partial token match (>=50% tokens hit)
    if query_tokens:
        hit_count = sum(1 for token in query_tokens if token in normalized_title)
        if hit_count >= max(len(query_tokens) // 2, 1) and hit_count < len(query_tokens):
            return 50
    # Fuzzy matching on normalized titles (strip articles/punctuation)
    match_query = _normalize_title_for_matching(query)
    match_title = _normalize_title_for_matching(title)
    if match_query and match_title:
        if match_query in match_title or match_title in match_query:
            return 60
        dist = _levenshtein_distance(match_query, match_title)
        max_len = max(len(match_query), len(match_title))
        if max_len > 0 and dist <= 3 and dist / max_len <= 0.3:
            return 40
    # Cross-language matching (Chinese query ↔ English title, or vice versa)
    cross_score = _cross_lang_score(query, title)
    if cross_score > 0:
        return cross_score
    return 0


def _truncate_text(value: str | None, limit: int) -> str | None:
    return utils.truncate_text_optional(value, limit)


def _extract_article_overview(render_scene: dict[str, Any]) -> str | None:
    return utils.extract_article_overview(render_scene)


def _extract_stable_record_insights(render_scene: dict[str, Any], *, limit: int = 3) -> list[str]:
    entries_raw = render_scene.get("sentence_entries") or render_scene.get("sentenceEntries")
    if not isinstance(entries_raw, list):
        return []

    insights: list[str] = []
    seen: set[str] = set()
    for entry in entries_raw:
        if not isinstance(entry, dict):
            continue
        title = _truncate_text(entry.get("title") or entry.get("label") or entry.get("entry_type") or entry.get("entryType"), 40)
        content = _truncate_text(entry.get("content"), 120)
        if not title or not content:
            continue
        summary = f"{title}: {content}"
        if summary in seen:
            continue
        seen.add(summary)
        insights.append(summary)
        if len(insights) >= limit:
            break
    return insights


def lookup_structured_record_assets(
    *,
    record_id: str,
    record_title: str | None,
    render_scene: dict[str, Any],
    page_state_json: dict[str, Any] | None = None,
    reason: str | None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    overview = utils.resolve_record_overview(
        render_scene=render_scene,
        page_state_json=page_state_json,
    )
    article_overview = overview.get("overview")
    record_insights = _extract_stable_record_insights(render_scene)
    source_labels = ["external_record"]
    if article_overview:
        source_labels.append(str(overview.get("source") or "article_overview"))
    if record_insights:
        source_labels.append("record_assets")
    if not article_overview:
        source_labels.append("overview_missing")
    return {
        "record_id": record_id,
        "record_title": record_title,
        "updated_at": updated_at,
        "article_overview": article_overview,
        "article_overview_status": overview.get("status"),
        "article_overview_source": overview.get("source"),
        "article_overview_confidence": overview.get("confidence"),
        "record_insights": record_insights,
        "reason": reason or "explicit_attachment",
        "source_labels": source_labels,
    }


def _analysis_asset_candidates(
    *,
    record_id: str,
    record_title: str | None,
    render_scene: dict[str, Any],
) -> list[dict[str, str]]:
    entries_raw = render_scene.get("sentence_entries") or render_scene.get("sentenceEntries")
    if not isinstance(entries_raw, list):
        return []

    candidates: list[dict[str, str]] = []
    for entry in entries_raw:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("source_kind") or "").strip() == "ask_supplement":
            continue
        entry_type = str(entry.get("entry_type") or entry.get("entryType") or "").strip()
        if not entry_type or entry_type == "content_summary":
            continue
        asset_id = str(entry.get("id") or "").strip()
        if not asset_id:
            continue
        title = _truncate_text(entry.get("title") or entry.get("label") or entry_type, 60)
        summary = _truncate_text(entry.get("content"), 180)
        candidates.append(
            {
                "record_id": record_id,
                "record_title": record_title or "",
                "asset_type": "analysis",
                "asset_id": asset_id,
                "entry_type": entry_type,
                "title": title or entry_type,
                "summary": summary or "稳定分析对象",
                "content_md": str(entry.get("content") or "").strip(),
                "source_labels": ["external_asset", "analysis"],
            }
        )
    return candidates


def _supplement_asset_candidates(
    *,
    record_id: str,
    record_title: str | None,
    supplements: list[dict[str, Any]],
) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for row in supplements:
        asset_id = str(row.get("id") or "").strip()
        if not asset_id:
            continue
        title = _truncate_text(row.get("title"), 60)
        summary = _truncate_text(row.get("content"), 180)
        entry_type = str(row.get("supplement_type") or row.get("entry_type") or "grammar_note")
        candidates.append(
            {
                "record_id": record_id,
                "record_title": record_title or "",
                "asset_type": "supplement",
                "asset_id": asset_id,
                "entry_type": entry_type,
                "title": title or "AI 补充",
                "summary": summary or "AI 补充",
                "content_md": str(row.get("content") or "").strip(),
                "source_labels": ["external_asset", "supplement"],
            }
        )
    return candidates


def _filter_asset_candidates(
    candidates: list[dict[str, str]],
    *,
    requested_asset_type: Literal["analysis", "supplement"] | None,
    explicit_asset_id: str | None = None,
    explicit_entry_type: str | None = None,
) -> list[dict[str, str]]:
    filtered = list(candidates)
    if requested_asset_type is not None:
        filtered = [item for item in filtered if item.get("asset_type") == requested_asset_type]
    if explicit_asset_id:
        filtered = [item for item in filtered if item.get("asset_id") == explicit_asset_id]
    if explicit_entry_type:
        filtered = [item for item in filtered if item.get("entry_type") == explicit_entry_type]
    return filtered


def _candidate_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Build a disambiguation candidate payload from a record row."""
    payload = {
        "record_id": row["id"],
        "title": row.get("title") or "Untitled",
        "updated_at": row.get("updated_at"),
    }
    overview_hint = utils.truncate_text_optional(
        utils.resolve_record_overview(
            render_scene=row.get("render_scene_json") or {},
            page_state_json=row.get("page_state_json") or {},
        ).get("overview"),
        140,
    )
    if overview_hint:
        payload["overview_hint"] = overview_hint
    return payload


async def resolve_structured_asset_references(
    *,
    user_id: UUID,
    current_record_id: UUID,
    external_record_refs: list[dict[str, str]],
    structured_asset_needs: planner.ReaderAskStructuredAssetNeeds,
    bundle_loader: Callable[[UUID, UUID], Awaitable[dict[str, Any]]] | None = None,
    supplement_loader: Callable[[UUID, UUID], Awaitable[list[dict[str, Any]]]] | None = None,
    explicit_asset_refs: list[dict[str, str]] | None = None,
) -> planner.ReaderAskStructuredAssetResolution:
    if not external_record_refs and not explicit_asset_refs:
        return planner.ReaderAskStructuredAssetResolution()
    if not structured_asset_needs.requested and not explicit_asset_refs:
        return planner.ReaderAskStructuredAssetResolution()

    if bundle_loader is None:
        raise RuntimeError("bundle_loader is required for structured asset resolution")
    if supplement_loader is None:
        raise RuntimeError("supplement_loader is required for structured asset resolution")

    explicit_refs = explicit_asset_refs or []
    if explicit_refs:
        resolved_assets: list[dict[str, str]] = []
        for asset_ref in explicit_refs:
            record_id = str(asset_ref.get("record_id") or "").strip()
            if not record_id:
                continue
            record_uuid = UUID(record_id)
            if record_uuid == current_record_id:
                continue
            bundle = await bundle_loader(user_id, record_uuid)
            supplement_rows = await supplement_loader(user_id, record_uuid)
            candidates = [
                *_analysis_asset_candidates(record_id=record_id, record_title=bundle.get("title"), render_scene=bundle.get("render_scene") or {}),
                *_supplement_asset_candidates(record_id=record_id, record_title=bundle.get("title"), supplements=supplement_rows),
            ]
            matches = _filter_asset_candidates(
                candidates,
                requested_asset_type=asset_ref.get("asset_type"),  # type: ignore[arg-type]
                explicit_asset_id=str(asset_ref.get("asset_id") or "").strip() or None,
                explicit_entry_type=str(asset_ref.get("entry_type") or "").strip() or None,
            )
            resolved_assets.extend(matches[:1])
        return planner.ReaderAskStructuredAssetResolution(
            attempted=bool(resolved_assets),
            status="resolved" if resolved_assets else "not_found",
            requested_asset_type=structured_asset_needs.requested_asset_type,
            reason="已并入显式指定的外部稳定资产。" if resolved_assets else "没有找到显式指定的外部稳定资产。",
            record_id=resolved_assets[0]["record_id"] if resolved_assets else None,
            record_title=resolved_assets[0].get("record_title") if resolved_assets else None,
            resolved_assets=resolved_assets,
        )

    if len(external_record_refs) != 1:
        return planner.ReaderAskStructuredAssetResolution(
            attempted=False,
            status="not_needed",
            requested_asset_type=structured_asset_needs.requested_asset_type,
            reason="需要先确定唯一外部文章，再继续定位其中的稳定资产。",
        )

    target = external_record_refs[0]
    record_id = str(target.get("record_id") or "").strip()
    if not record_id:
        return planner.ReaderAskStructuredAssetResolution()
    record_uuid = UUID(record_id)
    if record_uuid == current_record_id:
        return planner.ReaderAskStructuredAssetResolution()

    bundle = await bundle_loader(user_id, record_uuid)
    supplement_rows = await supplement_loader(user_id, record_uuid)
    candidates = [
        *_analysis_asset_candidates(record_id=record_id, record_title=bundle.get("title"), render_scene=bundle.get("render_scene") or {}),
        *_supplement_asset_candidates(record_id=record_id, record_title=bundle.get("title"), supplements=supplement_rows),
    ]
    matches = _filter_asset_candidates(
        candidates,
        requested_asset_type=structured_asset_needs.requested_asset_type,
    )
    if not matches:
        return planner.ReaderAskStructuredAssetResolution(
            attempted=True,
            status="not_found",
            requested_asset_type=structured_asset_needs.requested_asset_type,
            reason="已定位到外部文章，但当前没有命中可并入的稳定资产。",
            record_id=record_id,
            record_title=bundle.get("title"),
        )
    if len(matches) > 1:
        return planner.ReaderAskStructuredAssetResolution(
            attempted=True,
            status="ambiguous",
            requested_asset_type=structured_asset_needs.requested_asset_type,
            reason="已定位到外部文章，但命中了多个稳定资产，请先指定要并入哪一个。",
            record_id=record_id,
            record_title=bundle.get("title"),
            ambiguous_assets=matches[:4],
        )
    return planner.ReaderAskStructuredAssetResolution(
        attempted=True,
        status="resolved",
        requested_asset_type=structured_asset_needs.requested_asset_type,
        reason="已命中外部文章里的稳定资产。",
        record_id=record_id,
        record_title=bundle.get("title"),
        resolved_assets=matches,
    )


async def resolve_known_references(
    *,
    user_id: UUID,
    current_record_id: UUID,
    reference_needs: planner.ReaderAskReferenceNeeds,
    finder: Callable[..., Awaitable[list[dict[str, str]]]] | None = None,
) -> planner.ReaderAskReferenceResolution:
    if not reference_needs.requested:
        return planner.ReaderAskReferenceResolution()

    if not reference_needs.query:
        # When user references another article without a title, return recent records as candidates
        rows = await repo.list_recent_records(
            user_id,
            exclude_record_id=current_record_id,
            limit=5,
        )
        if not rows:
            return planner.ReaderAskReferenceResolution(
                attempted=True,
                status="ambiguous",
                query=None,
                reason="请补充你想引用的文章标题，我再把它并入当前讨论。",
            )
        return planner.ReaderAskReferenceResolution(
            attempted=True,
            status="ambiguous",
            query=None,
            reason="请从以下最近阅读的文章中选择你想引用的：",
            ambiguous_records=[_candidate_payload(row) for row in rows],
        )

    finder_fn = finder or repo.search_records_by_title
    rows = await finder_fn(
        user_id,
        query=reference_needs.query,
        exclude_record_id=current_record_id,
        limit=8,
    )

    # When ILIKE returns no results, fall back to recent records for
    # cross-language / weak-semantic matching. The _score_title_match
    # function (which includes _cross_lang_score) will rank them.
    if not rows:
        recent_rows = await repo.list_recent_records(
            user_id,
            exclude_record_id=current_record_id,
            limit=20,
        )
        rows = recent_rows

    ranked: list[tuple[int, dict[str, str]]] = []
    for row in rows:
        score = _score_title_match(reference_needs.query, row.get("title") or "")
        if score <= 0:
            continue
        ranked.append((score, row))
    ranked.sort(key=lambda item: item[0], reverse=True)

    if not ranked:
        return planner.ReaderAskReferenceResolution(
            attempted=True,
            status="not_found",
            query=reference_needs.query,
            reason=f"没有找到标题能直接命中“{reference_needs.query}”的已知文章。",
        )

    top_score = ranked[0][0]
    # Very low confidence (< 50) — no meaningful match at all
    if top_score < 50:
        return planner.ReaderAskReferenceResolution(
            attempted=True,
            status="not_found",
            query=reference_needs.query,
            reason=f'没有找到标题能直接命中\u201c{reference_needs.query}\u201d的已知文章。',
        )
    # Weak but meaningful match (50-69) — present as ambiguous candidates
    # so the user can pick from the list. This covers cross-language and
    # partial-title matches that aren't strong enough for auto-resolve.
    if top_score < 70:
        weak_candidates = [row for score, row in ranked if score >= 50]
        return planner.ReaderAskReferenceResolution(
            attempted=True,
            status="ambiguous",
            query=reference_needs.query,
            reason=f'\u201c{reference_needs.query}\u201d可能命中了以下文章，请确认你想引用哪一篇：',
            ambiguous_records=[_candidate_payload(row) for row in weak_candidates[:4]],
        )
    top_hits = [row for score, row in ranked if score == top_score]
    runner_up_score = ranked[1][0] if len(ranked) > 1 else None
    high_confidence_single_hit = top_score >= 90 and len(top_hits) == 1
    clear_margin = runner_up_score is None or (top_score - runner_up_score) >= 20
    if not high_confidence_single_hit or not clear_margin:
        return planner.ReaderAskReferenceResolution(
            attempted=True,
            status="ambiguous",
            query=reference_needs.query,
            reason=f"“{reference_needs.query}”命中了多个候选，请补充更完整的标题。",
            ambiguous_records=[
                _candidate_payload(row)
                for row in (top_hits[:3] if top_hits else [candidate for _, candidate in ranked[:3]])
            ],
        )

    match = top_hits[0]
    resolved_payload = {
        "record_id": match["id"],
        "title": match.get("title") or reference_needs.query,
        "updated_at": match.get("updated_at"),
    }
    overview_hint = utils.truncate_text_optional(
        utils.resolve_record_overview(
            render_scene=match.get("render_scene_json") or {},
            page_state_json=match.get("page_state_json") or {},
        ).get("overview"),
        140,
    )
    if overview_hint:
        resolved_payload["overview_hint"] = overview_hint
    return planner.ReaderAskReferenceResolution(
        attempted=True,
        status="resolved",
        query=reference_needs.query,
        reason=f"已命中历史文章“{match.get('title') or reference_needs.query}”。",
        resolved_records=[resolved_payload],
    )
