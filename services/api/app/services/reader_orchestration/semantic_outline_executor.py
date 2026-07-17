"""T5.8b — DI-only real semantic outline generator (PydanticAI).

Never the production default. Inject via ``SemanticOutlineWorkerService(generator=...)``.
No repair provider calls; agent output retries forced to 0.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_ai import Agent
from pydantic_ai.exceptions import ToolRetryError, UnexpectedModelBehavior

from app.config.settings import Settings, get_settings
from app.llm.agent_runner import extract_run_usage, run_reader_scoped_agent
from app.llm.call_guard import assert_real_llm_allowed
from app.llm.router import build_model_for_route
from app.llm.routes import MODEL_ROUTE_READER_LAYER_SEMANTIC_OUTLINE
from app.llm.types import RunModelSettings
from app.services.analysis.prompting.prompt_loader import (
    get_prompt_version,
    load_agent_instructions,
)

from .semantic_outline_execution_policy import SemanticOutlineExecutionPolicy
from .semantic_outline_publisher import SemanticOutlineCandidateNode
from .semantic_outline_worker import (
    SemanticOutlineExecutionResult,
    SemanticOutlineGenerationError,
    SemanticOutlineJobContext,
    SemanticOutlineWorkerInput,
)

SEMANTIC_OUTLINE_PROMPT_AGENT_NAME = "reader_semantic_outline"
SEMANTIC_OUTLINE_AGENT_NAME = "reader_semantic_outline_agent"


class OutlineCandidateModel(BaseModel):
    """Structured candidate — forbid durable identity fields."""

    model_config = ConfigDict(extra="forbid")

    candidate_ref: str = Field(min_length=1)
    parent_candidate_ref: str | None = None
    depth: int = Field(ge=1)
    title: str = Field(min_length=1)
    start_unit_id: str = Field(min_length=1)
    end_unit_id: str = Field(min_length=1)
    start_anchor_segment_id: str | None = None
    end_anchor_segment_id: str | None = None


class OutlineCandidatesOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[OutlineCandidateModel]


def _build_user_prompt(worker_input: SemanticOutlineWorkerInput) -> str:
    """Serialize bounded whitelist only (no full base text)."""
    payload = {
        "base_id": worker_input.base_id,
        "generation": worker_input.generation,
        "units": [
            {
                "unit_id": u.unit_id,
                "order_index": u.order_index,
                "unit_type": u.unit_type,
                "preview": u.preview,
            }
            for u in worker_input.units
        ],
        "anchors": [
            {"anchor_segment_id": a[0], "unit_id": a[1]} for a in worker_input.anchors
        ],
        "total_preview_chars": worker_input.total_preview_chars,
    }
    return (
        "Generate semantic outline candidates from the following bounded input JSON.\n"
        "Respond with a single raw JSON object only (field candidates).\n\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def _map_candidates(
    structured: OutlineCandidatesOutput,
) -> tuple[SemanticOutlineCandidateNode, ...]:
    return tuple(
        SemanticOutlineCandidateNode(
            candidate_ref=c.candidate_ref,
            parent_candidate_ref=c.parent_candidate_ref,
            depth=c.depth,
            title=c.title,
            start_unit_id=c.start_unit_id,
            end_unit_id=c.end_unit_id,
            start_anchor_segment_id=c.start_anchor_segment_id,
            end_anchor_segment_id=c.end_anchor_segment_id,
        )
        for c in structured.candidates
    )


def _is_transient_provider_error(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    markers = (
        "timeout",
        "timed out",
        "connection",
        "network",
        "temporarily",
        "503",
        "502",
        "429",
        "unavailable",
    )
    return any(m in name or m in msg for m in markers)


def _is_structured_output_failure(exc: BaseException) -> bool:
    """True when PydanticAI failed to validate model output into output_type.

    Observed (pydantic-ai 1.107): ``UnexpectedModelBehavior`` after output
    retries, often with ``ToolRetryError`` / ``ValidationError`` as cause.
    """
    if isinstance(exc, UnexpectedModelBehavior):
        return True
    if isinstance(exc, ToolRetryError):
        return True
    if isinstance(exc, ValidationError):
        return True
    # Walk cause chain (Agent may wrap validation as UnexpectedModelBehavior
    # with ToolRetryError cause — already covered above).
    cause = exc.__cause__
    if cause is not None and cause is not exc:
        if isinstance(cause, (ToolRetryError, ValidationError, UnexpectedModelBehavior)):
            return True
    return False


def _usage_for_structured_failure(usage: object) -> dict[str, Any]:
    """Always produce a usage envelope so worker writes exactly one failed event."""
    if isinstance(usage, dict):
        return usage
    return {"aggregate": {}}


def apply_output_token_cap(
    base: RunModelSettings | None,
    *,
    max_output_tokens: int,
) -> RunModelSettings:
    """Merge policy output cap into model settings (min with existing max_tokens)."""
    if max_output_tokens < 1:
        raise ValueError("max_output_tokens must be >= 1")
    if base is None:
        return RunModelSettings(max_tokens=max_output_tokens)
    if base.max_tokens is None:
        return base.with_max_tokens(max_output_tokens)
    return base.with_max_tokens(min(int(base.max_tokens), max_output_tokens))


class PydanticAISemanticOutlineGenerator:
    """Real outline generator — only when DI-injected; default worker stays Unconfigured."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        policy: SemanticOutlineExecutionPolicy | None = None,
    ) -> None:
        self._settings = settings
        self._policy = policy
        # Test seam: last model_settings passed into agent.run (output cap).
        self.last_model_settings: RunModelSettings | None = None

    def _resolve_settings(self) -> Settings:
        return self._settings or get_settings()

    def _resolve_policy(self, settings: Settings) -> SemanticOutlineExecutionPolicy:
        return self._policy or SemanticOutlineExecutionPolicy.from_settings(settings)

    def _build_agent(self, *, model: Any) -> Agent:
        # V1: no output repair retries (no implicit second provider call).
        return Agent(
            model=model,
            output_type=OutlineCandidatesOutput,
            instructions=load_agent_instructions(SEMANTIC_OUTLINE_PROMPT_AGENT_NAME),
            name=SEMANTIC_OUTLINE_AGENT_NAME,
            retries={"tools": 0, "output": 0},
        )

    async def _run_agent(
        self,
        agent: Agent,
        prompt: str,
        *,
        model_settings: RunModelSettings | None,
    ) -> Any:
        kwargs: dict[str, Any] = {}
        if model_settings is not None:
            pai_settings = model_settings.to_pydantic_ai()
            if pai_settings is not None:
                kwargs["model_settings"] = pai_settings
        return await run_reader_scoped_agent(agent, prompt, **kwargs)

    async def generate(
        self, context: SemanticOutlineJobContext
    ) -> SemanticOutlineExecutionResult:
        settings = self._resolve_settings()
        policy = self._resolve_policy(settings)
        prompt_version = get_prompt_version()
        profile_name = str(settings.reader_semantic_outline_model_profile or "").strip()
        profile_configured = bool(profile_name)

        # Pre-call: disabled / missing profile / envelope / call budget.
        policy.assert_can_call_provider(
            profile_configured=profile_configured,
            worker_input=context.worker_input,
            provider_calls_so_far=0,
        )

        model, model_config = build_model_for_route(
            settings,
            MODEL_ROUTE_READER_LAYER_SEMANTIC_OUTLINE,
        )
        if model is None or model_config is None:
            raise SemanticOutlineGenerationError(
                "reader_layer_semantic_outline model route is not configured",
                failure_class="configuration",
                failure_code="model_route_unavailable",
                retryable=False,
                provider_call_made=False,
                prompt_version=prompt_version,
                model_route=MODEL_ROUTE_READER_LAYER_SEMANTIC_OUTLINE,
                model_profile=profile_name or None,
            )

        provenance = {
            "prompt_version": prompt_version,
            "model_route": MODEL_ROUTE_READER_LAYER_SEMANTIC_OUTLINE,
            "model_profile": str(model_config.profile_name),
            "model_provider": str(model_config.provider),
            "model_name": str(model_config.model_name),
        }

        assert_real_llm_allowed(
            (
                "app.services.reader_orchestration.semantic_outline_executor."
                "PydanticAISemanticOutlineGenerator"
            ),
            model_config=model_config,
        )

        # Policy output cap must enter Agent/model settings (not decorative).
        run_model_settings = apply_output_token_cap(
            model_config.model_settings,
            max_output_tokens=policy.max_output_tokens,
        )
        self.last_model_settings = run_model_settings

        agent = self._build_agent(model=model)
        user_prompt = _build_user_prompt(context.worker_input)
        try:
            result = await self._run_agent(
                agent, user_prompt, model_settings=run_model_settings
            )
        except SemanticOutlineGenerationError:
            raise
        except Exception as exc:
            usage = None
            # Best-effort: some providers attach partial usage on failures.
            try:
                usage = extract_run_usage(exc)  # type: ignore[arg-type]
            except Exception:
                usage = None
            # Structured-output failures raised *inside* agent.run must not
            # look like generic provider/transient errors.
            if _is_structured_output_failure(exc):
                raise SemanticOutlineGenerationError(
                    f"semantic outline produced invalid candidate output: {exc}",
                    failure_class="validation",
                    failure_code="model_output_invalid",
                    retryable=False,
                    provider_call_made=True,
                    usage_data=_usage_for_structured_failure(usage),
                    **provenance,
                ) from exc
            transient = _is_transient_provider_error(exc)
            raise SemanticOutlineGenerationError(
                f"semantic outline agent execution failed: {exc}",
                failure_class="provider",
                failure_code=type(exc).__name__,
                retryable=transient,
                provider_call_made=True,
                usage_data=usage if isinstance(usage, dict) else None,
                **provenance,
            ) from exc

        usage_data = extract_run_usage(result)
        try:
            # Prefer already-validated agent output; re-validate for fail-closed.
            raw_output = result.output
            structured = OutlineCandidatesOutput.model_validate(raw_output)
            candidates = _map_candidates(structured)
        except (ValidationError, ValueError, TypeError) as exc:
            raise SemanticOutlineGenerationError(
                f"semantic outline produced invalid candidate output: {exc}",
                failure_class="validation",
                failure_code="model_output_invalid",
                retryable=False,
                provider_call_made=True,
                # Always write one failed usage event after a real provider call.
                usage_data=_usage_for_structured_failure(usage_data),
                **provenance,
            ) from exc

        return SemanticOutlineExecutionResult(
            candidates=candidates,
            worker_failure=False,
            model=str(model_config.model_name),
            usage_data=usage_data if isinstance(usage_data, dict) else None,
            provider_call_made=True,
            **provenance,
        )


__all__ = [
    "OutlineCandidateModel",
    "OutlineCandidatesOutput",
    "PydanticAISemanticOutlineGenerator",
    "SEMANTIC_OUTLINE_PROMPT_AGENT_NAME",
    "apply_output_token_cap",
]
