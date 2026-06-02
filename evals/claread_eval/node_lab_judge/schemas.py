from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


JudgeNodeName = Literal["grammar", "vocabulary", "translation"]
JudgeStrategy = Literal[
    "grammar_item_review",
    "vocabulary_item_review",
    "translation_output_review",
]
JudgeMethod = Literal["rubric_only", "rubric_plus_pairwise", "anti_template_probe", "raw"]
JudgeOutputSchemaKind = Literal[
    "grammar_item_scoring",
    "vocabulary_item_scoring",
    "translation_output_scoring",
    "pairwise_review",
    "probe_appendix",
]
PriorityProfile = Literal["structure_first", "collocation_first", "default"]


class ResolvedJudgeContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_intent: str
    user_profile: str
    help_style: str


class RubricCriterionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    assertion: str
    pass_when: str
    fail_when: str


class ItemTypeRubricSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criteria: list[RubricCriterionSpec] = Field(default_factory=list)


class StrategyRubricSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: JudgeStrategy
    item_types: dict[str, ItemTypeRubricSpec] = Field(default_factory=dict)
    output_level: ItemTypeRubricSpec | None = None


class ProbeQuestionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    prompt: str


class PacketPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority_profile: PriorityProfile | None = None
    max_items_per_side: int | None = None
    max_sentences_per_side: int | None = None
    include_raw_appendix: bool = False
    priority_profile_by_variant: dict[str, PriorityProfile] = Field(default_factory=dict)
    sentence_sampling: Literal["head_and_tail"] | None = None
    sampling_mode: Literal["broad_probe_sample"] | None = None


class PresetPairwiseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    question: str | None = None


class JudgePreset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset_id: str
    title: str
    node_name: JudgeNodeName
    strategy: JudgeStrategy
    method: JudgeMethod
    ui_label: str
    packet_policy: PacketPolicy
    rubric_bundle: dict[str, list[str]] = Field(default_factory=dict)
    pairwise: PresetPairwiseSpec | None = None
    output_mode: Literal["probe_appendix"] | None = None
    probe_appendix: dict[str, list[ProbeQuestionSpec]] | None = None


class JudgeOutputSchemaDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: JudgeStrategy | None = None
    top_level: Literal["rubric_scoring_result", "pairwise_result", "probe_appendix_result"]


