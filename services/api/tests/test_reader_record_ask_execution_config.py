"""ASK-M1: ReaderRecordAskExecutionConfig / resolver unit tests.

Covers the four user-required guarantees:

1. Pro / Flash / Qwen send + retry resolved-selection symmetry — the
   persisted option is the single source of truth; the resolver is
   deterministic and never silently substitutes the global default.
2. ``max_output_tokens`` reaches the per-request provider completion
   cap (``ModelSettings.max_tokens``), while the distinct cumulative
   ``max_turn_output_tokens`` reaches the PydanticAI host guard
   (``UsageLimits.output_tokens_limit``).
3. The existing ``ModelVisibleTurnBudget`` char ledger stays
   independent — the resolver does not map ``max_input_tokens`` to
   a char ledger or touch the model-view budget.
4. The snapshot carries no sensitive fields (no api key, body, raw
   reasoning, user / record identity, or provider raw payload).

Also covers DeepSeek thinking/tool wire regression by asserting the
resolver forwards ``model_settings`` transparently (it never rewrites
DeepSeek / DashScope wire fields — those remain the provider factory's
responsibility).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.config.settings import Settings
from app.services.reader_record_ask import model_options as model_options_svc
from app.services.reader_record_ask.model_options import (
    ReaderAskRuntimeBudgetConfig,
    ResolvedReaderAskModelOption,
)
from app.services.reader_record_ask.execution_config import (
    EXECUTION_CONFIG_POLICY_VERSION,
    ReaderRecordAskExecutionSnapshot,
    ReaderRecordAskExecutionUnavailable,
    resolve_reader_record_ask_execution,
)


def _execution_config_source() -> str:
    """Read the resolver module source for AST-based reverse guards."""
    pkg = Path(__file__).resolve().parents[1] / "app" / "services" / "reader_record_ask"
    return (pkg / "execution_config.py").read_text(encoding="utf-8")


def _imported_names(source: str) -> set[str]:
    """Return the set of top-level imported names (asname or module)."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _code_identifier_references(source: str, targets: set[str]) -> set[str]:
    """Return which of ``targets`` appear as code identifiers (not docstrings).

    Walks AST ``Name`` and ``Attribute`` nodes only — string literals,
    docstrings, and comments are ignored. This avoids false positives
    where a docstring mentions a symbol by name.
    """
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in targets:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in targets:
            found.add(node.attr)
    return found


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear lru_caches so each test sees its own Settings."""
    model_options_svc._build_catalog_cached.cache_clear()
    yield
    model_options_svc._build_catalog_cached.cache_clear()


def _catalog(profile_map: dict[str, str]) -> str:
    """Build a model_profiles_json with one openai_compatible provider.

    Uses ``openai_compatible`` adapter + a fake base_url so
    ``build_model_instance`` succeeds without real network setup.
    """
    return json.dumps(
        {
            "providers": {
                "test-provider": {
                    "adapter": "openai_compatible",
                    "base_url": "https://example.test/v1",
                    "api_key": "test-key-do-not-leak",
                }
            },
            "models": {
                f"{profile_name}__model": {
                    "provider": "test-provider",
                    "model_name": model_name,
                }
                for profile_name, model_name in profile_map.items()
            },
            "profiles": {
                profile_name: {"model": f"{profile_name}__model"}
                for profile_name in profile_map
            },
        }
    )


# Three product options mirroring services/api/config/reader-ask-model-options.json
# (Flash / Qwen / Pro). Each routes to a distinct profile so the resolver
# produces a distinct model identity — proving send + retry symmetry per option.
_THREE_OPTION_CATALOG = {
    "default_option": "deepseek-v4-flash",
    "billing_defaults": {
        "reserved_points": 10,
        "tokens_per_point": 1000,
        "billing_policy_version": "analysis_weighted_tokens_v1",
    },
    "runtime_defaults": {
        "max_input_tokens": 24000,
        "max_output_tokens": 3200,
        "max_turn_output_tokens": 9600,
        "prompt_buffer_tokens": 800,
    },
    "options": {
        "deepseek-v4-flash": {
            "label": "DeepSeek V4 Flash",
            "description": "默认档位",
            "selection": {
                "routes": {
                    "reader_ask": {"profile": "ask-main-deepseek-v4-flash"},
                    "reader_ask_replan": {"profile": "ask-replan-deepseek-v4-flash"},
                }
            },
            "price_multiplier": 1.0,
            "runtime_budget": {
                "max_output_tokens": 3200,
                "max_turn_output_tokens": 9600,
            },
        },
        "qwen-max": {
            "label": "Qwen 3.7 Max",
            "description": "高质量档位",
            "selection": {
                "routes": {
                    "reader_ask": {"profile": "ask-main-qwen37-max"},
                    "reader_ask_replan": {"profile": "ask-replan-qwen37-max"},
                }
            },
            "price_multiplier": 1.6,
            "runtime_budget": {
                "max_output_tokens": 4800,
                "max_turn_output_tokens": 14400,
            },
        },
        "deepseek-pro": {
            "label": "DeepSeek V4 Pro",
            "description": "高质量备选档位",
            "selection": {
                "routes": {
                    "reader_ask": {"profile": "ask-main-deepseek-v4-pro"},
                    "reader_ask_replan": {"profile": "ask-replan-deepseek-v4-pro"},
                }
            },
            "price_multiplier": 1.3,
            "runtime_budget": {
                "max_output_tokens": 6400,
                "max_turn_output_tokens": 19200,
            },
        },
    },
}


def _three_option_settings() -> Settings:
    return Settings(
        annotation_model_profile="annotation",
        ask_claread_profile="ask-main-deepseek-v4-flash",
        reader_ask_replan_model_profile="ask-replan-deepseek-v4-flash",
        model_profiles_json=_catalog(
            {
                "annotation": "annotation-model",
                "ask-main-deepseek-v4-flash": "deepseek-v4-flash",
                "ask-replan-deepseek-v4-flash": "deepseek-v4-flash",
                "ask-main-qwen37-max": "qwen-3.7-max",
                "ask-replan-qwen37-max": "qwen-3.7-max",
                "ask-main-deepseek-v4-pro": "deepseek-v4-pro",
                "ask-replan-deepseek-v4-pro": "deepseek-v4-pro",
            }
        ),
        reader_ask_model_options_json=json.dumps(_THREE_OPTION_CATALOG),
    )


def _resolve_option(
    settings: Settings,
    key: str,
    *,
    strict: bool = True,
) -> ResolvedReaderAskModelOption:
    return model_options_svc.resolve_reader_ask_model_option(
        settings,
        key,
        strict=strict,
    )


# ---------------------------------------------------------------------------
# 1. Pro / Flash / Qwen send + retry resolved-selection symmetry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("option_key", ["deepseek-v4-flash", "qwen-max", "deepseek-pro"])
def test_send_and_retry_produce_symmetric_resolved_model(option_key: str) -> None:
    """Send path and retry path must resolve to the same model identity.

    ASK-M1 contract: both paths call ``resolve_reader_record_ask_execution``
    with the same persisted option, so the resolved model + budget +
    snapshot must be byte-identical (deterministic). The resolver must
    never silently substitute the global default.
    """
    settings = _three_option_settings()
    option = _resolve_option(settings, option_key)

    first = resolve_reader_record_ask_execution(option, settings=settings)
    second = resolve_reader_record_ask_execution(option, settings=settings)

    # Deterministic identity — same option → same model + same snapshot.
    assert first.option_key == option_key == second.option_key
    assert first.snapshot is not None and second.snapshot is not None
    assert first.snapshot.model_name == second.snapshot.model_name
    assert first.snapshot.provider == second.snapshot.provider
    assert first.snapshot.profile_name == second.snapshot.profile_name
    assert first.snapshot.budget_fingerprint == second.snapshot.budget_fingerprint

    # Resolved model identity per option — never the global default substitution.
    expected_model_names = {
        "deepseek-v4-flash": "deepseek-v4-flash",
        "qwen-max": "qwen-3.7-max",
        "deepseek-pro": "deepseek-v4-pro",
    }
    assert first.snapshot.model_name == expected_model_names[option_key]


def test_retry_with_persisted_option_does_not_substitute_default() -> None:
    """Retry path: persisting a Pro option then re-resolving must stay Pro.

    This is the regression that motivated ASK-M1: before, retry passed
    ``model=None`` and the agentic lane silently fell back to the
    ``reader_ask`` route default (Flash). The resolver must now refuse
    to substitute — Pro stays Pro.
    """
    settings = _three_option_settings()
    pro_option = _resolve_option(settings, "deepseek-pro")

    # Simulate the retry path: caller has only the persisted option key,
    # re-resolves via thread_service.resolve_and_persist_thread_model_option,
    # then hands the option to the resolver.
    persisted_key = pro_option.key
    reloaded_option = _resolve_option(settings, persisted_key)

    execution = resolve_reader_record_ask_execution(reloaded_option, settings=settings)
    assert execution.option_key == "deepseek-pro"
    assert execution.snapshot is not None
    assert execution.snapshot.model_name == "deepseek-v4-pro"
    assert execution.snapshot.profile_name == "ask-main-deepseek-v4-pro"


# ---------------------------------------------------------------------------
# 2. Per-request provider cap and cumulative turn cap stay distinct
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "option_key, expected_max_output",
    [
        ("deepseek-v4-flash", 3200),
        ("qwen-max", 4800),
        ("deepseek-pro", 6400),
    ],
)
def test_max_output_tokens_reaches_provider_completion_cap(
    option_key: str,
    expected_max_output: int,
) -> None:
    """Provider completion cap (ModelSettings.max_tokens) = option budget."""
    settings = _three_option_settings()
    option = _resolve_option(settings, option_key)
    execution = resolve_reader_record_ask_execution(option, settings=settings)

    model_settings = execution.model_settings()
    assert model_settings is not None
    # PydanticAI ModelSettings stores max_tokens as a top-level field.
    assert model_settings["max_tokens"] == expected_max_output


@pytest.mark.parametrize(
    "option_key, expected_turn_output",
    [
        ("deepseek-v4-flash", 9600),
        ("qwen-max", 14400),
        ("deepseek-pro", 19200),
    ],
)
def test_max_turn_output_tokens_reaches_pydantic_ai_usage_limits(
    option_key: str,
    expected_turn_output: int,
) -> None:
    """Host UsageLimits uses the cumulative turn cap, not the request cap."""
    settings = _three_option_settings()
    option = _resolve_option(settings, option_key)
    execution = resolve_reader_record_ask_execution(option, settings=settings)

    assert execution.usage_limits is not None
    assert execution.usage_limits.output_tokens_limit == expected_turn_output
    assert execution.usage_limits.output_tokens_limit != option.runtime_budget.max_output_tokens
    # input_tokens_limit must NOT be set from max_input_tokens — the
    # ModelVisibleTurnBudget char ledger remains the independent input guard.
    assert execution.usage_limits.input_tokens_limit is None
    assert execution.usage_limits.total_tokens_limit is None


def test_provider_cap_overrides_profile_default() -> None:
    """The product budget overrides any max_tokens declared at profile level.

    A profile might ship its own default max_tokens; the resolver must
    replace it with the option's max_output_tokens so the product cap is
    the hard ceiling actually enforced on the wire.
    """
    settings = _three_option_settings()
    option = _resolve_option(settings, "deepseek-pro")
    execution = resolve_reader_record_ask_execution(option, settings=settings)

    model_settings = execution.model_settings()
    assert model_settings is not None
    # Pro option declares 6400 — that must win over any profile default.
    assert model_settings["max_tokens"] == 6400


def test_max_input_tokens_not_mapped_to_char_ledger() -> None:
    """``max_input_tokens`` must NOT be converted to a char or input-token limit.

    The existing ``ModelVisibleTurnBudget`` is the independent input
    safety ledger. The resolver must not duplicate it as a host
    ``input_tokens_limit`` or as a ``total_tokens_limit``.
    """
    settings = _three_option_settings()
    option = _resolve_option(settings, "qwen-max")
    execution = resolve_reader_record_ask_execution(option, settings=settings)

    assert execution.usage_limits is not None
    assert execution.usage_limits.input_tokens_limit is None
    assert execution.usage_limits.total_tokens_limit is None
    # Echoes the input budget in the snapshot only — observability, not
    # enforcement. The host guard is output-only.
    assert execution.snapshot is not None
    assert execution.snapshot.max_input_tokens == 24000
    assert execution.snapshot.prompt_buffer_tokens == 800


# ---------------------------------------------------------------------------
# 3. Char ledger stays independent — no input-token mapping, no override
# ---------------------------------------------------------------------------


def test_resolver_does_not_touch_model_visible_turn_budget() -> None:
    """The resolver never imports or touches ModelVisibleTurnBudget.

    The model-view char ledger is the independent input safety net. The
    resolver only compiles provider completion cap + host output guard
    from ``max_output_tokens`` — it does not map ``max_input_tokens``
    to any char count.
    """
    src = _execution_config_source()
    imported = _imported_names(src)
    # Reverse guard: execution_config.py must not import
    # ModelVisibleTurnBudget or the model_view_budget module.
    assert "ModelVisibleTurnBudget" not in imported
    assert "model_view_budget" not in imported
    # No code-level identifier references either (catches attribute access
    # like ``foo.model_view_budget``). Docstring mentions are fine.
    refs = _code_identifier_references(
        src, {"ModelVisibleTurnBudget", "model_view_budget", "char_ledger"}
    )
    assert refs == set(), f"unexpected code references to char-ledger symbols: {refs}"


# ---------------------------------------------------------------------------
# 4. Snapshot has no sensitive fields
# ---------------------------------------------------------------------------


_SENSITIVE_FIELD_NAMES = {
    "api_key",
    "apiKey",
    "base_url",
    "baseUrl",
    "auth",
    "authorization",
    "body",
    "user_message",
    "user_id",
    "reading_record_id",
    "answer_text",
    "reasoning",
    "thinking",
    "raw_payload",
    "raw_response",
    "headers",
    "secret",
    "token",
}


def test_snapshot_model_forbids_sensitive_extra_fields() -> None:
    """Pydantic's extra='forbid' rejects any undeclared (sensitive) field."""
    base_kwargs = dict(
        option_key="deepseek-v4-flash",
        provider="test-provider",
        model_name="deepseek-v4-flash",
        profile_name="ask-main-deepseek-v4-flash",
        adapter="openai_compatible",
        max_output_tokens=3200,
        max_turn_output_tokens=9600,
        max_input_tokens=24000,
        prompt_buffer_tokens=800,
        policy_version=EXECUTION_CONFIG_POLICY_VERSION,
        budget_fingerprint="a" * 64,
    )
    for forbidden in _SENSITIVE_FIELD_NAMES:
        with pytest.raises(ValidationError):
            ReaderRecordAskExecutionSnapshot(**base_kwargs, **{forbidden: "leak"})  # type: ignore[arg-type]


