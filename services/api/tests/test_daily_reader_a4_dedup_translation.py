"""A-4: highlight cross-paragraph dedup + translation single source of truth.

Seams:
- ``_normalize_highlight_key`` / ``_dedupe_highlights`` (projection after reconcile)
- takeaways prompt assembly (full paragraph translations, no 1500-char cap)
- ``_snap_sentence_translations`` (SentenceNote.translation ⊆ paragraph translation)
"""

from __future__ import annotations

from app.agents.daily_interpretation_agent import (
    DailyInterpretationAgentDeps,
    build_daily_interpretation_prompt,
)
from app.agents.daily_vocab_agent import DailyVocabAgentDeps, build_daily_vocab_prompt
from app.schemas.internal.daily_drafts import SentenceNote
from app.services.daily_reader.workflow import (
    _dedupe_highlights,
    _normalize_highlight_key,
    _paragraph_translations_context,
    _snap_sentence_translations,
    daily_projection_node,
)
from app.services.prompting.prompt_loader import load_agent_instructions

# ---------------------------------------------------------------------------
# Highlight key normalization
# ---------------------------------------------------------------------------


class TestNormalizeHighlightKey:
    def test_manifesto_plural_and_case(self):
        assert _normalize_highlight_key("manifesto") == _normalize_highlight_key("Manifestos")
        assert _normalize_highlight_key("MANIFESTO") == _normalize_highlight_key("manifestos")

    def test_initiate_ing_and_ed(self):
        assert _normalize_highlight_key("initiate") == _normalize_highlight_key("initiating")
        assert _normalize_highlight_key("initiate") == _normalize_highlight_key("initiated")

    def test_whitespace_and_mixed_case_phrase(self):
        assert _normalize_highlight_key("  Push   BACK ") == _normalize_highlight_key("push back")

    def test_ss_ending_not_stripped_to_cl(self):
        # "class" must not collapse into "clas" via naive -s strip.
        assert _normalize_highlight_key("class") == "class"
        assert _normalize_highlight_key("classes") == _normalize_highlight_key("class")

    def test_distinct_lemmas_stay_distinct(self):
        assert _normalize_highlight_key("volunteer") != _normalize_highlight_key("deliver")


class TestDedupeHighlights:
    def test_keeps_first_across_paragraphs(self):
        highlights = [
            {"id": "a", "text": "manifesto", "paragraph_id": "p_0", "start": 0, "end": 9},
            {"id": "b", "text": "Manifestos", "paragraph_id": "p_3", "start": 0, "end": 10},
            {"id": "c", "text": "initiate", "paragraph_id": "p_1", "start": 0, "end": 8},
            {"id": "d", "text": "initiating", "paragraph_id": "p_4", "start": 0, "end": 10},
        ]
        kept = _dedupe_highlights(highlights)
        assert [h["id"] for h in kept] == ["a", "c"]

    def test_empty_and_blank_text_passthrough(self):
        assert _dedupe_highlights([]) == []
        kept = _dedupe_highlights([{"id": "z", "text": "", "paragraph_id": "p_0"}])
        assert [h["id"] for h in kept] == ["z"]


class TestProjectionAppliesDedup:
    def test_projection_drops_later_stem_duplicates(self):
        paragraphs = [
            {"paragraph_id": "p_0", "text": "The manifesto changed everything."},
            {"paragraph_id": "p_1", "text": "Later Manifestos flooded the market."},
        ]
        highlights = [
            {"id": "hl0", "text": "manifesto", "paragraph_id": "p_0", "start": 4, "end": 13},
            {"id": "hl1", "text": "Manifestos", "paragraph_id": "p_1", "start": 6, "end": 16},
        ]
        result = daily_projection_node(
            {
                "normalized_paragraphs": paragraphs,
                "highlights_json": highlights,
                "paragraph_notes_json": {"notes": []},
                "takeaways_json": {},
            }
        )
        texts = [h["text"] for h in result["highlights_json"]]
        assert texts == ["manifesto"]
        body_p1 = result["body_json"]["paragraphs"][1]
        assert body_p1["highlights"] == []


# ---------------------------------------------------------------------------
# Takeaways input: full paragraph translations
# ---------------------------------------------------------------------------


class TestParagraphTranslationsContext:
    def test_includes_full_translation_past_1500_chars(self):
        long_tr = "段译" * 800  # 1600 chars
        notes = {
            "notes": [
                {"paragraph_id": "p_0", "translation": long_tr},
                {"paragraph_id": "p_1", "translation": "第二段译文。"},
            ]
        }
        ctx = _paragraph_translations_context(notes)
        assert long_tr in ctx
        assert "p_0:" in ctx
        assert "p_1: 第二段译文。" in ctx
        assert len(ctx) > 1500

    def test_prompt_builder_does_not_truncate_translations(self):
        long_tr = "甲" * 1800
        ctx = _paragraph_translations_context(
            {"notes": [{"paragraph_id": "p_0", "translation": long_tr}]}
        )
        prompt = build_daily_interpretation_prompt(
            DailyInterpretationAgentDeps(
                full_text="English paragraph.",
                title="T",
                paragraph_translations=ctx,
            )
        )
        assert long_tr in prompt
        assert "<paragraph_translations>" in prompt or "p_0:" in prompt


