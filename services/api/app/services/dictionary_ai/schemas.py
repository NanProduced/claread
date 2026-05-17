from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.services.dictionary.schemas import (
    DictionaryExample,
    DictionaryMeaning,
    DictionaryPhrase,
)

DictionaryAISource = Literal["reader_click", "selection", "manual_search"]
DictionaryAIConfidence = Literal["high", "medium", "low"]
DictionaryAIClassification = Literal[
    "valid_word",
    "slang_or_informal",
    "proper_noun",
    "domain_term",
    "variant_or_inflection",
    "possible_typo_or_ocr",
    "unrecognized_noise",
]


class DictionaryAIRequestBase(BaseModel):
    mode: str
    query: str = Field(min_length=1, max_length=100)
    query_type: Literal["word", "phrase"]
    context_sentence: str = Field(min_length=1, max_length=5000)
    occurrence: int | None = Field(default=None, ge=1)
    record_id: UUID | None = None
    sentence_id: str | None = None
    source: DictionaryAISource | None = None


class DictionaryAIContextExplainRequest(DictionaryAIRequestBase):
    mode: Literal["context_explain"] = "context_explain"
    entry_id: int = Field(ge=1)


class DictionaryAIMissingFallbackRequest(DictionaryAIRequestBase):
    mode: Literal["missing_fallback"] = "missing_fallback"


DictionaryAIRequest = Annotated[
    DictionaryAIContextExplainRequest | DictionaryAIMissingFallbackRequest,
    Field(discriminator="mode"),
]


class DictionaryAIContextExplainResponse(BaseModel):
    mode: Literal["context_explain"] = "context_explain"
    query: str
    summary: str = Field(min_length=1)
    best_fit_sense: str | None = None
    why_here: str | None = None
    cue: str | None = None
    translation: str | None = None
    contrast: str | None = None
    learning_tip: str | None = None
    confidence: DictionaryAIConfidence | None = None


class AIDictionaryEntryPayload(BaseModel):
    word: str = Field(min_length=1)
    base_word: str | None = None
    phonetic: str | None = None
    meanings: list[DictionaryMeaning] = Field(default_factory=list)
    examples: list[DictionaryExample] = Field(default_factory=list)
    phrases: list[DictionaryPhrase] = Field(default_factory=list)
    entry_kind: Literal["entry", "fragment"] | None = None
    exchange: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class DictionaryAIMissingFallbackBase(BaseModel):
    mode: Literal["missing_fallback"] = "missing_fallback"
    query: str
    classification: DictionaryAIClassification
    summary: str = Field(min_length=1)
    confidence: DictionaryAIConfidence | None = None
    verified: Literal[False] = False
    source: Literal["ai_generated"] = "ai_generated"


class DictionaryAIMissingFallbackEntryResponse(DictionaryAIMissingFallbackBase):
    result_kind: Literal["ai_entry"] = "ai_entry"
    entry: AIDictionaryEntryPayload
    suggested_query: list[str] = Field(default_factory=list)


class DictionaryAIMissingFallbackUnresolvedResponse(DictionaryAIMissingFallbackBase):
    result_kind: Literal["ai_unresolved"] = "ai_unresolved"
    reason: str | None = None
    suggested_query: list[str] = Field(default_factory=list)


DictionaryAIResponse = (
    DictionaryAIContextExplainResponse
    | DictionaryAIMissingFallbackEntryResponse
    | DictionaryAIMissingFallbackUnresolvedResponse
)
