"""T-02: Daily Reader workflow structure regression tests & golden samples.

Purpose: lock down current structural problems so that the upcoming
raw_blocks -> reading_units restructuring (Workstream 1) and API
contract fixes (Workstream 3) can be verified against these baselines.

Tests marked xfail express the *future target*; they fail today because
the current workflow lacks the corresponding structural step.
Each xfail reason explicitly names the workstream / task that will fix it.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.daily_reader.workflow import (
    MAX_PARAGRAPH_CHARS,
    MIN_REQUIRED_HIGHLIGHT_CHARS,
    READING_UNIT_MIN_CHARS,
    READING_UNIT_TARGET_CHARS,
    DailyReaderState,
    _build_notes_map,
    _check_highlight_coverage,
    _check_paragraph_notes_coverage,
    _classify_raw_blocks,
    _is_section_heading_candidate,
    _merge_content_blocks_into_units,
    _merge_short_groups,
    _merge_short_units,
    _paragraphs_requiring_highlight,
    _plan_reading_units,
    _reconcile_highlights,
    _split_into_raw_blocks,
    _split_into_paragraphs,
    daily_projection_node,
    light_normalize_node,
)

client = TestClient(app)


SINGLE_NEWLINE_NEWS = (
    "Trump's tariffs could push up UK car prices\n"
    "Some vehicles could cost up to £10,000 more\n"
    "Experts warn of significant impact on the automotive market\n"
    "The US president announced sweeping new trade measures on Wednesday\n"
    "British manufacturers face difficult choices about pricing\n"
    "Industry leaders have called for urgent government intervention\n"
    "The Society of Motor Manufacturers said the tariffs were deeply concerning\n"
    "A spokesperson said they would work with officials to minimise the impact\n"
    "Analysts expect used car prices to rise as new cars become more expensive\n"
    "Consumers may delay purchases until there is more clarity on trade policy"
)

ARTICLE_WITH_SECTION_HEADINGS = (
    "Court rules against tech giant in landmark case\n"
    "\n"
    "Found liable before\n"
    "\n"
    "The company had previously been found liable in a similar case two years ago, "
    "when regulators imposed a record fine for anti-competitive behaviour.\n"
    "\n"
    "New developments\n"
    "\n"
    "Recent evidence has changed the landscape significantly, with internal emails "
    "revealing a deliberate strategy to suppress competition in the market.\n"
    "\n"
    "What happens next\n"
    "\n"
    "The ruling opens the door for further legal action, and consumer groups "
    "are already preparing class-action lawsuits that could cost billions."
)

ARTICLE_TITLE_DUPLICATE = (
    "Climate summit ends with historic agreement\n"
    "\n"
    "Climate summit ends with historic agreement\n"
    "\n"
    "World leaders reached a landmark deal on emissions reduction after two weeks "
    "of intense negotiations in Geneva.\n"
    "\n"
    "Key commitments\n"
    "\n"
    "The agreement includes binding targets for carbon neutrality by 2050 and "
    "a new fund worth $100 billion for developing nations."
)

HURRICANE_SAMPLE = (
    "How do hurricanes and typhoons form and is climate change making them stronger?\n"
    "The 2026 Atlantic hurricane season is expected to be quieter than usual, "
    "according to the US science agency NOAA.\n"
    "It has forecast between three and six hurricanes between June and November - "
    "compared with the average of seven.\n"
    "Meanwhile the hurricane seasons in the central and eastern Pacific are likely "
    "to be above average, NOAA says.\n"
    "That is largely because the emerging El Niño weather pattern - which is likely "
    "to strengthen over the coming months - tends to disrupt tropical storms in the "
    "Atlantic but supports them in the Pacific.\n"
    "Climate change is not thought to increase the number of hurricanes, typhoons "
    "and cyclones worldwide.\n"
    "But rising temperatures mean that those that do form have the potential to bring "
    "stronger winds and heavier rain - and scientists warn it only takes one strong "
    "storm to bring major impacts.\n"
    "What are hurricanes and where do they happen?\n"
    "Hurricanes are powerful storms which develop in warm tropical ocean waters.\n"
    "In some parts of the world, they are known as cyclones or typhoons. Collectively, "
    'these storms are referred to as "tropical cyclones".\n'
    "Hurricanes can be categorised by their peak sustained wind speed.\n"
    "Major hurricanes are rated category three and above, meaning they reach at "
    "least 111mph (178km/h).\n"
    "How do hurricanes form?\n"
    "As warm, moist air rises from the ocean surface, winds begin to spin. The process "
    "is linked to how the Earth's rotation affects winds in tropical regions just away "
    "from the equator.\n"
    "For a hurricane to develop and keep spinning, the sea surface generally needs to "
    "be at least 27C to provide enough energy, and the winds need to not vary much "
    "with height.\n"
    "If all these factors come together, an intense hurricane can form, although the "
    "exact causes of individual storms are complex.\n"
    "Have hurricanes been getting worse?\n"
    'But it is "likely" that a higher proportion of tropical cyclones across the globe '
    "have reached category three or above over the past four decades, meaning they "
    "reach the highest wind speeds, according to the UN's climate body, the IPCC.\n"
    'The IPCC quotes "medium confidence" that there has been an increase in the average '
    "and peak rainfall rates associated with tropical cyclones.\n"
    "There also seems to have been a slowdown in the speed at which tropical cyclones "
    "move across the Earth's surface. This typically brings more rainfall for a given "
    'location. For example, in 2017 Hurricane Harvey "stalled" over Houston, releasing '
    "100cm of rain in three days.\n"
    "How is climate change affecting hurricanes?\n"
    "Assessing the precise influence of climate change on individual tropical cyclones "
    "can be challenging due to the complexity of these storm systems.\n"
    "But rising temperatures can affect these storms in several ways.\n"
    "Firstly, warmer ocean waters mean storms can pick up more energy, leading to "
    "higher wind speeds.\n"
    "Secondly, a warmer atmosphere can hold more moisture, leading to more intense "
    "rainfall.\n"
    "Finally, sea-levels are rising, mainly due to a combination of melting glaciers "
    "and ice sheets, and the fact that warmer water takes up more space. Local factors "
    "can also play a part. This means storm surges happen on top of already elevated "
    "sea levels, worsening coastal flooding.\n"
    'Overall, the IPCC concludes that there is "high confidence" that humans have '
    "contributed to increases in precipitation associated with tropical cyclones, and "
    '"medium confidence" that humans have contributed to the higher probability of a '
    "tropical cyclone being more intense.\n"
    "How might hurricanes change in the future?\n"
    'But as the world warms, it says it is "very likely" they will have higher rates '
    "of rainfall and reach higher top wind speeds. This means a higher proportion "
    "would reach the most intense categories, four and five.\n"
    "The more global temperatures rise, the more extreme these changes will tend to be.\n"
    "The proportion of tropical cyclones reaching category four and five may increase "
    "by around 10% if global temperature rises are limited to 1.5C, increasing to 13% "
    "at 2C and 20% at 4C, the IPCC says - although the exact numbers are uncertain."
)


def _make_state(
    original_text: str = "",
    title: str = "Test Article",
    **overrides: object,
) -> DailyReaderState:
    base: DailyReaderState = {
        "original_text": original_text,
        "title": title,
        "subtitle": "",
        "source": "The Guardian",
        "source_url": "https://example.com/article",
        "cover_image_url": None,
        "tags": [],
        "difficulty": "B2",
        "read_time_minutes": 5,
        "pipeline_source": "guardian_api",
        "pipeline_meta": {},
        "normalized_paragraphs": [],
        "vocab_draft": None,
        "highlights_json": [],
        "highlight_retry_exhausted": False,
        "highlight_retry_missing_paragraph_ids": [],
        "paragraph_notes_json": {},
        "takeaways_json": {},
        "review_result": None,
        "refinement_result": None,
        "abort": False,
        "body_json": {},
        "usage_summary": None,
    }
    base.update(overrides)
    return base


class TestSingleNewlineNewsStructure:
    """Golden sample: single-newline news (daily_2026_05_23_003 pattern).

    Current _split_into_paragraphs splits on single \\n when no double
    newline is present, producing one paragraph per sentence.
    After Workstream 1 restructuring, these should be grouped into
    meaningful reading units (4-8 per article, not 10+).
    """

    def test_single_newline_news_should_not_produce_per_line_paragraphs(self):
        result = _split_into_paragraphs(SINGLE_NEWLINE_NEWS)
        assert len(result) <= 4, (
            f"Single-newline news produced {len(result)} paragraphs; "
            f"should be grouped into ≤4 reading units. "
            f"Current paragraphs: {[p[:60] for p in result]}"
        )


class TestSectionHeadingStructure:
    """Golden sample: article with section headings (daily_2026_05_23_002 pattern).

    Current workflow has no denoise_and_classify_blocks step.
    Section headings like "Found liable before" enter normalized_paragraphs
    as regular body text and will generate reading notes.
    After Workstream 1, headings should be classified and excluded from
    per-paragraph note generation.
    """

    def test_section_headings_should_not_be_regular_paragraphs(self):
        state = _make_state(original_text=ARTICLE_WITH_SECTION_HEADINGS)
        result = light_normalize_node(state)
        paragraphs = result["normalized_paragraphs"]

        heading_texts = {"Found liable before", "What happens next", "New developments"}
        heading_paragraphs = [
            p for p in paragraphs
            if p["text"].strip() in heading_texts
        ]
        assert len(heading_paragraphs) == 0, (
            f"Section headings should not appear as regular body paragraphs. "
            f"Found: {[p['text'] for p in heading_paragraphs]}"
        )


class TestTitleDuplicateStructure:
    """Golden sample: article where title is repeated in body.

    Current workflow does not deduplicate titles from body text.
    After Workstream 1 denoise_and_classify_blocks, the repeated title
    should be removed from display paragraphs.
    """

    def test_repeated_title_should_not_appear_in_body_paragraphs(self):
        state = _make_state(
            original_text=ARTICLE_TITLE_DUPLICATE,
            title="Climate summit ends with historic agreement",
        )
        result = light_normalize_node(state)
        paragraphs = result["normalized_paragraphs"]

        title_text = "Climate summit ends with historic agreement"
        title_in_body = [
            p for p in paragraphs if p["text"].strip() == title_text
        ]
        assert len(title_in_body) == 0, (
            f"Repeated title should be deduplicated from body paragraphs. "
            f"Found {len(title_in_body)} copies."
        )


class TestRawBlocksAndReadingUnits:
    """Unit tests for the raw_blocks → reading_units pipeline helpers."""

    def test_split_into_raw_blocks_single_newline(self):
        blocks = _split_into_raw_blocks(SINGLE_NEWLINE_NEWS)
        assert len(blocks) == 10
        assert all(b["section_idx"] == 0 for b in blocks)
        assert not any(b["is_solo_in_section"] for b in blocks)

    def test_split_into_raw_blocks_double_newline_sections(self):
        blocks = _split_into_raw_blocks(ARTICLE_WITH_SECTION_HEADINGS)
        section_ids = {b["section_idx"] for b in blocks}
        assert len(section_ids) >= 4
        heading_texts = {"Found liable before", "New developments", "What happens next"}
        for h in heading_texts:
            matching = [b for b in blocks if b["text"] == h]
            assert len(matching) == 1
            assert matching[0]["is_solo_in_section"] is True

    def test_classify_raw_blocks_title_duplicate(self):
        blocks = _split_into_raw_blocks(ARTICLE_TITLE_DUPLICATE)
        classified = _classify_raw_blocks(blocks, title="Climate summit ends with historic agreement")
        dupes = [b for b in classified if b["role"] == "title_duplicate"]
        assert len(dupes) == 2

    def test_classify_raw_blocks_section_heading(self):
        blocks = _split_into_raw_blocks(ARTICLE_WITH_SECTION_HEADINGS)
        classified = _classify_raw_blocks(blocks, title="")
        headings = [b for b in classified if b["role"] == "section_heading"]
        heading_texts = {b["text"] for b in headings}
        assert "Found liable before" in heading_texts
        assert "New developments" in heading_texts
        assert "What happens next" in heading_texts

    def test_is_section_heading_candidate_first_block_not_heading(self):
        assert _is_section_heading_candidate("Short title", True, 0) is False

    def test_is_section_heading_candidate_long_text_not_heading(self):
        assert _is_section_heading_candidate("A" * 80, True, 1) is False

    def test_is_section_heading_candidate_ends_with_period(self):
        assert _is_section_heading_candidate("This is a sentence.", True, 1) is False

    def test_is_section_heading_candidate_not_solo(self):
        assert _is_section_heading_candidate("Short phrase", False, 1) is False

    def test_is_section_heading_candidate_valid_heading(self):
        assert _is_section_heading_candidate("Found liable before", True, 1) is True

    def test_plan_reading_units_removes_title_duplicates(self):
        blocks = [
            {"block_id": "b_0", "text": "Title text", "section_idx": 0, "is_solo_in_section": True, "role": "title_duplicate"},
            {"block_id": "b_1", "text": "Content text here.", "section_idx": 1, "is_solo_in_section": True, "role": "content"},
        ]
        units = _plan_reading_units(blocks)
        assert len(units) == 1
        assert units[0]["text"] == "Content text here."

    def test_plan_reading_units_drops_section_headings(self):
        blocks = [
            {"block_id": "b_0", "text": "Lead paragraph.", "section_idx": 0, "is_solo_in_section": True, "role": "content"},
            {"block_id": "b_1", "text": "Section heading", "section_idx": 1, "is_solo_in_section": True, "role": "section_heading"},
            {"block_id": "b_2", "text": "Content after heading.", "section_idx": 2, "is_solo_in_section": True, "role": "content"},
        ]
        units = _plan_reading_units(blocks)
        unit_texts = [u["text"] for u in units]
        assert "Section heading" not in unit_texts
        merged_text = " ".join(unit_texts)
        assert "Lead paragraph." in merged_text
        assert "Content after heading." in merged_text

    def test_merge_content_blocks_respects_target(self):
        blocks = [
            {"block_id": f"b_{i}", "text": f"Sentence number {i} with some extra words.", "section_idx": 0}
            for i in range(20)
        ]
        units = _merge_content_blocks_into_units(blocks)
        assert len(units) < 20
        for unit in units:
            assert len(unit["text"]) > 0
            assert len(unit["text"]) >= READING_UNIT_MIN_CHARS or len(units) == 1

    def test_light_normalize_produces_stable_ids(self):
        state = _make_state(original_text=SINGLE_NEWLINE_NEWS)
        result = light_normalize_node(state)
        paragraphs = result["normalized_paragraphs"]
        for i, p in enumerate(paragraphs):
            assert p["paragraph_id"] == f"p_{i}"


class TestReadingUnitDensity:
    """Density regression tests for reading unit splitting.

    Validates that real-world articles produce comfortable reading
    paragraph granularity, not fragmented card-sized chunks.
    """

    def test_hurricane_sample_reading_unit_density(self):
        state = _make_state(
            original_text=HURRICANE_SAMPLE,
            title="How do hurricanes and typhoons form and is climate change making them stronger?",
        )
        result = light_normalize_node(state)
        paragraphs = result["normalized_paragraphs"]

        assert len(paragraphs) <= 12, (
            f"Hurricane sample produced {len(paragraphs)} reading units; "
            f"should be ≤12. "
            f"Lengths: {[len(p['text']) for p in paragraphs]}"
        )

        short_units = [p for p in paragraphs if len(p["text"]) < 220]
        assert len(short_units) <= 2, (
            f"Found {len(short_units)} reading units < 220 chars; should be ≤2. "
            f"Short units: {[(p['paragraph_id'], len(p['text'])) for p in short_units]}"
        )

        title_text = "How do hurricanes and typhoons form and is climate change making them stronger?"
        title_in_body = [p for p in paragraphs if p["text"].strip() == title_text]
        assert len(title_in_body) == 0, "Title should not appear in body paragraphs."

    def test_short_units_merged_to_meet_min_chars(self):
        total_len = READING_UNIT_MIN_CHARS * 2 + 50
        chunk_count = 8
        chunk_len = total_len // chunk_count
        blocks = [
            {"block_id": f"b_{i}", "text": "A" * chunk_len, "section_idx": 0}
            for i in range(chunk_count)
        ]
        assert sum(len(b["text"]) for b in blocks) <= MAX_PARAGRAPH_CHARS

        units = _merge_content_blocks_into_units(blocks)
        for unit in units:
            assert len(unit["text"]) >= READING_UNIT_MIN_CHARS or len(units) == 1, (
                f"Unit too short after merge: {len(unit['text'])} chars "
                f"(min={READING_UNIT_MIN_CHARS}). "
                f"All lengths: {[len(u['text']) for u in units]}"
            )

    def test_no_heading_short_content_isolated(self):
        blocks = [
            {"block_id": "b_0", "text": "A" * 100, "section_idx": 0, "is_solo_in_section": True, "role": "content"},
            {"block_id": "b_1", "text": "Section heading", "section_idx": 1, "is_solo_in_section": True, "role": "section_heading"},
            {"block_id": "b_2", "text": "B" * 80, "section_idx": 2, "is_solo_in_section": True, "role": "content"},
            {"block_id": "b_3", "text": "C" * 300, "section_idx": 3, "is_solo_in_section": True, "role": "content"},
        ]
        units = _plan_reading_units(blocks)
        unit_texts = [u["text"] for u in units]
        assert "Section heading" not in unit_texts
        short_standalone = [u for u in units if len(u["text"]) < READING_UNIT_MIN_CHARS]
        assert len(short_standalone) == 0, (
            f"Short content should not be isolated as its own unit. "
            f"Short units: {[len(u['text']) for u in short_standalone]}"
        )

    def test_merge_short_groups_merges_first_short_group_forward(self):
        short_group = [
            {"block_id": "b_0", "text": "Short lead.", "section_idx": 0, "is_solo_in_section": True, "role": "content"},
        ]
        normal_group = [
            {"block_id": "b_1", "text": "A" * READING_UNIT_MIN_CHARS, "section_idx": 1, "is_solo_in_section": True, "role": "content"},
        ]
        groups = [short_group, normal_group]
        result = _merge_short_groups(groups)
        assert len(result) == 1, (
            f"First short group should merge forward into second group. "
            f"Got {len(result)} groups."
        )
        assert len(result[0]) == 2

    def test_merge_short_units_merges_last_short_unit_backward(self):
        units = [
            {"text": "A" * READING_UNIT_MIN_CHARS},
            {"text": "Short ending."},
        ]
        result = _merge_short_units(units)
        assert len(result) == 1, (
            f"Last short unit should merge backward. Got {len(result)} units."
        )
        assert result[0]["text"].endswith("Short ending.")


class TestReadingUnitCoverageAndNotes:
    """Coverage and note checks use reading unit semantics.

    Short reading units (< MIN_REQUIRED_HIGHLIGHT_CHARS) are not
    forced to have highlights or notes.
    Coverage/note reports include reading_unit aliases.
    """

    def test_short_reading_unit_not_required_highlight(self):
        short_unit = [
            {"paragraph_id": "p_0", "text": "Short text."},
        ]
        highlights = []
        result = _paragraphs_requiring_highlight(short_unit, highlights)
        assert len(result) == 0, (
            "Short reading unit should not require highlight. "
            f"MIN_REQUIRED_HIGHLIGHT_CHARS={MIN_REQUIRED_HIGHLIGHT_CHARS}"
        )

    def test_long_reading_unit_required_highlight(self):
        long_text = "A" * 150 + " more text here."
        long_unit = [
            {"paragraph_id": "p_0", "text": long_text},
        ]
        highlights = []
        result = _paragraphs_requiring_highlight(long_unit, highlights)
        assert len(result) == 1, (
            "Long reading unit should require highlight when none exists."
        )

    def test_short_unit_with_existing_highlight_not_in_retry(self):
        short_unit = [
            {"paragraph_id": "p_0", "text": "Short text."},
            {"paragraph_id": "p_1", "text": "A" * 150},
        ]
        highlights = [
            {"paragraph_id": "p_1", "text": "AAA", "start": 0, "end": 3},
        ]
        result = _paragraphs_requiring_highlight(short_unit, highlights)
        assert len(result) == 0, (
            "p_0 is short (no highlight needed), p_1 already has highlight. "
            "No retry should be needed."
        )

    def test_highlight_coverage_report_includes_reading_unit_fields(self):
        paragraphs = [
            {"paragraph_id": "p_0", "text": "A" * 200},
            {"paragraph_id": "p_1", "text": "B" * 200},
        ]
        highlights = [
            {"paragraph_id": "p_0", "text": "AAA", "start": 0, "end": 3},
        ]
        report = _check_highlight_coverage(paragraphs, highlights)
        assert "total_reading_units" in report
        assert "covered_reading_units" in report
        assert report["total_reading_units"] == report["total_paragraphs"]
        assert report["covered_reading_units"] == report["covered_paragraphs"]

    def test_notes_coverage_report_includes_reading_unit_fields(self):
        paragraphs = [
            {"paragraph_id": "p_0", "text": "First."},
            {"paragraph_id": "p_1", "text": "Second."},
        ]
        paragraph_notes = {
            "notes": [
                {"paragraph_id": "p_0", "focus_question": "Q0", "micro_summary": "S0", "translation": "T0"},
            ]
        }
        report = _check_paragraph_notes_coverage(paragraphs, paragraph_notes)
        assert "total_reading_units" in report
        assert "noted_reading_units" in report
        assert report["total_reading_units"] == report["total_paragraphs"]
        assert report["noted_reading_units"] == report["noted_paragraphs"]

    def test_min_required_highlight_chars_is_120(self):
        assert MIN_REQUIRED_HIGHLIGHT_CHARS == 120

    def test_short_reading_unit_missing_note_not_required(self):
        paragraphs = [
            {"paragraph_id": "p_0", "text": "Short."},
            {"paragraph_id": "p_1", "text": "A" * 150},
        ]
        paragraph_notes = {"notes": []}
        report = _check_paragraph_notes_coverage(paragraphs, paragraph_notes)
        assert "p_0" not in report["missing_required_note_ids"], (
            "Short reading unit missing note should not be in required missing list"
        )
        assert "p_1" in report["missing_required_note_ids"], (
            "Long reading unit missing note should be in required missing list"
        )

    def test_short_unit_has_note_not_in_missing_required(self):
        paragraphs = [
            {"paragraph_id": "p_0", "text": "Short."},
            {"paragraph_id": "p_1", "text": "A" * 150},
        ]
        paragraph_notes = {
            "notes": [
                {"paragraph_id": "p_1", "focus_question": "Q", "micro_summary": "S", "translation": "T"},
            ]
        }
        report = _check_paragraph_notes_coverage(paragraphs, paragraph_notes)
        assert report["missing_required_note_ids"] == [], (
            "Long unit has note, short unit not required. No required missing."
        )

    def test_notes_coverage_report_includes_missing_required_note_ids(self):
        paragraphs = [
            {"paragraph_id": "p_0", "text": "A" * 200},
        ]
        paragraph_notes = {"notes": []}
        report = _check_paragraph_notes_coverage(paragraphs, paragraph_notes)
        assert "missing_required_note_ids" in report
        assert "p_0" in report["missing_required_note_ids"]


class TestPromptPolicyNoOldWording:
    """Prompt and policy files must not contain old 'every raw paragraph
    must be covered' wording. They should use reading unit semantics."""

    def test_daily_review_yaml_no_old_paragraph_wording(self):
        from pathlib import Path
        review_path = Path(__file__).parent.parent / "prompts" / "agents" / "daily_review.yaml"
        content = review_path.read_text(encoding="utf-8")
        assert "每段是否都有完整的 note" not in content
        assert "每段 1-3 个" not in content
        assert "paragraph_notes_completeness" not in content
        assert "reading unit" in content

    def test_daily_refinement_yaml_no_old_wording(self):
        from pathlib import Path
        refine_path = Path(__file__).parent.parent / "prompts" / "agents" / "daily_refinement.yaml"
        content = refine_path.read_text(encoding="utf-8")
        assert "优先为未覆盖段落补充高亮" not in content
        assert "reading unit" in content

    def test_daily_policy_yaml_no_old_paragraph_wording(self):
        from pathlib import Path
        policy_path = Path(__file__).parent.parent / "prompts" / "policies" / "daily.yaml"
        content = policy_path.read_text(encoding="utf-8")
        assert "必须覆盖输入的每一段" not in content
        assert "不允许跳过任何段落" not in content
        assert "每个段落都必须有对应的 note" not in content
        assert "reading unit" in content

    def test_daily_footer_yaml_no_old_paragraph_wording(self):
        from pathlib import Path
        footer_path = Path(__file__).parent.parent / "prompts" / "agents" / "daily_footer.yaml"
        content = footer_path.read_text(encoding="utf-8")
        assert "每个段落都必须有对应的 note" not in content
        assert "不允许遗漏" not in content
        assert "段落透读" not in content
        assert "reading unit" in content

    def test_daily_vocab_yaml_no_old_paragraph_wording(self):
        from pathlib import Path
        vocab_path = Path(__file__).parent.parent / "prompts" / "agents" / "daily_vocab.yaml"
        content = vocab_path.read_text(encoding="utf-8")
        assert "必须覆盖输入的每一段" not in content
        assert "不允许跳过任何段落" not in content
        assert "每段 1-3 个" not in content
        assert "reading unit" in content

    def test_daily_vocab_agent_no_old_paragraph_wording(self):
        from pathlib import Path
        agent_path = Path(__file__).parent.parent / "app" / "agents" / "daily_vocab_agent.py"
        content = agent_path.read_text(encoding="utf-8")
        assert "每一段都生成标注" not in content
        assert "不要遗漏任何段落" not in content
        assert "reading unit" in content


