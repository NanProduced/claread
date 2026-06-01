from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.llm.types import ModelSelection

NodeLabFewShotMode = Literal["off", "baseline", "candidate", "rag"]
NodeLabNodeName = Literal["grammar", "vocabulary", "translation"]


class NodeLabExampleEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    example_type: Literal[
        "vocab",
        "phrase",
        "context",
        "grammar",
        "sentence_analysis",
        "translation",
    ]
    sentence_text: str = Field(min_length=1)
    output_fragment: str = Field(min_length=1)


class InstructionOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["baseline", "override_text"] = "baseline"
    text: str | None = None

    @model_validator(mode="after")
    def _validate_text(self) -> InstructionOverride:
        if self.mode == "override_text" and not isinstance(self.text, str):
            raise ValueError("instruction_override.text must be a string")
        return self


class PolicyOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["baseline", "override_lines"] = "baseline"
    lines: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_lines(self) -> PolicyOverride:
        if self.mode == "override_lines" and any(not isinstance(line, str) for line in self.lines):
            raise ValueError("policy_override.lines must be strings")
        return self


class FewShotOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    few_shot_mode: NodeLabFewShotMode = "baseline"
    examples: list[NodeLabExampleEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_examples(self) -> FewShotOverride:
        if self.few_shot_mode == "rag" and self.examples:
            raise ValueError("few_shot_mode='rag' does not accept inline examples")
        if self.few_shot_mode != "candidate" and self.examples:
            raise ValueError("inline examples require few_shot_mode='candidate'")
        return self


class NodeLabRuntimeOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str | None = None
    node_name: NodeLabNodeName
    target: Literal["article_analysis"] = "article_analysis"
    instruction_override: InstructionOverride = Field(default_factory=InstructionOverride)
    policy_override: PolicyOverride = Field(default_factory=PolicyOverride)
    few_shot_override: FewShotOverride = Field(default_factory=FewShotOverride)
    model_selection: ModelSelection | None = None
    snapshot_hash: str | None = None
