"""Provider-thinking capability contract (R4-A5-8A1).

Narrow, dialect-aware configuration for low-cost Ask routes that may enable
chain-of-thought / reasoning transport. Does **not** merge DeepSeek direct,
DashScope DeepSeek, and DashScope Qwen into a single ``thinking=true``
branch — each dialect has its own enable payload and continuation rules.

Privacy: this module only describes *how* to talk to providers. It never
logs or persists raw reasoning content.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.llm.types import (
    ModelAdapter,
    OpenAIProfileConfig,
    ResolvedModelConfig,
    RunModelSettings,
)

ThinkingDialect = Literal[
    "deepseek_direct",
    "dashscope_deepseek",
    "dashscope_qwen",
    "none",
]

# How thinking is enabled on the wire for this dialect.
EnablePayloadKind = Literal[
    "thinking_type_enabled",  # DeepSeek direct: extra_body.thinking.type=enabled
    "enable_thinking_bool",  # DashScope: enable_thinking=true
    "none",
]

# Direct DeepSeek V4 wire thinking state (R4-A5-8A1R2).
#
# DeepSeek V4's official default is thinking ON, so the Direct path must
# never conflate "field absent" with "thinking disabled". Product default
# is to send an explicit ``{"type": "enabled"}``; an explicit off must
# actually emit ``{"type": "disabled"}`` rather than delete the field.
DirectDeepSeekThinkingMode = Literal[
    "absent",  # no thinking field on wire (server default applies)
    "enabled",  # thinking: {"type": "enabled"}
    "disabled",  # thinking: {"type": "disabled"}
]


@dataclass(frozen=True, slots=True)
class ThinkingProviderCapability:
    """Resolved thinking transport contract for one model build.

    Fields are host-only control plane facts — never model-visible text.
    """

    dialect: ThinkingDialect
    thinking_enabled: bool
    enable_payload_kind: EnablePayloadKind
    # DeepSeek direct optional effort string (e.g. low/medium/high) when set.
    reasoning_effort: str | None
    # DashScope thinking_budget (token-ish budget) when set.
    thinking_budget: int | None
    # Whether the route is documented as streaming-only for thinking.
    streaming_only: bool
    # Provider field that carries raw reasoning (never user-facing).
    reasoning_field: str
    # Whether assistant history must echo ThinkingPart as reasoning_field
    # on tool-call turns (DeepSeek / DashScope DeepSeek requirement).
    tool_round_must_return_thinking: bool
    # DeepSeek direct thinking mode: omit meaningless sampling knobs.
    strip_sampling_params: bool
    # Direct DeepSeek V4 wire thinking state (absent/enabled/disabled).
    # Only meaningful for the ``deepseek_direct`` dialect; ``"absent"`` for
    # every other dialect. Drives tool_choice omission and reasoning_effort
    # so the wire payload reflects the *effective* thinking state, not a
    # bool that collapses absent with disabled.
    direct_thinking_mode: DirectDeepSeekThinkingMode = "absent"

    @property
    def preserve_reasoning_on_history(self) -> bool:
        """Whether message conversion must write reasoning_content back."""
        return (
            self.thinking_enabled
            and self.tool_round_must_return_thinking
            and self.dialect != "none"
        )

    @property
    def direct_thinking_enabled_on_wire(self) -> bool:
        """True only when Direct DeepSeek wire thinking is explicitly enabled."""
        return self.direct_thinking_mode == "enabled"


def _provider_looks_like_deepseek(
    *,
    provider: str,
    model_name: str,
    base_url: str,
    profile_hint: str,
    openai_profile: OpenAIProfileConfig | None,
) -> bool:
    blob = f"{provider} {model_name} {base_url} {profile_hint}".lower()
    if "deepseek" in blob:
        return True
    if profile_hint == "deepseek_v4":
        return True
    if openai_profile is not None:
        if openai_profile.openai_chat_thinking_field == "reasoning_content":
            # Ambiguous alone — need deepseek signal elsewhere.
            pass
    return False


def _provider_looks_like_qwen(*, provider: str, model_name: str) -> bool:
    blob = f"{provider} {model_name}".lower()
    return "qwen" in blob


def _base_url_looks_like_dashscope(base_url: str) -> bool:
    u = (base_url or "").lower()
    return "dashscope" in u or "aliyuncs.com" in u


def resolve_thinking_dialect(
    *,
    adapter: ModelAdapter,
    provider: str,
    model_name: str,
    base_url: str = "",
    provider_options: dict[str, object] | None = None,
    openai_profile: OpenAIProfileConfig | None = None,
) -> ThinkingDialect:
    """Classify the thinking dialect from transport + identity facts.

    Priority is adapter-first, then provider/model identity — never a
    bare ``model_name`` branch that would collapse dialects.
    """
    options = provider_options or {}
    profile_hint = str(options.get("profile", "") or "")
    is_deepseek = _provider_looks_like_deepseek(
        provider=provider,
        model_name=model_name,
        base_url=base_url,
        profile_hint=profile_hint,
        openai_profile=openai_profile,
    )
    is_qwen = _provider_looks_like_qwen(provider=provider, model_name=model_name)

    if adapter == "dashscope_native":
        if is_deepseek:
            return "dashscope_deepseek"
        if is_qwen:
            return "dashscope_qwen"
        # Native DashScope unknown family: treat as Qwen-like for thinking
        # enable (enable_thinking bool) when thinking is requested.
        return "dashscope_qwen"

    if adapter == "openai_compatible":
        if is_deepseek and _base_url_looks_like_dashscope(base_url):
            return "dashscope_deepseek"
        if is_deepseek:
            return "deepseek_direct"
        return "none"

    return "none"


def _thinking_flag_from_settings(settings: RunModelSettings | None) -> bool:
    if settings is None:
        return False
    return settings.thinking_enabled()


def _resolve_direct_deepseek_thinking_mode(
    settings: RunModelSettings | None,
) -> DirectDeepSeekThinkingMode:
    """Read the explicit Direct DeepSeek wire thinking state from settings.

    Distinguishes three states so the Direct path never conflates
    "field absent" (server default, V4 = ON) with "explicitly disabled".
    Only ``extra_body.thinking.type`` is consulted — ``enable_thinking``
    is a DashScope key and is ignored here.
    """
    if settings is None or not settings.extra_body:
        return "absent"
    thinking = settings.extra_body.get("thinking")
    if not isinstance(thinking, dict):
        return "absent"
    kind = thinking.get("type")
    if kind == "enabled":
        return "enabled"
    if kind == "disabled":
        return "disabled"
    return "absent"


# Direct DeepSeek V4 official effort values.
_DEEPSEEK_DIRECT_EFFORT_NATIVE: frozenset[str] = frozenset({"high", "max"})
# Deterministic aliases → native values (never pass through raw aliases).
_DEEPSEEK_DIRECT_EFFORT_ALIASES: dict[str, str] = {
    "low": "high",
    "medium": "high",
    "xhigh": "max",
}


class ThinkingEffortConfigError(ValueError):
    """Stable configuration error for unsupported Direct DeepSeek effort."""

    def __init__(self, raw: str) -> None:
        self.raw = raw
        super().__init__(
            "deepseek_direct_reasoning_effort_unsupported "
            f"value={raw!r} allowed=high|max aliases=low|medium|xhigh"
        )


def normalize_deepseek_direct_effort(raw: str | None) -> str | None:
    """Map effort for Direct DeepSeek V4 or raise a stable config error.

    - ``high`` / ``max``: pass through
    - ``low`` / ``medium`` → ``high``; ``xhigh`` → ``max``
    - any other non-empty string: :class:`ThinkingEffortConfigError`
    - ``None`` / empty: no effort field
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ThinkingEffortConfigError(repr(raw))
    value = raw.strip().lower()
    if not value:
        return None
    if value in _DEEPSEEK_DIRECT_EFFORT_NATIVE:
        return value
    if value in _DEEPSEEK_DIRECT_EFFORT_ALIASES:
        return _DEEPSEEK_DIRECT_EFFORT_ALIASES[value]
    raise ThinkingEffortConfigError(raw)