class TestParagraphIdConsistency:
    """paragraph_id must be the stable anchor across paragraphs, notes,
    highlights, and takeaways.

    These tests verify the current projection/helper behavior.
    They should pass now and continue to pass after restructuring
    (with paragraph_id redefined as reading_unit_id).
    """

    def test_notes_map_keys_match_paragraph_ids(self):
        paragraphs = [
            {"paragraph_id": "p_0", "text": "First paragraph."},
            {"paragraph_id": "p_1", "text": "Second paragraph."},
            {"paragraph_id": "p_2", "text": "Third paragraph."},
        ]
        paragraph_notes = {
            "notes": [
                {"paragraph_id": "p_0", "focus_question": "Q0", "micro_summary": "S0", "translation": "T0"},
                {"paragraph_id": "p_1", "focus_question": "Q1", "micro_summary": "S1", "translation": "T1"},
                {"paragraph_id": "p_2", "focus_question": "Q2", "micro_summary": "S2", "translation": "T2"},
            ]
        }
        notes_map = _build_notes_map(paragraph_notes)
        assert set(notes_map.keys()) == {"p_0", "p_1", "p_2"}
        for pid in ("p_0", "p_1", "p_2"):
            assert notes_map[pid]["paragraph_id"] == pid

    def test_highlights_paragraph_id_matches_paragraph_list(self):
        paragraphs = [
            {"paragraph_id": "p_0", "text": "The quick brown fox jumps over the lazy dog."},
            {"paragraph_id": "p_1", "text": "A completely different sentence about economics."},
        ]
        highlights = [
            {"id": "hl_p00_01", "text": "quick brown fox", "paragraph_id": "p_0", "start": 4, "end": 19},
            {"id": "hl_p01_01", "text": "economics", "paragraph_id": "p_1", "start": 38, "end": 47},
        ]
        result = _reconcile_highlights(paragraphs, highlights)
        para_ids = {p["paragraph_id"] for p in paragraphs}
        hl_pids = {h["paragraph_id"] for h in result}
        assert hl_pids.issubset(para_ids), (
            f"Highlight paragraph_ids {hl_pids} must be subset of "
            f"paragraph ids {para_ids}"
        )

    def test_projection_body_paragraph_ids_match_normalized(self):
        paragraphs = [
            {"paragraph_id": "p_0", "text": "Alpha paragraph."},
            {"paragraph_id": "p_1", "text": "Beta paragraph."},
        ]
        highlights = [
            {"id": "hl_p00_01", "text": "Alpha", "paragraph_id": "p_0", "start": 0, "end": 5},
        ]
        paragraph_notes = {
            "notes": [
                {"paragraph_id": "p_0", "focus_question": "Q0", "micro_summary": "S0", "translation": "T0"},
            ]
        }
        takeaways = {"article_takeaway": "Overall takeaway"}

        state = _make_state(
            normalized_paragraphs=paragraphs,
            highlights_json=highlights,
            paragraph_notes_json=paragraph_notes,
            takeaways_json=takeaways,
        )
        result = daily_projection_node(state)
        body = result["body_json"]

        body_pids = [p["id"] for p in body["paragraphs"]]
        assert body_pids == ["p_0", "p_1"], (
            f"Body paragraph ids should match normalized_paragraphs order. "
            f"Got: {body_pids}"
        )

    def test_projection_reading_note_attached_to_correct_paragraph(self):
        paragraphs = [
            {"paragraph_id": "p_0", "text": "Alpha paragraph."},
            {"paragraph_id": "p_1", "text": "Beta paragraph."},
        ]
        paragraph_notes = {
            "notes": [
                {"paragraph_id": "p_1", "focus_question": "Q1", "micro_summary": "S1", "translation": "T1"},
            ]
        }
        state = _make_state(
            normalized_paragraphs=paragraphs,
            paragraph_notes_json=paragraph_notes,
        )
        result = daily_projection_node(state)
        body = result["body_json"]

        p0 = body["paragraphs"][0]
        p1 = body["paragraphs"][1]
        assert p0["reading_note"] is None, "p_0 has no note, should be None"
        assert p1["reading_note"] is not None, "p_1 has a note, should be attached"
        assert p1["reading_note"]["paragraph_id"] == "p_1"

    def test_notes_coverage_report_uses_paragraph_id(self):
        paragraphs = [
            {"paragraph_id": "p_0", "text": "First."},
            {"paragraph_id": "p_1", "text": "Second."},
            {"paragraph_id": "p_2", "text": "Third."},
        ]
        paragraph_notes = {
            "notes": [
                {"paragraph_id": "p_0", "focus_question": "Q0", "micro_summary": "S0", "translation": "T0"},
            ]
        }
        report = _check_paragraph_notes_coverage(paragraphs, paragraph_notes)
        assert report["total_paragraphs"] == 3
        assert report["noted_paragraphs"] == 1
        assert report["missing_paragraph_ids"] == ["p_1", "p_2"]