class NodeLabJudgeCatalog(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    version: str
    contexts: dict[str, dict[str, ResolvedJudgeContext]]
    rubrics: dict[str, StrategyRubricSpec]
    presets: dict[str, JudgePreset]
    output_schemas: dict[JudgeOutputSchemaKind, JudgeOutputSchemaDefinition]
    root: Path


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    item_type: str
    sentence_id: str | None = None
    label: str | None = None
    source_excerpt: str | None = None
    sentence_text: str | None = None
    explanation: str | None = None
    anchor_texts: list[str] = Field(default_factory=list)
    raw_item: dict[str, Any] = Field(default_factory=dict)


class TranslationOutputUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sentence_id: str | None = None
    source_sentence: str | None = None
    translation: str | None = None
    translation_strategy_hint: str | None = None


class RubricPacketSide(BaseModel):
    model_config = ConfigDict(extra="forbid")

    participant: Literal["baseline", "candidate"]
    item_count_by_type: dict[str, int] = Field(default_factory=dict)
    items: list[EvidenceItem] = Field(default_factory=list)
    output_units: list[TranslationOutputUnit] = Field(default_factory=list)


class RubricPacket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_name: JudgeNodeName
    strategy: JudgeStrategy
    method: JudgeMethod
    reading_goal: str
    reading_variant: str
    context: ResolvedJudgeContext
    rubric_bundle: dict[str, list[RubricCriterionSpec]] = Field(default_factory=dict)
    baseline: RubricPacketSide
    candidate: RubricPacketSide
    compare_summary: dict[str, Any] = Field(default_factory=dict)


class PairwiseFailedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    participant: Literal["baseline", "candidate"]
    item_id: str
    item_type: str
    criterion_id: str
    reason: str
    evidence: str | None = None


class PairwiseSelectedAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_type: str
    label: str | None = None
    content: str | None = None
    anchor_text_preview: str | None = None


class PairwiseSentenceUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sentence_id: str
    source_sentence: str
    baseline_selected_annotations: list[PairwiseSelectedAnnotation] = Field(default_factory=list)
    candidate_selected_annotations: list[PairwiseSelectedAnnotation] = Field(default_factory=list)
    rubric_watchouts: list[str] = Field(default_factory=list)


class PairwiseTranslationUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sentence_id: str
    source_sentence: str
    baseline_translation: str | None = None
    candidate_translation: str | None = None
    rubric_watchouts: list[str] = Field(default_factory=list)


class PairwisePacket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_name: JudgeNodeName
    strategy: JudgeStrategy
    method: JudgeMethod
    reading_goal: str
    reading_variant: str
    context: ResolvedJudgeContext
    aggregate: dict[str, Any] = Field(default_factory=dict)
    watchouts: list[str] = Field(default_factory=list)
    failed_items: list[PairwiseFailedItem] = Field(default_factory=list)
    sentence_units: list[PairwiseSentenceUnit] = Field(default_factory=list)
    translation_units: list[PairwiseTranslationUnit] = Field(default_factory=list)
    question: str


class ProbePacket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_name: Literal["grammar"]
    strategy: Literal["grammar_item_review"]
    method: Literal["anti_template_probe"]
    reading_goal: str
    reading_variant: str
    context: ResolvedJudgeContext
    baseline_items: list[EvidenceItem] = Field(default_factory=list)
    candidate_items: list[EvidenceItem] = Field(default_factory=list)
    questions: list[ProbeQuestionSpec] = Field(default_factory=list)


class JudgeCriterionScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_id: str
    score: Literal[0, 1]
    reason: str
    evidence: str | None = None


class JudgeItemSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: int = Field(ge=0, default=0)
    failed: int = Field(ge=0, default=0)


class JudgeAggregate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_count: int | None = Field(default=None, ge=0)
    criteria_count: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)


class NodeLabJudgeItemResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    item_type: str
    sentence_id: str | None = None
    label: str | None = None
    source_excerpt: str | None = None
    criteria: list[JudgeCriterionScore] = Field(default_factory=list)
    item_summary: JudgeItemSummary


class NodeLabJudgeSideResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[NodeLabJudgeItemResult] = Field(default_factory=list)
    output_level_scores: list[JudgeCriterionScore] = Field(default_factory=list)
    aggregate: JudgeAggregate


class NodeLabRubricScoringResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: JudgeStrategy
    method: JudgeMethod
    baseline: NodeLabJudgeSideResult
    candidate: NodeLabJudgeSideResult
    meta: dict[str, Any] = Field(default_factory=dict)


class NodeLabPairwiseReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_side: Literal["baseline", "candidate", "mixed", "inconclusive"]
    overall_judgment: str
    baseline_strengths: list[str] = Field(default_factory=list)
    candidate_strengths: list[str] = Field(default_factory=list)
    baseline_risks: list[str] = Field(default_factory=list)
    candidate_risks: list[str] = Field(default_factory=list)
    manual_check_points: list[str] = Field(default_factory=list)


class NodeLabPairwiseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: JudgeStrategy
    method: JudgeMethod
    pairwise_review: NodeLabPairwiseReview
    meta: dict[str, Any] = Field(default_factory=dict)


class NodeLabProbeQuestionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    detected: bool
    description: str
    evidence: list[str] = Field(default_factory=list)


class NodeLabProbeAppendixResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    probe_type: str
    questions: list[NodeLabProbeQuestionResult] = Field(default_factory=list)
    summary: str | None = None
