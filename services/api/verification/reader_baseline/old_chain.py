"""Legacy article_analysis chain introspection and metrics.

The legacy chain (the ``article_analysis`` LangGraph workflow that
the D4/D5 refactor is replacing) does not have a deterministic fake
executor and always calls the real LLM. It also does not write
``enhancement_layers`` or ``reader_events``; it writes
``analysis_results.render_scene_json``.

This module provides two surfaces:

1. :func:`introspect` -- a static, schema-only description of what
   the legacy chain produces. Safe to call without any LLM
   credentials. Always available.

2. :func:`run_end_to_end` -- actually runs the legacy chain on a
   plain text input via ``run_article_analysis_with_state``. Gated
   behind ``READER_BASELINE_REAL_LLM=1`` and a positive env override
   because:

   - It calls a real LLM, which costs money and is slow.
   - Without a valid model profile it raises inside
     ``validate_model_selection`` and never returns a render scene.
   - The T0.1 task is *not* about optimising the legacy chain; it
     is about establishing a baseline. The introspection surface
     is the durable contract; the end-to-end path is opt-in.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.schemas.analysis import (
    AnalyzeRequest,
    AnyRenderSceneModel,
)

# Reading goal / variant used as the default for legacy chain
# introspection calls. They match AnalyzeRequest defaults so we do
# not need to override them at the call site.
from app.schemas.internal.analysis import (
    ReadingGoal,
    ReadingVariant,
)

DEFAULT_LEGACY_READING_GOAL: ReadingGoal = "daily_reading"
DEFAULT_LEGACY_READING_VARIANT: ReadingVariant = "intermediate_reading"

# Legacy chain capability code written to ai_usage_events. Sourced
# from app.services.ai_usage.capabilities so we don't duplicate the
# string here.
from app.services.ai_usage.capabilities import CAPABILITY_ANALYSIS_FULL

from verification.reader_baseline.new_chain import UsageMetrics

LEGACY_CAPABILITY_CODE: str = CAPABILITY_ANALYSIS_FULL
LEGACY_REAL_LLM_ENV_FLAG: str = "READER_BASELINE_REAL_LLM"


@dataclass(frozen=True, slots=True)
class LegacyChainContract:
    """Static description of the legacy chain output contract.

    This is the durable "shape of what the old chain produces" that
    we compare the new chain against. It does not depend on whether
    anyone actually ran the legacy chain.
    """

    chain_name: str
    capability_code: str
    render_scene_field: str
    article_field: str
    translations_field: str
    sentence_entries_field: str
    inline_marks_field: str
    vocabulary_field: str
    grammar_notes_field: str
    known_top_level_fields: tuple[str, ...]
    expected_persisted_columns: tuple[str, ...]
    ai_usage_event_columns: tuple[str, ...]
    usage_summary_field: str
    model_route: str
    notes: str

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "chain_name": self.chain_name,
            "capability_code": self.capability_code,
            "render_scene_field": self.render_scene_field,
            "article_field": self.article_field,
            "translations_field": self.translations_field,
            "sentence_entries_field": self.sentence_entries_field,
            "inline_marks_field": self.inline_marks_field,
            "vocabulary_field": self.vocabulary_field,
            "grammar_notes_field": self.grammar_notes_field,
            "known_top_level_fields": list(self.known_top_level_fields),
            "expected_persisted_columns": list(self.expected_persisted_columns),
            "ai_usage_event_columns": list(self.ai_usage_event_columns),
            "usage_summary_field": self.usage_summary_field,
            "model_route": self.model_route,
            "notes": self.notes,
        }


def introspect() -> LegacyChainContract:
    """Return the static contract of the legacy chain.

    The values here are documented in
    ``services/api/app/workflow/analyze.py`` and
    ``services/api/app/workflow/analyze_nodes.py``, plus the
    persistence path in
    ``services/api/app/services/analysis/task_executor.py``. They
    are stable contract values, not runtime measurements.
    """
    return LegacyChainContract(
        chain_name="article_analysis",
        capability_code=LEGACY_CAPABILITY_CODE,
        render_scene_field="render_scene_json",
        article_field="article",
        translations_field="translations",
        sentence_entries_field="sentence_entries",
        inline_marks_field="inline_marks",
        vocabulary_field="vocabulary",
        grammar_notes_field="grammar_notes",
        known_top_level_fields=(
            "schema_version",
            "article",
            "translations",
            "sentence_entries",
            "inline_marks",
            "vocabulary",
            "grammar_notes",
        ),
        expected_persisted_columns=(
            "analysis_records.id",
            "analysis_records.user_id",
            "analysis_records.title",
            "analysis_results.render_scene_json",
            "analysis_tasks.status",
        ),
        ai_usage_event_columns=(
            "capability_code = 'analysis_full'",
            "billing_mode = 'user_points'",
            "usage_scope = 'user_billed'",
            "reading_record_id IS NULL",
            "reader_run_id IS NULL",
            "reader_job_id IS NULL",
        ),
        usage_summary_field="state.usage_summary",
        model_route="annotation_generation",
        notes=(
            "Legacy chain always calls a real LLM. It does not have a "
            "deterministic fake executor. End-to-end metrics require "
            "READER_BASELINE_REAL_LLM=1 and a configured model profile."
        ),
    )


@dataclass(frozen=True, slots=True)
class LegacyChainRunOutcome:
    """End-to-end legacy chain run outcome.

    Populated only when :func:`run_end_to_end` is allowed and
    succeeds. ``latency_ms`` is wall-clock for the call. ``render_scene``
    is the model as a JSON-safe dict, not the Pydantic instance.
    ``usage`` aggregates ``state["usage_summary"]`` so the report
    can compare token / call counts with the new chain's
    ``ai_usage_events`` aggregation.
    """

    capability_code: str
    model_route: str
    latency_ms: int
    render_scene: dict[str, Any] = field(default_factory=dict)
    render_scene_keys: tuple[str, ...] = ()
    translation_count: int = 0
    sentence_entry_count: int = 0
    inline_mark_count: int = 0
    vocabulary_count: int = 0
    grammar_note_count: int = 0
    request_id: str = ""
    usage: UsageMetrics | None = None

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "capability_code": self.capability_code,
            "model_route": self.model_route,
            "latency_ms": self.latency_ms,
            "render_scene_keys": list(self.render_scene_keys),
            "translation_count": self.translation_count,
            "sentence_entry_count": self.sentence_entry_count,
            "inline_mark_count": self.inline_mark_count,
            "vocabulary_count": self.vocabulary_count,
            "grammar_note_count": self.grammar_note_count,
            "request_id": self.request_id,
            # We deliberately do not serialise the full render_scene
            # blob here; it is large and the keys + counts are what
            # the report needs for comparison.
            "render_scene_size_bytes": _approx_dict_size(self.render_scene),
            "usage": (
                self.usage.to_jsonable() if self.usage is not None else None
            ),
        }


def is_real_llm_runs_allowed() -> bool:
    """Whether the legacy chain is allowed to actually call the LLM.

    The check is opt-in. We never auto-enable it: the legacy chain
    is expensive and the T0.1 task only needs the introspection
    surface to be reusable.
    """
    return os.environ.get(LEGACY_REAL_LLM_ENV_FLAG, "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _approx_dict_size(payload: dict[str, Any]) -> int:
    """Best-effort dict size estimate in bytes.

    We do not need a precise measurement for the report; we just
    want a sense of how large the render scene is so the report can
    flag unusually large outputs.
    """
    try:
        import json

        return len(json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:
        return 0


def _count_at(d: dict[str, Any], key: str) -> int:
    value = d.get(key)
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    return 0


def _usage_from_state(state: dict[str, Any]) -> UsageMetrics | None:
    """Map ``state['usage_summary']`` to a :class:`UsageMetrics` object.

    Returns ``None`` when the workflow did not produce a usage
    summary. The contract for ``usage_summary`` is owned by
    ``services/api/app/workflow/analyze_nodes.py:_aggregate_usage_summary``.
    """
    summary = state.get("usage_summary")
    if not isinstance(summary, dict):
        return None
    if not summary.get("available", False):
        return None
    per_agent_raw = summary.get("per_agent") or {}
    per_agent: dict[str, UsageMetrics] = {}
    for agent_name, payload in per_agent_raw.items():
        if not isinstance(payload, dict):
            continue
        per_agent[str(agent_name)] = UsageMetrics(
            event_count=1,
            input_tokens=int(payload.get("input_tokens", 0) or 0),
            output_tokens=int(payload.get("output_tokens", 0) or 0),
            total_tokens=int(payload.get("total_tokens", 0) or 0),
            latency_ms=0,
            source="usage_summary",
        )
    aggregate = summary.get("aggregate") or {}
    if not isinstance(aggregate, dict):
        aggregate = {}
    return UsageMetrics(
        event_count=len(per_agent),
        input_tokens=int(aggregate.get("input_tokens", 0) or 0),
        output_tokens=int(aggregate.get("output_tokens", 0) or 0),
        total_tokens=int(aggregate.get("total_tokens", 0) or 0),
        latency_ms=0,
        by_capability=per_agent,
        source="usage_summary",
    )


async def run_end_to_end(
    *,
    plain_text: str,
    reading_goal: ReadingGoal = DEFAULT_LEGACY_READING_GOAL,
    reading_variant: ReadingVariant = DEFAULT_LEGACY_READING_VARIANT,
    source_type: str = "user_input",
) -> LegacyChainRunOutcome:
    """Run the legacy chain on a plain text input.

    Raises ``RuntimeError`` if the env opt-in is missing or the
    legacy chain fails (including the model selection validation
    that fires before any LLM call).
    """
    if not is_real_llm_runs_allowed():
        raise RuntimeError(
            "legacy article_analysis end-to-end run is disabled; set "
            f"{LEGACY_REAL_LLM_ENV_FLAG}=1 to enable"
        )
    from app.workflow.analyze import run_article_analysis_with_state

    request_id = str(uuid4())
    payload = AnalyzeRequest(
        request_id=request_id,
        source_type=source_type,
        text=plain_text,
        reading_goal=reading_goal,
        reading_variant=reading_variant,
    )
    started = time.perf_counter()
    state = await run_article_analysis_with_state(payload)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    render_scene: AnyRenderSceneModel | None = state.get("render_scene")
    if render_scene is None:
        raise RuntimeError("legacy chain returned no render_scene")
    render_scene_dict = render_scene.model_dump(mode="json")
    usage = _usage_from_state(state)
    return LegacyChainRunOutcome(
        capability_code=LEGACY_CAPABILITY_CODE,
        model_route="annotation_generation",
        latency_ms=elapsed_ms,
        render_scene=render_scene_dict,
        render_scene_keys=tuple(sorted(render_scene_dict.keys())),
        translation_count=_count_at(render_scene_dict, "translations"),
        sentence_entry_count=_count_at(render_scene_dict, "sentence_entries"),
        inline_mark_count=_count_at(render_scene_dict, "inline_marks"),
        vocabulary_count=_count_at(render_scene_dict, "vocabulary"),
        grammar_note_count=_count_at(render_scene_dict, "grammar_notes"),
        request_id=request_id,
        usage=usage,
    )


__all__ = [
    "LEGACY_CAPABILITY_CODE",
    "LEGACY_REAL_LLM_ENV_FLAG",
    "LegacyChainContract",
    "LegacyChainRunOutcome",
    "introspect",
    "is_real_llm_runs_allowed",
    "run_end_to_end",
]
