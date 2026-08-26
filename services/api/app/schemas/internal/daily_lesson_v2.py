"""Daily Reader teaching-v2 stage DTOs (transport hard boundary).

Mirrors the frozen P-4E stage contract of the evals real-run harness
(``evals/scripts/run_daily_reader_teaching_prototype.py``): collection
bounds, UnitId shape, required fields and the title contract are enforced
as pydantic validation, so a malformed stage output burns an in-call
output retry (DTO hard boundary, defense line 1) instead of shipping an
unresolvable artifact to the hard gates.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.services.daily_reader.teaching.contract import (
    TRANSFER_CONTENT_REQUIREMENT_VALUES,
    TRANSFER_TASK_KIND_BY_ARTICLE_TYPE,
)
from app.services.daily_reader.teaching.prototype import make_review_evidence
from app.services.daily_reader.teaching.schema import (
    CHECKPOINT_SKILLS,
    DIFFICULTIES,
    TRANSFER_TASK_KINDS,
)

# Mirrors teaching/schema.py UNIT_ID_RE exactly: a malformed anchor id ("14")
# becomes an output-validation failure that burns an in-call retry instead
# of shipping an unresolvable anchor to the hard gates.
UnitId = Annotated[str, Field(pattern=r"^u\d{2,3}$")]


class CheckpointDraft(BaseModel):
    skill: Literal[*CHECKPOINT_SKILLS]
    prompt: str
    prompt_subject: str
    reference_answer: str
    reference_answer_subject: str
    evidence_paragraph_ids: list[UnitId]
    answer_evidence_paragraph_ids: list[UnitId]


class TransferTaskDraft(BaseModel):
    task_kind: Literal[*TRANSFER_TASK_KINDS]
    content_requirement: Literal[*TRANSFER_CONTENT_REQUIREMENT_VALUES]
    required_language_target_expressions: list[str]
    prompt: str = ""
    scaffold: str = ""
    reference_points: list[str] = []


class StructureNodeDraft(BaseModel):
    label: str
    function: str
    paragraph_ids: list[UnitId]


class BlueprintDraft(BaseModel):
    article_type: Literal[*TRANSFER_TASK_KIND_BY_ARTICLE_TYPE]
    effective_difficulty: Literal[*DIFFICULTIES]
    # P-5A title contract (刊物级中文标题, 一句话副题, 2-4 个全中文标签);
    # length bounds stay with the gates.
    title_zh: str = Field(min_length=1)
    subtitle_zh: str = Field(min_length=1)
    tags_zh: list[str] = Field(min_length=2, max_length=4)
    reading_mission: str
    reading_mission_stance: Literal["neutral"]
    # Collection bounds mirror gates.py exactly (_BOUNDS + 1-2 / 2-6):
    # over-generation is an output-validation failure burning an in-call
    # output retry instead of a guaranteed hard-gate failure after the run.
    learning_objectives: list[str] = Field(min_length=1, max_length=2)
    structure_map: list[StructureNodeDraft] = Field(min_length=2, max_length=6)
    selected_paragraph_ids: list[UnitId]
    comprehension_checkpoints: list[CheckpointDraft] = Field(min_length=2, max_length=4)
    transfer_task: TransferTaskDraft


class LanguageTargetDraft(BaseModel):
    expression: str
    paragraph_id: UnitId
    target_kind: str
    teaching_purpose: str
    # P-1 §3.4 minimum semantic fields: omission or blank values are output-
    # validation failures and burn an in-call output retry, never silent "".
    meaning_zh: str = Field(pattern=r"\S")
    usage_note: str = Field(pattern=r"\S")
    reusable_pattern: str = Field(pattern=r"\S")


class SentenceMapDraft(BaseModel):
    sentence: str
    paragraph_id: UnitId
    translation: str = ""
    complexity_kind: Literal["complex_syntax", "argument_structure"] | None = None
    teaching_purpose: str = ""


class LanguageSupportDraft(BaseModel):
    language_targets: list[LanguageTargetDraft] = Field(min_length=3, max_length=5)
    sentence_maps: list[SentenceMapDraft] = Field(min_length=1, max_length=2)
    high_difficulty_unit_ids: list[str]


class TranslationItemDraft(BaseModel):
    paragraph_id: str
    translation: str


class TranslationDraft(BaseModel):
    translations: list[TranslationItemDraft]


class ReviewIssueDraft(BaseModel):
    contract: str
    field: str
    problem: str


class ContractResultDraft(BaseModel):
    contract: str
    passed: bool
    rationale: str


class SemanticReviewDraft(BaseModel):
    verdict: Literal["PASS", "FAIL"]
    issues: list[ReviewIssueDraft]
    remaining_issues: list[str]
    contract_results: list[ContractResultDraft]
    reviewed_at_stage: Literal["before_refinement"]
    refinement_requested: bool

    @model_validator(mode="after")
    def _canonical_review_contract(self) -> SemanticReviewDraft:
        # Delegate the whole PASS/FAIL cross-field contract to the canonical
        # authority so violations become output-validation failures and burn a
        # PydanticAI output retry inside the same logical call.
        try:
            make_review_evidence(**self.model_dump())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"semantic review violates canonical contract: {exc}") from exc
        return self


class RefinementDraft(BaseModel):
    refinement_patch: dict[str, Any]
    rechecked_contract_results: list[ContractResultDraft]
    remaining_issues: list[ReviewIssueDraft]
