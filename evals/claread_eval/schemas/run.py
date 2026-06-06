from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class EvalRunConfig(BaseModel):
    run_id: str = Field(description="Unique run identifier; used as directory name")
    dataset_id: str = Field(description="Which dataset this run evaluates")
    mode: Literal["workflow", "node_probe"] = Field(default="workflow")
    eval_purpose: Literal["dataset_regression", "prompt_experiment", "manual_debug"] = Field(
        default="dataset_regression",
    )
    git_sha: str | None = Field(default=None)
    prompt_version: str | None = Field(default=None)
    prompt_variant_id: str | None = Field(default=None)
    workflow_version: str | None = Field(default=None)
    model_selection: dict[str, Any] = Field(default_factory=dict)
    rag_mode: Literal["off", "baseline", "rag", "rag_fallback", "settings"] = Field(default="off")
    trace_scope: Literal["off", "isolated", "inherit"] = Field(default="off")
    trace_project: str | None = Field(default="claread-eval")
    timeout_seconds: float | None = Field(default=None, gt=0.0)
    created_at: datetime = Field(default_factory=datetime.now)


class ModelIdentity(BaseModel):
    route: str | None = None
    profile_name: str | None = None
    provider: str | None = None
    model_name: str | None = None
    fallback_profiles: list[str] = Field(default_factory=list)
    model_settings: dict[str, Any] | None = None


class WorkflowIdentity(BaseModel):
    workflow_name: str | None = None
    workflow_version: str | None = None
    topology_mode: str | None = None


class SchemaIdentity(BaseModel):
    schema_version: str | None = None
    render_schema_version: str | None = None
    topology_mode: str | None = None


class PromptIdentity(BaseModel):
    prompt_version: str | None = None
    prompt_snapshot_hash: str | None = None
    prompt_variant_id: str | None = None


class UsageSummary(BaseModel):
    total_tokens: int = Field(default=0)
    per_agent: dict[str, Any] = Field(default_factory=dict)


class DropLogEntry(BaseModel):
    sentence_id: str
    reason: str


class WarningEntry(BaseModel):
    code: str
    level: str
    message: str
    sentence_id: str | None = None
    annotation_id: str | None = None


class EvalCaseArtifact(BaseModel):
    case_id: str = Field(description="EvalCase id this artifact belongs to")
    run_id: str = Field(description="EvalRun run_id")
    adapter_status: Literal["succeeded", "failed", "timeout"] = "succeeded"
    input_snapshot: dict[str, Any] = Field(default_factory=dict)
    run_config_snapshot: dict[str, Any] = Field(default_factory=dict)
    workflow_identity: WorkflowIdentity = Field(default_factory=WorkflowIdentity)
    schema_identity: SchemaIdentity = Field(default_factory=SchemaIdentity)
    prompt_identity: PromptIdentity = Field(default_factory=PromptIdentity)
    output: dict[str, Any] = Field(
        default_factory=dict, description="Render scene dict from adapter"
    )
    user_facing_state: str | None = None
    translations: list[dict[str, Any]] = Field(default_factory=list)
    inline_marks: list[dict[str, Any]] = Field(default_factory=list)
    sentence_entries: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[WarningEntry] = Field(default_factory=list)
    drop_log: list[DropLogEntry] = Field(default_factory=list)
    preprocess_summary: dict[str, Any] | None = None
    normalize_summary: dict[str, Any] | None = None
    drop_log_summary: dict[str, Any] | None = None
    runtime_summary: dict[str, Any] | None = None
    rag_debug: dict[str, Any] | None = None
    trace_refs: dict[str, Any] | None = None
    usage_summary: UsageSummary = Field(default_factory=UsageSummary)
    model_identity: ModelIdentity = Field(default_factory=ModelIdentity)
    grader_results: list[dict[str, Any]] = Field(default_factory=list)
    error: dict[str, Any] | None = None
    timeout: bool = Field(default=False)
    latency_seconds: float | None = None