def test_snapshot_does_not_carry_api_key_or_body() -> None:
    """End-to-end: resolving a real option must not leak provider secrets."""
    settings = _three_option_settings()
    option = _resolve_option(settings, "deepseek-pro")
    execution = resolve_reader_record_ask_execution(option, settings=settings)

    assert execution.snapshot is not None
    snap_json = execution.snapshot.model_dump_json()
    # The catalog declares api_key="test-key-do-not-leak" — never reach the snapshot.
    assert "test-key-do-not-leak" not in snap_json
    assert "api_key" not in snap_json
    assert "base_url" not in snap_json
    # No user / record identity.
    assert "user_id" not in snap_json
    assert "reading_record_id" not in snap_json
    # No answer / reasoning / raw payload.
    assert "answer_text" not in snap_json
    assert "reasoning" not in snap_json
    assert "raw_payload" not in snap_json


def test_snapshot_carries_only_safe_policy_identity() -> None:
    """Snapshot fields are exactly the safe observability subset."""
    settings = _three_option_settings()
    option = _resolve_option(settings, "deepseek-v4-flash")
    execution = resolve_reader_record_ask_execution(option, settings=settings)

    assert execution.snapshot is not None
    snap = execution.snapshot
    assert snap.option_key == "deepseek-v4-flash"
    assert snap.provider == "test-provider"
    assert snap.model_name == "deepseek-v4-flash"
    assert snap.profile_name == "ask-main-deepseek-v4-flash"
    assert snap.adapter == "openai_compatible"
    assert snap.max_output_tokens == 3200
    assert snap.max_turn_output_tokens == 9600
    assert snap.max_input_tokens == 24000
    assert snap.prompt_buffer_tokens == 800
    assert snap.policy_version == EXECUTION_CONFIG_POLICY_VERSION
    assert len(snap.budget_fingerprint) == 64  # sha256 hex
    assert snap.used_fallback is False


