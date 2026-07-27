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
from app.services.reader_record_ask.web_search_contracts import (
    ResolvedWebSearchCapability,
    WebSearchMode,
    WebSearchProtocol,
)

logger = logging.getLogger(__name__)

# Policy version stamped on every snapshot. Bumped only when the
# resolver's *compilation semantics* change (new fields, new mapping
# rules). Option-level config drift is captured by ``budget_fingerprint``.
EXECUTION_CONFIG_POLICY_VERSION: str = "reader_record_ask_execution_v2"

# Web search capability policy version. Bumped only when the
# capability-resolution semantics change (new provider, new protocol,
# new max-calls / max-results mapping).
WEB_SEARCH_CAPABILITY_POLICY_VERSION: str = "reader_record_ask_web_search_v1"

# ASK-WEB-G1-R2: default capability shape constants. The fake backend
# is test-only — production paths must NEVER resolve to ``fake`` via
# this module. Tests inject :class:`FakeWebSearchBackend` directly via
# the stream constructor; they do NOT route through the resolver.
_DEFAULT_WEB_SEARCH_MAX_CALLS: int = 1
_DEFAULT_WEB_SEARCH_MAX_RESULTS_PER_CALL: int = 3

# Closed map: provider identifier (from Settings) → protocol. Only
# providers registered here AND backed by a real adapter in the
# production adapter registry may resolve to an enabled capability.
#
# ASK-WEB-G1-R3: this map is intentionally EMPTY. The previous entries
# (``dashscope_responses`` / ``deepseek_anthropic``) were reserved
# placeholders — they mapped protocol names to themselves without any
# real adapter being registered in the production adapter registry.
# That caused the resolver to return ``enabled_for_turn=True`` whenever
# the operator set ``settings.reader_record_ask_web_search_provider``
# to one of those names, even though no ``WebSearchBackend`` adapter
# existed in production. The runtime would then mount the
# ``search_web`` tool with no executable backend, producing a
# "假可用" (fake-available) capability.
#
# G2+ will populate this map only after:
#   1. a real ``WebSearchBackend`` adapter is implemented;
#   2. the adapter is registered in the production adapter registry;
#   3. the adapter can be constructed with the current settings;
#   4. required config (API key, endpoint) is present;
#   5. the adapter declares support for the current wire model.
# Until then, every production path returns
# ``enabled_for_turn=False`` (typed unavailable).
_SUPPORTED_WEB_SEARCH_PROTOCOLS: dict[str, WebSearchProtocol] = {}


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
    settings: Settings | None = None,
) -> ResolvedWebSearchCapability | None:
    """Resolve the per-turn web search capability (G0-b6 / G1-R2).

    Single source of truth for translating the user-visible request
    toggle (``web_search_mode``) into the server-owned execution truth
    (:class:`ResolvedWebSearchCapability`).

    Contract
    --------
    - ``web_search_mode="disabled"`` → returns ``None`` (capability not
      granted; the runtime must NOT mount the ``search_web`` tool).
    - ``web_search_mode="allowed"`` → returns a non-None capability
      ONLY when a real provider is wired via
      ``settings.reader_record_ask_web_search_provider``. When the
      provider is empty or not registered, returns a capability with
      ``enabled_for_turn=False`` (typed unavailable — never silently
      fake). Production must NEVER resolve to the fake protocol; the
      fake backend is test-only and is injected directly via the
      stream constructor (``web_search_backend=FakeWebSearchBackend(...)``).

    The capability is intentionally NOT part of the envelope fingerprint
    — it may change across retry without rewriting the fence identity.
    Only ``web_search_mode`` (the request toggle) enters the fingerprint.

    Fail-closed: any unknown / unsupported configuration returns a
    capability with ``enabled_for_turn=False`` (typed unavailable — the
    ``search_web`` tool returns ``unavailable``). The resolver never
    raises ``ReaderRecordAskExecutionUnavailable`` for web search
    because G0/G1 has no "user required web search" path — the model
    can always fall back to article-grounded answers.
    """
    if web_search_mode == "disabled":
        return None

    cfg = settings or get_settings()
    provider_id = cfg.reader_record_ask_web_search_provider.strip()
    protocol = _SUPPORTED_WEB_SEARCH_PROTOCOLS.get(provider_id)

    # No provider wired (default) OR provider not registered → typed
    # unavailable. The capability is retained for internal diagnostics,
    # but ``enabled_for_turn=False`` prevents the runtime from mounting
    # ``search_web`` or advertising the capability publicly.
    if protocol is None:
        return ResolvedWebSearchCapability(
            enabled_for_turn=False,
            # Use the requested provider id (safe — not a secret) when
            # non-empty so the snapshot records what the operator
            # configured. Empty string is normalised to ``unwired``.
            provider=provider_id or "unwired",
            protocol="fake",  # placeholder; never executed
            execution_mode="host_function",
            decision_mode="agent_auto",
            max_calls=_DEFAULT_WEB_SEARCH_MAX_CALLS,
            max_results_per_call=_DEFAULT_WEB_SEARCH_MAX_RESULTS_PER_CALL,
            policy_version=WEB_SEARCH_CAPABILITY_POLICY_VERSION,
        )

    # G2+ path: a real provider transport is wired. Until the G2 wire
    # probes pass, this branch is unreachable in production (no provider
    # id in ``_SUPPORTED_WEB_SEARCH_PROTOCOLS`` maps to a working
    # backend). When G2 lands, replace this branch with provider-aware
    # readiness resolution (API key presence, model capability probe).
    return ResolvedWebSearchCapability(
        enabled_for_turn=True,
        provider=provider_id,
        protocol=protocol,
        execution_mode="host_function",
        decision_mode="agent_auto",
        max_calls=_DEFAULT_WEB_SEARCH_MAX_CALLS,
        max_results_per_call=_DEFAULT_WEB_SEARCH_MAX_RESULTS_PER_CALL,
        policy_version=WEB_SEARCH_CAPABILITY_POLICY_VERSION,
    )


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

    web_search_capability = resolve_web_search_capability(
        web_search_mode=web_search_mode,
        settings=cfg,
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
        model_settings_payload=payload,
        usage_limits=usage_limits,
        runtime_budget=budget,
        snapshot=snapshot,
        web_search_capability=web_search_capability,
    )


__all__ = [
    "EXECUTION_CONFIG_POLICY_VERSION",
    "WEB_SEARCH_CAPABILITY_POLICY_VERSION",
    "ReaderRecordAskExecutionConfig",
    "ReaderRecordAskExecutionSnapshot",
    "ReaderRecordAskExecutionUnavailable",
    "resolve_reader_record_ask_execution",
    "resolve_web_search_capability",
]