# ---------------------------------------------------------------------------
# SentenceNote.translation snapped to paragraph translation
# ---------------------------------------------------------------------------


class TestSnapSentenceTranslations:
    def test_retranslation_replaced_with_paragraph_span(self):
        takeaways = {
            "sentence_notes": [
                {
                    "sentence": "Charlie is the last sentence.",
                    "paragraph_id": "p_0",
                    "translation": "查理是最后那句话。",
                    "breakdown": "b",
                    "takeaway": "t",
                }
            ]
        }
        paragraphs = [
            {
                "paragraph_id": "p_0",
                "text": (
                    "Alpha is the first sentence. "
                    "Bravo is the second sentence which is longer than the first. "
                    "Charlie is the last sentence."
                ),
            }
        ]
        notes = {
            "notes": [
                {
                    "paragraph_id": "p_0",
                    "translation": "甲是第一句。乙是更长的第二句。丙是最后一句。",
                }
            ]
        }
        snapped = _snap_sentence_translations(takeaways, paragraphs, notes)
        got = snapped["sentence_notes"][0]["translation"]
        para_tr = notes["notes"][0]["translation"]
        assert got in para_tr
        assert "丙是最后一句。" in got

    def test_already_contained_translation_kept(self):
        takeaways = {
            "sentence_notes": [
                {
                    "sentence": "Charlie is the last sentence.",
                    "paragraph_id": "p_0",
                    "translation": "丙是最后一句。",
                    "breakdown": "b",
                    "takeaway": "t",
                }
            ]
        }
        paragraphs = [
            {
                "paragraph_id": "p_0",
                "text": "Alpha. Charlie is the last sentence.",
            }
        ]
        notes = {
            "notes": [
                {"paragraph_id": "p_0", "translation": "甲。丙是最后一句。"},
            ]
        }
        snapped = _snap_sentence_translations(takeaways, paragraphs, notes)
        assert snapped["sentence_notes"][0]["translation"] == "丙是最后一句。"

    def test_drops_sentence_note_when_paragraph_has_no_translation(self):
        takeaways = {
            "sentence_notes": [
                {
                    "sentence": "Keep this sentence.",
                    "paragraph_id": "p_0",
                    "translation": "保留这句。",
                    "breakdown": "b",
                    "takeaway": "t",
                },
                {
                    "sentence": "Orphan sentence with no paragraph translation.",
                    "paragraph_id": "p_9",
                    "translation": "这段没有段译。",
                    "breakdown": "b",
                    "takeaway": "t",
                },
            ]
        }
        paragraphs = [
            {"paragraph_id": "p_0", "text": "Keep this sentence. More text."},
            {"paragraph_id": "p_9", "text": "Orphan sentence with no paragraph translation."},
        ]
        notes = {
            "notes": [
                {"paragraph_id": "p_0", "translation": "保留这句。更多文字。"},
            ]
        }
        snapped = _snap_sentence_translations(takeaways, paragraphs, notes)
        assert [n["paragraph_id"] for n in snapped["sentence_notes"]] == ["p_0"]
        assert snapped["sentence_notes"][0]["translation"] in notes["notes"][0]["translation"]

    def test_projection_snaps_takeaways(self):
        paragraphs = [
            {
                "paragraph_id": "p_0",
                "text": "Alpha is first. Charlie is the last sentence.",
            }
        ]
        notes = {
            "notes": [
                {"paragraph_id": "p_0", "translation": "甲是第一句。丙是最后一句。"},
            ]
        }
        takeaways = {
            "sentence_notes": [
                {
                    "sentence": "Charlie is the last sentence.",
                    "paragraph_id": "p_0",
                    "translation": "这句被重新翻译了。",
                    "breakdown": "b",
                    "takeaway": "t",
                }
            ]
        }
        result = daily_projection_node(
            {
                "normalized_paragraphs": paragraphs,
                "highlights_json": [],
                "paragraph_notes_json": notes,
                "takeaways_json": takeaways,
            }
        )
        got = result["takeaways_json"]["sentence_notes"][0]["translation"]
        assert got in notes["notes"][0]["translation"]
        assert "重新翻译" not in got


# ---------------------------------------------------------------------------
# Prompt / schema leftovers
# ---------------------------------------------------------------------------


class TestVocabPromptLeftovers:
    def test_yaml_has_no_unformatted_batch_placeholders(self):
        text = load_agent_instructions("daily_vocab")
        assert "{batch_index}" not in text
        assert "{total_batches}" not in text

    def test_yaml_asks_no_cross_batch_repeat(self):
        text = load_agent_instructions("daily_vocab")
        assert "跨批不重复标注同一表达" in text

    def test_batch_info_still_formatted_in_user_prompt(self):
        prompt = build_daily_vocab_prompt(
            DailyVocabAgentDeps(
                paragraphs=[{"paragraph_id": "p_0", "text": "hello"}],
                batch_index=1,
                total_batches=3,
            )
        )
        assert "第 2 批（共 3 批）" in prompt


class TestSentenceNoteSchema:
    def test_translation_field_requires_verbatim_span(self):
        desc = SentenceNote.model_fields["translation"].description or ""
        assert "段译" in desc or "paragraph" in desc.lower()
        assert "重新翻译" in desc or "retranslat" in desc.lower()
