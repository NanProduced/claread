"""T5.8b — local pre-call policy for semantic outline provider calls.

Not ExecutionBudget / coverage. V1: max 1 provider call per job; bounded
input envelope must not exceed OUTLINE_MAX_*; generation_enabled + profile
admission before any model build.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config.settings import Settings, get_settings

from .semantic_outline_worker import (
    OUTLINE_MAX_TOTAL_PREVIEW_CHARS,
    OUTLINE_MAX_UNIT_PREVIEW_CHARS,
    OUTLINE_MAX_UNITS_FOR_PREVIEW,
    SemanticOutlineGenerationError,
    SemanticOutlineWorkerInput,
)

# V1 product default for max provider calls (no repair).
DEFAULT_MAX_PROVIDER_CALLS_PER_JOB = 1
# Optional output-token ceiling; product may override in T5.8d.
DEFAULT_MAX_OUTPUT_TOKENS = 4096


@dataclass(frozen=True, slots=True)
class SemanticOutlineExecutionPolicy:
    """Outline-local execution guardrails (DI-friendly)."""

    generation_enabled: bool = False
    max_provider_calls_per_job: int = DEFAULT_MAX_PROVIDER_CALLS_PER_JOB
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    max_unit_preview_chars: int = OUTLINE_MAX_UNIT_PREVIEW_CHARS
    max_total_preview_chars: int = OUTLINE_MAX_TOTAL_PREVIEW_CHARS
    max_units_for_preview: int = OUTLINE_MAX_UNITS_FOR_PREVIEW

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> SemanticOutlineExecutionPolicy:
        s = settings or get_settings()
        return cls(
            generation_enabled=bool(s.semantic_outline_generation_enabled),
            # V1 fixed until product fills T5.8d numbers.
            max_provider_calls_per_job=DEFAULT_MAX_PROVIDER_CALLS_PER_JOB,
            max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
        )

    @classmethod
    def for_tests(
        cls,
        *,
        generation_enabled: bool = True,
        max_provider_calls_per_job: int = DEFAULT_MAX_PROVIDER_CALLS_PER_JOB,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        max_unit_preview_chars: int = OUTLINE_MAX_UNIT_PREVIEW_CHARS,
        max_total_preview_chars: int = OUTLINE_MAX_TOTAL_PREVIEW_CHARS,
        max_units_for_preview: int = OUTLINE_MAX_UNITS_FOR_PREVIEW,
    ) -> SemanticOutlineExecutionPolicy:
        """Explicit test/DI policy — never use as production default."""
        return cls(
            generation_enabled=generation_enabled,
            max_provider_calls_per_job=max_provider_calls_per_job,
            max_output_tokens=max_output_tokens,
            max_unit_preview_chars=max_unit_preview_chars,
            max_total_preview_chars=max_total_preview_chars,
            max_units_for_preview=max_units_for_preview,
        )

    def assert_can_call_provider(
        self,
        *,
        profile_configured: bool,
        worker_input: SemanticOutlineWorkerInput,
        provider_calls_so_far: int = 0,
    ) -> None:
        """Fail-closed admission before build_model / agent run.

        Raises :class:`SemanticOutlineGenerationError` with ``retryable=False``
        and ``provider_call_made=False`` (zero usage).
        """
        if not self.generation_enabled:
            raise SemanticOutlineGenerationError(
                "semantic outline generation is disabled",
                failure_class="configuration",
                failure_code="semantic_outline_generation_disabled",
                retryable=False,
                provider_call_made=False,
            )
        if not profile_configured:
            raise SemanticOutlineGenerationError(
                (
                    "semantic outline model profile is not configured; set "
                    "reader_semantic_outline_model_profile"
                ),
                failure_class="configuration",
                failure_code="model_route_unavailable",
                retryable=False,
                provider_call_made=False,
            )
        if provider_calls_so_far >= self.max_provider_calls_per_job:
            raise SemanticOutlineGenerationError(
                (
                    f"semantic outline provider call budget exhausted "
                    f"({provider_calls_so_far}/{self.max_provider_calls_per_job})"
                ),
                failure_class="configuration",
                failure_code="semantic_outline_provider_call_budget_exhausted",
                retryable=False,
                provider_call_made=False,
            )
        self._assert_input_envelope(worker_input)

    def _assert_input_envelope(self, worker_input: SemanticOutlineWorkerInput) -> None:
        """Reject oversized **non-empty** previews only.

        Units may exceed ``max_units_for_preview`` when they are
        identity-only (empty preview) — that is intentional for long
        articles. Policy must not reject empty-preview identity rows.
        """
        if worker_input.total_preview_chars > self.max_total_preview_chars:
            raise SemanticOutlineGenerationError(
                (
                    "semantic outline input total preview exceeds policy cap "
                    f"({worker_input.total_preview_chars}>{self.max_total_preview_chars})"
                ),
                failure_class="configuration",
                failure_code="semantic_outline_input_envelope_exceeded",
                retryable=False,
                provider_call_made=False,
            )
        non_empty_preview_units = 0
        for unit in worker_input.units:
            preview_len = len(unit.preview)
            if preview_len == 0:
                # Identity-only unit: never reject for count or empty preview.
                continue
            non_empty_preview_units += 1
            if preview_len > self.max_unit_preview_chars:
                raise SemanticOutlineGenerationError(
                    (
                        "semantic outline unit preview exceeds policy cap "
                        f"for {unit.unit_id!r} ({preview_len}>{self.max_unit_preview_chars})"
                    ),
                    failure_class="configuration",
                    failure_code="semantic_outline_input_envelope_exceeded",
                    retryable=False,
                    provider_call_made=False,
                )
        # Cap how many units may carry non-empty preview text (not identity count).
        if non_empty_preview_units > self.max_units_for_preview:
            raise SemanticOutlineGenerationError(
                (
                    "semantic outline non-empty preview unit count exceeds policy cap "
                    f"({non_empty_preview_units}>{self.max_units_for_preview})"
                ),
                failure_class="configuration",
                failure_code="semantic_outline_input_envelope_exceeded",
                retryable=False,
                provider_call_made=False,
            )


__all__ = [
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "DEFAULT_MAX_PROVIDER_CALLS_PER_JOB",
    "SemanticOutlineExecutionPolicy",
]
