from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.reader_input_adapter import InputSuitabilityRequest
from app.services.reader_orchestration.input_suitability_gate import (
    InputSuitabilityGate,
    evaluate_input_suitability,
)


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


def test_code_heavy_input_is_rejected() -> None:
    text = """
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
""".strip()
    result = _evaluate(text=text)

    assert result.outcome == "input_rejected_or_action_required"
    assert "code_dominant" in result.flags


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


def test_markdown_table_requires_candidate_document() -> None:
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

    assert result.outcome == "candidate_document_required"
    assert "markdown_complex_structure" in result.flags
    assert "table_structure_uncertain" in result.flags


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
