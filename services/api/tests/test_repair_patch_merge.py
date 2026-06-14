"""Tests for repair patch merge helper."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.schemas.internal.analysis import PreparedSentence, TextSpan
from app.schemas.internal.drafts import DraftVocabHighlight
from app.schemas.internal.normalized import (
    CanonicalSpan,
    NormalizedAnnotationResult,
    NormalizedContextGloss,
    NormalizedVocabHighlight,
)
from app.schemas.internal.repair import (
    RepairPatch,
    RepairPatchRequest,
    RepairPatchResult,
    RepairTarget,
)
from app.services.analysis.postprocess.repair_items import (
    RepairMergeResult,
    RepairMergeStats,
    apply_repair_patches_to_normalized_result,
)

# ── Helpers ────────────────────────────────────────────────────────


def _make_sentence(sid: str, text: str, start: int = 0) -> PreparedSentence:
    return PreparedSentence(
        sentence_id=sid,
        paragraph_id="p1",
        text=text,
        sentence_span=TextSpan(start=start, end=start + len(text)),
    )


def _make_base_result(**overrides) -> NormalizedAnnotationResult:
    defaults = dict(
        annotations=[],
        normalized_annotations=[],
        sentence_translations=[],
        drop_log=[],
        canonical_drop_log=[],
        canonical_stats=None,
    )
    defaults.update(overrides)
    return NormalizedAnnotationResult.model_construct(**defaults)


# ── Tests ──────────────────────────────────────────────────────────


class TestApplyRepairPatches:
    """Tests for apply_repair_patches_to_normalized_result."""

    def test_successful_patch_merged_into_normalized_annotations(self):
        """A replace patch with a groundable draft adds one annotation."""
        sentence = _make_sentence("s1", "The example is here.")
        base = _make_base_result(normalized_annotations=[])

        draft = DraftVocabHighlight(sentence_id="s1", text="example")
        patch = RepairPatch(
            target_index=0,
            action="replace",
            annotation=draft,
            repair_reason="retry",
        )
        patch_result = RepairPatchResult(patches=[patch])

        target = RepairTarget(
            source_agent="vocabulary",
            annotation_type="vocab_highlight",
            sentence_id="s1",
            anchor_text="example",
            drop_reason="quote_not_found",
            drop_stage="grounding",
            is_canonical=True,
            draft_payload=None,
        )
        patch_request = RepairPatchRequest(
            sentences=[{"sentence_id": "s1", "text": "The example is here."}],
            targets=[target],
        )

        merge = apply_repair_patches_to_normalized_result(
            base, patch_result, patch_request, [sentence],
        )

        assert isinstance(merge, RepairMergeResult)
        result = merge.result
        assert len(result.normalized_annotations) == 1
        ann = result.normalized_annotations[0]
        assert isinstance(ann, NormalizedVocabHighlight)
        assert ann.sentence_id == "s1"
        assert ann.spans[0].text == "example"

    def test_failed_patch_not_merged_drop_preserved(self):
        """A replace patch whose draft cannot be grounded produces no annotation
        but appends a drop entry to canonical_drop_log."""
        sentence = _make_sentence("s1", "The cat sat.")
        base = _make_base_result(normalized_annotations=[], canonical_drop_log=[])

        draft = DraftVocabHighlight(sentence_id="s1", text="nonexistent")
        patch = RepairPatch(
            target_index=0,
            action="replace",
            annotation=draft,
            repair_reason="retry",
        )
        patch_result = RepairPatchResult(patches=[patch])

        target = RepairTarget(
            source_agent="vocabulary",
            annotation_type="vocab_highlight",
            sentence_id="s1",
            anchor_text="nonexistent",
            drop_reason="quote_not_found",
            drop_stage="grounding",
            is_canonical=True,
            draft_payload=None,
        )
        patch_request = RepairPatchRequest(
            sentences=[{"sentence_id": "s1", "text": "The cat sat."}],
            targets=[target],
        )

        merge = apply_repair_patches_to_normalized_result(
            base, patch_result, patch_request, [sentence],
        )

        result = merge.result
        assert len(result.normalized_annotations) == 0
        assert len(result.canonical_drop_log) >= 1
        assert result.canonical_drop_log[-1].drop_reason == "quote_not_found"

    def test_existing_annotations_not_changed(self):
        """Existing annotations survive a merge that adds a new one."""
        existing = NormalizedVocabHighlight(
            sentence_id="s1",
            spans=[CanonicalSpan(
                sentence_id="s1",
                start=4,
                end=11,
                text="example",
                resolution_kind="exact",
            )],
        )
        sentence_s1 = _make_sentence("s1", "The example is here.")
        sentence_s2 = _make_sentence("s2", "Another word here.", start=22)
        base = _make_base_result(normalized_annotations=[existing])

        draft = DraftVocabHighlight(sentence_id="s2", text="word")
        patch = RepairPatch(
            target_index=0,
            action="replace",
            annotation=draft,
            repair_reason="retry",
        )
        patch_result = RepairPatchResult(patches=[patch])

        target = RepairTarget(
            source_agent="vocabulary",
            annotation_type="vocab_highlight",
            sentence_id="s2",
            anchor_text="word",
            drop_reason="quote_not_found",
            drop_stage="grounding",
            is_canonical=True,
            draft_payload=None,
        )
        patch_request = RepairPatchRequest(
            sentences=[{"sentence_id": "s2", "text": "Another word here."}],
            targets=[target],
        )

        merge = apply_repair_patches_to_normalized_result(
            base, patch_result, patch_request, [sentence_s1, sentence_s2],
        )

        result = merge.result
        assert len(result.normalized_annotations) == 2
        # Original annotation preserved
        assert result.normalized_annotations[0] == existing
        # New annotation added
        new_ann = result.normalized_annotations[1]
        assert isinstance(new_ann, NormalizedVocabHighlight)
        assert new_ann.sentence_id == "s2"
        assert new_ann.spans[0].text == "word"

    def test_duplicate_does_not_create_duplicate_highlights(self):
        """A patch that produces the same dedup key as an existing annotation
        does not create a duplicate."""
        existing = NormalizedVocabHighlight(
            sentence_id="s1",
            spans=[CanonicalSpan(
                sentence_id="s1",
                start=4,
                end=11,
                text="example",
                resolution_kind="exact",
            )],
        )
        sentence = _make_sentence("s1", "The example is here.")
        base = _make_base_result(normalized_annotations=[existing])

        draft = DraftVocabHighlight(sentence_id="s1", text="example")
        repair_patch = RepairPatch(
            target_index=0,
            action="replace",
            annotation=draft,
            repair_reason="retry",
        )
        patch_result = RepairPatchResult(patches=[repair_patch])

        target = RepairTarget(
            source_agent="vocabulary",
            annotation_type="vocab_highlight",
            sentence_id="s1",
            anchor_text="example",
            drop_reason="quote_not_found",
            drop_stage="grounding",
            is_canonical=True,
            draft_payload=None,
        )
        patch_request = RepairPatchRequest(
            sentences=[{"sentence_id": "s1", "text": "The example is here."}],
            targets=[target],
        )

        # Mock draft_to_normalized_annotation to return an annotation with
        # the same dedup key as the existing one.
        mock_annotation = NormalizedVocabHighlight(
            sentence_id="s1",
            spans=[CanonicalSpan(
                sentence_id="s1",
                start=4,
                end=11,
                text="example",
                resolution_kind="exact",
            )],
        )

        with patch(
            "app.services.analysis.postprocess.repair_items.draft_to_normalized_annotation",
            return_value=mock_annotation,
        ):
            merge = apply_repair_patches_to_normalized_result(
                base, patch_result, patch_request, [sentence],
            )

        assert len(merge.result.normalized_annotations) == 1

    def test_out_of_range_target_index_skipped(self):
        """Out-of-range target_index is logged and skipped, not crashed."""
        sentence = _make_sentence("s1", "The example is here.")
        base = _make_base_result(normalized_annotations=[])

        draft = DraftVocabHighlight(sentence_id="s1", text="example")
        # target_index=99 is out of range (only 1 target)
        patch = RepairPatch(
            target_index=99,
            action="replace",
            annotation=draft,
            repair_reason="retry",
        )
        patch_result = RepairPatchResult(patches=[patch])

        target = RepairTarget(
            source_agent="vocabulary",
            annotation_type="vocab_highlight",
            sentence_id="s1",
            anchor_text="example",
            drop_reason="quote_not_found",
            drop_stage="grounding",
            is_canonical=True,
            draft_payload=None,
        )
        patch_request = RepairPatchRequest(
            sentences=[{"sentence_id": "s1", "text": "The example is here."}],
            targets=[target],
        )

        merge = apply_repair_patches_to_normalized_result(
            base, patch_result, patch_request, [sentence],
        )

        result = merge.result
        # No annotation added, but no crash either
        assert len(result.normalized_annotations) == 0
        # Drop log records the invalid index
        assert any(
            d.drop_reason == "repair_invalid_target_index"
            for d in result.canonical_drop_log
        )

    def test_same_word_different_offset_not_deduped(self):
        """Same word at different offsets in the same sentence are distinct."""
        # "the" appears twice in "the cat and the dog"
        existing = NormalizedVocabHighlight(
            sentence_id="s1",
            spans=[CanonicalSpan(
                sentence_id="s1",
                start=0,
                end=3,
                text="the",
                resolution_kind="exact",
            )],
        )
        sentence = _make_sentence("s1", "the cat and the dog")
        base = _make_base_result(normalized_annotations=[existing])

        draft = DraftVocabHighlight(sentence_id="s1", text="the")
        repair_patch = RepairPatch(
            target_index=0,
            action="replace",
            annotation=draft,
            repair_reason="retry",
        )
        patch_result = RepairPatchResult(patches=[repair_patch])

        target = RepairTarget(
            source_agent="vocabulary",
            annotation_type="vocab_highlight",
            sentence_id="s1",
            anchor_text="the",
            drop_reason="quote_not_found",
            drop_stage="grounding",
            is_canonical=True,
            draft_payload=None,
        )
        patch_request = RepairPatchRequest(
            sentences=[{"sentence_id": "s1", "text": "the cat and the dog"}],
            targets=[target],
        )

        # Mock to return "the" at the second occurrence (offset 12)
        mock_annotation = NormalizedVocabHighlight(
            sentence_id="s1",
            spans=[CanonicalSpan(
                sentence_id="s1",
                start=12,
                end=15,
                text="the",
                resolution_kind="exact",
            )],
        )

        with patch(
            "app.services.analysis.postprocess.repair_items.draft_to_normalized_annotation",
            return_value=mock_annotation,
        ):
            merge = apply_repair_patches_to_normalized_result(
                base, patch_result, patch_request, [sentence],
            )

        # Both annotations present — different offsets, not deduped
        assert len(merge.result.normalized_annotations) == 2

    def test_repair_patch_removed_by_conflict_resolution(self):
        """A repair patch that conflicts with a higher-priority annotation
        is removed by conflict resolution."""
        # context_gloss has priority 3, vocab_highlight has priority 1
        existing = NormalizedContextGloss(
            sentence_id="s1",
            spans=[CanonicalSpan(
                sentence_id="s1",
                start=0,
                end=10,
                text="the exampl",
                resolution_kind="exact",
            )],
            display="test display",
            gloss="test gloss",
            reason="test reason",
        )
        sentence = _make_sentence("s1", "the example is here")
        base = _make_base_result(normalized_annotations=[existing])

        draft = DraftVocabHighlight(sentence_id="s1", text="example")
        repair_patch = RepairPatch(
            target_index=0,
            action="replace",
            annotation=draft,
            repair_reason="retry",
        )
        patch_result = RepairPatchResult(patches=[repair_patch])

        target = RepairTarget(
            source_agent="vocabulary",
            annotation_type="vocab_highlight",
            sentence_id="s1",
            anchor_text="example",
            drop_reason="quote_not_found",
            drop_stage="grounding",
            is_canonical=True,
            draft_payload=None,
        )
        patch_request = RepairPatchRequest(
            sentences=[{"sentence_id": "s1", "text": "the example is here"}],
            targets=[target],
        )

        # Mock to return vocab_highlight at overlapping span (0,10) — lower priority
        mock_annotation = NormalizedVocabHighlight(
            sentence_id="s1",
            spans=[CanonicalSpan(
                sentence_id="s1",
                start=0,
                end=10,
                text="the exampl",
                resolution_kind="exact",
            )],
        )

        with patch(
            "app.services.analysis.postprocess.repair_items.draft_to_normalized_annotation",
            return_value=mock_annotation,
        ):
            merge = apply_repair_patches_to_normalized_result(
                base, patch_result, patch_request, [sentence],
                annotation_density=3,
            )

        result = merge.result
        # Only the context_gloss survives, vocab_highlight conflict-resolved away
        assert len(result.normalized_annotations) == 1
        assert isinstance(result.normalized_annotations[0], NormalizedContextGloss)
        assert any(
            d.drop_reason == "conflict_resolution"
            for d in result.canonical_drop_log
        )

    def test_repair_patch_removed_by_density_control(self):
        """A repair patch that exceeds annotation_density is removed by density control."""
        existing_1 = NormalizedVocabHighlight(
            sentence_id="s1",
            spans=[CanonicalSpan(
                sentence_id="s1",
                start=0,
                end=5,
                text="hello",
                resolution_kind="exact",
            )],
        )
        existing_2 = NormalizedVocabHighlight(
            sentence_id="s1",
            spans=[CanonicalSpan(
                sentence_id="s1",
                start=10,
                end=15,
                text="world",
                resolution_kind="exact",
            )],
        )
        sentence = _make_sentence("s1", "hello world test")
        base = _make_base_result(normalized_annotations=[existing_1, existing_2])

        draft = DraftVocabHighlight(sentence_id="s1", text="test")
        repair_patch = RepairPatch(
            target_index=0,
            action="replace",
            annotation=draft,
            repair_reason="retry",
        )
        patch_result = RepairPatchResult(patches=[repair_patch])

        target = RepairTarget(
            source_agent="vocabulary",
            annotation_type="vocab_highlight",
            sentence_id="s1",
            anchor_text="test",
            drop_reason="quote_not_found",
            drop_stage="grounding",
            is_canonical=True,
            draft_payload=None,
        )
        patch_request = RepairPatchRequest(
            sentences=[{"sentence_id": "s1", "text": "hello world test"}],
            targets=[target],
        )

        # Mock to return a 3rd vocab highlight at (20,25)
        mock_annotation = NormalizedVocabHighlight(
            sentence_id="s1",
            spans=[CanonicalSpan(
                sentence_id="s1",
                start=20,
                end=25,
                text="test",
                resolution_kind="exact",
            )],
        )

        with patch(
            "app.services.analysis.postprocess.repair_items.draft_to_normalized_annotation",
            return_value=mock_annotation,
        ):
            merge = apply_repair_patches_to_normalized_result(
                base, patch_result, patch_request, [sentence],
                annotation_density=2,
            )

        result = merge.result
        # Only 2 annotations survive; the 3rd was density-controlled
        assert len(result.normalized_annotations) == 2
        assert any(
            d.drop_stage == "density_control"
            for d in result.canonical_drop_log
        )

    def test_canonical_stats_recalculated_after_merge(self):
        """canonical_stats is recalculated after merge, not left as None."""
        existing = NormalizedVocabHighlight(
            sentence_id="s1",
            spans=[CanonicalSpan(
                sentence_id="s1",
                start=4,
                end=11,
                text="example",
                resolution_kind="exact",
            )],
        )
        sentence = _make_sentence("s1", "The example is here.")
        base = _make_base_result(
            normalized_annotations=[existing],
            canonical_stats=None,
        )

        draft = DraftVocabHighlight(sentence_id="s1", text="here")
        repair_patch = RepairPatch(
            target_index=0,
            action="replace",
            annotation=draft,
            repair_reason="retry",
        )
        patch_result = RepairPatchResult(patches=[repair_patch])

        target = RepairTarget(
            source_agent="vocabulary",
            annotation_type="vocab_highlight",
            sentence_id="s1",
            anchor_text="here",
            drop_reason="quote_not_found",
            drop_stage="grounding",
            is_canonical=True,
            draft_payload=None,
        )
        patch_request = RepairPatchRequest(
            sentences=[{"sentence_id": "s1", "text": "The example is here."}],
            targets=[target],
        )

        merge = apply_repair_patches_to_normalized_result(
            base, patch_result, patch_request, [sentence],
        )

        result = merge.result
        assert result.canonical_stats is not None
        # Stats should reflect the updated annotation count
        counts = result.canonical_stats.get(
            "canonical_normalized_counts", {},
        )
        assert counts.get("vocab_highlight", 0) >= 1

    def test_repair_merge_stats_fields_populated_correctly(self):
        """RepairMergeStats fields reflect the merge outcome."""
        sentence = _make_sentence("s1", "The example is here.")
        base = _make_base_result(normalized_annotations=[])

        # One replace patch
        replace_draft = DraftVocabHighlight(sentence_id="s1", text="example")
        replace_patch = RepairPatch(
            target_index=0,
            action="replace",
            annotation=replace_draft,
            repair_reason="retry",
        )
        # One delete patch
        delete_patch = RepairPatch(
            target_index=1,
            action="delete",
            annotation=None,
            repair_reason="unfixable",
        )
        patch_result = RepairPatchResult(patches=[replace_patch, delete_patch])

        target_0 = RepairTarget(
            source_agent="vocabulary",
            annotation_type="vocab_highlight",
            sentence_id="s1",
            anchor_text="example",
            drop_reason="quote_not_found",
            drop_stage="grounding",
            is_canonical=True,
            draft_payload=None,
        )
        target_1 = RepairTarget(
            source_agent="vocabulary",
            annotation_type="vocab_highlight",
            sentence_id="s1",
            anchor_text="other",
            drop_reason="quote_not_found",
            drop_stage="grounding",
            is_canonical=False,
            draft_payload=None,
        )
        patch_request = RepairPatchRequest(
            sentences=[{"sentence_id": "s1", "text": "The example is here."}],
            targets=[target_0, target_1],
        )

        merge = apply_repair_patches_to_normalized_result(
            base, patch_result, patch_request, [sentence],
        )

        stats = merge.stats
        assert isinstance(stats, RepairMergeStats)
        assert stats.patched_count == 1
        assert stats.delete_count == 1
        assert stats.invalid_patch_count == 0


class TestRepairPatchValidator:
    """Tests for RepairPatch model_validator."""

    def test_replace_without_annotation_raises(self):
        with pytest.raises(ValueError, match="replace.*annotation"):
            RepairPatch(
                target_index=0,
                action="replace",
                annotation=None,
                repair_reason="retry",
            )

    def test_delete_with_annotation_raises(self):
        with pytest.raises(ValueError, match="delete.*None"):
            RepairPatch(
                target_index=0,
                action="delete",
                annotation=DraftVocabHighlight(
                    sentence_id="s1", text="word",
                ),
                repair_reason="unfixable",
            )

    def test_replace_with_annotation_valid(self):
        patch = RepairPatch(
            target_index=0,
            action="replace",
            annotation=DraftVocabHighlight(
                sentence_id="s1", text="word",
            ),
            repair_reason="retry",
        )
        assert patch.action == "replace"
        assert patch.annotation is not None

    def test_delete_without_annotation_valid(self):
        patch = RepairPatch(
            target_index=0,
            action="delete",
            annotation=None,
            repair_reason="unfixable",
        )
        assert patch.action == "delete"
        assert patch.annotation is None