MOCK_DRAFT_ROW = {
    "id": "daily_2026_05_23_draft_001",
    "title": "Draft Article",
    "subtitle": "Not yet published",
    "source": "The Guardian",
    "source_url": "https://example.com/draft",
    "publish_date": date(2026, 5, 23),
    "difficulty": "B2",
    "read_time_minutes": 5,
    "tags": ["politics"],
    "cover_image_url": None,
    "cover_theme": "editorial_warm",
    "body_json": {"paragraphs": [{"text": "Draft content"}]},
    "highlights_json": [],
    "paragraph_notes_json": {"notes": []},
    "takeaways_json": {},
}

MOCK_PUBLISHED_ROW = {
    "id": "daily_2026_05_23_001",
    "title": "Published Article",
    "subtitle": "Live content",
    "source": "The Guardian",
    "source_url": "https://example.com/published",
    "publish_date": date(2026, 5, 23),
    "difficulty": "B2",
    "read_time_minutes": 5,
    "tags": ["science"],
    "cover_image_url": None,
    "cover_theme": "editorial_warm",
    "body_json": {"paragraphs": [{"text": "Published content"}]},
    "highlights_json": [],
    "paragraph_notes_json": {"notes": []},
    "takeaways_json": {},
}


class TestPublicApiDraftLeak:
    """GET /daily-reader/{article_id} must not return draft articles.

    get_article_by_id() now includes AND status = 'published'.
    get_article_by_id_any_status() is available for admin routes.
    """

    @patch("app.services.daily_reader.service.db_connection.DB_POOL")
    def test_detail_endpoint_rejects_draft(self, mock_pool):
        async def _sql_aware_fetchrow(sql, *args):
            if "status" in sql.lower() and "published" in sql.lower():
                return None
            return MOCK_DRAFT_ROW

        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = _sql_aware_fetchrow
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/daily-reader/daily_2026_05_23_draft_001")
        assert response.status_code == 404, (
            "Draft articles must not be accessible via public detail endpoint"
        )

    @patch("app.services.daily_reader.service.db_connection.DB_POOL")
    def test_detail_endpoint_returns_published(self, mock_pool):
        async def _sql_aware_fetchrow(sql, *args):
            if "status" in sql.lower() and "published" in sql.lower():
                return MOCK_PUBLISHED_ROW
            return None

        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = _sql_aware_fetchrow
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/daily-reader/daily_2026_05_23_001")
        assert response.status_code == 200
        assert response.json()["id"] == "daily_2026_05_23_001"

    @patch("app.services.daily_reader.service.db_connection.DB_POOL")
    def test_detail_sql_includes_status_published_filter(self, mock_pool):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        client.get("/daily-reader/daily_2026_05_23_001")

        sql = mock_conn.fetchrow.call_args[0][0].lower()
        assert "status" in sql and "published" in sql, (
            f"Public detail query must filter by status='published'. Got: {sql}"
        )