def _read_reasoning_effort(settings: RunModelSettings | None) -> str | None:
    if settings is None or not settings.extra_body:
        return None
    body = settings.extra_body
    # Prefer top-level reasoning_effort; accept nested for migration.
    effort = body.get("reasoning_effort")
    if isinstance(effort, str) and effort.strip():
        return effort.strip()
    thinking = body.get("thinking")
    if isinstance(thinking, dict):
        nested = thinking.get("reasoning_effort") or thinking.get("effort")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return None


def _read_thinking_budget(settings: RunModelSettings | None) -> int | None:
    if settings is None or not settings.extra_body:
        return None
    raw = settings.extra_body.get("thinking_budget")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int) and raw > 0:
        return raw
    if isinstance(raw, float) and raw > 0:
        return int(raw)
    if isinstance(raw, str):
        try:
            value = int(raw)
        except ValueError:
            return None
        return value if value > 0 else None
    return None


def resolve_thinking_capability(
    *,
    adapter: ModelAdapter,
    provider: str,
    model_name: str,
    base_url: str = "",
    provider_options: dict[str, object] | None = None,
    model_settings: RunModelSettings | None = None,
    openai_profile: OpenAIProfileConfig | None = None,
) -> ThinkingProviderCapability:
    """Build the dialect-specific thinking capability for one model config."""
    dialect = resolve_thinking_dialect(
        adapter=adapter,
        provider=provider,
        model_name=model_name,
        base_url=base_url,
        provider_options=provider_options,
        openai_profile=openai_profile,
    )
    enabled = _thinking_flag_from_settings(model_settings)

    if dialect == "deepseek_direct":
        mode = _resolve_direct_deepseek_thinking_mode(model_settings)
        enabled_on_wire = mode == "enabled"
        raw_effort = _read_reasoning_effort(model_settings) if enabled_on_wire else None
        effort = normalize_deepseek_direct_effort(raw_effort) if enabled_on_wire else None
        return ThinkingProviderCapability(
            dialect=dialect,
            thinking_enabled=enabled_on_wire,
            enable_payload_kind="thinking_type_enabled",
            reasoning_effort=effort,
            thinking_budget=None,
            streaming_only=False,
            reasoning_field="reasoning_content",
            tool_round_must_return_thinking=True,
            strip_sampling_params=enabled_on_wire,
            direct_thinking_mode=mode,
        )
    if dialect == "dashscope_deepseek":
        return ThinkingProviderCapability(
            dialect=dialect,
            thinking_enabled=enabled,
            enable_payload_kind="enable_thinking_bool",
            reasoning_effort=_read_reasoning_effort(model_settings) if enabled else None,
            thinking_budget=_read_thinking_budget(model_settings) if enabled else None,
            streaming_only=True,
            reasoning_field="reasoning_content",
            tool_round_must_return_thinking=True,
            strip_sampling_params=False,
        )
    if dialect == "dashscope_qwen":
        return ThinkingProviderCapability(
            dialect=dialect,
            thinking_enabled=enabled,
            enable_payload_kind="enable_thinking_bool",
            reasoning_effort=None,
            thinking_budget=_read_thinking_budget(model_settings) if enabled else None,
            streaming_only=True,
            reasoning_field="reasoning_content",
            # Qwen DashScope: preserve only when thinking is enabled so
            # non-thinking turns stay lean.
            tool_round_must_return_thinking=enabled,
            strip_sampling_params=False,
        )
    return ThinkingProviderCapability(
        dialect="none",
        thinking_enabled=False,
        enable_payload_kind="none",
        reasoning_effort=None,
        thinking_budget=None,
        streaming_only=False,
        reasoning_field="reasoning_content",
        tool_round_must_return_thinking=False,
        strip_sampling_params=False,
    )


