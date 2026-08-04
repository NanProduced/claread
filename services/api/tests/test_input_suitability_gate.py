# task-history: D6-I3A (renamed from test_d6_i3a_input_suitability_gate.py)
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.reader_input_adapter import InputSuitabilityRequest
from app.services.reader_orchestration.input_suitability_gate import (
    InputSuitabilityGate,
    evaluate_input_suitability,
)

pytestmark = [
    pytest.mark.chain_reader_parse,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]


def _english_paragraph(multiplier: int = 1) -> str:
    sentence = (
        "This article explains how communities compare evidence, revise plans, "
        "and discuss tradeoffs before making a decision about public projects. "
        "Each paragraph stays focused on natural language reading, includes "
        "complete sentences, and keeps enough context for vocabulary, grammar, "
        "and sentence analysis to be genuinely useful for an English learner."
    )
    return "\n\n".join(sentence for _ in range(multiplier))


def _evaluate(
    *,
    source_type: str = "pasted_text",
    text: str,
    filename: str | None = None,
    source_metadata: dict | None = None,
):
    gate = InputSuitabilityGate()
    request = InputSuitabilityRequest(
        source_type=source_type,
        text=text,
        filename=filename,
        source_metadata=source_metadata or {},
    )
    return gate.evaluate(request)


def test_blank_input_is_rejected() -> None:
    result = _evaluate(text="  \n\t  ")

    assert result.outcome == "input_rejected_or_action_required"
    assert "too_short_for_learning" in result.flags


def test_short_english_input_is_rejected() -> None:
    result = _evaluate(
        text="This short paragraph is grammatical but far too brief to support useful reading analysis."
    )

    assert result.outcome == "input_rejected_or_action_required"
    assert "too_short_for_learning" in result.flags


def test_normal_pasted_english_paragraphs_are_stable() -> None:
    result = _evaluate(text=_english_paragraph(multiplier=2))

    assert result.outcome == "stable_document_ready"
    assert result.word_count >= 50
    assert result.english_word_ratio >= 0.70
    assert result.natural_language_score > 0.5


def test_txt_file_with_enough_english_can_be_stable() -> None:
    result = _evaluate(
        source_type="txt_file",
        filename="article.txt",
        text=_english_paragraph(multiplier=2),
    )

    assert result.outcome == "stable_document_ready"
    assert result.source_type == "txt_file"


def test_mostly_non_english_text_is_rejected() -> None:
    result = _evaluate(
        text=(
            "这是一个中文段落，主要讨论城市更新、社区反馈和公共预算。"
            "这些句子几乎都不是英文，因此不适合作为英文阅读解读材料。"
        )
    )

    assert result.outcome == "input_rejected_or_action_required"
    assert "non_english_or_mixed_language" in result.flags


def test_code_heavy_input_requires_candidate_document() -> None:
    # Pure Python code wrapped in a fenced block, no Markdown prose
    # structure (no heading / paragraph / list outside the fence).
    # The parser sees only code_block tokens, so prose_structure_count
    # is 0 and code_line_ratio is high → code_dominant triggers, but
    # per the downgrade policy the outcome is candidate (not rejected).
    text = """
```python
def build_report(data):
    summary = []
    for item in data:
        if item["enabled"]:
            summary.append(item["title"])
    return summary

class ReportRunner:
    def execute(self, rows):
        return [row for row in rows if row]

import json
from pathlib import Path

def save_output(payload):
    Path("report.json").write_text(json.dumps(payload))
```
""".strip()
    result = _evaluate(text=text)

    assert result.outcome == "candidate_document_required"
    assert "code_dominant" in result.flags


def test_legal_document_with_year_semicolon_not_code_dominant() -> None:
    # Regression: the legacy hardcoded regex misclassified legal
    # citations like "(2019);" as code lines because the punctuation
    # count hit the heuristic threshold. The parser-based signal
    # treats the input as paragraphs (prose), so code_dominant must
    # not fire.
    text = """
In re Smith v. Thompson (2019); the court held that the statute applied retroactively to all pending claims.
Jackson v. City Council (2020); see also the dissenting opinion in Reyes (2018) for a parallel reading.
The defendant appealed the lower court ruling twice (2021); however, the jury returned a unanimous verdict.
Brown v. Board of Education (1954); the landmark ruling established a new framework for judicial review.
These cases illustrate how courts interpret statutory language over time and balance precedent with new facts.
""".strip()
    result = _evaluate(text=text)

    assert "code_dominant" not in result.flags
    assert result.outcome == "stable_document_ready"


