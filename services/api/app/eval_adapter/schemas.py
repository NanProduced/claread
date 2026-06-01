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

EvalStatus = Literal["succeeded", "failed", "timeout"]
RagMode = Literal["off", "baseline", "rag", "rag_fallback", "settings"]
TraceScope = Literal["off", "isolated", "inherit"]
NodeProbeName = Literal["grammar", "vocabulary", "translation"]
NodeLabWorkspace = Literal["single_run", "baseline_compare"]


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