def resolve_thinking_capability_from_config(
    model_config: ResolvedModelConfig,
) -> ThinkingProviderCapability:
    """Convenience wrapper over :class:`ResolvedModelConfig`."""
    return resolve_thinking_capability(
        adapter=model_config.adapter,
        provider=model_config.provider,
        model_name=model_config.model_name,
        base_url=model_config.base_url,
        provider_options=model_config.provider_options,
        model_settings=model_config.model_settings,
        openai_profile=model_config.openai_profile,
    )


def apply_thinking_to_model_settings(
    settings: RunModelSettings | None,
    capability: ThinkingProviderCapability,
) -> RunModelSettings | None:
    """Normalize model settings for the dialect's thinking enable payload.

    - Ensures the correct enable shape is present when thinking is on.
    - For DeepSeek direct thinking, strips temperature / top_p / penalties
      that the vendor documents as ignored or harmful.
    - Does not invent secrets or mutate global profiles.
    """
    if capability.dialect == "none":
        return settings

    base = (
        settings.model_copy(deep=True)
        if settings is not None
        else RunModelSettings()
    )
    body: dict[str, object] = dict(base.extra_body or {})

    if capability.thinking_enabled:
        if capability.enable_payload_kind == "thinking_type_enabled":
            # Direct DeepSeek V4: thinking is only {type: enabled}.
            # reasoning_effort is a **sibling** top-level extra_body key.
            body["thinking"] = {"type": "enabled"}
            body.pop("enable_thinking", None)
            # Strip any nested effort left over from older configs.
            if isinstance(body.get("thinking"), dict):
                nested = body["thinking"]
                if isinstance(nested, dict):
                    nested.pop("reasoning_effort", None)
                    nested.pop("effort", None)
            if capability.reasoning_effort:
                body["reasoning_effort"] = capability.reasoning_effort
            else:
                body.pop("reasoning_effort", None)
        elif capability.enable_payload_kind == "enable_thinking_bool":
            body["enable_thinking"] = True
            if capability.thinking_budget is not None:
                body["thinking_budget"] = capability.thinking_budget
            if capability.reasoning_effort is not None:
                # Some DashScope DeepSeek docs accept effort alongside enable.
                body.setdefault("reasoning_effort", capability.reasoning_effort)
    else:
        # Explicit off when dialect supports thinking — avoid accidental on.
        if capability.enable_payload_kind == "enable_thinking_bool":
            body.setdefault("enable_thinking", False)
        elif capability.enable_payload_kind == "thinking_type_enabled":
            # Direct DeepSeek three-state wire (R4-A5-8A1R2): V4 default is
            # thinking ON, so "disabled" must emit {"type": "disabled"}
            # rather than delete the field; "absent" leaves no field.
            if capability.direct_thinking_mode == "disabled":
                body["thinking"] = {"type": "disabled"}
                body.pop("enable_thinking", None)
            else:  # "absent"
                body.pop("thinking", None)
            body.pop("reasoning_effort", None)

    update: dict[str, Any] = {"extra_body": body if body else None}
    if capability.strip_sampling_params:
        update.update(
            {
                "temperature": None,
                "top_p": None,
                "presence_penalty": None,
                "frequency_penalty": None,
            }
        )
    return base.model_copy(update=update, deep=True)


def thinking_kwargs_for_dashscope(
    capability: ThinkingProviderCapability,
) -> dict[str, Any]:
    """Extra DashScope SDK kwargs derived from capability (no secrets)."""
    if not capability.thinking_enabled:
        return {"enable_thinking": False} if capability.dialect.startswith(
            "dashscope"
        ) else {}
    out: dict[str, Any] = {}
    if capability.enable_payload_kind == "enable_thinking_bool":
        out["enable_thinking"] = True
        if capability.thinking_budget is not None:
            out["thinking_budget"] = capability.thinking_budget
    return out


__all__ = [
    "DirectDeepSeekThinkingMode",
    "EnablePayloadKind",
    "ThinkingDialect",
    "ThinkingEffortConfigError",
    "ThinkingProviderCapability",
    "apply_thinking_to_model_settings",
    "normalize_deepseek_direct_effort",
    "resolve_thinking_capability",
    "resolve_thinking_capability_from_config",
    "resolve_thinking_dialect",
    "thinking_kwargs_for_dashscope",
]