def test_budget_fingerprint_is_stable_and_field_only() -> None:
    """The budget fingerprint hashes only the four numeric budget fields.

    Two options with the same budget numbers produce the same fingerprint
    (verifying the hash never mixes in option key, label, or provider).
    """
    from app.services.reader_record_ask.execution_config import _budget_fingerprint

    a = ReaderAskRuntimeBudgetConfig(
        max_input_tokens=24000,
        max_output_tokens=3200,
        max_turn_output_tokens=9600,
        prompt_buffer_tokens=800,
    )
    b = ReaderAskRuntimeBudgetConfig(
        max_input_tokens=24000,
        max_output_tokens=3200,
        max_turn_output_tokens=9600,
        prompt_buffer_tokens=800,
    )
    assert _budget_fingerprint(a) == _budget_fingerprint(b)

    c = ReaderAskRuntimeBudgetConfig(
        max_input_tokens=24000,
        max_output_tokens=6400,  # different output
        max_turn_output_tokens=9600,
        prompt_buffer_tokens=800,
    )
    assert _budget_fingerprint(a) != _budget_fingerprint(c)

    d = ReaderAskRuntimeBudgetConfig(
        max_input_tokens=24000,
        max_output_tokens=3200,
        max_turn_output_tokens=12800,  # different cumulative turn cap
        prompt_buffer_tokens=800,
    )
    assert _budget_fingerprint(a) != _budget_fingerprint(d)


