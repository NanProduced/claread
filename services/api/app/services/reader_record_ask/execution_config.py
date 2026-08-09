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

R1A extension
-------------
When ``settings.reader_record_ask_memory_enabled`` is true, the resolver
also compiles a :class:`CompactorBudgetConfig` placeholder for the
thread-memory compactor (R2 will wire the actual model call). R1A only
compiles the config; the compactor is **not** invoked in R1A.

H9 handling convention
----------------------
- ``response_format`` is documented on :class:`CompactorBudgetConfig` for
  observability but is **not** forwarded to the provider wire directly.
  The actual response format is derived by PydanticAI from the
  compactor agent's ``output_type`` (same mechanism as the main answer
  agent). The field exists only so an operator can audit which schema
  the compactor will use.
- ``thinking_enabled`` is explicitly ``False`` for the compactor. The
  compactor does **not** reuse the main answer's thinking-enabled model
  profile; it uses its own non-thinking model settings (R2 will wire
  the actual ``ModelSettings``).

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
from app.llm.types import ResolvedModelConfig, RunModelSettings
from app.services.reader_record_ask import web_search_common
from app.services.reader_record_ask.model_options import (
    ReaderAskRuntimeBudgetConfig,
    ResolvedReaderAskModelOption,
)
from app.services.reader_record_ask.web_search_adapter_registry import (
    WEB_SEARCH_CAPABILITY_POLICY_VERSION as _REGISTRY_POLICY_VERSION,
)
from app.services.reader_record_ask.web_search_contracts import (
    ResolvedWebSearchCapability,
    WebSearchMode,
)
from app.services.reader_record_ask.web_search_port import WebSearchBackend

logger = logging.getLogger(__name__)

# Policy version stamped on every snapshot. Bumped only when the
# resolver's *compilation semantics* change (new fields, new mapping
# rules). Option-level config drift is captured by ``budget_fingerprint``.
EXECUTION_CONFIG_POLICY_VERSION: str = "reader_record_ask_execution_v2"

# Web search capability policy version. Re-exported from the registry
# module so existing imports keep working. The registry is now the
# single source of truth for capability resolution.
WEB_SEARCH_CAPABILITY_POLICY_VERSION: str = _REGISTRY_POLICY_VERSION


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


