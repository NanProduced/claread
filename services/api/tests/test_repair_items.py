"""Tests for repair_items candidate builder."""
from __future__ import annotations

from app.schemas.common import TextSpan
from app.schemas.internal.analysis import PreparedSentence
from app.schemas.internal.drafts import (
    AnchorQuote,
    DraftGrammarNote,
    DraftVocabHighlight,
    GrammarDraft,
    VocabularyDraft,
)
from app.schemas.internal.normalized import DropLogEntry
from app.services.analysis.postprocess.repair_items import (
    build_repair_patch_request,
    build_repair_patch_request_with_stats,
)

# ── Helpers ────────────────────────────────────────────────────────


def _make_sentence(sid: str, text: str) -> PreparedSentence:
    return PreparedSentence(
        sentence_id=sid,
        paragraph_id="p1",
        text=text,
        sentence_span=TextSpan(start=0, end=len(text)),
    )


def _make_drop(
    source_agent: str = "vocabulary",
    annotation_type: str = "vocab_highlight",
    sentence_id: str = "s1",
    anchor_text: str = "word",
    drop_reason: str = "quote_not_found",
    drop_stage: str = "grounding",
) -> DropLogEntry:
    return DropLogEntry(
        source_agent=source_agent,
        annotation_type=annotation_type,
        sentence_id=sentence_id,
        anchor_text=anchor_text,
        drop_reason=drop_reason,
        drop_stage=drop_stage,
    )


# ── Tests ──────────────────────────────────────────────────────────