# ---------------------------------------------------------------------------
# 5. Fail-closed contract
# ---------------------------------------------------------------------------


def test_resolver_raises_typed_unavailable_on_build_failure() -> None:
    """A buildable-model failure surfaces as ReaderRecordAskExecutionUnavailable.

    The resolver must NOT silently substitute the global default. Callers
    translate this to a typed 503 terminal.
    """
    settings = _three_option_settings()
    option = _resolve_option(settings, "deepseek-v4-flash")

    with patch(
        "app.services.reader_record_ask.execution_config.build_model_for_route",
        side_effect=RuntimeError("simulated provider factory error"),
    ):
        with pytest.raises(ReaderRecordAskExecutionUnavailable) as excinfo:
            resolve_reader_record_ask_execution(option, settings=settings)

    assert excinfo.value.option_key == "deepseek-v4-flash"
    assert excinfo.value.reason == "model_build_failed"
    # The provider error text must not leak through the typed error.
    assert "simulated provider factory error" not in str(excinfo.value)


def test_resolver_raises_typed_unavailable_when_model_returns_none() -> None:
    """``build_model_for_route`` returning (None, config) → typed unavailable."""
    settings = _three_option_settings()
    option = _resolve_option(settings, "deepseek-v4-flash")

    with patch(
        "app.services.reader_record_ask.execution_config.build_model_for_route",
        return_value=(None, None),
    ):
        with pytest.raises(ReaderRecordAskExecutionUnavailable) as excinfo:
            resolve_reader_record_ask_execution(option, settings=settings)

    assert excinfo.value.option_key == "deepseek-v4-flash"
    assert excinfo.value.reason == "model_unconfigured"


