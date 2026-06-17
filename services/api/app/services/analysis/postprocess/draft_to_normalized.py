"""DraftAnnotation → NormalizedAnnotation mapper.

将 LLM Draft 层 annotation 转换为后端可信的 NormalizedAnnotation。
resolve 失败时写入 drop_log 并返回 None。
"""

from __future__ import annotations

from app.schemas.internal.analysis import PreparedSentence
from app.schemas.internal.drafts import (
    DraftAnnotation,
    DraftContextGloss,
    DraftGrammarNote,
    DraftPhraseGloss,
    DraftSentenceAnalysis,
    DraftVocabHighlight,
)
from app.schemas.internal.normalized import (
    DropLogEntry,
    NormalizedAnnotation,
    NormalizedContextGloss,
    NormalizedGrammarNote,
    NormalizedPhraseGloss,
    NormalizedSentenceAnalysis,
    NormalizedVocabHighlight,
)
from app.services.analysis.postprocess.anchor_quote_resolver import (
    QuoteResolveError,
    resolve_anchor_quotes,
    resolve_vocab_text_to_canonical_span,
)


def _log_quote_errors(
    errors: list[QuoteResolveError],
    source_agent: str,
    annotation_type: str,
    drop_log: list[DropLogEntry],
) -> None:
    """将 QuoteResolveError 列表写入 drop_log。"""
    for error in errors:
        drop_log.append(
            DropLogEntry.model_construct(
                source_agent=source_agent,
                annotation_type=annotation_type,
                sentence_id=error.sentence_id,
                anchor_text=error.quote_text,
                drop_reason=error.reason,
                drop_stage="grounding",
            )
        )


def draft_to_normalized_annotation(
    draft: DraftAnnotation,
    sentence_map: dict[str, PreparedSentence],
    drop_log: list[DropLogEntry],
    *,
    source_agent: str = "vocabulary",
) -> NormalizedAnnotation | None:
    """将 DraftAnnotation 转换为 NormalizedAnnotation。

    失败时写入 drop_log 并返回 None。
    """
    if isinstance(draft, DraftVocabHighlight):
        return _map_vocab_highlight(draft, sentence_map, drop_log, source_agent)
    if isinstance(draft, DraftPhraseGloss):
        return _map_phrase_gloss(draft, sentence_map, drop_log, source_agent)
    if isinstance(draft, DraftContextGloss):
        return _map_context_gloss(draft, sentence_map, drop_log, source_agent)
    if isinstance(draft, DraftGrammarNote):
        return _map_grammar_note(draft, sentence_map, drop_log, source_agent)
    if isinstance(draft, DraftSentenceAnalysis):
        return _map_sentence_analysis(draft, sentence_map, drop_log, source_agent)
    raise ValueError(f"Unknown DraftAnnotation type: {type(draft)}")


def _get_sentence(
    sentence_id: str,
    sentence_map: dict[str, PreparedSentence],
    annotation_type: str,
    source_agent: str,
    drop_log: list[DropLogEntry],
) -> PreparedSentence | None:
    """获取 PreparedSentence，不存在时写 drop_log 并返回 None。"""
    sentence = sentence_map.get(sentence_id)
    if sentence is None:
        drop_log.append(
            DropLogEntry.model_construct(
                source_agent=source_agent,
                annotation_type=annotation_type,
                sentence_id=sentence_id,
                anchor_text="",
                drop_reason="sentence_id_invalid",
                drop_stage="grounding",
            )
        )
    return sentence


def _map_vocab_highlight(
    draft: DraftVocabHighlight,
    sentence_map: dict[str, PreparedSentence],
    drop_log: list[DropLogEntry],
    source_agent: str,
) -> NormalizedVocabHighlight | None:
    sentence = _get_sentence(
        draft.sentence_id, sentence_map, "vocab_highlight",
        source_agent, drop_log,
    )
    if sentence is None:
        return None

    span, errors = resolve_vocab_text_to_canonical_span(sentence, draft.text)
    if errors:
        _log_quote_errors(errors, source_agent, "vocab_highlight", drop_log)
        return None

    return NormalizedVocabHighlight(
        sentence_id=draft.sentence_id,
        spans=[span],
    )


def _map_phrase_gloss(
    draft: DraftPhraseGloss,
    sentence_map: dict[str, PreparedSentence],
    drop_log: list[DropLogEntry],
    source_agent: str,
) -> NormalizedPhraseGloss | None:
    sentence = _get_sentence(
        draft.sentence_id, sentence_map, "phrase_gloss",
        source_agent, drop_log,
    )
    if sentence is None:
        return None

    spans, errors = resolve_anchor_quotes(sentence, draft.anchor_quotes)
    if errors:
        _log_quote_errors(errors, source_agent, "phrase_gloss", drop_log)
        return None

    return NormalizedPhraseGloss(
        sentence_id=draft.sentence_id,
        spans=spans,
        label=draft.label,
        phrase_type=draft.phrase_type,
        zh=draft.zh,
    )


def _map_context_gloss(
    draft: DraftContextGloss,
    sentence_map: dict[str, PreparedSentence],
    drop_log: list[DropLogEntry],
    source_agent: str,
) -> NormalizedContextGloss | None:
    sentence = _get_sentence(
        draft.sentence_id, sentence_map, "context_gloss",
        source_agent, drop_log,
    )
    if sentence is None:
        return None

    spans, errors = resolve_anchor_quotes(sentence, draft.anchor_quotes)
    if errors:
        _log_quote_errors(errors, source_agent, "context_gloss", drop_log)
        return None

    return NormalizedContextGloss(
        sentence_id=draft.sentence_id,
        spans=spans,
        display=draft.display,
        gloss=draft.gloss,
        reason=draft.reason,
    )


def _map_grammar_note(
    draft: DraftGrammarNote,
    sentence_map: dict[str, PreparedSentence],
    drop_log: list[DropLogEntry],
    source_agent: str,
) -> NormalizedGrammarNote | None:
    sentence = _get_sentence(
        draft.sentence_id, sentence_map, "grammar_note",
        source_agent, drop_log,
    )
    if sentence is None:
        return None

    spans, errors = resolve_anchor_quotes(sentence, draft.anchor_quotes)
    if errors:
        _log_quote_errors(errors, source_agent, "grammar_note", drop_log)
        return None

    return NormalizedGrammarNote(
        sentence_id=draft.sentence_id,
        spans=spans,
        grammar_point=draft.grammar_point,
        pattern=draft.pattern,
        note_zh=draft.note_zh,
    )


def _map_sentence_analysis(
    draft: DraftSentenceAnalysis,
    sentence_map: dict[str, PreparedSentence],
    drop_log: list[DropLogEntry],
    source_agent: str,
) -> NormalizedSentenceAnalysis | None:
    sentence = _get_sentence(
        draft.sentence_id, sentence_map, "sentence_analysis",
        source_agent, drop_log,
    )
    if sentence is None:
        return None

    # sentence_analysis 不需要 anchor quote resolve
    return NormalizedSentenceAnalysis(
        sentence_id=draft.sentence_id,
        label=draft.label,
        analysis_zh=draft.analysis_zh,
        chunks=draft.chunks,
    )
