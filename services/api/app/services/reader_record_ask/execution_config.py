"""ASK-M1: unified execution config + resolver for reader_record_ask.

Fixes two P0 gaps surfaced by the model-control-plane research:

1. ``retry`` previously did not re-resolve the persisted thread model
   option and fell back to the global ``reader_ask`` route default. A
   user who selected DeepSeek Pro could silently get Flash on retry.
2. The product option's ``runtime_budget`` (24k input / 3.2k output /
   800 prompt buffer) never reached the agentic runtime. The agentic
   lane only had an independent 24k-char model-view safety ledger.

This module compiles a persisted option into a single
:class:`ReaderRecordAskExecutionConfig` that carries:

- the resolved ``Model`` instance (send + retry use the same path);
- a provider completion cap derived from
  ``option.runtime_budget.max_output_tokens`` (mapped to
  ``RunModelSettings.max_tokens`` and forwarded as PydanticAI
  ``ModelSettings``);
- a PydanticAI :class:`UsageLimits` carrying ``output_tokens_limit``
  derived from ``option.runtime_budget.max_turn_output_tokens`` so the
  host can enforce an explicit cumulative per-turn output cap;
- a privacy-safe :class:`ReaderRecordAskExecutionSnapshot` (option
  key, resolved provider / model / profile names, budget policy
  version + fingerprint) — never API key, body, raw reasoning,
  user / record identity, or provider raw payload.

Fail-closed contract
--------------------
When the selected option cannot resolve to a buildable model, the
resolver raises :class:`ReaderRecordAskExecutionUnavailable`. Callers
must surface a typed ``unavailable`` terminal — they must **not**
silently substitute the global default model.
"""

from __future__ import annotations

import copy
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from app.config.settings import Settings, get_settings
from app.llm.provider_factory import ModelProviderError
from app.llm.router import ModelSelectionError, build_model_for_route
from app.llm.routes import MODEL_ROUTE_READER_ASK
from app.llm.types import RunModelSettings
from app.services.reader_ask.model_options import (
    ReaderAskRuntimeBudgetConfig,
    ResolvedReaderAskModelOption,
)

logger = logging.getLogger(__name__)

# Policy version stamped on every snapshot. Bumped only when the
# resolver's *compilation semantics* change (new fields, new mapping
# rules). Option-level config drift is captured by ``budget_fingerprint``.
EXECUTION_CONFIG_POLICY_VERSION: str = "reader_record_ask_execution_v2"


class ReaderRecordAskExecutionUnavailable(RuntimeError):
    """Typed fail-closed error: selected option cannot resolve a model.

    Carries the option key (safe — not a secret, not raw provider
    payload) so callers can produce a typed ``unavailable`` terminal
    without leaking internal exception text, profile names, or
    provider errors into the wire.
    """

    def __init__(self, *, option_key: str, reason: str) -> None:
        super().__init__(f"execution_unavailable option={option_key}: {reason}")
        self.option_key = option_key
        self.reason = reason