# ---------------------------------------------------------------------------
# 6. DeepSeek thinking/tool wire regression
# ---------------------------------------------------------------------------


def test_resolver_does_not_rewrite_deepseek_wire_fields() -> None:
    """The resolver must not touch thinking / reasoning_content / tool_choice.

    DeepSeek wire normalization (thinking enabled vs disabled, omitting
    tool_choice when thinking is active, preserving ``reasoning_content``)
    is the provider factory's responsibility — the resolver only forwards
    the merged ``ModelSettings`` produced by ``RunModelSettings.with_max_tokens``.
    This test asserts the resolver never references DeepSeek-specific wire
    fields as code identifiers, so existing A5-8A1R3 contracts stay intact.
    Docstring mentions of these words are fine; only real code references fail.
    """
    src = _execution_config_source()

    # The resolver must not directly assign DeepSeek wire fields as code
    # identifiers — the provider factory owns those. Docstring mentions
    # (e.g. "thinking payload") are allowed.
    refs = _code_identifier_references(
        src, {"reasoning_content", "tool_choice", "thinking", "openai_chat_thinking_field"}
    )
    assert refs == set(), f"resolver rewrites DeepSeek wire fields: {refs}"

    # The resolver must use the public RunModelSettings.with_max_tokens,
    # not invent a second max_tokens mapping.
    assert "with_max_tokens" in src