MOCK_SAME_DAY_ROW_A = {
    "id": "daily_2026_05_23_001",
    "title": "Article A",
    "subtitle": "First article of the day",
    "source": "The Guardian",
    "source_url": "https://example.com/a",
    "publish_date": date(2026, 5, 23),
    "difficulty": "B2",
    "read_time_minutes": 5,
    "tags": ["science"],
    "cover_image_url": None,
    "cover_theme": "editorial_warm",
}

MOCK_SAME_DAY_ROW_B = {
    "id": "daily_2026_05_23_002",
    "title": "Article B",
    "subtitle": "Second article of the day",
    "source": "BBC News",
    "source_url": "https://example.com/b",
    "publish_date": date(2026, 5, 23),
    "difficulty": "B1",
    "read_time_minutes": 4,
    "tags": ["politics"],
    "cover_image_url": None,
    "cover_theme": "editorial_warm",
}

MOCK_SAME_DAY_ROW_C = {
    "id": "daily_2026_05_23_003",
    "title": "Article C",
    "subtitle": "Third article of the day",
    "source": "Reuters",
    "source_url": "https://example.com/c",
    "publish_date": date(2026, 5, 23),
    "difficulty": "C1",
    "read_time_minutes": 6,
    "tags": ["technology"],
    "cover_image_url": None,
    "cover_theme": "editorial_warm",
}