@dataclass(frozen=True, slots=True)
class CompactorBudgetConfig:
    """R1A: thread-memory compactor budget placeholder (H9 handling).

    R1A compiles this config but does **not** invoke the compactor.
    R2 will consume it to build the compactor ``Model`` + ``ModelSettings``
    + ``UsageLimits`` and call the PydanticAI compactor agent.

    H9 convention
    --------------
    - ``response_format`` is documented here for observability only. The
      actual wire response format is derived by PydanticAI from the
      compactor agent's ``output_type`` (same mechanism as the main
      answer agent). This field is **not** forwarded to the provider
      wire directly; it exists so an operator can audit which schema
      the compactor will produce.
    - ``thinking_enabled`` is explicitly ``False``. The compactor does
      not reuse the main answer's thinking-enabled model profile — it
      uses its own non-thinking model settings (R2 will wire the
      actual ``ModelSettings``).
    - ``model_profile`` is the fixed Reader Ask *profile name*
      ``ask-main-deepseek-v4-flash``.  ``deepseek-v4-flash`` is the product
      option/model key and cannot be passed to the profile router directly.
      The compactor remains independent of the learner-selected answer model.
    """

    model_profile: str = "ask-main-deepseek-v4-flash"
    max_output_tokens: int = 2048  # §5 compactor_output_cap_tokens
    timeout_seconds: float = 10.0  # §5 compactor_timeout_seconds
    retry_count: int = 1  # §5 retry_count (共 2 次；2 次失败即 emergency)
    thinking_enabled: bool = False  # H9: 显式 disabled，不复用主答 thinking profile
    # H9: 仅文档化；实际 response_format 由 PydanticAI output_type 派生。
    response_format: str = "json_object"


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
    # G0-b6: web search capability echo (safe names only — no API key,
    # no provider payload). ``web_search_enabled_for_turn`` mirrors
    # :attr:`ResolvedWebSearchCapability.enabled_for_turn` so an operator
    # can audit whether the ``search_web`` tool was mounted without
    # re-running the resolver. ``web_search_provider`` /
    # ``web_search_protocol`` / ``web_search_policy_version`` identify
    # the resolved capability shape. ``None`` means the capability was
    # not resolved this turn (e.g. ``web_search_mode="disabled"``).
    web_search_enabled_for_turn: bool = False
    web_search_provider: str | None = Field(default=None, min_length=1, max_length=64)
    web_search_protocol: str | None = Field(default=None, min_length=1, max_length=64)
    web_search_policy_version: str | None = Field(default=None, min_length=1, max_length=64)


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
    # Server-only resolved model identity used for same-authority
    # projector routing. Never enter DTO/SSE/logs/repr (api_key).
    resolved_model_config: Any | None = field(default=None, repr=False)
    # Merged provider settings dict (max_tokens + temperature +
    # extra_body + extra_headers + ...). Excluded from repr — not
    # safely loggable; extra_headers may carry auth sentinels.
    model_settings_payload: dict[str, Any] = field(default_factory=dict, repr=False)
    # Host-side second-layer guard (PydanticAI UsageLimits).
    usage_limits: UsageLimits | None = None
    # Runtime budget actually applied (echoed in snapshot).
    runtime_budget: ReaderAskRuntimeBudgetConfig | None = None
    snapshot: ReaderRecordAskExecutionSnapshot | None = None
    # G0-b6: resolved web search capability for this turn. ``None`` when
    # ``web_search_mode="disabled"`` (the safe default). When non-None,
    # the runtime reads ``enabled_for_turn`` to decide whether to mount
    # the ``search_web`` tool (G1-b4) and to construct the
    # :class:`WebEvidenceRegistry` + inject the :class:`WebSearchBackend`
    # port into :class:`ReaderRecordAskDeps`. The model never reads this
    # object — it only observes the mounted tool.
    web_search_capability: ResolvedWebSearchCapability | None = None
    # G3-R3: executable backend produced by the same registry resolution
    # that produced ``web_search_capability``. ``None`` when capability
    # is ``None`` (disabled) or when capability is non-None but disabled
    # (adapter unverified / missing key / unsupported model). Carries
    # provider auth material — excluded from repr (callers should log
    # ``snapshot`` only). Send and retry MUST rebuild from the same
    # persisted model option + web_search_mode so the backend identity
    # is deterministic.
    web_search_backend: WebSearchBackend | None = field(default=None, repr=False)
    # R1A: thread-memory compactor budget placeholder. ``None`` when
    # ``settings.reader_record_ask_memory_enabled`` is False (default —
    # the assembly path behaves exactly as today). When non-None, R2
    # will consume this to build and invoke the compactor agent. R1A
    # only compiles the config; the compactor is NOT invoked.
    compactor_budget: CompactorBudgetConfig | None = None

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


def resolve_web_search_capability(
    *,
    web_search_mode: WebSearchMode,
    model_config: ResolvedModelConfig,
    settings: Settings | None = None,
) -> ResolvedWebSearchCapability | None:
    """Resolve the per-turn web search capability (G0-b6 / G1-R2 / G3-R3).

    Single source of truth for translating the user-visible request
    toggle (``web_search_mode``) into the server-owned execution truth
    (:class:`ResolvedWebSearchCapability`).

    Contract
    --------
    - ``web_search_mode="disabled"`` → returns ``None`` (capability not
      granted; the runtime must NOT mount the ``search_web`` tool).
    - ``web_search_mode="allowed"`` → delegates to the canonical
      :func:`resolve_web_search_binding` helper (G3-R1) which calls the
      production registry exactly once and returns the binding whose
      ``capability`` field is projected here. Callers that need BOTH
      capability AND backend MUST use
      :func:`resolve_reader_record_ask_execution` — never re-derive
      capability separately from the backend.
    - When the model config does not match any registered adapter (or
      the matching adapter cannot be constructed), the binding carries
      a non-None but disabled capability (``enabled_for_turn=False``)
      and ``None`` backend. Production must NEVER resolve to the
      ``fake`` protocol; the fake backend is test-only and is injected
      directly via the stream constructor
      (``web_search_backend=FakeWebSearchBackend(...)``).

    The capability is intentionally NOT part of the envelope fingerprint
    — it may change across retry without rewriting the fence identity.
    Only ``web_search_mode`` (the request toggle) enters the fingerprint.

    Fail-closed: any unknown / unsupported configuration returns a
    capability with ``enabled_for_turn=False`` (typed unavailable — the
    ``search_web`` tool returns ``unavailable``). The resolver never
    raises ``ReaderRecordAskExecutionUnavailable`` for web search
    because there is no "user required web search" path — the model
    can always fall back to article-grounded answers.
    """
    if web_search_mode == "disabled":
        return None

    # G3-R1: delegate to the canonical binding resolver. The helper
    # calls the production registry exactly once and returns the binding
    # produced by the same resolution call. ``settings`` is retained
    # for API compatibility but no longer drives the decision — the
    # global provider string is no longer consulted, and there is no
    # second registry instance constructed here.
    _ = settings or get_settings()  # may be used for future readiness flags
    binding = web_search_common.resolve_web_search_binding(model_config)
    return binding.capability