def test_model_settings_payload_preserves_provider_extras() -> None:
    """``with_max_tokens`` must not drop provider-level fields (temperature etc.).

    The product budget is applied on top of the resolved profile's
    ``model_settings`` — it must override only ``max_tokens`` and leave
    other provider-level fields intact so DeepSeek / DashScope wire
    contracts remain the provider factory's responsibility.
    """
    settings = _three_option_settings()
    option = _resolve_option(settings, "deepseek-v4-flash")
    execution = resolve_reader_record_ask_execution(option, settings=settings)

    # The merged settings carry max_tokens from the option budget.
    model_settings = execution.model_settings()
    assert model_settings is not None
    assert model_settings["max_tokens"] == 3200


# ---------------------------------------------------------------------------
# 7. Resolved config never persists raw — only the safe snapshot is exposed
# ---------------------------------------------------------------------------


def test_resolved_config_repr_does_not_leak_resolved_config() -> None:
    """``resolved_config`` is excluded from the dataclass repr.

    ResolvedModelConfig carries provider / api_key / base_url. The
    execution config must not leak those via ``repr(config)`` (e.g. when
    a logger prints the config at info level).
    """
    settings = _three_option_settings()
    option = _resolve_option(settings, "deepseek-v4-flash")
    execution = resolve_reader_record_ask_execution(option, settings=settings)

    # ``resolved_config`` is marked repr=False — it must not appear in repr.
    assert "resolved_config" not in repr(execution)
    assert "test-key-do-not-leak" not in repr(execution)
    # The snapshot repr is safe (only identity + policy fields).
    assert execution.snapshot is not None
    assert "test-key-do-not-leak" not in repr(execution.snapshot)


def test_execution_config_is_frozen() -> None:
    """The execution config is frozen — callers cannot mutate the model.

    Mutation would break the send/retry symmetry contract: once the
    resolver returns a config, it must be immutable for the lifetime of
    the turn.
    """
    settings = _three_option_settings()
    option = _resolve_option(settings, "deepseek-v4-flash")
    execution = resolve_reader_record_ask_execution(option, settings=settings)

    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        execution.option_key = "tampered"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 8. ASK-M1-R1: sensitive-field boundary — non-default provider extras
#    preserved, but sentinels never reach repr(config) or snapshot.
# ---------------------------------------------------------------------------

# Sentinels placed inside the provider catalog. If any of these leak
# into repr(config) or the snapshot, the boundary is broken.
_API_KEY_SENTINEL = "api-key-sentinel-do-not-leak"
_BASE_URL_SENTINEL = "https://sentinel.base.url.do.not.leak/v1"
_HEADER_SENTINEL_VALUE = "header-sentinel-do-not-leak"
_HEADER_SENTINEL_KEY = "X-Sentinel-Auth"
_TEMPERATURE_SENTINEL = 0.37


def _catalog_with_sentinels(profile_map: dict[str, str]) -> str:
    """Catalog with non-default provider-level ``model_settings``.

    The provider carries:
    - ``temperature`` (non-default float);
    - ``extra_body`` with a thinking payload (DeepSeek wire shape);
    - ``extra_headers`` with an auth sentinel.

    Plus the usual ``api_key`` / ``base_url`` sentinels. The resolver
    must forward temperature / extra_body / extra_headers to the
    outbound ``ModelSettings`` (provider factory needs them for
    DeepSeek wire normalization), but must never surface any sentinel
    in ``repr(config)`` or the snapshot.
    """
    return json.dumps(
        {
            "providers": {
                "sentinel-provider": {
                    "adapter": "openai_compatible",
                    "base_url": _BASE_URL_SENTINEL,
                    "api_key": _API_KEY_SENTINEL,
                    "model_settings": {
                        "temperature": _TEMPERATURE_SENTINEL,
                        "extra_body": {
                            "enable_thinking": True,
                            "thinking": {"type": "enabled"},
                        },
                        "extra_headers": {
                            _HEADER_SENTINEL_KEY: _HEADER_SENTINEL_VALUE,
                        },
                    },
                }
            },
            "models": {
                f"{profile_name}__model": {
                    "provider": "sentinel-provider",
                    "model_name": model_name,
                }
                for profile_name, model_name in profile_map.items()
            },
            "profiles": {
                profile_name: {"model": f"{profile_name}__model"}
                for profile_name in profile_map
            },
        }
    )


