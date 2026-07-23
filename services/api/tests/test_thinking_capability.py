"""R4-A5-8A1: provider-thinking capability contract (offline)."""

from __future__ import annotations

from app.llm.thinking_capability import (
    apply_thinking_to_model_settings,
    resolve_thinking_capability,
    resolve_thinking_dialect,
)
from app.llm.types import RunModelSettings


def test_dialect_deepseek_direct_vs_dashscope_deepseek():
    assert (
        resolve_thinking_dialect(
            adapter="openai_compatible",
            provider="deepseek",
            model_name="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            provider_options={"profile": "deepseek_v4"},
        )
        == "deepseek_direct"
    )
    assert (
        resolve_thinking_dialect(
            adapter="dashscope_native",
            provider="dashscope",
            model_name="deepseek-v4-flash",
            base_url="",
            provider_options={},
        )
        == "dashscope_deepseek"
    )
    assert (
        resolve_thinking_dialect(
            adapter="openai_compatible",
            provider="dashscope-deepseek",
            model_name="deepseek-v4-flash",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            provider_options={"profile": "deepseek_v4"},
        )
        == "dashscope_deepseek"
    )
    assert (
        resolve_thinking_dialect(
            adapter="dashscope_native",
            provider="dashscope",
            model_name="qwen-flash",
            base_url="",
            provider_options={},
        )
        == "dashscope_qwen"
    )


def test_deepseek_direct_enable_and_strip_sampling():
    settings = RunModelSettings(
        temperature=0.7,
        top_p=0.9,
        presence_penalty=0.1,
        extra_body={"thinking": {"type": "enabled", "reasoning_effort": "high"}},
    )
    cap = resolve_thinking_capability(
        adapter="openai_compatible",
        provider="deepseek",
        model_name="deepseek-v4-pro",
        base_url="https://api.deepseek.com",
        provider_options={"profile": "deepseek_v4"},
        model_settings=settings,
    )
    assert cap.dialect == "deepseek_direct"
    assert cap.thinking_enabled is True
    assert cap.enable_payload_kind == "thinking_type_enabled"
    assert cap.reasoning_effort == "high"
    assert cap.strip_sampling_params is True
    assert cap.tool_round_must_return_thinking is True
    normalized = apply_thinking_to_model_settings(settings, cap)
    assert normalized is not None
    assert normalized.temperature is None
    assert normalized.top_p is None
    assert normalized.presence_penalty is None
    assert normalized.extra_body is not None
    # Direct DeepSeek V4: thinking is only {type: enabled}; effort is top-level.
    assert normalized.extra_body["thinking"] == {"type": "enabled"}
    assert "reasoning_effort" not in normalized.extra_body["thinking"]
    assert normalized.extra_body["reasoning_effort"] == "high"


def test_dashscope_qwen_enable_thinking_budget():
    settings = RunModelSettings(
        extra_body={"enable_thinking": True, "thinking_budget": 2048}
    )
    cap = resolve_thinking_capability(
        adapter="dashscope_native",
        provider="dashscope",
        model_name="qwen3-flash",
        model_settings=settings,
    )
    assert cap.dialect == "dashscope_qwen"
    assert cap.thinking_enabled is True
    assert cap.enable_payload_kind == "enable_thinking_bool"
    assert cap.thinking_budget == 2048
    assert cap.streaming_only is True
    assert cap.preserve_reasoning_on_history is True
    normalized = apply_thinking_to_model_settings(settings, cap)
    assert normalized is not None
    assert normalized.extra_body["enable_thinking"] is True
    assert normalized.extra_body["thinking_budget"] == 2048


def test_dashscope_deepseek_tool_round_preserve():
    settings = RunModelSettings(extra_body={"enable_thinking": True})
    cap = resolve_thinking_capability(
        adapter="dashscope_native",
        provider="dashscope",
        model_name="deepseek-v4-flash",
        model_settings=settings,
    )
    assert cap.dialect == "dashscope_deepseek"
    assert cap.tool_round_must_return_thinking is True
    assert cap.preserve_reasoning_on_history is True


def test_non_thinking_qwen_does_not_preserve():
    settings = RunModelSettings(extra_body={"enable_thinking": False})
    cap = resolve_thinking_capability(
        adapter="dashscope_native",
        provider="dashscope",
        model_name="qwen-flash",
        model_settings=settings,
    )
    assert cap.thinking_enabled is False
    assert cap.preserve_reasoning_on_history is False


def test_dialects_not_collapsed_to_generic_true():
    """Different dialects keep distinct enable payload kinds."""
    s = RunModelSettings(extra_body={"enable_thinking": True})
    ds = resolve_thinking_capability(
        adapter="openai_compatible",
        provider="deepseek",
        model_name="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        provider_options={"profile": "deepseek_v4"},
        model_settings=RunModelSettings(
            extra_body={"thinking": {"type": "enabled"}}
        ),
    )
    dq = resolve_thinking_capability(
        adapter="dashscope_native",
        provider="dashscope",
        model_name="qwen-flash",
        model_settings=s,
    )
    assert ds.enable_payload_kind != dq.enable_payload_kind
    assert ds.dialect != dq.dialect