def test_shebang_first_line_triggers_code_dominant() -> None:
    # Even when the parser sees raw code (no fence) as a paragraph,
    # the shebang line is a strong out-of-spec signal that the input
    # is a script, not prose.
    text = """
#!/usr/bin/env python
import sys
import json
from pathlib import Path

def main():
    data = json.loads(sys.stdin.read())
    Path("out.json").write_text(json.dumps(data))
    return 0
""".strip()
    result = _evaluate(text=text)

    assert "code_dominant" in result.flags
    assert result.outcome == "candidate_document_required"


def test_markdown_article_80_pct_code_with_multiple_headings_not_code_dominant() -> None:
    # 80% of the lines are inside a fenced code block, but the
    # multiple headings keep prose_structure_count > 1, so the
    # code-line-ratio-only trigger must not fire.
    text = """
# Engineering Notes

## Section One

```python
def process_reading_records(records):
    filtered = [r for r in records if r.active]
    summary = sum(r.word_count for r in filtered)
    return summary

def validate_record(record):
    if not record.title:
        return False
    if not record.blocks:
        return False
    return True

def transform_record(record, schema):
    new_blocks = []
    for block in record.blocks:
        new_blocks.append(schema.apply(block))
    return new_blocks
```
""".strip()
    result = _evaluate(
        source_type="markdown_file",
        filename="engineering.md",
        text=text,
    )

    assert "code_dominant" not in result.flags


def test_raw_code_with_modeline_triggers_code_dominant() -> None:
    # Raw code (no fence, parsed as a single paragraph) but the
    # `# -*- coding: ... -*-` modeline is a strong signal that the
    # input is source code.
    text = """
# -*- coding: utf-8 -*-
def main():
    print("hello world")
    return 0
""".strip()
    result = _evaluate(text=text)

    assert "code_dominant" in result.flags
    assert result.outcome == "candidate_document_required"


def test_link_list_input_is_rejected() -> None:
    text = """
- https://example.com/report-one
- https://example.com/report-two
- https://example.com/report-three
- https://example.com/report-four
- https://example.com/report-five
""".strip()
    result = _evaluate(text=text)

    assert result.outcome == "input_rejected_or_action_required"
    assert "link_list_dominant" in result.flags


def test_simple_markdown_heading_and_list_can_be_stable() -> None:
    text = f"""
# Weekly Review

{_english_paragraph()}

- The team compared evidence before changing the plan.
- Members described costs, risks, and public feedback in plain language.
- Readers can still learn vocabulary and sentence structure from this text.

> The final paragraph explains the decision in a calm and readable style.
""".strip()
    result = _evaluate(
        source_type="markdown_file",
        filename="weekly-review.md",
        text=text,
    )

    assert result.outcome == "stable_document_ready"


def test_markdown_article_with_small_fenced_code_is_not_rejected_as_code_dominant() -> None:
    text = f"""
# Review Notes

{_english_paragraph(multiplier=2)}

```python
def add(a, b):
    return a + b
```

The short code sample only illustrates one point in the article and should not make the whole input code-dominant.
""".strip()
    result = _evaluate(
        source_type="markdown_file",
        filename="review-notes.md",
        text=text,
    )

    assert result.outcome == "stable_document_ready"
    assert "code_dominant" not in result.flags


def test_markdown_article_with_unclosed_fenced_code_requires_candidate_document() -> None:
    text = f"""
# Review Notes

{_english_paragraph(multiplier=2)}

```python
def add(a, b):
    return a + b

The closing fence is missing, so the remainder of the source is structurally ambiguous for deterministic normalization.
""".strip()
    result = _evaluate(
        source_type="markdown_file",
        filename="review-notes.md",
        text=text,
    )

    assert result.outcome == "candidate_document_required"
    assert "markdown_complex_structure" in result.flags
    assert "document_block_degraded" in result.flags


def test_deterministic_markdown_table_is_stable_document_ready() -> None:
    """L1: 表头分隔行齐全、行列一致的 GFM table → stable_document_ready。"""
    text = f"""
{_english_paragraph()}

| City | Cost | Status |
| --- | --- | --- |
| A | 10 | Proposed |
| B | 12 | Reviewed |
""".strip()
    result = _evaluate(
        source_type="markdown_file",
        filename="report.md",
        text=text,
    )

    assert result.outcome == "stable_document_ready"
    assert "table_structure_uncertain" not in result.flags
    assert "markdown_complex_structure" not in result.flags