class TestBuildRepairPatchRequest:
    """Tests for build_repair_patch_request()."""

    def test_only_repair_worthy_drops_selected(self) -> None:
        """Only repair-worthy drops become targets."""
        drops = [
            _make_drop(sentence_id="s1", anchor_text="w1", drop_reason="quote_not_found"),
            _make_drop(sentence_id="s2", anchor_text="w2", drop_reason="duplicate"),
            _make_drop(sentence_id="s3", anchor_text="w3", drop_reason="anchor_invalid"),
        ]
        sentences = [
            _make_sentence("s1", "Hello"),
            _make_sentence("s2", "world"),
            _make_sentence("s3", "test"),
        ]
        result = build_repair_patch_request(drops, sentences)
        assert result is not None
        assert len(result.targets) == 2
        assert {t.anchor_text for t in result.targets} == {"w1", "w3"}

    def test_deterministic_drops_excluded(self) -> None:
        """Deterministic drops (density_control / low_value_word) yield None."""
        drops = [
            _make_drop(drop_reason="low_value_word", drop_stage="density_control"),
        ]
        sentences = [_make_sentence("s1", "Hello")]
        result = build_repair_patch_request(drops, sentences)
        assert result is None

    def test_canonical_drop_log_entries_included(self) -> None:
        """Entries from canonical_drop_log appear with is_canonical=True."""
        canonical = [
            _make_drop(
                sentence_id="s1",
                anchor_text="cw1",
                drop_reason="quote_not_found",
                drop_stage="grounding",
            ),
        ]
        sentences = [_make_sentence("s1", "Hello")]
        result = build_repair_patch_request(
            [],
            sentences,
            canonical_drop_log=canonical,
        )
        assert result is not None
        assert len(result.targets) == 1
        assert result.targets[0].is_canonical is True

    def test_only_relevant_sentences_included(self) -> None:
        """Only sentences referenced by drops appear in result.sentences."""
        sentences = [
            _make_sentence("s1", "Hello"),
            _make_sentence("s2", "world"),
            _make_sentence("s3", "test"),
        ]
        drops = [
            _make_drop(sentence_id="s1", anchor_text="w1"),
            _make_drop(sentence_id="s3", anchor_text="w3"),
        ]
        result = build_repair_patch_request(drops, sentences)
        assert result is not None
        assert len(result.sentences) == 2
        assert {s["sentence_id"] for s in result.sentences} == {"s1", "s3"}

    def test_targets_capped_at_max_targets(self) -> None:
        """Targets are truncated to max_targets."""
        drops = [
            _make_drop(sentence_id=f"s{i}", anchor_text=f"w{i}")
            for i in range(10)
        ]
        sentences = [_make_sentence(f"s{i}", f"text{i}") for i in range(10)]
        result = build_repair_patch_request(drops, sentences, max_targets=3)
        assert result is not None
        assert len(result.targets) == 3

    def test_draft_payload_matched_from_vocabulary_draft(self) -> None:
        """Vocabulary draft items are matched into draft_payload."""
        drops = [
            _make_drop(
                source_agent="vocabulary",
                annotation_type="vocab_highlight",
                sentence_id="s1",
                anchor_text="example",
            ),
        ]
        vocab_draft = VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="example"),
            ],
        )
        sentences = [_make_sentence("s1", "This is an example sentence")]
        result = build_repair_patch_request(drops, sentences, vocabulary_draft=vocab_draft)
        assert result is not None
        target = result.targets[0]
        assert target.draft_payload is not None
        assert target.draft_payload["type"] == "vocab_highlight"
        assert target.draft_payload["text"] == "example"

    def test_draft_payload_matched_from_grammar_draft(self) -> None:
        """Grammar draft items are matched into draft_payload."""
        drops = [
            _make_drop(
                source_agent="grammar",
                annotation_type="grammar_note",
                sentence_id="s1",
                anchor_text="example phrase",
            ),
        ]
        grammar_draft = GrammarDraft(
            grammar_notes=[
                DraftGrammarNote(
                    sentence_id="s1",
                    anchor_quotes=[AnchorQuote(text="example phrase")],
                    grammar_point="test",
                    pattern="test",
                    note_zh="测试",
                ),
            ],
        )
        sentences = [_make_sentence("s1", "This is an example phrase here")]
        result = build_repair_patch_request(drops, sentences, grammar_draft=grammar_draft)
        assert result is not None
        target = result.targets[0]
        assert target.draft_payload is not None
        assert target.draft_payload["type"] == "grammar_note"
        assert target.draft_payload["grammar_point"] == "test"

    def test_returns_none_when_no_repair_worthy_drops(self) -> None:
        """Empty drop_log and no canonical_drop_log returns None."""
        drops: list[DropLogEntry] = []
        sentences = [_make_sentence("s1", "Hello")]
        result = build_repair_patch_request(drops, sentences)
        assert result is None

    def test_missing_sentence_does_not_starve_valid_targets(self) -> None:
        """Missing sentence targets are filtered before max_targets, so valid
        targets after them are not starved."""
        drops = [
            # First 8 drops all reference missing sentences
            *[
                _make_drop(
                    sentence_id=f"s_missing_{i}",
                    anchor_text=f"orphan_{i}",
                    drop_reason="quote_not_found",
                    drop_stage="grounding",
                )
                for i in range(8)
            ],
            # 9th drop references a valid sentence
            _make_drop(
                sentence_id="s1",
                anchor_text="valid",
                drop_reason="quote_not_found",
                drop_stage="grounding",
            ),
        ]
        sentences = [_make_sentence("s1", "This is valid")]
        result = build_repair_patch_request(drops, sentences, max_targets=8)
        assert result is not None
        assert len(result.targets) == 1
        assert result.targets[0].sentence_id == "s1"

    def test_build_with_stats_observable(self) -> None:
        """build_repair_patch_request_with_stats returns observable stats."""
        drops = [
            _make_drop(
                sentence_id="s_missing",
                anchor_text="orphan",
                drop_reason="quote_not_found",
                drop_stage="grounding",
            ),
            _make_drop(
                sentence_id="s1",
                anchor_text="valid",
                drop_reason="quote_not_found",
                drop_stage="grounding",
            ),
            # Non-repair-worthy drop
            _make_drop(
                drop_reason="duplicate",
                drop_stage="deduplication",
            ),
        ]
        sentences = [_make_sentence("s1", "This is valid")]
        result = build_repair_patch_request_with_stats(drops, sentences)
        assert result.request is not None
        assert result.stats.repair_worthy_count == 2
        assert result.stats.missing_sentence_count == 1
        assert result.stats.selected_target_count == 1

    def test_missing_sentence_target_is_skipped(self) -> None:
        """A drop whose sentence_id is not in the sentences list is skipped."""
        drops = [
            _make_drop(
                sentence_id="s_missing",
                anchor_text="orphan",
                drop_reason="quote_not_found",
                drop_stage="grounding",
            ),
        ]
        # Sentences do NOT include "s_missing"
        sentences = [_make_sentence("s1", "Hello")]
        result = build_repair_patch_request(drops, sentences)
        assert result is None

    def test_missing_sentence_no_target_stats_observable(self) -> None:
        """When all repair-worthy drops are missing sentences, _with_stats
        returns request=None but stats show the real missing count."""
        drops = [
            _make_drop(
                sentence_id="s_missing_1",
                anchor_text="orphan_1",
                drop_reason="quote_not_found",
                drop_stage="grounding",
            ),
            _make_drop(
                sentence_id="s_missing_2",
                anchor_text="orphan_2",
                drop_reason="anchor_invalid",
                drop_stage="grounding",
            ),
        ]
        # Sentences do NOT include any missing sentence IDs
        sentences = [_make_sentence("s1", "Hello")]
        result = build_repair_patch_request_with_stats(drops, sentences)
        assert result.request is None
        assert result.stats.repair_worthy_count == 2
        assert result.stats.missing_sentence_count == 2
        assert result.stats.selected_target_count == 0