class TestPaginationStability:
    """Same-day multi-article pagination must be stable.

    list_articles now uses composite ORDER BY publish_date DESC, id DESC
    and composite cursor (date|id) to avoid same-day skip/duplication.
    """

    @patch("app.services.daily_reader.service.db_connection.DB_POOL")
    def test_list_sql_uses_composite_order_by(self, mock_pool):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [MOCK_SAME_DAY_ROW_A, MOCK_SAME_DAY_ROW_B]
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        client.get("/daily-reader?limit=2")

        sql = mock_conn.fetch.call_args[0][0].lower()
        order_by_part = sql.split("order by")[-1]
        assert "id" in order_by_part, (
            "SQL ORDER BY must include id as tie-breaker for same-day articles. "
            f"Current ORDER BY clause: {order_by_part.strip()}"
        )

    @patch("app.services.daily_reader.service.db_connection.DB_POOL")
    def test_list_sql_cursor_condition_references_id(self, mock_pool):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [MOCK_SAME_DAY_ROW_A, MOCK_SAME_DAY_ROW_B]
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        client.get("/daily-reader?cursor=2026-05-23|daily_2026_05_23_001&limit=2")

        sql = mock_conn.fetch.call_args[0][0].lower()
        where_part = sql.split("where")[-1]
        assert "id" in where_part, (
            "SQL cursor condition must reference id for stable pagination. "
            f"Current WHERE clause: {where_part.strip()}"
        )

    @patch("app.services.daily_reader.service.db_connection.DB_POOL")
    def test_cursor_includes_article_id(self, mock_pool):
        all_rows = [MOCK_SAME_DAY_ROW_A, MOCK_SAME_DAY_ROW_B, MOCK_SAME_DAY_ROW_C]
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = all_rows
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/daily-reader?limit=2")
        data = response.json()
        cursor = data.get("cursor")

        assert cursor is not None
        assert "|" in cursor, (
            "Composite cursor must contain '|' separator between date and id. "
            f"Got: {cursor!r}"
        )
        parts = cursor.split("|", 1)
        assert parts[1].startswith("daily_"), (
            f"Cursor id part must be an article id. Got: {parts[1]!r}"
        )
