from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# D5-V3 vocabulary worker constants mirrored here so the eval harness can run
# without importing from services/api. Keep values in sync with
# services/api/app/services/reader_orchestration/vocabulary_worker.py.
MAX_VOCABULARY_ITEMS = 5
MAX_VOCABULARY_CANDIDATE_TEXT_LENGTH = 160
MAX_VOCABULARY_CANDIDATE_NOTE_LENGTH = 240
MAX_VOCABULARY_DIAGNOSTIC_ITEMS = 8
MAX_VOCABULARY_DIAGNOSTIC_TEXT_LENGTH = 80

DifficultyBand = Literal["K1", "K2", "AWL", "off-list", "mixed"]

ALLOWED_REASON_CODES: tuple[str, ...] = (
    "anchor_segment_unknown",
    "selected_text_not_found",
    "selected_text_ambiguous",
    "selected_text_outside_segment",
    "selected_text_slice_mismatch",
    "span_conflict_higher_priority_kept",
    "candidate_limit_exceeded",
    "resolved_item_invalid",
    "boundary_low_fallback_window",
)

ALLOWED_ITEM_TYPES: tuple[str, ...] = (
    "vocab_highlight",
    "phrase_gloss",
    "context_gloss",
)

ALLOWED_PHRASE_TYPES: tuple[str, ...] = (
    "collocation",
    "phrasal_verb",
    "idiom",
    "proper_noun",
    "compound",
    "other",
)


class VocabularyAnchorSegmentFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor_segment_id: str = Field(min_length=1)
    sentence_id: str | None = None
    segment_type: Literal["sentence", "clause", "fallback_window"] = "sentence"
    unit_start_utf16: int = Field(ge=0)
    unit_end_utf16: int = Field(gt=0)
    text: str = Field(min_length=1)
    boundary_quality: Literal["normal", "low"] = "normal"

    @model_validator(mode="after")
    def _check_offsets(self) -> VocabularyAnchorSegmentFixture:
        if self.unit_end_utf16 <= self.unit_start_utf16:
            raise ValueError("unit_end_utf16 must be greater than unit_start_utf16")
        return self


class VocabularyGoldItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_type: Literal["vocab_highlight", "phrase_gloss", "context_gloss"]
    anchor_segment_id: str = Field(min_length=1)
    selected_text: str = Field(min_length=1)
    headword: str | None = None
    phrase: str | None = None
    phrase_type: str | None = None
    display: str | None = None
    gloss: str | None = None
    brief_explanation: str | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _check_phrase_type(self) -> VocabularyGoldItem:
        if (
            self.item_type == "phrase_gloss"
            and self.phrase_type is not None
            and self.phrase_type not in ALLOWED_PHRASE_TYPES
        ):
            raise ValueError(
                f"phrase_type={self.phrase_type} not in {ALLOWED_PHRASE_TYPES}"
            )
        return self


class VocabularyExpectedDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_item_count: int | None = Field(default=None, ge=0)
    resolved_item_count: int | None = Field(default=None, ge=0)
    skipped_item_count: int | None = Field(default=None, ge=0)
    skipped_item_count_at_least: int | None = Field(default=None, ge=0)
    skipped_reason_codes: list[str] = Field(default_factory=list)
    skipped_reason_codes_at_least: list[str] = Field(default_factory=list)
    skipped_items_truncated_count: int | None = Field(default=None, ge=0)


class VocabularyResolvedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_type: Literal["vocab_highlight", "phrase_gloss", "context_gloss"]
    anchor_segment_id: str = Field(min_length=1)
    unit_start_utf16: int = Field(ge=0)
    unit_end_utf16: int = Field(gt=0)
    selected_text: str = Field(min_length=1)
    text_hash: str = Field(pattern=r"^[0-9a-f]{8}$")


class VocabularyResolvedOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    items: list[VocabularyResolvedCandidate] = Field(default_factory=list)


