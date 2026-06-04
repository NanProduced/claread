from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.llm.routes import ModelRoute
from app.llm.types import ModelSelection
from app.schemas.analysis import AnyRenderSceneModel, SourceType
from app.schemas.internal.analysis import ReadingGoal, ReadingVariant
from app.services.analysis.prompting.node_lab_runtime import (
    FewShotOverride,
    InstructionOverride,
    NodeLabExampleEntry,
    NodeLabRuntimeOverride,
    PolicyOverride,
)
from app.services.analysis.prompting.runtime_context import PromptRuntimeOverride

EVAL_ADAPTER_SCHEMA_VERSION = "article-analysis-eval-v1"
NODE_PROBE_SCHEMA_VERSION = "article-analysis-node-probe-v1"
NODE_LAB_SCHEMA_VERSION = "article-analysis-node-lab-v1"
NODE_LAB_JUDGE_SCHEMA_VERSION = "article-analysis-node-lab-judge-v1"

EvalStatus = Literal["succeeded", "failed", "timeout"]
RagMode = Literal["off", "baseline", "rag", "rag_fallback", "settings"]
TraceScope = Literal["off", "isolated", "inherit"]
NodeProbeName = Literal["grammar", "vocabulary", "translation"]
WorkflowLabPromptAgentName = Literal["vocabulary", "grammar", "translation", "repair"]
NodeLabWorkspace = Literal["single_run", "baseline_compare"]
JudgeStrategy = Literal["grammar_item_review", "vocabulary_item_review", "translation_output_review"]
JudgeMethod = Literal["rubric_only", "rubric_plus_pairwise", "anti_template_probe"]
JudgeOutputMode = Literal["rubric_scoring", "pairwise", "probe_appendix"]
JudgeOutputSchemaKind = Literal[
    "grammar_item_scoring",
    "vocabulary_item_scoring",
    "translation_output_scoring",
    "pairwise_review",
    "probe_appendix",
]


class RequestSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str | None = None
    run_id: str | None = None
    request_id: str
    source_text_hash: str
    source_char_count: int
    reading_goal: ReadingGoal
    reading_variant: ReadingVariant
    source_type: SourceType
    extended: bool
    rag_mode: RagMode
    trace_scope: TraceScope


class EvalError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class WorkflowIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_name: str
    workflow_version: str
    topology_mode: Literal["learning", "academic", "unknown"] = "unknown"


class SchemaIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    render_schema_version: str | None = None
    topology_mode: Literal["learning", "academic", "unknown"] = "unknown"


class PromptIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_version: str
    prompt_snapshot_hash: str | None = None
    prompt_variant_id: str | None = None


class ModelIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: ModelRoute
    profile_name: str | None = None
    provider: str | None = None
    model_name: str | None = None
    fallback_profiles: list[str] = Field(default_factory=list)
    model_settings: dict[str, Any] = Field(default_factory=dict)


class ModelProfileSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_name: str
    provider: str
    model_name: str
    annotation_route_default: bool = False
    default_profile: bool = False


class NodeLabBaselineConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_name: NodeProbeName = "grammar"
    reading_goal: ReadingGoal = "daily_reading"
    reading_variant: ReadingVariant = "intermediate_reading"

    @model_validator(mode="after")
    def _validate_node_lab_goal(self) -> NodeLabBaselineConfigRequest:
        if self.reading_goal == "academic":
            raise ValueError(
                "node_lab v1 only supports daily_reading and exam; academic should use a dedicated academic lab/workflow"
            )
        return self


class NodeLabBaselineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_name: NodeProbeName
    reading_goal: ReadingGoal
    reading_variant: ReadingVariant
    prompt_version: str
    prompt_profile: str
    policy_focus: str
    agent_instructions: str
    policy_lines: list[str] = Field(default_factory=list)
    baseline_examples: list[NodeLabExampleEntry] = Field(default_factory=list)
    baseline_model_profile: str | None = None


class WorkflowLabBaselineBundleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reading_goal: ReadingGoal = "daily_reading"
    reading_variant: ReadingVariant = "intermediate_reading"
    few_shot_mode: Literal["off", "baseline", "variant", "settings"] = "baseline"
    sample_sentences: list[dict[str, Any]] | None = None

    @model_validator(mode="after")
    def _validate_learning_only(self) -> WorkflowLabBaselineBundleRequest:
        if self.reading_goal == "academic":
            raise ValueError("workflow_lab v1 only supports learning topology")
        return self


class WorkflowLabPromptLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_name: WorkflowLabPromptAgentName
    label: str
    instructions: str
    policy_name: str | None = None
    policy_focus: str | None = None
    policy_variant: str | None = None
    policy_lines: list[str] = Field(default_factory=list)
    examples: list[NodeLabExampleEntry] = Field(default_factory=list)
    prompt_template: str = ""


class WorkflowLabBaselineBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["workflow-prompt-bundle-v1"] = "workflow-prompt-bundle-v1"
    target: Literal["article_analysis"] = "article_analysis"
    reading_goal: ReadingGoal
    reading_variant: ReadingVariant
    prompt_version: str
    prompt_profile: str
    topology_mode: Literal["learning"]
    few_shot_mode: Literal["off", "baseline", "variant", "settings"]
    agents: dict[WorkflowLabPromptAgentName, WorkflowLabPromptLayer]


class NodeLabResultEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    participant_label: Literal["baseline", "candidate"]
    candidate_id: str | None = None
    snapshot_hash: str | None = None
    status: EvalStatus
    error: EvalError | None = None
    prompt_identity: PromptIdentity
    model_identity: ModelIdentity | None = None
    node_output: dict[str, Any] | None = None
    prompt_preview: str | None = None
    agent_instructions: str | None = None
    prepared_sentences: list[dict[str, Any]] = Field(default_factory=list)
    example_summary: dict[str, Any] | None = None
    preprocess_summary: dict[str, Any] | None = None
    runtime_summary: dict[str, Any] | None = None
    quick_validation: dict[str, Any] | None = None
    rag_debug: dict[str, Any] | None = None
    trace_refs: dict[str, Any] | None = None
    warnings: list[dict[str, Any]] = Field(default_factory=list)


class ArticleAnalysisNodeLabRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eval_adapter_schema_version: Literal["article-analysis-node-lab-v1"] = NODE_LAB_SCHEMA_VERSION
    request_id: str | None = None
    session_id: str | None = None
    trial_id: str | None = None
    node_name: NodeProbeName = "grammar"
    text: str = Field(min_length=1)
    reading_goal: ReadingGoal = "daily_reading"
    reading_variant: ReadingVariant = "intermediate_reading"
    source_type: SourceType = "user_input"
    extended: bool = False
    trace_scope: TraceScope = "off"
    trace_project: str | None = "claread-eval"
    timeout_seconds: float | None = Field(default=None, gt=0.0)
    dry_run: bool = False
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    candidate_override: NodeLabRuntimeOverride | None = None

    @model_validator(mode="after")
    def _validate_candidate_node(self) -> ArticleAnalysisNodeLabRunRequest:
        if self.reading_goal == "academic":
            raise ValueError(
                "node_lab v1 only supports daily_reading and exam; academic should use a dedicated academic lab/workflow"
            )
        if self.candidate_override is not None and self.candidate_override.node_name != self.node_name:
            raise ValueError("candidate_override.node_name must match node_name")
        if (
            self.candidate_override is not None
            and self.candidate_override.few_shot_override.few_shot_mode == "rag"
            and self.node_name != "grammar"
        ):
            raise ValueError("few_shot_mode='rag' is only supported for grammar in node_lab v1")
        return self


class ArticleAnalysisNodeLabRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eval_adapter_schema_version: Literal["article-analysis-node-lab-v1"] = NODE_LAB_SCHEMA_VERSION
    node_name: NodeProbeName
    request_snapshot: RequestSnapshot
    workflow_identity: WorkflowIdentity
    schema_identity: SchemaIdentity
    run: NodeLabResultEntry


class ArticleAnalysisNodeLabCompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eval_adapter_schema_version: Literal["article-analysis-node-lab-v1"] = NODE_LAB_SCHEMA_VERSION
    request_id: str | None = None
    session_id: str | None = None
    trial_id: str | None = None
    node_name: NodeProbeName = "grammar"
    text: str = Field(min_length=1)
    reading_goal: ReadingGoal = "daily_reading"
    reading_variant: ReadingVariant = "intermediate_reading"
    source_type: SourceType = "user_input"
    extended: bool = False
    trace_scope: TraceScope = "off"
    trace_project: str | None = "claread-eval"
    timeout_seconds: float | None = Field(default=None, gt=0.0)
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    candidate_override: NodeLabRuntimeOverride

    @model_validator(mode="after")
    def _validate_candidate_node(self) -> ArticleAnalysisNodeLabCompareRequest:
        if self.reading_goal == "academic":
            raise ValueError(
                "node_lab v1 only supports daily_reading and exam; academic should use a dedicated academic lab/workflow"
            )
        if self.candidate_override.node_name != self.node_name:
            raise ValueError("candidate_override.node_name must match node_name")
        if (
            self.candidate_override.few_shot_override.few_shot_mode == "rag"
            and self.node_name != "grammar"
        ):
            raise ValueError("few_shot_mode='rag' is only supported for grammar in node_lab v1")
        return self


class ArticleAnalysisNodeLabCompareResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eval_adapter_schema_version: Literal["article-analysis-node-lab-v1"] = NODE_LAB_SCHEMA_VERSION
    node_name: NodeProbeName
    request_snapshot: RequestSnapshot
    workflow_identity: WorkflowIdentity
    schema_identity: SchemaIdentity
    baseline: NodeLabResultEntry
    candidate: NodeLabResultEntry
    compare_summary: dict[str, Any] = Field(default_factory=dict)


class NodeLabJudgeCriterionScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_id: str
    score: Literal[0, 1, 2]
    reason: str = Field(min_length=1)
    evidence: str | None = None


class NodeLabJudgeItemSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: int = Field(ge=0, default=0)
    partial: int = Field(ge=0, default=0)
    failed: int = Field(ge=0, default=0)


class NodeLabJudgeAggregate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_count: int | None = Field(default=None, ge=0)
    criteria_count: int = Field(ge=0)
    passed: int = Field(ge=0)
    partial: int = Field(ge=0)
    failed: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)


class NodeLabJudgeItemResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    item_type: str
    sentence_id: str | None = None
    label: str | None = None
    source_excerpt: str | None = None
    criteria: list[NodeLabJudgeCriterionScore] = Field(default_factory=list)
    item_summary: NodeLabJudgeItemSummary


class NodeLabJudgeSideResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[NodeLabJudgeItemResult] = Field(default_factory=list)
    output_level_scores: list[NodeLabJudgeCriterionScore] = Field(default_factory=list)
    aggregate: NodeLabJudgeAggregate


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
    overall_judgment: str = Field(min_length=1)
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
    description: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)


class NodeLabProbeAppendixResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    probe_type: str
    questions: list[NodeLabProbeQuestionResult] = Field(default_factory=list)
    summary: str | None = None


class NodeLabJudgeExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eval_adapter_schema_version: Literal["article-analysis-node-lab-judge-v1"] = NODE_LAB_JUDGE_SCHEMA_VERSION
    request_id: str | None = None
    node_name: NodeProbeName
    judge_strategy: JudgeStrategy
    judge_method: JudgeMethod
    reading_goal: ReadingGoal
    reading_variant: ReadingVariant
    judger_model_profile: str = Field(min_length=1)
    judger_model_settings: dict[str, Any] = Field(default_factory=dict)
    system_prompt: str = Field(min_length=1)
    user_prompt: str = Field(min_length=1)
    output_mode: JudgeOutputMode
    output_schema_kind: JudgeOutputSchemaKind
    metadata: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float | None = Field(default=None, gt=0.0)

    @model_validator(mode="after")
    def _validate_request(self) -> "NodeLabJudgeExecuteRequest":
        if self.reading_goal == "academic":
            raise ValueError(
                "node_lab judge v1 only supports daily_reading and exam; academic should use a dedicated academic lab/workflow"
            )
        allowed_by_node: dict[str, set[str]] = {
            "grammar": {"grammar_item_review"},
            "vocabulary": {"vocabulary_item_review"},
            "translation": {"translation_output_review"},
        }
        if self.judge_strategy not in allowed_by_node[self.node_name]:
            raise ValueError("judge_strategy is not compatible with node_name")
        if self.output_mode == "probe_appendix" and self.judge_method != "anti_template_probe":
            raise ValueError("probe_appendix output_mode requires judge_method='anti_template_probe'")
        if self.judge_method == "anti_template_probe" and self.node_name != "grammar":
            raise ValueError("anti_template_probe is only supported for grammar in node_lab judge v1")
        expected_schema: dict[tuple[str, str], str] = {
            ("grammar_item_review", "rubric_scoring"): "grammar_item_scoring",
            ("vocabulary_item_review", "rubric_scoring"): "vocabulary_item_scoring",
            ("translation_output_review", "rubric_scoring"): "translation_output_scoring",
            ("grammar_item_review", "pairwise"): "pairwise_review",
            ("vocabulary_item_review", "pairwise"): "pairwise_review",
            ("translation_output_review", "pairwise"): "pairwise_review",
            ("grammar_item_review", "probe_appendix"): "probe_appendix",
        }
        expected = expected_schema.get((self.judge_strategy, self.output_mode))
        if expected is None or self.output_schema_kind != expected:
            raise ValueError("output_schema_kind is not compatible with strategy/output_mode")
        return self


class NodeLabJudgeExecuteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eval_adapter_schema_version: Literal["article-analysis-node-lab-judge-v1"] = NODE_LAB_JUDGE_SCHEMA_VERSION
    request_id: str
    node_name: NodeProbeName
    judge_strategy: JudgeStrategy
    judge_method: JudgeMethod
    output_mode: JudgeOutputMode
    output_schema_kind: JudgeOutputSchemaKind
    status: EvalStatus
    error: EvalError | None = None
    model_identity: ModelIdentity | None = None
    runtime_summary: dict[str, Any] | None = None
    trace_refs: dict[str, Any] | None = None
    rubric_scoring_result: NodeLabRubricScoringResult | None = None
    pairwise_result: NodeLabPairwiseResult | None = None
    probe_appendix_result: NodeLabProbeAppendixResult | None = None
    warnings: list[dict[str, Any]] = Field(default_factory=list)


class NodeLabJudgeRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eval_adapter_schema_version: Literal["article-analysis-node-lab-judge-v1"] = NODE_LAB_JUDGE_SCHEMA_VERSION
    request_id: str | None = None
    judge_request_id: str | None = None
    node_name: NodeProbeName
    trial_id: str = Field(min_length=1)
    session_id: str | None = None
    judge_config_snapshot: dict[str, Any] = Field(default_factory=dict)
    compare_result: ArticleAnalysisNodeLabCompareResult
    participants: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float | None = Field(default=None, gt=0.0)


class NodeLabJudgeRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eval_adapter_schema_version: Literal["article-analysis-node-lab-judge-v1"] = NODE_LAB_JUDGE_SCHEMA_VERSION
    judge_request_id: str
    trial_id: str
    session_id: str | None = None
    preset_id: str
    node_name: NodeProbeName
    judge_method: JudgeMethod
    judge_strategy: JudgeStrategy
    step_runs: dict[str, Any] = Field(default_factory=dict)
    rubric_scoring_result: NodeLabRubricScoringResult | None = None
    pairwise_result: NodeLabPairwiseResult | None = None
    pairwise_error: EvalError | None = None
    probe_appendix_result: NodeLabProbeAppendixResult | None = None


class ArticleAnalysisEvalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eval_adapter_schema_version: Literal["article-analysis-eval-v1"] = (
        EVAL_ADAPTER_SCHEMA_VERSION
    )
    case_id: str | None = None
    run_id: str | None = None
    request_id: str | None = None
    text: str = Field(min_length=1)
    reading_goal: ReadingGoal = "daily_reading"
    reading_variant: ReadingVariant = "intermediate_reading"
    source_type: SourceType = "user_input"
    extended: bool = False
    model_selection: ModelSelection | None = None
    rag_mode: RagMode = "off"
    prompt_variant_id: str | None = None
    prompt_override: PromptRuntimeOverride | None = None
    trace_scope: TraceScope = "off"
    trace_project: str | None = "claread-eval"
    timeout_seconds: float | None = Field(default=None, gt=0.0)
    source_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_prompt_identity(self) -> ArticleAnalysisEvalRequest:
        if (
            self.prompt_variant_id
            and self.prompt_override is not None
            and self.prompt_variant_id != self.prompt_override.variant_id
        ):
            raise ValueError("prompt_variant_id must match prompt_override.variant_id")
        if self.prompt_override is not None and self.rag_mode != "off":
            raise ValueError("prompt_override v1 requires rag_mode='off'")
        return self


class ArticleAnalysisNodeProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eval_adapter_schema_version: Literal["article-analysis-node-probe-v1"] = (
        NODE_PROBE_SCHEMA_VERSION
    )
    case_id: str | None = None
    run_id: str | None = None
    request_id: str | None = None
    node_name: NodeProbeName = "grammar"
    text: str = Field(min_length=1)
    reading_goal: ReadingGoal = "daily_reading"
    reading_variant: ReadingVariant = "intermediate_reading"
    source_type: SourceType = "user_input"
    extended: bool = False
    model_selection: ModelSelection | None = None
    rag_mode: RagMode = "off"
    prompt_variant_id: str | None = None
    prompt_override: PromptRuntimeOverride | None = None
    trace_scope: TraceScope = "off"
    trace_project: str | None = "claread-eval"
    timeout_seconds: float | None = Field(default=None, gt=0.0)
    dry_run: bool = False
    source_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_prompt_identity(self) -> ArticleAnalysisNodeProbeRequest:
        if (
            self.prompt_variant_id
            and self.prompt_override is not None
            and self.prompt_variant_id != self.prompt_override.variant_id
        ):
            raise ValueError("prompt_variant_id must match prompt_override.variant_id")
        if self.prompt_override is not None and self.rag_mode != "off":
            raise ValueError("prompt_override v1 requires rag_mode='off'")
        return self


class ArticleAnalysisEvalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eval_adapter_schema_version: Literal["article-analysis-eval-v1"] = (
        EVAL_ADAPTER_SCHEMA_VERSION
    )
    status: EvalStatus
    error: EvalError | None = None
    request_snapshot: RequestSnapshot
    workflow_identity: WorkflowIdentity
    schema_identity: SchemaIdentity
    prompt_identity: PromptIdentity
    model_identity: ModelIdentity | None = None
    render_scene: AnyRenderSceneModel | None = None
    preprocess_summary: dict[str, Any] | None = None
    normalize_summary: dict[str, Any] | None = None
    drop_log_summary: dict[str, Any] | None = None
    runtime_summary: dict[str, Any] | None = None
    academic_quality: dict[str, Any] | None = None
    rag_debug: dict[str, Any] | None = None
    trace_refs: dict[str, Any] | None = None
    warnings: list[dict[str, Any]] = Field(default_factory=list)


class ArticleAnalysisNodeProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eval_adapter_schema_version: Literal["article-analysis-node-probe-v1"] = (
        NODE_PROBE_SCHEMA_VERSION
    )
    status: EvalStatus
    error: EvalError | None = None
    request_snapshot: RequestSnapshot
    workflow_identity: WorkflowIdentity
    schema_identity: SchemaIdentity
    prompt_identity: PromptIdentity
    model_identity: ModelIdentity | None = None
    node_name: NodeProbeName
    node_output: dict[str, Any] | None = None
    prompt_preview: str | None = None
    agent_instructions: str | None = None
    prepared_sentences: list[dict[str, Any]] = Field(default_factory=list)
    example_summary: dict[str, Any] | None = None
    preprocess_summary: dict[str, Any] | None = None
    runtime_summary: dict[str, Any] | None = None
    rag_debug: dict[str, Any] | None = None
    trace_refs: dict[str, Any] | None = None
    warnings: list[dict[str, Any]] = Field(default_factory=list)


class ExampleLabGenerateRagFieldsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sentence_text: str = Field(min_length=1)
    output_fragment: dict[str, Any] = Field(default_factory=dict)
    reading_variant: str = "default"
    model_profile: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0.0)


class ExampleLabGenerateRagFieldsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grammar_tags: list[str] = Field(default_factory=list)
    structure_signals: list[str] = Field(default_factory=list)
    teaching_goal: str = "balanced"
    retrieval_text: str = ""
    generated_by: str = "rule"
    latency_ms: int = 0


# ---------------------------------------------------------------------------
# Workflow Lab compare-level LLM judge
# ---------------------------------------------------------------------------

WORKFLOW_LAB_COMPARE_JUDGE_SCHEMA_VERSION = "workflow-compare-judge-v1"
VALID_COMPARE_JUDGE_VERDICTS = (
    "candidate_preferred",
    "baseline_preferred",
    "tie",
    "needs_review",
)


class WorkflowLabCompareJudgeSidePayload(BaseModel):
    """Per-side sentence evidence supplied to the compare LLM judge."""

    model_config = ConfigDict(extra="forbid")

    user_facing_state: str | None = None
    sentence_id: str | None = None
    sentence_text: str = ""
    translation: Any | None = None
    inline_marks: list[dict[str, Any]] = Field(default_factory=list)
    sentence_entries: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    drop_log: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowLabCompareJudgePacket(BaseModel):
    """One sentence-level compare packet for the LLM judge."""

    model_config = ConfigDict(extra="forbid")

    compare_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    sentence_id: str | None = None
    sentence_text: str = ""
    reading_goal: str | None = None
    reading_variant: str | None = None
    baseline: WorkflowLabCompareJudgeSidePayload
    candidate: WorkflowLabCompareJudgeSidePayload


class WorkflowLabCompareJudgeRequest(BaseModel):
    """Request body for the API-side Workflow compare LLM judge.

    Attributes:
        timeout_seconds: Per-packet LLM call timeout (passed straight to the
            shared structured-completion helper). Bounded by 600s by the
            adapter.
        total_timeout_seconds: Optional overall budget for the whole request.
            Once the budget is exhausted, the API stops scheduling new
            packets and short-circuits the remaining ones with a
            ``WORKFLOW_COMPARE_JUDGE_TOTAL_TIMEOUT`` case error.
        concurrency: Maximum number of in-flight packet LLM calls. Defaults
            to 1 (serial) for backward compatibility; callers can raise it
            when the chosen model profile tolerates parallel traffic.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["workflow-compare-judge-v1"] = WORKFLOW_LAB_COMPARE_JUDGE_SCHEMA_VERSION
    judge_run_id: str = Field(min_length=1)
    compare_id: str = Field(min_length=1)
    rubric_id: str = Field(min_length=1)
    rubric_version: str | None = None
    judge_model_profile: str = Field(min_length=1)
    packets: list[WorkflowLabCompareJudgePacket] = Field(default_factory=list)
    timeout_seconds: float | None = Field(default=None, gt=0.0)
    total_timeout_seconds: float | None = Field(default=None, gt=0.0)
    concurrency: int | None = Field(default=None, ge=1, le=8)


class WorkflowLabCompareJudgeCaseError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class WorkflowLabCompareJudgeCaseResult(BaseModel):
    """One LLM-judged case result, shaped to match Directus artifact fields."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    status: Literal["succeeded", "error"]
    verdict: Literal["candidate_preferred", "baseline_preferred", "tie", "needs_review"]
    preferred_side: Literal["baseline", "candidate"] | None = None
    overall_score: float | None = None
    summary: str = ""
    reasons: list[str] = Field(default_factory=list)
    error: WorkflowLabCompareJudgeCaseError | None = None


class WorkflowLabCompareJudgeResult(BaseModel):
    """Response body for the API-side Workflow compare LLM judge."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["workflow-compare-judge-v1"] = WORKFLOW_LAB_COMPARE_JUDGE_SCHEMA_VERSION
    judge_run_id: str
    compare_id: str
    rubric_id: str
    judge_model_profile: str
    model_name: str | None = None
    profile_name: str | None = None
    provider: str | None = None
    base_url: str | None = None
    results: list[WorkflowLabCompareJudgeCaseResult] = Field(default_factory=list)
