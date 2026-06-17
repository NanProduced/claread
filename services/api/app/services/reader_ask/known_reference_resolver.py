"""Known reference resolution: candidate pool, title scoring, and policy.

This module handles resolving user references to known articles via
ILIKE search → candidate pool → title scoring → resolution policy.

It was extracted from resolver.py (Phase 4 Round 4) to separate known
reference resolution concerns from structured asset resolution.

Internal types (ReferenceCandidate, ScoredReferenceCandidate) are
re-exported by resolver.py for backward compatibility but must NOT
leak to service.py or the answer agent prompt.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from app.services.reader_ask import planner
from app.services.reader_ask import repository as repo
from app.services.reader_ask import utils


# ---------------------------------------------------------------------------
# Typed internal candidate contracts (Phase 4 Round 3)
#
# These dataclasses replace loose dict rows inside the resolver pipeline.
# They are internal to the resolver — they do NOT leak to service.py or
# the answer agent prompt. service.py only consumes
# ReaderAskReferenceResolution.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ReferenceCandidate:
    """A normalized candidate record from the candidate pool.

    Constructed from repo row dicts via _to_reference_candidate().
    Preserves all fields needed for scoring and payload building.
    """

    record_id: str
    title: str
    updated_at: str | None = None
    render_scene_json: dict[str, Any] = field(default_factory=dict)
    page_state_json: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ScoredReferenceCandidate:
    """A reference candidate with its title match score."""

    score: int
    candidate: ReferenceCandidate


# ---------------------------------------------------------------------------
# Semantic reranker contract (Phase 4 Round 5)
#
# Protocol for pluggable semantic reranking. The default implementation
# is identity (no-op) — it returns candidates unchanged. This contract
# exists so that a future Round can inject LLM/embedding-based reranking
# without changing the resolve_known_references pipeline shape.
#
# This is an internal contract — it must NOT leak to service.py.
# ---------------------------------------------------------------------------


@runtime_checkable
class ReferenceReranker(Protocol):
    """Protocol for semantic reranking of scored reference candidates.

    Implementations may reorder candidates and/or adjust scores based on
    semantic similarity (e.g. LLM/embedding). The default implementation
    is identity (no-op) — it returns candidates unchanged.
    """

    async def rerank(
        self,
        query: str,
        ranked: list[ScoredReferenceCandidate],
    ) -> list[ScoredReferenceCandidate]: ...


class IdentityReferenceReranker:
    """Default no-op reranker: returns candidates unchanged.

    Used when no semantic reranking is configured. Preserves the exact
    order and scores from deterministic title matching.
    """

    async def rerank(
        self,
        query: str,
        ranked: list[ScoredReferenceCandidate],
    ) -> list[ScoredReferenceCandidate]:
        return ranked


# ---------------------------------------------------------------------------
# LLM semantic reranker adapter (Phase 4 Round 6)
#
# Stable I/O contracts and adapter for LLM-based semantic reranking.
# The callback receives only safe input fields and returns score
# adjustments. The adapter maps outputs back with defensive validation.
#
# Not enabled by default — only used when explicitly injected via
# resolve_known_references(reranker=...).
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class SemanticRerankInput:
    """Stable input contract for semantic reranking.

    Only exposes fields safe for LLM consumption — no internal state,
    no render_scene_json, no page_state_json.
    """

    record_id: str
    title: str
    updated_at: str | None
    overview_hint: str | None
    deterministic_score: int


@dataclass(slots=True, frozen=True)
class SemanticRerankOutput:
    """Stable output contract from semantic reranking.

    The adapter maps this back to ScoredReferenceCandidate with
    defensive validation (unknown record_ids ignored, duplicates
    de-duped, scores clamped).
    """

    record_id: str
    score_adjustment: int  # delta applied to deterministic score
    reason: str | None = None


SemanticRerankCallback = Callable[
    [str, list[SemanticRerankInput]],
    Awaitable[list[SemanticRerankOutput]],
]

_SEMANTIC_SCORE_MIN = 0
_SEMANTIC_SCORE_MAX = 100


class LlmReferenceReranker:
    """Reranker that delegates to an LLM semantic callback.

    The callback receives only safe input fields (SemanticRerankInput)
    and returns score adjustments (SemanticRerankOutput). The adapter
    maps outputs back to ScoredReferenceCandidate with defensive
    validation:

    - Unknown record_ids are ignored
    - Duplicate record_ids keep the first occurrence
    - Empty/exception results fall back to original ranked list
    - Final scores are clamped to [0, 100]
    """

    def __init__(self, callback: SemanticRerankCallback) -> None:
        self._callback = callback

    async def rerank(
        self,
        query: str,
        ranked: list[ScoredReferenceCandidate],
    ) -> list[ScoredReferenceCandidate]:
        if not ranked:
            return ranked

        inputs = self._build_inputs(ranked)

        try:
            outputs = await self._callback(query, inputs)
        except Exception:
            return ranked

        if not outputs:
            return ranked

        return self._apply_outputs(ranked, outputs)

    @staticmethod
    def _build_inputs(ranked: list[ScoredReferenceCandidate]) -> list[SemanticRerankInput]:
        inputs: list[SemanticRerankInput] = []
        for item in ranked:
            c = item.candidate
            overview_hint = utils.truncate_text_optional(
                utils.resolve_record_overview(
                    render_scene=c.render_scene_json,
                    page_state_json=c.page_state_json,
                ).get("overview"),
                140,
            )
            inputs.append(SemanticRerankInput(
                record_id=c.record_id,
                title=c.title,
                updated_at=c.updated_at,
                overview_hint=overview_hint,
                deterministic_score=item.score,
            ))
        return inputs

    @staticmethod
    def _apply_outputs(
        ranked: list[ScoredReferenceCandidate],
        outputs: list[SemanticRerankOutput],
    ) -> list[ScoredReferenceCandidate]:
        adjustments: dict[str, SemanticRerankOutput] = {}
        for out in outputs:
            if out.record_id not in adjustments:
                adjustments[out.record_id] = out

        result: list[ScoredReferenceCandidate] = []
        for item in ranked:
            rid = item.candidate.record_id
            if rid in adjustments:
                new_score = max(
                    _SEMANTIC_SCORE_MIN,
                    min(_SEMANTIC_SCORE_MAX, item.score + adjustments[rid].score_adjustment),
                )
                result.append(ScoredReferenceCandidate(score=new_score, candidate=item.candidate))
            else:
                result.append(ScoredReferenceCandidate(score=item.score, candidate=item.candidate))

        return result


def build_reference_reranker(
    *,
    enabled: bool = False,
    callback: SemanticRerankCallback | None = None,
) -> ReferenceReranker | None:
    """Build a reference reranker based on configuration.

    Returns None when reranking is disabled (default), which causes
    resolve_known_references to use IdentityReferenceReranker.

    When enabled=True and callback is provided, returns an
    LlmReferenceReranker wrapping the callback.

    This factory is the single point where reranker construction
    is controlled. Callers should pass the result to
    resolve_known_references(reranker=...).
    """
    if not enabled:
        return None
    if callback is None:
        return None
    return LlmReferenceReranker(callback)


def _to_reference_candidate(row: dict[str, Any]) -> ReferenceCandidate | None:
    """Normalize a repo row dict into a ReferenceCandidate.

    Missing record IDs are invalid for reference resolution and are filtered
    out before scoring/policy. Missing optional fields degrade gracefully.
    """
    record_id = str(row.get("id") or "").strip()
    if not record_id:
        return None
    return ReferenceCandidate(
        record_id=record_id,
        title=str(row.get("title") or ""),
        updated_at=row.get("updated_at"),
        render_scene_json=row.get("render_scene_json") if isinstance(row.get("render_scene_json"), dict) else {},
        page_state_json=row.get("page_state_json") if isinstance(row.get("page_state_json"), dict) else {},
    )


def _to_reference_candidates(rows: list[dict[str, Any]]) -> list[ReferenceCandidate]:
    candidates: list[ReferenceCandidate] = []
    for row in rows:
        candidate = _to_reference_candidate(row)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


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


# Legacy semantic fallback: cross-language title matching.
#
# This is a deterministic keyword mapping, NOT a semantic resolver.
# It bridges the gap when users describe an English-titled article in
# Chinese (or vice versa). It will be replaced by an LLM-based
# resolver in Phase 4 (Resolver / Retrieval).
#
# Known limitations:
# - Only covers domain-agnostic high-frequency terms
# - Generic words (e.g. "问题"/problem, "发展"/development) produce
#   low-confidence matches that can only be ambiguous, never auto-resolved
# - No understanding of context, domain, or user intent
#
# Contract: cross-lang scores are always in the 50-55 range, which
# stays below the 90+ unique-and-margin auto-resolve policy. They can
# only produce "ambiguous" results, never "resolved".
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

    Legacy semantic fallback — deterministic keyword mapping, not an LLM
    resolver. Returns scores in the 40-55 range (always below the 90+
    unique-and-margin auto-resolve policy). Will be replaced in Phase 4.

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
    """Score a query against a record title.

    Scoring tiers (deterministic title matching):
    - 100: exact match
    - 90: prefix match (query starts the title)
    - 80: substring match (query contained in title)
    - 70: all query tokens present in title
    - 60: fuzzy match (stripped articles/punctuation)
    - 50-55: cross-language keyword match (legacy semantic fallback)
    - 50: partial token match (≥50% tokens hit)
    - 40: levenshtein fuzzy or partial English token overlap
    - 0: no match

    Contract: scores < 50 → not_found; scores 50-69 → ambiguous only;
    scores 70-89 → ambiguous only; scores ≥ 90 with a single top hit and
    clear margin → auto-resolve.
    """
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


def _candidate_payload_from_typed(candidate: ReferenceCandidate) -> dict[str, Any]:
    """Build a disambiguation candidate payload from a ReferenceCandidate."""
    payload: dict[str, Any] = {
        "record_id": candidate.record_id,
        "title": candidate.title or "Untitled",
        "updated_at": candidate.updated_at,
    }
    overview_hint = utils.truncate_text_optional(
        utils.resolve_record_overview(
            render_scene=candidate.render_scene_json,
            page_state_json=candidate.page_state_json,
        ).get("overview"),
        140,
    )
    if overview_hint:
        payload["overview_hint"] = overview_hint
    return payload


# ---------------------------------------------------------------------------
# Reference resolution: candidate pool, scoring, and policy
# ---------------------------------------------------------------------------


async def build_reference_candidate_pool(
    *,
    user_id: UUID,
    current_record_id: UUID,
    query: str | None,
    finder: Callable[..., Awaitable[list[dict[str, str]]]] | None = None,
) -> tuple[list[ReferenceCandidate], planner.ReaderAskResolutionStrategy]:
    """Build the candidate pool for reference resolution.

    Returns (candidates, strategy) where strategy indicates the source:
    - RESOLUTION_STRATEGY_TITLE_SEARCH: ILIKE search returned results
    - RESOLUTION_STRATEGY_RECENT_FALLBACK: ILIKE returned nothing, fell back to recent records
    - RESOLUTION_STRATEGY_NO_QUERY_RECENT: no query provided, using recent records directly
    """
    if query is None:
        rows = await repo.list_recent_records(
            user_id,
            exclude_record_id=current_record_id,
            limit=5,
        )
        return _to_reference_candidates(rows), planner.RESOLUTION_STRATEGY_NO_QUERY_RECENT

    finder_fn = finder or repo.search_records_by_title
    rows = await finder_fn(
        user_id,
        query=query,
        exclude_record_id=current_record_id,
        limit=8,
    )

    if rows:
        return _to_reference_candidates(rows), planner.RESOLUTION_STRATEGY_TITLE_SEARCH

    # When ILIKE returns no results, fall back to recent records for
    # cross-language / weak-semantic matching. The _score_title_match
    # function (which includes legacy _cross_lang_score) will rank them.
    #
    # NOTE: recent records are only a candidate pool — they do NOT
    # represent semantic success. If _score_title_match doesn't find
    # a meaningful match (score ≥ 50), the result is still not_found.
    recent_rows = await repo.list_recent_records(
        user_id,
        exclude_record_id=current_record_id,
        limit=20,
    )
    return _to_reference_candidates(recent_rows), planner.RESOLUTION_STRATEGY_RECENT_FALLBACK


def score_reference_candidates(
    query: str,
    candidates: list[ReferenceCandidate],
) -> list[ScoredReferenceCandidate]:
    """Score and rank candidates by title match.

    Returns a list of ScoredReferenceCandidate sorted by score descending,
    excluding candidates with score <= 0.
    """
    ranked: list[ScoredReferenceCandidate] = []
    for candidate in candidates:
        score = _score_title_match(query, candidate.title)
        if score <= 0:
            continue
        ranked.append(ScoredReferenceCandidate(score=score, candidate=candidate))
    ranked.sort(key=lambda item: item.score, reverse=True)
    return ranked


async def rerank_reference_candidates(
    query: str,
    ranked: list[ScoredReferenceCandidate],
    reranker: ReferenceReranker | None = None,
) -> list[ScoredReferenceCandidate]:
    """Rerank scored candidates using the configured reranker.

    If no reranker is provided, uses IdentityReferenceReranker (no-op).
    After reranking, the output is always re-sorted by score descending.
    This ensures the downstream policy always receives a well-ordered list,
    even if a reranker returns candidates in a different order. Rerankers
    that want to change the effective ranking should adjust scores rather
    than just reordering — the re-sort normalizes the output.
    """
    effective_reranker = reranker or IdentityReferenceReranker()
    result = await effective_reranker.rerank(query, ranked)
    result.sort(key=lambda item: item.score, reverse=True)
    return result


def _build_resolution_meta(
    *,
    strategy: planner.ReaderAskResolutionStrategy,
    candidate_count: int,
    ranked: list[ScoredReferenceCandidate],
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    """Build observation metadata for a reference resolution result.

    Uses contract field names from planner.RESOLUTION_META_* constants.
    """
    top_score = ranked[0].score if ranked else None
    runner_up_score = ranked[1].score if len(ranked) > 1 else None
    return {
        planner.RESOLUTION_META_STRATEGY: strategy,
        planner.RESOLUTION_META_CANDIDATE_COUNT: candidate_count,
        planner.RESOLUTION_META_SCORED_CANDIDATE_COUNT: len(ranked),
        planner.RESOLUTION_META_TOP_SCORE: top_score,
        planner.RESOLUTION_META_RUNNER_UP_SCORE: runner_up_score,
        planner.RESOLUTION_META_FALLBACK_REASON: fallback_reason,
    }


def apply_reference_resolution_policy(
    *,
    query: str,
    ranked: list[ScoredReferenceCandidate],
    strategy: planner.ReaderAskResolutionStrategy,
    candidate_count: int,
) -> planner.ReaderAskReferenceResolution:
    """Apply resolution policy to scored candidates.

    Policy:
    - score < 50 → not_found
    - score 50-89 → ambiguous
    - score >= 90 with unique top hit and margin >= 20 → resolved
    - otherwise → ambiguous

    Args:
        candidate_count: Normalized candidate pool size before scoring,
            i.e. len(candidates) from the candidate pool builder.
    """
    fallback_reason = planner.RESOLUTION_FALLBACK_ILIKE_EMPTY if strategy == planner.RESOLUTION_STRATEGY_RECENT_FALLBACK else None
    meta = _build_resolution_meta(
        strategy=strategy,
        candidate_count=candidate_count,
        ranked=ranked,
        fallback_reason=fallback_reason,
    )

    if not ranked:
        return planner.ReaderAskReferenceResolution(
            attempted=True,
            status="not_found",
            query=query,
            reason=f"没有找到标题能直接命中\u201c{query}\u201d的已知文章。",
            resolution_meta=meta,
        )

    top_score = ranked[0].score
    # Very low confidence (< 50) — no meaningful match at all
    if top_score < 50:
        return planner.ReaderAskReferenceResolution(
            attempted=True,
            status="not_found",
            query=query,
            reason=f'没有找到标题能直接命中\u201c{query}\u201d的已知文章。',
            resolution_meta=meta,
        )
    # Weak but meaningful match (50-69) — present as ambiguous candidates
    # so the user can pick from the list. This covers cross-language and
    # partial-title matches that aren't strong enough for auto-resolve.
    if top_score < 70:
        weak_candidates = [item.candidate for item in ranked if item.score >= 50]
        return planner.ReaderAskReferenceResolution(
            attempted=True,
            status="ambiguous",
            query=query,
            reason=f'\u201c{query}\u201d可能命中了以下文章，请确认你想引用哪一篇：',
            ambiguous_records=[_candidate_payload_from_typed(c) for c in weak_candidates[:4]],
            resolution_meta=meta,
        )
    top_hits = [item.candidate for item in ranked if item.score == top_score]
    runner_up_score = ranked[1].score if len(ranked) > 1 else None
    high_confidence_single_hit = top_score >= 90 and len(top_hits) == 1
    clear_margin = runner_up_score is None or (top_score - runner_up_score) >= 20
    if not high_confidence_single_hit or not clear_margin:
        return planner.ReaderAskReferenceResolution(
            attempted=True,
            status="ambiguous",
            query=query,
            reason=f"\u201c{query}\u201d命中了多个候选，请补充更完整的标题。",
            ambiguous_records=[
                _candidate_payload_from_typed(c)
                for c in (top_hits[:3] if top_hits else [item.candidate for item in ranked[:3]])
            ],
            resolution_meta=meta,
        )

    match = top_hits[0]
    resolved_payload: dict[str, Any] = {
        "record_id": match.record_id,
        "title": match.title or query,
        "updated_at": match.updated_at,
    }
    overview_hint = utils.truncate_text_optional(
        utils.resolve_record_overview(
            render_scene=match.render_scene_json,
            page_state_json=match.page_state_json,
        ).get("overview"),
        140,
    )
    if overview_hint:
        resolved_payload["overview_hint"] = overview_hint
    return planner.ReaderAskReferenceResolution(
        attempted=True,
        status="resolved",
        query=query,
        reason=f"已命中历史文章\u201c{match.title or query}\u201d。",
        resolved_records=[resolved_payload],
        resolution_meta=meta,
    )


async def resolve_known_references(
    *,
    user_id: UUID,
    current_record_id: UUID,
    reference_needs: planner.ReaderAskReferenceNeeds,
    finder: Callable[..., Awaitable[list[dict[str, str]]]] | None = None,
    reranker: ReferenceReranker | None = None,
) -> planner.ReaderAskReferenceResolution:
    if not reference_needs.requested:
        return planner.ReaderAskReferenceResolution(
            resolution_meta=_build_resolution_meta(
                strategy=planner.RESOLUTION_STRATEGY_NOT_REQUESTED,
                candidate_count=0,
                ranked=[],
            ),
        )

    if not reference_needs.query:
        # When user references another article without a title, return recent records as candidates
        candidates, strategy = await build_reference_candidate_pool(
            user_id=user_id,
            current_record_id=current_record_id,
            query=None,
            finder=finder,
        )
        if not candidates:
            return planner.ReaderAskReferenceResolution(
                attempted=True,
                status="ambiguous",
                query=None,
                reason="请补充你想引用的文章标题，我再把它并入当前讨论。",
                resolution_meta=_build_resolution_meta(
                    strategy=strategy,
                    candidate_count=0,
                    ranked=[],
                ),
            )
        return planner.ReaderAskReferenceResolution(
            attempted=True,
            status="ambiguous",
            query=None,
            reason="请从以下最近阅读的文章中选择你想引用的：",
            ambiguous_records=[_candidate_payload_from_typed(c) for c in candidates],
            resolution_meta=_build_resolution_meta(
                strategy=strategy,
                candidate_count=len(candidates),
                ranked=[],
            ),
        )

    candidates, strategy = await build_reference_candidate_pool(
        user_id=user_id,
        current_record_id=current_record_id,
        query=reference_needs.query,
        finder=finder,
    )
    ranked = score_reference_candidates(reference_needs.query, candidates)
    ranked = await rerank_reference_candidates(reference_needs.query, ranked, reranker=reranker)
    return apply_reference_resolution_policy(
        query=reference_needs.query,
        ranked=ranked,
        strategy=strategy,
        candidate_count=len(candidates),
    )