def resolve_reader_record_ask_execution(
    option: ResolvedReaderAskModelOption,
    *,
    web_search_mode: WebSearchMode = "disabled",
    settings: Settings | None = None,
) -> ReaderRecordAskExecutionConfig:
    """Compile a persisted option into a unified execution config.

    Single source of truth for both ``send`` and ``retry`` paths:
    both must call this resolver (or read a previously persisted
    snapshot) — never fall back to the global ``reader_ask`` route
    default when an option has been selected.

    ``web_search_mode`` is the user-visible request toggle carried on
    the context envelope. When ``allowed``, the resolver also resolves
    a :class:`ResolvedWebSearchCapability` and attaches it to the
    returned config so the runtime can mount the ``search_web`` tool
    (G1-b4) and inject the :class:`WebSearchBackend` port.

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

    # G3-R1: capability + backend produced by the SAME registry resolution
    # call via the canonical :func:`resolve_web_search_binding` helper.
    # The helper is the single source of truth — callers MUST NOT
    # re-derive capability from ``model_config`` separately, and MUST NOT
    # construct a second production registry instance. When
    # ``web_search_mode="disabled"`` the binding is short-circuited at
    # the resolver layer (capability=None, backend=None).
    if web_search_mode == "disabled":
        web_search_capability: ResolvedWebSearchCapability | None = None
        web_search_backend: WebSearchBackend | None = None
    else:
        binding = web_search_common.resolve_web_search_binding(model_config)
        web_search_capability = binding.capability
        web_search_backend = binding.backend

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
        web_search_enabled_for_turn=(
            web_search_capability is not None
            and web_search_capability.enabled_for_turn
        ),
        web_search_provider=(
            web_search_capability.provider if web_search_capability is not None else None
        ),
        web_search_protocol=(
            web_search_capability.protocol if web_search_capability is not None else None
        ),
        web_search_policy_version=(
            web_search_capability.policy_version
            if web_search_capability is not None
            else None
        ),
    )

    return ReaderRecordAskExecutionConfig(
        option_key=option.key,
        model=model,
        resolved_model_config=model_config,
        model_settings_payload=payload,
        usage_limits=usage_limits,
        runtime_budget=budget,
        snapshot=snapshot,
        web_search_capability=web_search_capability,
        web_search_backend=web_search_backend,
        # R1A: compile compactor budget placeholder when memory lane is
        # enabled. R1A does NOT invoke the compactor — R2 will consume
        # this config to build and call the compactor agent.
        compactor_budget=(
            CompactorBudgetConfig()
            if cfg.reader_record_ask_memory_enabled
            else None
        ),
    )


__all__ = [
    "EXECUTION_CONFIG_POLICY_VERSION",
    "WEB_SEARCH_CAPABILITY_POLICY_VERSION",
    "CompactorBudgetConfig",
    "ReaderRecordAskExecutionConfig",
    "ReaderRecordAskExecutionSnapshot",
    "ReaderRecordAskExecutionUnavailable",
    "resolve_reader_record_ask_execution",
    "resolve_web_search_capability",
]