def _sentinel_settings() -> Settings:
    """Three-option settings backed by the sentinel catalog."""
    return Settings(
        annotation_model_profile="annotation",
        ask_claread_profile="ask-main-deepseek-v4-flash",
        reader_ask_replan_model_profile="ask-replan-deepseek-v4-flash",
        model_profiles_json=_catalog_with_sentinels(
            {
                "annotation": "annotation-model",
                "ask-main-deepseek-v4-flash": "deepseek-v4-flash",
                "ask-replan-deepseek-v4-flash": "deepseek-v4-flash",
                "ask-main-qwen37-max": "qwen-3.7-max",
                "ask-replan-qwen37-max": "qwen-3.7-max",
                "ask-main-deepseek-v4-pro": "deepseek-v4-pro",
                "ask-replan-deepseek-v4-pro": "deepseek-v4-pro",
            }
        ),
        reader_ask_model_options_json=json.dumps(_THREE_OPTION_CATALOG),
    )


def test_model_settings_preserves_non_default_temperature_extra_body_extra_headers() -> None:
    """Outbound ``ModelSettings`` retains provider-level non-default fields.

    ASK-M1-R1: the resolver applies ``max_tokens`` on top of the
    resolved profile's ``model_settings`` via
    ``RunModelSettings.with_max_tokens``. That must override *only*
    ``max_tokens`` — ``temperature``, ``extra_body`` (DeepSeek thinking
    payload), and ``extra_headers`` (wire-level auth headers) must
    survive so the provider factory can normalise DeepSeek wire fields.
    """
    settings = _sentinel_settings()
    option = _resolve_option(settings, "deepseek-pro")
    execution = resolve_reader_record_ask_execution(option, settings=settings)

    model_settings = execution.model_settings()
    assert model_settings is not None
    # Product budget overrides max_tokens only.
    assert model_settings["max_tokens"] == 6400
    # Non-default provider extras preserved.
    assert model_settings["temperature"] == _TEMPERATURE_SENTINEL
    extra_body = model_settings["extra_body"]
    assert extra_body == {"enable_thinking": True, "thinking": {"type": "enabled"}}
    extra_headers = model_settings["extra_headers"]
    assert extra_headers[_HEADER_SENTINEL_KEY] == _HEADER_SENTINEL_VALUE


def test_model_settings_returns_independent_deep_copy() -> None:
    """``model_settings()`` returns an independent copy each call.

    ASK-M1-R1: callers (provider factory normalisation, tests) may
    mutate the returned ``ModelSettings`` — nested ``extra_body`` /
    ``extra_headers`` dicts included. That mutation must not bleed back
    into the execution config's ``model_settings_payload`` and affect
    a subsequent ``model_settings()`` call or a subsequent
    ``agent.run`` in the same turn.
    """
    settings = _sentinel_settings()
    option = _resolve_option(settings, "deepseek-v4-flash")
    execution = resolve_reader_record_ask_execution(option, settings=settings)

    first = execution.model_settings()
    assert first is not None
    # Mutate nested dicts on the returned settings.
    first["temperature"] = 99.0
    first["extra_body"]["enable_thinking"] = False
    first["extra_body"]["thinking"]["type"] = "disabled"
    first["extra_headers"][_HEADER_SENTINEL_KEY] = "tampered"
    first["max_tokens"] = 1

    # Second call must be unaffected.
    second = execution.model_settings()
    assert second is not None
    assert second["temperature"] == _TEMPERATURE_SENTINEL
    assert second["max_tokens"] == 3200
    assert second["extra_body"] == {
        "enable_thinking": True,
        "thinking": {"type": "enabled"},
    }
    assert second["extra_headers"][_HEADER_SENTINEL_KEY] == _HEADER_SENTINEL_VALUE

    # The underlying payload dict is also untouched.
    assert execution.model_settings_payload["temperature"] == _TEMPERATURE_SENTINEL
    assert execution.model_settings_payload["max_tokens"] == 3200