class ReaderRecordAskExecutionSnapshot(BaseModel):
    """Privacy-safe projection of one resolved execution config.

    Persistence-safe: may be stored on turn-run metadata, logged at
    info level, or projected onto runtime events. Must never carry:

    - API keys or provider auth material;
    - user message body, evidence snippets, or answer text;
    - raw reasoning / thinking content;
    - user id, reading record id, or other account identity;
    - provider raw payloads, headers, or full ResolvedModelConfig dumps.

    Only identity + policy fields that let an operator reproduce
    *which* model / profile / budget was selected for one turn.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    option_key: str = Field(min_length=1)
    # Resolved identity (safe names only — no api_key / base_url).
    provider: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    profile_name: str = Field(min_length=1)
    adapter: str = Field(min_length=1)
    # Budget policy echo (mirrors what was compiled into ModelSettings /
    # UsageLimits so an operator can audit the cap without re-running
    # the resolver).
    max_output_tokens: int = Field(ge=1)
    max_turn_output_tokens: int = Field(ge=1)
    max_input_tokens: int = Field(ge=1)
    prompt_buffer_tokens: int = Field(ge=0)
    # Resolver / policy identity.
    policy_version: str = Field(min_length=1)
    budget_fingerprint: str = Field(min_length=1)
    # ``used_fallback`` mirrors ResolvedReaderAskModelOption.used_fallback
    # so observers can tell a persisted default from an explicit choice.
    used_fallback: bool = False


def _budget_fingerprint(budget: ReaderAskRuntimeBudgetConfig) -> str:
    """Stable SHA-256 fingerprint of the resolved budget policy.

    Only the four numeric fields enter the hash — never the option
    key, label, or provider identity. This lets an operator verify
    "did the budget change between two turns of the same option"
    without learning anything else.
    """
    payload = (
        f"in={budget.max_input_tokens};"
        f"out={budget.max_output_tokens};"
        f"turn_out={budget.max_turn_output_tokens};"
        f"buf={budget.prompt_buffer_tokens}"
    ).encode()
    return hashlib.sha256(payload).hexdigest()


@dataclass(slots=True, frozen=True)
class ReaderRecordAskExecutionConfig:
    """Compiled execution inputs for one reader_record_ask turn.

    Carries everything the agentic runtime needs to call ``agent.run``
    with an explicit model + provider completion cap + host usage
    limit, plus a safe snapshot for observability.

    Sensitive-field boundary
    ------------------------
    The raw ``Model`` instance and the raw provider settings payload
    are excluded from ``repr(config)``. They are **not** "safely
    loggable" — the ``Model`` may carry provider auth material, and
    ``model_settings_payload`` is the merged dict form that may
    include ``extra_headers`` containing auth sentinels. Operators
    observing a config should only log :attr:`snapshot` (which is
    purpose-built for that) plus ``option_key`` / ``runtime_budget``.

    ``model_settings_payload`` is the dict form forwarded to PydanticAI
    as ``ModelSettings`` and uses the per-request
    ``option.runtime_budget.max_output_tokens`` cap. ``usage_limits`` is
    the PydanticAI :class:`UsageLimits` instance and uses the distinct
    cumulative ``max_turn_output_tokens`` cap. ``max_input_tokens`` is
    intentionally **not** mapped to a host token limit (the existing
    :class:`ModelVisibleTurnBudget` remains the independent input safety
    ledger).
    """

    option_key: str
    # Raw ``Model`` instance — excluded from repr (carries provider
    # auth / base_url inside the model object).
    model: Model | str = field(repr=False)
    # Merged provider settings dict (max_tokens + temperature +
    # extra_body + extra_headers + ...). Excluded from repr — not
    # safely loggable; extra_headers may carry auth sentinels.
    model_settings_payload: dict[str, Any] = field(default_factory=dict, repr=False)
    # Host-side second-layer guard (PydanticAI UsageLimits).
    usage_limits: UsageLimits | None = None
    # Runtime budget actually applied (echoed in snapshot).
    runtime_budget: ReaderAskRuntimeBudgetConfig | None = None
    snapshot: ReaderRecordAskExecutionSnapshot | None = None

    def model_settings(self) -> ModelSettings | None:
        """Return a fresh ``ModelSettings`` copy of the provider settings.

        Returns ``None`` when no cap is set (callers pass ``None`` to
        ``agent.run(model_settings=...)`` — PydanticAI then uses the
        agent / model default).

        Each call returns an **independent** copy: mutating the returned
        ``ModelSettings`` (or its nested ``extra_body`` / ``extra_headers``
        dicts) does not affect subsequent calls or the underlying
        execution config. This guards against callers (e.g. provider
        factory normalisation) writing back into the shared payload.
        """
        if not self.model_settings_payload:
            return None
        payload = copy.deepcopy(self.model_settings_payload)
        return ModelSettings(payload)  # type: ignore[arg-type]


def _resolve_model_settings(
    *,
    base: RunModelSettings | None,
    max_output_tokens: int,
) -> tuple[dict[str, Any], RunModelSettings | None]:
    """Merge ``max_tokens`` into the resolved base settings.

    The provider completion cap is the product option's
    ``max_output_tokens``. It overrides any ``max_tokens`` declared at
    profile / model / provider level so the product budget is the hard
    cap actually enforced on the wire. Other provider-level fields
    (temperature, thinking payload, extra_body) are preserved.

    Returns ``(payload_dict, merged_settings)`` for provider execution.
    The payload may contain sensitive ``extra_headers`` and must never
    be logged; use :class:`ReaderRecordAskExecutionSnapshot` for
    observability.
    """
    merged = base or RunModelSettings()
    merged = merged.with_max_tokens(max_output_tokens)
    payload = merged.model_dump(exclude_none=True)
    return payload, merged


def _resolve_usage_limits(
    *,
    max_turn_output_tokens: int,
) -> UsageLimits:
    """Map the cumulative turn cap to PydanticAI host UsageLimits.

    PydanticAI checks ``output_tokens_limit`` against cumulative
    ``RunUsage.output_tokens`` after each model response. It must
    therefore use the explicit per-turn cap, not the per-request
    provider ``max_output_tokens`` setting. We
    intentionally do **not** set ``input_tokens_limit`` or
    ``total_tokens_limit`` from ``max_input_tokens`` — the existing
    :class:`ModelVisibleTurnBudget` is the independent fail-closed
    input safety ledger, and provider token-count reliability varies.
    Setting a host input limit on top would either duplicate or
    conflict with that ledger.

    ``request_limit`` is left at PydanticAI's default (50) so the
    agent can still perform its bounded tool fan-out (read_range /
    search_current_article / expand_evidence).
    """
    return UsageLimits(output_tokens_limit=max_turn_output_tokens)


def resolve_reader_record_ask_execution(
    option: ResolvedReaderAskModelOption,
    *,
    settings: Settings | None = None,
) -> ReaderRecordAskExecutionConfig:
    """Compile a persisted option into a unified execution config.

    Single source of truth for both ``send`` and ``retry`` paths:
    both must call this resolver (or read a previously persisted
    snapshot) — never fall back to the global ``reader_ask`` route
    default when an option has been selected.

    Raises :class:`ReaderRecordAskExecutionUnavailable` on any
    resolution / build failure. Callers must translate this into a
    typed ``unavailable`` terminal; they must **not** retry against
    a different model or silently substitute the default.
    """
    cfg = settings or get_settings()
    try:
        model, model_config = build_model_for_route(
            cfg,
            MODEL_ROUTE_READER_ASK,
            option.selection,
        )
    except (ModelProviderError, ModelSelectionError) as exc:
        logger.warning(
            "reader_record_ask execution resolve failed: option=%s "
            "error_type=%s",
            option.key,
            type(exc).__name__,
        )
        raise ReaderRecordAskExecutionUnavailable(
            option_key=option.key,
            reason="model_build_failed",
        ) from None
    except Exception as exc:  # noqa: BLE001 — fail closed, no leakage
        logger.warning(
            "reader_record_ask execution resolve failed: option=%s "
            "error_type=%s",
            option.key,
            type(exc).__name__,
        )
        raise ReaderRecordAskExecutionUnavailable(
            option_key=option.key,
            reason="model_build_failed",
        ) from None

    if model is None or model_config is None:
        logger.warning(
            "reader_record_ask execution resolve returned no model: "
            "option=%s profile=%s",
            option.key,
            getattr(model_config, "profile_name", None),
        )
        raise ReaderRecordAskExecutionUnavailable(
            option_key=option.key,
            reason="model_unconfigured",
        )

    budget = option.runtime_budget
    payload, merged_settings = _resolve_model_settings(
        base=model_config.model_settings,
        max_output_tokens=budget.max_output_tokens,
    )
    usage_limits = _resolve_usage_limits(
        max_turn_output_tokens=budget.max_turn_output_tokens,
    )

    snapshot = ReaderRecordAskExecutionSnapshot(
        option_key=option.key,
        provider=model_config.provider,
        model_name=model_config.model_name,
        profile_name=model_config.profile_name,
        adapter=model_config.adapter,
        max_output_tokens=budget.max_output_tokens,
        max_turn_output_tokens=budget.max_turn_output_tokens,
        max_input_tokens=budget.max_input_tokens,
        prompt_buffer_tokens=budget.prompt_buffer_tokens,
        policy_version=EXECUTION_CONFIG_POLICY_VERSION,
        budget_fingerprint=_budget_fingerprint(budget),
        used_fallback=option.used_fallback,
    )

    return ReaderRecordAskExecutionConfig(
        option_key=option.key,
        model=model,
        model_settings_payload=payload,
        usage_limits=usage_limits,
        runtime_budget=budget,
        snapshot=snapshot,
    )


__all__ = [
    "EXECUTION_CONFIG_POLICY_VERSION",
    "ReaderRecordAskExecutionConfig",
    "ReaderRecordAskExecutionSnapshot",
    "ReaderRecordAskExecutionUnavailable",
    "resolve_reader_record_ask_execution",
]
