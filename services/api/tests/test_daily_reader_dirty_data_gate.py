"""T-03 (A-1): Daily Reader dirty-data gate tests.

Covers the four exposed dirty-data forms and their gates:
1. BBC "- Published" / "(Published …)" head residue removal;
2. BBC "external"/"internal" link-badge removal + false-positive boundary;
3. copyright/subscribe/syndication/transcript-disclaimer footer removal;
4. NPR transcript (HOST/BYLINE/SOUNDBITE) detection → rejection with a
   pipeline_meta reason, plus the workflow review boilerplate hard gate.

No DB / network / LLM access.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.daily_reader.extraction import (
    ExtractionResult,
    clean_extracted_article,
    detect_transcript_markers,
    extract_with_trafilatura,
    find_boilerplate_hits,
)
from app.services.daily_reader.workflow import (
    build_daily_reader_graph,
    light_normalize_node,
    quality_review_node,
)

DIRTY_BBC_TEXT = (
    "- Published\n"
    "Dates app Bumble has divided its users by dropping its signature rule.\n"
    "This past week, Meta CEO published a 6,500 word open letter, "
    "external entitled \"The Future\".\n"
    "He wrote about the spread of misinformation on his platforms, external.\n"
    "According to Ofcom's Online Nation, external figures, Bumble's audience fell."
)

DIRTY_FOOTER_TEXT = (
    "The council will review the figures next month.\n"
    "This article originally appeared on BBC News Online.\n"
    "Copyright \u00a9 2026 BBC. All rights reserved. "
    "Subscribe to the BBC Future newsletter for more stories.\n"
    "Accuracy and availability of NPR transcripts may vary. Transcript text may be revised."
)

NPR_TRANSCRIPT_TEXT = (
    "Researchers have successfully used AI to create brand new viruses\n"
    "JUANA SUMMERS, HOST:\n"
    "Here's a new addition to the list of things AI can do.\n"
    "KATIA RIDDLE, BYLINE: Researchers have long been able to engineer certain viruses.\n"
    "BRIAN HIE: The lab spontaneously broke into applause.\n"
    "(SOUNDBITE OF DORIAN CONCEPT'S \"HIDE (RAW)\")\n"
    "Copyright \u00a9 2026 NPR. All rights reserved."
)


class TestPublishedHeadResidue:
    def test_bare_published_line_removed(self):
        cleaned = clean_extracted_article("- Published\nThe app launched in 2014.")
        assert "- Published" not in cleaned
        assert cleaned.startswith("The app launched in 2014.")

    def test_paren_published_line_removed(self):
        cleaned = clean_extracted_article("(Published 12 August 2026)\nThe app launched.")
        assert "Published" not in cleaned
        assert cleaned == "The app launched."

    def test_plain_published_sentence_kept(self):
        text = "Published in 1999, the report remains influential."
        assert clean_extracted_article(text) == text


class TestExternalInternalBadge:
    def test_badge_between_commas_removed(self):
        cleaned = clean_extracted_article("new figures, external, released by the council")
        assert cleaned == "new figures, released by the council"

    def test_badge_before_period_removed(self):
        cleaned = clean_extracted_article("misinformation on his platforms, external.")
        assert cleaned == "misinformation on his platforms."

    def test_badge_before_lowercase_word_removed(self):
        text = "a 6,500 word open letter, external entitled \"The Future\""
        cleaned = clean_extracted_article(text)
        assert cleaned == "a 6,500 word open letter entitled \"The Future\""

    def test_badge_at_paragraph_end_removed(self):
        text = "According to Ofcom's Online Nation, external figures, it fell."
        cleaned = clean_extracted_article(text)
        assert cleaned == "According to Ofcom's Online Nation figures, it fell."

    def test_internal_badge_removed(self):
        cleaned = clean_extracted_article("see the internal memo, internal.")
        assert cleaned == "see the internal memo."

    def test_external_without_comma_kept(self):
        text = "External auditors reviewed the accounts."
        assert clean_extracted_article(text) == text

    def test_external_as_normal_adjective_kept(self):
        text = "The study cited external factors behind the slowdown."
        assert clean_extracted_article(text) == text


class TestBoilerplateFooterLines:
    def test_copyright_line_removed(self):
        cleaned = clean_extracted_article(DIRTY_FOOTER_TEXT)
        assert "Copyright" not in cleaned
        assert "All rights reserved" not in cleaned

    def test_syndication_line_removed(self):
        assert "originally appeared" not in clean_extracted_article(DIRTY_FOOTER_TEXT)

    def test_transcript_disclaimer_line_removed(self):
        assert "Accuracy and availability" not in clean_extracted_article(DIRTY_FOOTER_TEXT)

    def test_subscribe_line_removed(self):
        text = "Good content here.\nSubscribe to our newsletter for more."
        assert clean_extracted_article(text) == "Good content here."

    def test_content_line_kept(self):
        assert clean_extracted_article(DIRTY_FOOTER_TEXT).startswith(
            "The council will review the figures next month."
        )


class TestTranscriptDetection:
    def test_npr_transcript_detected(self):
        markers = detect_transcript_markers(NPR_TRANSCRIPT_TEXT)
        assert markers
        assert any("speaker_cue" in m for m in markers)

    def test_clean_article_not_detected(self):
        assert detect_transcript_markers(DIRTY_BBC_TEXT) == []

    def test_single_cue_alone_not_detected(self):
        text = "Intro paragraph.\nSOMEONE, HOST:\nOnly one cue here."
        assert detect_transcript_markers(text) == []

    def test_single_cue_plus_soundbite_treated_as_cleanable_framing(self):
        text = "SOMEONE, HOST:\nWords.\n(SOUNDBITE OF MUSIC)"
        assert detect_transcript_markers(text) == []

    def test_two_cues_detected_as_transcript(self):
        text = "SOMEONE, HOST:\nWords.\nANOTHER, BYLINE:\nMore words."
        markers = detect_transcript_markers(text)
        assert markers == ["speaker_cue_x2"]

    def test_two_soundbites_detected_as_transcript(self):
        text = "Words.\n(SOUNDBITE OF MUSIC 1)\n(SOUNDBITE OF MUSIC 2)"
        markers = detect_transcript_markers(text)
        assert markers == ["soundbite_x2"]

    def test_transcript_framing_lines_removed_by_clean_extracted_article(self):
        text = (
            "SCOTT SIMON, HOST:\n"
            "This is Europe's summer of heat.\n"
            "(SOUNDBITE OF HERMANOS GUTIERREZ'S \"MESA REDONDA\")\n"
            "Copyright © 2026 NPR. All rights reserved."
        )
        cleaned = clean_extracted_article(text)
        assert "SCOTT SIMON, HOST:" not in cleaned
        assert "(SOUNDBITE OF" not in cleaned
        assert "Copyright" not in cleaned
        assert cleaned == "This is Europe's summer of heat."


class TestLightNormalizeGate:
    def test_transcript_rejected_with_pipeline_meta_reason(self):
        state = {
            "original_text": NPR_TRANSCRIPT_TEXT,
            "title": "AI viruses",
            "pipeline_meta": {"score": 7.0},
        }
        result = light_normalize_node(state)
        assert result["abort"] is True
        assert result["normalized_paragraphs"] == []
        rejection = result["pipeline_meta"]["rejection"]
        assert rejection["code"] == "transcript_rejected"
        assert rejection["markers"]
        assert result["pipeline_meta"]["score"] == 7.0

    def test_dirty_bbc_text_cleaned(self):
        state = {"original_text": DIRTY_BBC_TEXT, "title": "Bumble"}
        result = light_normalize_node(state)
        joined = "\n".join(p["text"] for p in result["normalized_paragraphs"])
        assert "- Published" not in joined
        assert ", external" not in joined
        assert "Online Nation figures" in joined
        assert "open letter entitled" in joined

    def test_europe_heat_framing_not_aborted_in_light_normalize(self):
        state = {
            "original_text": (
                "SCOTT SIMON, HOST:\n"
                "This is Europe's summer of heat. All 27 cities Italy's health ministry monitors "
                "are on red alert this week.\n"
                "(SOUNDBITE OF HERMANOS GUTIERREZ'S \"MESA REDONDA\")\n"
                "Copyright © 2026 NPR. All rights reserved."
            ),
            "title": "Europe's summer of heat",
        }
        result = light_normalize_node(state)
        assert result.get("abort") is not True
        assert len(result.get("normalized_paragraphs", [])) > 0
        joined = "\n".join(p["text"] for p in result["normalized_paragraphs"])
        assert "SCOTT SIMON, HOST:" not in joined
        assert "(SOUNDBITE OF" not in joined
        assert "Copyright" not in joined


class TestGraphTranscriptShortCircuit:
    async def test_graph_aborts_before_any_llm_node(self):
        graph = build_daily_reader_graph()
        final_state = await graph.ainvoke(
            {
                "original_text": NPR_TRANSCRIPT_TEXT,
                "title": "AI viruses",
                "pipeline_meta": {"score": 7.0},
            }
        )
        assert final_state.get("abort") is True
        assert final_state["pipeline_meta"]["rejection"]["code"] == "transcript_rejected"
        # short-circuited: no LLM nodes ran, so no artifacts exist
        assert "body_json" not in final_state
        assert "highlights_json" not in final_state


class TestReviewBoilerplateGate:
    async def test_dirty_highlight_fails_review_without_llm(self):
        state = {
            "original_text": "clean article text",
            "normalized_paragraphs": [{"paragraph_id": "p_0", "text": "clean article text"}],
            "highlights_json": [{"text": "figures, external,", "gloss": "外部数据"}],
            "paragraph_notes_json": {},
            "takeaways_json": {},
        }
        with patch(
            "app.services.daily_reader.workflow._run_daily_review_llm_span",
            new_callable=AsyncMock,
        ) as llm_span:
            result = await quality_review_node(state)
        review = result["review_result"]
        assert review["passed"] is False
        assert review["reason"] == "boilerplate_leak"
        assert review["issues"][0]["dimension"] == "boilerplate"
        llm_span.assert_not_called()

    async def test_dirty_translation_fails_review(self):
        state = {
            "original_text": "clean",
            "normalized_paragraphs": [],
            "highlights_json": [],
            "paragraph_notes_json": {
                "notes": [
                    {
                        "paragraph_id": "p_0",
                        "translation": (
                            "版权归 NPR 所有。Copyright © 2026 NPR. All rights reserved."
                        ),
                    }
                ]
            },
            "takeaways_json": {},
        }
        result = await quality_review_node(state)
        review = result["review_result"]
        assert review["passed"] is False
        assert review["reason"] == "boilerplate_leak"


class TestFindBoilerplateHits:
    def test_hits_on_dirty_surfaces(self):
        inputs = [
            "letter, external entitled",
            "JUANA SUMMERS, HOST:",
            "Copyright © 2026 NPR. All rights reserved.",
            "(SOUNDBITE OF MUSIC)",
        ]
        hits = find_boilerplate_hits(inputs)
        assert len(hits) == len(inputs)
        # single speaker cue is still detected as a surface leak
        assert any("JUANA SUMMERS, HOST:" in hit for hit in hits)
        # single soundbite is still detected as a surface leak
        assert any("SOUNDBITE OF" in hit for hit in hits)
        # copyright and external-link badges are still detected
        assert any("external" in hit for hit in hits)
        assert any("Copyright" in hit for hit in hits)

    def test_clean_surfaces_no_hits(self):
        assert find_boilerplate_hits(["The study cited external factors.", ""]) == []


class TestExtractionRejectsTranscript:
    async def test_transcript_url_rejected_with_reason(self):
        fake = MagicMock()
        fake.fetch_url.return_value = "<html>transcript</html>"
        fake.extract.return_value = NPR_TRANSCRIPT_TEXT
        with patch.dict(sys.modules, {"trafilatura": fake}):
            result = await extract_with_trafilatura("https://example.org/t")
        assert isinstance(result, ExtractionResult)
        assert result.rejection_reason is not None
        assert "npr_transcript" in result.rejection_reason
        assert result.text == ""

    async def test_dirty_bbc_url_cleaned(self):
        fake = MagicMock()
        fake.fetch_url.return_value = "<html>article</html>"
        fake.extract.side_effect = [
            DIRTY_BBC_TEXT,
            '{"author": "A", "description": "D"}',
        ]
        with patch.dict(sys.modules, {"trafilatura": fake}):
            result = await extract_with_trafilatura("https://example.org/a")
        assert result is not None
        assert result.rejection_reason is None
        assert "- Published" not in result.text
        assert ", external" not in result.text
        assert result.word_count == len(result.text.split())