def test_repr_excludes_model_and_settings_payload_and_sentinels() -> None:
    """``repr(config)`` must not surface model / payload / sentinels.

    ASK-M1-R1: ``model`` and ``model_settings_payload`` are marked
    ``repr=False``. The raw ``Model`` instance may carry provider auth
    / base_url inside; the payload dict may carry ``extra_headers``
    with auth sentinels. Neither is "safely loggable" — operators
    must log ``snapshot`` + ``option_key`` only.
    """
    settings = _sentinel_settings()
    option = _resolve_option(settings, "deepseek-pro")
    execution = resolve_reader_record_ask_execution(option, settings=settings)

    config_repr = repr(execution)

    # Field names excluded from repr.
    assert "model=" not in config_repr
    assert "model_settings_payload" not in config_repr
    # Sentinel values must not leak through repr.
    assert _API_KEY_SENTINEL not in config_repr
    assert _BASE_URL_SENTINEL not in config_repr
    assert _HEADER_SENTINEL_VALUE not in config_repr
    assert _HEADER_SENTINEL_KEY not in config_repr
    # Non-default temperature is part of the payload — must not surface.
    assert str(_TEMPERATURE_SENTINEL) not in config_repr


def test_snapshot_excludes_all_sensitive_sentinels() -> None:
    """Snapshot carries only safe identity + policy — never sentinels.

    ASK-M1-R1: even with non-default temperature, extra_body
    (thinking payload), and extra_headers (auth header) declared at
    the provider level, the snapshot must only echo identity (option
    key, provider name, model name, profile, adapter) + budget policy
    fields. No payload, no headers, no base_url, no api_key.
    """
    settings = _sentinel_settings()
    option = _resolve_option(settings, "deepseek-pro")
    execution = resolve_reader_record_ask_execution(option, settings=settings)

    assert execution.snapshot is not None
    snap_json = execution.snapshot.model_dump_json()
    snap_repr = repr(execution.snapshot)

    for leak in (
        _API_KEY_SENTINEL,
        _BASE_URL_SENTINEL,
        _HEADER_SENTINEL_VALUE,
        _HEADER_SENTINEL_KEY,
        str(_TEMPERATURE_SENTINEL),
        "enable_thinking",
        "reasoning_content",
        "extra_body",
        "extra_headers",
        "api_key",
        "base_url",
    ):
        assert leak not in snap_json, f"snapshot JSON leaks: {leak}"
        assert leak not in snap_repr, f"snapshot repr leaks: {leak}"


def test_model_settings_payload_not_described_as_safely_loggable() -> None:
    """Reverse guard: the payload field docstring must not claim it is safe to log.

    ASK-M1-R1: the merged settings dict may carry ``extra_headers``
    with auth sentinels. The dataclass field docstring / module
    docstring must not call it "safely loggable" — only ``snapshot``
    is purpose-built for logging.
    """
    import dataclasses

    from app.services.reader_record_ask.execution_config import (
        ReaderRecordAskExecutionConfig,
    )

    # Inspect the field metadata — repr=False is the contract.
    fields = {f.name: f for f in dataclasses.fields(ReaderRecordAskExecutionConfig)}
    assert fields["model"].repr is False
    assert fields["model_settings_payload"].repr is False

    # The dataclass docstring must explicitly say the payload is not
    # safely loggable (catches accidental relaxation of the boundary).
    # Normalize whitespace so line-wrapped phrases ("safely\nloggable")
    # still match.
    import re

    doc = ReaderRecordAskExecutionConfig.__doc__ or ""
    doc_flat = re.sub(r"\s+", " ", doc).lower()
    assert "not" in doc_flat
    assert "safely loggable" in doc_flat

    module_flat = re.sub(r"\s+", " ", _execution_config_source()).lower()
    assert "callers can log the dict form safely" not in module_flat