def test_structure_uncertain_markdown_table_requires_candidate_document() -> None:
    """L1: 行列不一致（parser 会丢/补单元格）的 table → candidate (content_check)。"""
    text = f"""
{_english_paragraph()}

| City | Cost | Status |
| --- | --- | --- |
| A | 10 | Proposed |
| B | 12 | Reviewed | Extra |
""".strip()
    result = _evaluate(
        source_type="markdown_file",
        filename="report.md",
        text=text,
    )

    assert result.outcome == "candidate_document_required"
    assert "markdown_complex_structure" in result.flags
    assert "table_structure_uncertain" in result.flags
    adaptations = {record.code: record.classification for record in result.adaptations}
    assert adaptations["table_structure_uncertain"] == "content_check"


def test_markdown_image_requires_candidate_document() -> None:
    text = f"""
{_english_paragraph()}

![Map of the site](https://example.com/site-map.png)
""".strip()
    result = _evaluate(
        source_type="markdown_file",
        filename="report.md",
        text=text,
    )

    assert result.outcome == "candidate_document_required"
    assert "markdown_complex_structure" in result.flags
    assert "image_ocr_uncertain" in result.flags


def test_markdown_footnote_requires_candidate_document() -> None:
    text = f"""
{_english_paragraph()}

The committee cited an archival source for the final timeline.[^1]

[^1]: The archival note contains extra context that must stay attached to the passage.
""".strip()
    result = _evaluate(
        source_type="markdown_file",
        filename="report.md",
        text=text,
    )

    assert result.outcome == "candidate_document_required"
    assert "markdown_complex_structure" in result.flags
    assert "footnote_or_caption_merged" in result.flags


def test_raw_html_or_math_requires_candidate_document() -> None:
    text = f"""
{_english_paragraph()}

<aside class="note">Rendered callout from a source page.</aside>

The appendix also includes the expression $E = mc^2$ in the original source.
""".strip()
    result = _evaluate(
        source_type="markdown_file",
        filename="report.md",
        text=text,
    )

    assert result.outcome == "candidate_document_required"
    assert "markdown_complex_structure" in result.flags
    assert "document_block_degraded" in result.flags


def test_safe_aside_alone_is_adaptation_notice_not_candidate() -> None:
    """L1: 安全 <aside> 清洗后继续（adaptation_notice），不再触发 candidate。"""
    text = f"""
{_english_paragraph()}

<aside class="note">Rendered callout from a source page.</aside>

{_english_paragraph()}
""".strip()
    result = _evaluate(
        source_type="markdown_file",
        filename="report.md",
        text=text,
    )

    assert result.outcome == "stable_document_ready"
    assert "document_block_degraded" not in result.flags
    assert "markdown_complex_structure" not in result.flags
    adaptations = {record.code: record.classification for record in result.adaptations}
    assert adaptations["raw_html_block"] == "adaptation_notice"


def test_ocr_low_confidence_metadata_requires_candidate_document() -> None:
    result = _evaluate(
        source_type="ocr_text",
        text=_english_paragraph(multiplier=2),
        source_metadata={"ocr_confidence": 0.62},
    )

    assert result.outcome == "candidate_document_required"
    assert "ocr_low_confidence" in result.flags


def test_noisy_ocr_text_requires_candidate_document() -> None:
    text = """
The committee ex-
amined the report
before the second
meeting started
and compared every
budget line with
the earlier draft
to confirm the
main schedule and
reduce confusion
for local readers
who needed a clear
summary afterward.
The planning team
also reviewed the
public comments
from nearby blocks
and checked whether
the revised notice
still matched the
older timeline for
permits, transport,
and temporary work
around the library
before publishing
the final update.
""".strip()
    result = _evaluate(
        source_type="ocr_text",
        text=text,
        source_metadata={"ocr_confidence": 0.97},
    )

    assert result.outcome == "candidate_document_required"
    assert "ocr_low_confidence" in result.flags


def test_long_text_requires_candidate_document() -> None:
    text = " ".join(["reading"] * 8105)
    result = _evaluate(text=text)

    assert result.outcome == "candidate_document_required"
    assert "too_long_requires_envelope" in result.flags


def test_pydantic_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        InputSuitabilityRequest.model_validate(
            {
                "source_type": "pasted_text",
                "text": _english_paragraph(),
                "source_metadata": {},
                "unexpected_field": True,
            }
        )


def test_url_text_defaults_to_candidate_without_explicit_high_confidence() -> None:
    result = _evaluate(
        source_type="url_text",
        text=_english_paragraph(multiplier=2),
        source_metadata={"extraction_confidence": 0.90},
    )

    assert result.outcome == "candidate_document_required"


def test_high_confidence_simple_pdf_text_can_be_stable() -> None:
    result = evaluate_input_suitability(
        InputSuitabilityRequest(
            source_type="pdf_text",
            text=_english_paragraph(multiplier=2),
            source_metadata={"extraction_confidence": 0.99},
        )
    )

    assert result.outcome == "stable_document_ready"
