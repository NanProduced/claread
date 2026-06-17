"""Tests for normalized_postprocess helpers."""
from __future__ import annotations

from app.schemas.internal.normalized import (
    CanonicalSpan,
    DropLogEntry,
    NormalizedContextGloss,
    NormalizedGrammarNote,
    NormalizedPhraseGloss,
    NormalizedVocabHighlight,
)
from app.services.analysis.postprocess.normalized_postprocess import (
    build_canonical_stats,
    postprocess_normalized_annotations,
)

# ── Helpers ──────────────────────────────────────────────────────


def _make_span(sid: str, start: int, end: int, text: str) -> CanonicalSpan:
    return CanonicalSpan(
        sentence_id=sid, start=start, end=end, text=text, resolution_kind="exact",
    )


def _make_vocab(sid: str, span: CanonicalSpan) -> NormalizedVocabHighlight:
    return NormalizedVocabHighlight(sentence_id=sid, spans=[span])


def _make_phrase(sid: str, span: CanonicalSpan, label: str) -> NormalizedPhraseGloss:
    return NormalizedPhraseGloss(
        sentence_id=sid, spans=[span], label=label, phrase_type="collocation", zh=label,
    )


def _make_context(sid: str, span: CanonicalSpan, display: str) -> NormalizedContextGloss:
    return NormalizedContextGloss(
        sentence_id=sid, spans=[span], display=display, gloss=display, reason="context",
    )


def _make_grammar(sid: str, span: CanonicalSpan, point: str) -> NormalizedGrammarNote:
    return NormalizedGrammarNote(
        sentence_id=sid, spans=[span], grammar_point=point, pattern=point, note_zh=point,
    )


# ── Tests ────────────────────────────────────────────────────────


def test_dedup_removes_duplicate_annotations():
    """Dedup removes duplicate annotations."""
    span = _make_span("s1", 0, 10, "the word")
    a1 = _make_vocab("s1", span)
    a2 = _make_vocab("s1", span)

    drop_log: list[DropLogEntry] = []
    result = postprocess_normalized_annotations([a1, a2], drop_log, annotation_density=3)

    assert len(result) == 1
    assert len(drop_log) == 1
    assert drop_log[0].drop_reason == "duplicate"
    assert drop_log[0].drop_stage == "deduplication"


def test_partial_overlap_context_gloss_wins():
    """Partial overlap conflict — context_gloss wins over phrase_gloss and vocab_highlight."""
    span = _make_span("s1", 0, 10, "the word")
    vocab = _make_vocab("s1", span)
    phrase = _make_phrase("s1", span, "the word")
    context = _make_context("s1", span, "the word")

    drop_log: list[DropLogEntry] = []
    result = postprocess_normalized_annotations(
        [vocab, phrase, context], drop_log, annotation_density=3,
    )

    assert len(result) == 1
    assert result[0].type == "context_gloss"
    conflict_drops = [e for e in drop_log if e.drop_reason == "conflict_resolution"]
    assert len(conflict_drops) >= 2


def test_density_control_limits_per_sentence():
    """Density control limits per-sentence annotations."""
    span0 = _make_span("s1", 0, 5, "word0")
    span1 = _make_span("s1", 10, 15, "word1")
    span2 = _make_span("s1", 20, 25, "word2")
    span3 = _make_span("s1", 30, 35, "word3")

    annotations = [
        _make_vocab("s1", span0),
        _make_vocab("s1", span1),
        _make_vocab("s1", span2),
        _make_vocab("s1", span3),
    ]

    drop_log: list[DropLogEntry] = []
    result = postprocess_normalized_annotations(annotations, drop_log, annotation_density=2)

    assert len(result) == 2
    density_drops = [e for e in drop_log if e.drop_stage == "density_control"]
    assert len(density_drops) == 2


def test_build_canonical_stats_returns_correct_structure():
    """build_canonical_stats returns correct structure."""
    span_a = _make_span("s1", 0, 5, "wordA")
    span_b = _make_span("s1", 10, 15, "wordB")
    annotations = [
        _make_vocab("s1", span_a),
        _make_vocab("s1", span_b),
    ]

    drop_log = [
        DropLogEntry(
            source_agent="vocabulary",
            annotation_type="vocab_highlight",
            sentence_id="s1",
            anchor_text="wordC",
            drop_reason="duplicate",
            drop_stage="deduplication",
        ),
    ]

    stats = build_canonical_stats(annotations, drop_log)

    assert "canonical_normalized_counts" in stats
    assert "canonical_drop_counts_by_type" in stats
    assert "canonical_drop_counts_by_reason" in stats
    assert "canonical_span_count" in stats
    assert "canonical_anchor_drop_summary" in stats
    assert stats["canonical_normalized_counts"]["vocab_highlight"] == 2
    assert stats["canonical_span_count"] == 2


def test_grammar_notes_not_affected_by_vocab_conflict_resolution():
    """Grammar annotations not affected by vocab conflict resolution."""
    span = _make_span("s1", 0, 10, "the word")
    g1 = _make_grammar("s1", span, "present_perfect")
    g2 = _make_grammar("s1", span, "subjunctive")

    drop_log: list[DropLogEntry] = []
    result = postprocess_normalized_annotations([g1, g2], drop_log, annotation_density=3)

    assert len(result) == 2
    grammar_results = [a for a in result if a.type == "grammar_note"]
    assert len(grammar_results) == 2