def test_direct_deepseek_absent_mode_field_absent_no_effort_no_strip():
    """absent mode: no thinking field, effort None, no sampling strip.

    DeepSeek V4 default is thinking ON, so absent is a real third state
    (server default applies) — never conflated with disabled.
    """
    settings = RunModelSettings(
        temperature=0.5,
        extra_body={"thinking": {"type": "absent-unknown"}},
    )
    cap = resolve_thinking_capability(
        adapter="openai_compatible",
        provider="deepseek",
        model_name="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        model_settings=settings,
    )
    assert cap.dialect == "deepseek_direct"
    assert cap.direct_thinking_mode == "absent"
    assert cap.direct_thinking_enabled_on_wire is False
    assert cap.thinking_enabled is False
    assert cap.reasoning_effort is None
    assert cap.strip_sampling_params is False
    normalized = apply_thinking_to_model_settings(settings, cap)
    assert normalized is not None
    # absent mode removes the thinking field; if extra_body becomes empty
    # it is normalized to None (correct behaviour). The invariant is that
    # no thinking field survives on the wire payload.
    body = normalized.extra_body or {}
    assert "thinking" not in body, (
        "absent mode must remove the thinking field from the wire payload"
    )
    assert "reasoning_effort" not in body
    # No sampling stripping when thinking is not enabled.
    assert normalized.temperature == 0.5


def test_direct_deepseek_disabled_mode_emits_disabled_payload():
    """disabled mode: wire carries thinking={type: disabled}, no effort.

    V4's official default is thinking ON, so explicit off must emit the
    disabled payload — deleting the field would silently inherit ON.
    """
    settings = RunModelSettings(
        temperature=0.3,
        top_p=0.8,
        extra_body={
            "thinking": {"type": "disabled"},
            "reasoning_effort": "high",  # must be ignored
        },
    )
    cap = resolve_thinking_capability(
        adapter="openai_compatible",
        provider="deepseek",
        model_name="deepseek-v4-pro",
        base_url="https://api.deepseek.com",
        model_settings=settings,
    )
    assert cap.direct_thinking_mode == "disabled"
    assert cap.direct_thinking_enabled_on_wire is False
    assert cap.thinking_enabled is False
    assert cap.reasoning_effort is None, (
        "effort only applies when thinking is enabled"
    )
    assert cap.strip_sampling_params is False
    normalized = apply_thinking_to_model_settings(settings, cap)
    assert normalized is not None
    assert normalized.extra_body is not None
    assert normalized.extra_body["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in normalized.extra_body
    # Sampling params preserved because strip only fires when enabled.
    assert normalized.temperature == 0.3
    assert normalized.top_p == 0.8


def test_direct_deepseek_enabled_mode_keeps_effort_and_strips_sampling():
    """enabled mode: wire has {type: enabled}, top-level effort, sampling stripped.

    Cross-checks that enabled is the ONLY mode that strips sampling and
    emits reasoning_effort (absent and disabled do not).
    """
    settings = RunModelSettings(
        temperature=0.7,
        top_p=0.9,
        presence_penalty=0.1,
        frequency_penalty=0.2,
        extra_body={"thinking": {"type": "enabled", "reasoning_effort": "max"}},
    )
    cap = resolve_thinking_capability(
        adapter="openai_compatible",
        provider="deepseek",
        model_name="deepseek-v4-pro",
        base_url="https://api.deepseek.com",
        model_settings=settings,
    )
    assert cap.direct_thinking_mode == "enabled"
    assert cap.direct_thinking_enabled_on_wire is True
    assert cap.thinking_enabled is True
    assert cap.reasoning_effort == "max"
    assert cap.strip_sampling_params is True
    normalized = apply_thinking_to_model_settings(settings, cap)
    assert normalized is not None
    assert normalized.extra_body is not None
    assert normalized.extra_body["thinking"] == {"type": "enabled"}
    assert normalized.extra_body["reasoning_effort"] == "max"
    assert normalized.temperature is None
    assert normalized.top_p is None
    assert normalized.presence_penalty is None
    assert normalized.frequency_penalty is None


def test_direct_deepseek_modes_are_distinct_not_bool_collapse():
    """All three modes resolve to distinct direct_thinking_mode values.

    Guards against a regression that collapses absent with disabled (the
    classic bug when thinking is treated as a bool).
    """
    modes_seen: set[str] = set()
    for extra in (
        None,
        {"thinking": {"type": "enabled"}},
        {"thinking": {"type": "disabled"}},
        {"thinking": {"type": "weird"}},  # unknown → absent
        {"enable_thinking": True},  # DashScope key ignored on Direct path
    ):
        settings = RunModelSettings(extra_body=extra)
        cap = resolve_thinking_capability(
            adapter="openai_compatible",
            provider="deepseek",
            model_name="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            model_settings=settings,
        )
        modes_seen.add(cap.direct_thinking_mode)
    assert modes_seen == {"absent", "enabled", "disabled"}
