"""AnchorQuote → CanonicalSpan resolver.

将 LLM 输出的 AnchorQuote 列表 resolve 为后端可信的 CanonicalSpan 列表。
严格规则：只接受 exact/canonicalized/boundary_trimmed resolve，
不做 fuzzy fallback，fail-closed。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.schemas.internal.analysis import PreparedSentence
from app.schemas.internal.drafts import AnchorQuote
from app.schemas.internal.normalized import CanonicalSpan
from app.services.analysis.postprocess.anchor_resolution import (
    _build_flexible_pattern,
    _find_all,
    _normalize_anchor_input,
    _normalize_for_matching,
    _source_occurrence_for_span,
    canonicalize_text_anchor_to_source,
)

QuoteResolveReason = Literal[
    "quote_not_found",
    "quote_ambiguous",
    "quote_out_of_order",
    "quote_too_short",
]


@dataclass(frozen=True)
class QuoteResolveError:
    """单个 quote 的 resolve 错误。"""

    quote_text: str
    reason: QuoteResolveReason
    sentence_id: str


def _candidate_spans_for_quote(
    sentence: PreparedSentence,
    quote_text: str,
) -> list[tuple[int, int]]:
    """Return all acceptable local candidate spans for ambiguity checks.

    This intentionally mirrors the non-fuzzy parts of resolve_text_anchor:
    direct exact/casefold matches first, then flexible punctuation matches,
    then normalized matching. Exact and casefold are combined so a lowercase
    exact match cannot hide another uppercase occurrence in the same sentence.
    """
    normalized_quote = _normalize_anchor_input(quote_text)
    direct_matches = set(_find_all(sentence.text, normalized_quote))
    direct_matches.update(
        (m.start(), m.end())
        for m in re.finditer(
            re.escape(normalized_quote),
            sentence.text,
            flags=re.IGNORECASE,
        )
    )
    if direct_matches:
        return sorted(direct_matches)

    flexible_matches = {
        (m.start(), m.end())
        for m in re.finditer(
            _build_flexible_pattern(normalized_quote),
            sentence.text,
            flags=re.IGNORECASE,
        )
    }
    if flexible_matches:
        return sorted(flexible_matches)

    normalized_text, index_map = _normalize_for_matching(sentence.text)
    normalized_anchor, _ = _normalize_for_matching(normalized_quote)
    if not normalized_anchor:
        return []

    normalized_matches = _find_all(normalized_text, normalized_anchor)
    return sorted({
        (index_map[start], index_map[end - 1] + 1)
        for start, end in normalized_matches
    })


def _error(
    sentence: PreparedSentence,
    quote_text: str,
    reason: QuoteResolveReason,
) -> QuoteResolveError:
    return QuoteResolveError(
        quote_text=quote_text,
        reason=reason,
        sentence_id=sentence.sentence_id,
    )


def resolve_anchor_quotes(
    sentence: PreparedSentence,
    quotes: list[AnchorQuote],
    *,
    min_standalone_length: int = 2,
) -> tuple[list[CanonicalSpan], list[QuoteResolveError]]:
    """将 AnchorQuote 列表 resolve 为 CanonicalSpan 列表。

    严格规则：
    - 每个 quote.text 必须在 sentence.text 中找到 exact match
      （允许 canonicalized/boundary_trimmed）
    - 多次出现且无法消歧 → quote_ambiguous
    - multi quote 顺序错误 → quote_out_of_order
    - 单独极短功能词 → quote_too_short
    - 找不到 → quote_not_found
    - 任一 quote 失败则整个 annotation drop（fail-closed）

    Args:
        sentence: 目标句子
        quotes: AnchorQuote 列表
        min_standalone_length: 极短功能词阈值（字符数），
            单独 quote 长度 <= 此值时 drop quote_too_short，
            作为 multi-range 一部分时允许

    Returns:
        (spans, errors) — 如果有 errors 则 spans 为空列表
    """
    errors: list[QuoteResolveError] = []
    spans: list[CanonicalSpan] = []
    previous_end: int | None = None

    for quote in quotes:
        # 极短功能词检查：单独 quote 时 drop
        if len(quotes) == 1 and len(quote.text) <= min_standalone_length:
            errors.append(
                QuoteResolveError(
                    quote_text=quote.text,
                    reason="quote_too_short",
                    sentence_id=sentence.sentence_id,
                )
            )
            return [], errors

        candidate_spans = _candidate_spans_for_quote(sentence, quote.text)
        if len(candidate_spans) > 1:
            errors.append(_error(sentence, quote.text, "quote_ambiguous"))
            return [], errors

        # 用 canonicalize 获取 resolution_kind 和精确坐标
        resolved = canonicalize_text_anchor_to_source(
            sentence, quote.text,
        )
        if resolved is None:
            # 不应该到这里（歧义检查已通过），但 fail-closed
            errors.append(
                QuoteResolveError(
                    quote_text=quote.text,
                    reason="quote_not_found",
                    sentence_id=sentence.sentence_id,
                )
            )
            return [], errors

        # 只接受 exact / canonicalized / boundary_trimmed
        if resolved.resolution_kind not in (
            "exact", "canonicalized", "boundary_trimmed",
        ):
            errors.append(
                QuoteResolveError(
                    quote_text=quote.text,
                    reason="quote_not_found",
                    sentence_id=sentence.sentence_id,
                )
            )
            return [], errors

        if not candidate_spans:
            errors.append(_error(sentence, quote.text, "quote_not_found"))
            return [], errors

        # 顺序检查：multi quote 必须按源文本顺序
        if previous_end is not None and resolved.span.start < previous_end:
            errors.append(
                QuoteResolveError(
                    quote_text=quote.text,
                    reason="quote_out_of_order",
                    sentence_id=sentence.sentence_id,
                )
            )
            return [], errors

        # 计算 occurrence
        occurrence = _source_occurrence_for_span(
            sentence, resolved.text, resolved.span,
        )

        span = CanonicalSpan(
            sentence_id=sentence.sentence_id,
            start=resolved.span.start,
            end=resolved.span.end,
            text=resolved.text,
            role=quote.role,
            source_quote=quote.text,
            resolution_kind=resolved.resolution_kind,
            occurrence=occurrence,
        )
        spans.append(span)
        previous_end = resolved.span.end

    return spans, errors


def resolve_vocab_text_to_canonical_span(
    sentence: PreparedSentence,
    text: str,
) -> tuple[CanonicalSpan | None, list[QuoteResolveError]]:
    """将 DraftVocabHighlight.text resolve 为单个 CanonicalSpan。

    用于 DraftVocabHighlight（没有 anchor_quotes，只有 text）。
    同样只接受 exact/canonicalized/boundary_trimmed。
    返回 (span, errors)：成功时 errors 为空，失败时 span 为 None。
    """
    # 极短功能词检查
    if len(text) <= 2:
        return None, [
            QuoteResolveError(
                quote_text=text,
                reason="quote_too_short",
                sentence_id=sentence.sentence_id,
            ),
        ]

    candidate_spans = _candidate_spans_for_quote(sentence, text)
    if len(candidate_spans) > 1:
        return None, [_error(sentence, text, "quote_ambiguous")]

    # 用 canonicalize 获取 resolution_kind 和精确坐标
    resolved = canonicalize_text_anchor_to_source(
        sentence, text,
    )
    if resolved is None:
        return None, [
            QuoteResolveError(
                quote_text=text,
                reason="quote_not_found",
                sentence_id=sentence.sentence_id,
            ),
        ]

    if resolved.resolution_kind not in (
        "exact", "canonicalized", "boundary_trimmed",
    ):
        return None, [
            QuoteResolveError(
                quote_text=text,
                reason="quote_not_found",
                sentence_id=sentence.sentence_id,
            ),
        ]

    if not candidate_spans:
        return None, [_error(sentence, text, "quote_not_found")]

    occurrence = _source_occurrence_for_span(
        sentence, resolved.text, resolved.span,
    )

    return CanonicalSpan(
        sentence_id=sentence.sentence_id,
        start=resolved.span.start,
        end=resolved.span.end,
        text=resolved.text,
        role=None,
        source_quote=text,
        resolution_kind=resolved.resolution_kind,
        occurrence=occurrence,
    ), []