class VocabularyExecutionSnapshot(BaseModel):
    """Artifact produced by the deterministic eval harness.

    Mirrors the shape of `VocabularyExecutionResult` from the worker so the
    graders can run without importing services/api.
    """

    model_config = ConfigDict(extra="forbid")

    output: VocabularyResolvedOutput
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    fail_closed: bool = False
    fail_closed_reason: str | None = None


class VocabularyEvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: str = Field(min_length=1)
    description: str = Field(default="")
    unit_id: str = Field(min_length=1)
    unit_text: str = Field(min_length=1)
    anchor_segments: list[VocabularyAnchorSegmentFixture] = Field(min_length=1)
    gold_items: list[VocabularyGoldItem] = Field(default_factory=list)
    expected_diagnostics: VocabularyExpectedDiagnostics = Field(
        default_factory=VocabularyExpectedDiagnostics
    )
    unicode_pitfall: (
        Literal[
            "smart_quote",
            "em_dash",
            "accented_nfd",
            "nbsp",
            "surrogate_pair",
            "trailing_punctuation",
            None,
        ]
    ) = None
    difficulty_band: DifficultyBand = "mixed"
    tags: list[str] = Field(default_factory=list)
    execution: VocabularyExecutionSnapshot | None = None

    @model_validator(mode="after")
    def _check_anchor_alignment(self) -> VocabularyEvalCase:
        for item in self.gold_items:
            if not any(
                seg.anchor_segment_id == item.anchor_segment_id
                for seg in self.anchor_segments
            ):
                raise ValueError(
                    f"gold item {item.item_type} references unknown "
                    f"anchor_segment_id={item.anchor_segment_id}"
                )
        return self


VocabularySeverity = Literal["hard", "soft", "info"]
VocabularyVerdict = Literal["pass", "fail", "skip"]


class VocabularyGraderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grader_name: str
    case_id: str
    verdict: VocabularyVerdict
    severity: VocabularySeverity = "hard"
    metric: str = ""
    value: Any = None
    expected: Any = None
    evidence: str = ""


class VocabularyEvalDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: str = Field(min_length=1)
    description: str = Field(default="")
    case_globs: list[str] = Field(default_factory=lambda: ["cases/*.json"])
    tags: list[str] = Field(default_factory=list)


CandidateItemCount = Annotated[int, Field(ge=0, le=MAX_VOCABULARY_ITEMS)]


class VocabularyCaseSummary(BaseModel):
    """Per-case summary produced by the local deterministic runner."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    case_verdict: VocabularyVerdict
    hard_failures: int = 0
    soft_failures: int = 0
    skip_count: int = 0
    grader_results: list[VocabularyGraderResult] = Field(default_factory=list)


class VocabularySeedReport(BaseModel):
    """Aggregate report produced by `run_vocabulary_seed`.

    Intentionally distinct from `EvalReport` so vocabulary seed runs do not
    pollute the article-analysis contract.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_dir: str = Field(min_length=1)
    grader_names: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    total_cases: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    hard_failure_case_ids: list[str] = Field(default_factory=list)
    soft_failure_case_ids: list[str] = Field(default_factory=list)
    verdict_counts: dict[str, int] = Field(default_factory=dict)
    grader_pass_counts: dict[str, int] = Field(default_factory=dict)
    case_summaries: list[VocabularyCaseSummary] = Field(default_factory=list)


class VocabularyRunConfig(BaseModel):
    """Runner config for vocabulary seed runs.

    Mirrors the shape of `EvalRunConfig` but is intentionally separate to
    keep the article-analysis contract untouched.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    dataset_dir: str = Field(min_length=1)
    prompt_version: str | None = None
    workflow_version: str = "d5-v3-vocabulary-worker"
    note: str | None = None
ResolvedItemCount = Annotated[int, Field(ge=0, le=MAX_VOCABULARY_ITEMS)]