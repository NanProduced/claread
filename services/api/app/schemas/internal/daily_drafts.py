"""Daily Reader internal schemas for agent outputs and workflow state.

Redesigned per redesign-tracker.tmp.md:
- Paragraph-level reading notes + translations (replaces old footer_analysis)
- Close reading takeaways (replaces old full_article_analysis)
- Per-paragraph highlight generation with coverage tracking
- No backward compatibility with old schema — old data should be deleted and re-run
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DailyVocabHighlight(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    anchor: str = Field(description="The exact text from the original paragraph")
    start: int = Field(description="0-based start index in paragraph text")
    end: int = Field(description="0-based end index in paragraph text")
    type: Literal["vocab_highlight", "phrase_gloss", "context_gloss"] = Field(
        description="Highlight type"
    )
    gloss: str = Field(description="Chinese gloss/translation")


class DailyParagraphDraft(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    paragraph_id: str = Field(description="Paragraph ID like p_0, p_1")
    text: str = Field(description="Paragraph text (normalized)")
    highlights: list[DailyVocabHighlight] = Field(default_factory=list)


class DailyVocabDraft(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    paragraphs: list[DailyParagraphDraft] = Field(default_factory=list)


class DailyHighlightDetail(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: str = Field(description="Highlight ID like hl_p03_01")
    type: Literal["vocab_highlight", "phrase_gloss", "context_gloss"] = Field(
        description="Highlight type"
    )
    text: str = Field(description="Highlighted text")
    gloss: str = Field(description="Chinese gloss")
    paragraph_id: str = Field(description="Paragraph ID")
    start: int = Field(description="0-based start index")
    end: int = Field(description="0-based end index")
    detail: dict | None = Field(default=None, description="Extra detail: phonetic, pos, context_explanation")


class DailyHighlightsDraft(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    highlights: list[DailyHighlightDetail] = Field(default_factory=list)


class ParagraphReadingNote(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    paragraph_id: str = Field(description="Paragraph ID like p_0, p_1")
    focus_question: str = Field(
        description="A short question to guide reading of this paragraph (Chinese, 1 sentence)"
    )
    micro_summary: str = Field(
        description="A 1-2 sentence confirmation of understanding after reading (Chinese)"
    )
    translation: str = Field(
        description="Full Chinese translation of this paragraph"
    )


class ParagraphNotesDraft(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    article_summary: str = Field(
        description="One-sentence article summary in Chinese, shown as pre-reading guide"
    )
    reading_focus: list[str] = Field(
        default_factory=list,
        description="1-2 questions for the reader to keep in mind while reading the whole article (Chinese)"
    )
    notes: list[ParagraphReadingNote] = Field(default_factory=list)


class ExpressionPoint(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    expression: str = Field(description="The English expression/phrase")
    paragraph_id: str = Field(description="Paragraph ID where this expression appears")
    gloss: str = Field(description="Chinese gloss/translation")
    context_sentence: str = Field(description="The sentence from the article containing this expression")
    usage_note: str = Field(description="How to use this expression, collocations, common patterns (Chinese)")


class SentenceNote(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    sentence: str = Field(description="The original English sentence")
    paragraph_id: str = Field(description="Paragraph ID where this sentence appears")
    translation: str = Field(description="Chinese translation of the sentence")
    breakdown: str = Field(description="Structural breakdown explaining why it's hard to parse (Chinese)")
    takeaway: str = Field(description="What the reader can learn from this sentence pattern (Chinese)")


class WritingMove(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    anchor: str = Field(description="The text fragment that demonstrates this writing move")
    paragraph_id: str = Field(description="Paragraph ID where this anchor appears")
    move_type: str = Field(
        description="Type of writing technique: e.g. metaphor, concession, parallelism, hedging, etc."
    )
    explanation: str = Field(description="Why this move is effective and how it works (Chinese)")
    reusable_pattern: str | None = Field(
        default=None,
        description="A reusable pattern the reader can apply in their own writing (English + Chinese)"
    )


class CloseReadingTakeaways(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    article_takeaway: str = Field(
        description="One-sentence takeaway: what the reader should remember from this article (Chinese)"
    )
    key_expressions: list[ExpressionPoint] = Field(
        default_factory=list,
        description="3-5 key expressions worth learning"
    )
    sentence_notes: list[SentenceNote] = Field(
        default_factory=list,
        description="1-2 difficult sentence analyses"
    )
    writing_moves: list[WritingMove] = Field(
        ...,
        min_length=1,
        max_length=2,
        description="1-2 writing technique observations"
    )
    discussion_questions: list[str] = Field(
        ...,
        min_length=2,
        max_length=2,
        description="2 discussion questions in English"
    )


class QualityIssue(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    dimension: str
    severity: str = Field(description="minor or major")
    description: str
    suggestion: str


class DailyReviewDraft(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    passed: bool
    overall_score: float
    issues: list[QualityIssue] = Field(default_factory=list)


class DailyRefinementDraft(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    abort: bool = False
    refined_highlights: list[DailyHighlightDetail] | None = None
    refined_paragraph_notes: ParagraphNotesDraft | None = None
    refined_takeaways: CloseReadingTakeaways | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_json_strings(cls, data: dict) -> dict:
        if not isinstance(data, dict):
            return data
        for key in ("refined_highlights", "refined_paragraph_notes", "refined_takeaways"):
            val = data.get(key)
            if isinstance(val, str):
                try:
                    data[key] = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    data[key] = None
        return data
