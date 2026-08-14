"""G3-Web Search adapter registry + capability resolution (OFFLINE).

Tests the unified runtime binding contract:

1. Capability is derived from the current ``ResolvedModelConfig`` —
   not from a global provider string or option label.
2. One resolution produces BOTH ``ResolvedWebSearchCapability`` AND
   an executable ``WebSearchBackend`` — never separately.
3. ``provider`` / ``adapter`` / ``model_name`` / ``base_url`` /
   ``credential`` / adapter readiness all enter the decision.
4. ``web_search_mode="disabled"`` returns ``None`` (capability not
   granted; runtime must NOT mount ``search_web``).
5. ``web_search_mode="allowed"`` + adapter unverified / missing key /
   unsupported model → ``enabled_for_turn=False`` (typed unavailable).
6. Production never resolves to ``fake`` protocol.
7. Persisted ``allowed`` + adapter cannot construct → caller emits
   pre-stream typed 503.

All tests are OFFLINE — adapter HTTP calls are mocked at the registry
boundary by injecting test-only adapter factories.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.llm.types import ModelAdapter, ResolvedModelConfig
from app.services.reader_record_ask.model_options import (
    ReaderAskRuntimeBudgetConfig,
    ResolvedReaderAskModelOption,
)
from app.services.reader_record_ask.deepseek_anthropic_web_search_backend import (
    DeepseekAnthropicWebSearchBackend,
)
from app.services.reader_record_ask.execution_config import (
    WEB_SEARCH_CAPABILITY_POLICY_VERSION,
    resolve_reader_record_ask_execution,
    resolve_web_search_capability,
)
from app.services.reader_record_ask.qwen_dashscope_web_search_backend import (
    QwenDashscopeWebSearchBackend,
)
from app.services.reader_record_ask.web_search_adapter_registry import (
    ResolvedWebSearchBinding,
    WebSearchAdapterRegistry,
    build_production_web_search_adapter_registry,
)
from app.services.reader_record_ask.web_search_contracts import (
    ResolvedWebSearchCapability,
)
from app.services.reader_record_ask.web_search_port import (
    WebSearchBackend,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _qwen_model_config(
    *,
    api_key: str = "sk-qwen-test-KEY-12345",
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    model_name: str = "qwen3.7-max",
) -> ResolvedModelConfig:
    return ResolvedModelConfig(
        route="reader_ask",
        profile_name="qwen-max",
        provider="dashscope",
        adapter="openai_compatible",
        model_name=model_name,
        base_url=base_url,
        api_key=api_key,
    )


def _deepseek_model_config(
    *,
    api_key: str = "sk-deepseek-test-KEY-67890",
    base_url: str = "https://api.deepseek.com/anthropic",
    model_name: str = "deepseek-v4-flash",
) -> ResolvedModelConfig:
    return ResolvedModelConfig(
        route="reader_ask",
        profile_name="deepseek-flash",
        provider="deepseek",
        adapter="openai_compatible",
        model_name=model_name,
        base_url=base_url,
        api_key=api_key,
    )


def _unconfigured_model_config(
    *,
    provider: str = "unknown",
    adapter: ModelAdapter = "openai_compatible",
    api_key: str = "",
    base_url: str = "",
    model_name: str = "some-model",
) -> ResolvedModelConfig:
    return ResolvedModelConfig(
        route="reader_ask",
        profile_name="unknown",
        provider=provider,
        adapter=adapter,
        model_name=model_name,
        base_url=base_url,
        api_key=api_key,
    )


@dataclass(slots=True)
class _StubBackend:
    """Marker backend for tests — distinguishable from real adapters."""

    marker: str

    async def search_web(
        self,
        *,
        query: str,
        max_results: int,
    ) -> Any: # noqa:
        raise AssertionError(
            "Stub backend should never be called from registry tests"
        )


def _stub_backend_factory(marker: str):
    def factory(*, api_key: str, model_name: str, base_url: str) -> WebSearchBackend:
        return _StubBackend(marker=marker)

    return factory


# ---------------------------------------------------------------------------
# ResolvedWebSearchBinding contract
# ---------------------------------------------------------------------------


class TestResolvedWebSearchBindingContract:
    def test_binding_carries_capability_and_backend(self) -> None:
        cap = ResolvedWebSearchCapability(
            enabled_for_turn=True,
            provider="dashscope",
            protocol="dashscope_responses",
            execution_mode="host_function",
            decision_mode="agent_auto",
            max_calls=1,
            max_results_per_call=3,
            policy_version=WEB_SEARCH_CAPABILITY_POLICY_VERSION,
        )
        backend = _StubBackend(marker="qwen")
        binding = ResolvedWebSearchBinding(
            capability=cap,
            backend=backend,
        )
        assert binding.capability is cap
        assert binding.backend is backend

    def test_disabled_binding_has_none_capability_and_none_backend(self) -> None:
        binding = ResolvedWebSearchBinding(
            capability=None,
            backend=None,
        )
        assert binding.capability is None
        assert binding.backend is None


# ---------------------------------------------------------------------------
# Adapter registry: provider + adapter + model_name match
# ---------------------------------------------------------------------------


class TestAdapterRegistryProviderMatch:
    def test_qwen_provider_with_openai_compatible_adapter_resolves(self) -> None:
        registry = WebSearchAdapterRegistry()
        registry.register_qwen(_stub_backend_factory("qwen"))
        cfg = _qwen_model_config()
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is True
        assert binding.capability.provider == "dashscope"
        assert binding.capability.protocol == "dashscope_responses"
        assert isinstance(binding.backend, _StubBackend)
        assert binding.backend.marker == "qwen"  # type: ignore[attr-defined]

    def test_deepseek_provider_with_anthropic_base_url_resolves(self) -> None:
        registry = WebSearchAdapterRegistry()
        registry.register_deepseek(_stub_backend_factory("deepseek"))
        cfg = _deepseek_model_config()
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is True
        assert binding.capability.provider == "deepseek"
        assert binding.capability.protocol == "deepseek_anthropic"
        assert isinstance(binding.backend, _StubBackend)
        assert binding.backend.marker == "deepseek"  # type: ignore[attr-defined]

    def test_unknown_provider_returns_unavailable_binding(self) -> None:
        registry = WebSearchAdapterRegistry()
        cfg = _unconfigured_model_config(provider="totally_unknown")
        binding = registry.resolve(model_config=cfg)
        # Unavailable binding: capability is non-None but disabled; backend is None.
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is False
        assert binding.backend is None

    def test_qwen_provider_missing_api_key_returns_unavailable(self) -> None:
        registry = WebSearchAdapterRegistry()
        registry.register_qwen(_stub_backend_factory("qwen"))
        cfg = _qwen_model_config(api_key="")
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is False
        assert binding.backend is None

    def test_deepseek_provider_missing_api_key_returns_unavailable(self) -> None:
        registry = WebSearchAdapterRegistry()
        registry.register_deepseek(_stub_backend_factory("deepseek"))
        cfg = _deepseek_model_config(api_key="")
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is False
        assert binding.backend is None

    def test_qwen_provider_wrong_adapter_returns_unavailable(self) -> None:
        """Genuinely unknown adapters must not match the Qwen registry."""
        registry = WebSearchAdapterRegistry()
        registry.register_qwen(_stub_backend_factory("qwen"))
        cfg = _qwen_model_config()
        # Force a genuinely unknown adapter — not "dashscope_native"
        # (which IS a valid Qwen adapter per G3 production wiring).
        cfg = cfg.model_copy(update={"adapter": "totally_unknown_adapter"})
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is False
        assert binding.backend is None

    def test_qwen_dashscope_native_adapter_resolves(self) -> None:
        """dashscope_native adapter IS a valid Qwen path (production wiring).

        The production qwen-max option resolves with provider="dashscope_native"
        and adapter="dashscope_native" (the native SDK transport for chat).
        Web Search still routes through the Responses API endpoint, which
        the registry constructs from the canonical default URL when the
        resolved base_url is empty.
        """
        registry = WebSearchAdapterRegistry()
        registry.register_qwen(_stub_backend_factory("qwen"))
        cfg = _qwen_model_config()
        cfg = cfg.model_copy(
            update={
                "provider": "dashscope_native",
                "adapter": "dashscope_native",
                "base_url": "",  # native SDK has no OpenAI-compat URL
            }
        )
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is True
        assert binding.capability.provider == "dashscope"
        assert binding.capability.protocol == "dashscope_responses"
        assert isinstance(binding.backend, _StubBackend)
        assert binding.backend.marker == "qwen"  # type: ignore[attr-defined]

    def test_deepseek_with_chat_completions_base_url_resolves(self) -> None:
        """DeepSeek production model configs use ``/v1`` (chat-completions),
        not ``/anthropic``. The registry must still resolve them — Web
        Search always targets the Anthropic-compat endpoint per contract.
        """
        registry = WebSearchAdapterRegistry()
        registry.register_deepseek(_stub_backend_factory("deepseek"))
        cfg = _deepseek_model_config(base_url="https://api.deepseek.com/v1")
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is True
        assert binding.capability.provider == "deepseek"
        assert binding.capability.protocol == "deepseek_anthropic"
        assert isinstance(binding.backend, _StubBackend)
        assert binding.backend.marker == "deepseek"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Capability + backend same-binding (no separate judgement)
# ---------------------------------------------------------------------------


class TestCapabilityAndBackendSameBinding:
    def test_when_backend_unconstructible_capability_is_disabled(self) -> None:
        """If the adapter factory raises, capability must NOT be enabled."""

        def failing_factory(
            *, api_key: str, model_name: str, base_url: str
        ) -> WebSearchBackend:
            raise RuntimeError("simulated adapter construction failure")

        registry = WebSearchAdapterRegistry()
        registry.register_qwen(failing_factory)
        cfg = _qwen_model_config()
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is False
        assert binding.backend is None

    def test_disabled_mode_returns_none_none_binding_via_resolver(self) -> None:
        """``web_search_mode="disabled"`` → binding is None/None."""
        cfg = _qwen_model_config()
        cap = resolve_web_search_capability(
            web_search_mode="disabled",
            model_config=cfg,
        )
        assert cap is None


# ---------------------------------------------------------------------------
# Production adapter registry: real Qwen + DeepSeek factories
# ---------------------------------------------------------------------------


class TestProductionRegistryRealFactories:
    def test_production_registry_resolves_qwen_to_real_backend(self) -> None:
        registry = build_production_web_search_adapter_registry()
        cfg = _qwen_model_config()
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is True
        assert binding.capability.protocol == "dashscope_responses"
        assert isinstance(binding.backend, QwenDashscopeWebSearchBackend)
        # ASK-WEB-capability, backend, and Coordinator execution
        # upper bound must come from the same configuration fact.
        assert binding.capability.max_results_per_call == 5
        assert binding.backend.max_results_per_call == 5
        assert (
            binding.capability.max_results_per_call
            == binding.backend.max_results_per_call
        )

    def test_production_registry_resolves_deepseek_to_real_backend(self) -> None:
        registry = build_production_web_search_adapter_registry()
        cfg = _deepseek_model_config()
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is True
        assert binding.capability.protocol == "deepseek_anthropic"
        assert isinstance(binding.backend, DeepseekAnthropicWebSearchBackend)
        # ASK-WEB-capability, backend, and Coordinator execution
        # upper bound must come from the same configuration fact.
        assert binding.capability.max_results_per_call == 5
        assert binding.backend.max_results_per_call == 5
        assert (
            binding.capability.max_results_per_call
            == binding.backend.max_results_per_call
        )

    def test_production_registry_rejects_fake_protocol(self) -> None:
        """Production must NEVER resolve to ``fake`` protocol."""
        registry = build_production_web_search_adapter_registry()
        cfg = _unconfigured_model_config()
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is False
        assert binding.capability.protocol != "fake"


# ---------------------------------------------------------------------------
# Security: API key never on capability
# ---------------------------------------------------------------------------


class TestSecurityNoSecretLeakage:
    def test_capability_does_not_carry_api_key(self) -> None:
        registry = build_production_web_search_adapter_registry()
        cfg = _qwen_model_config(api_key="sk-SECRET-12345")
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        # Capability model_dump must not contain api_key / base_url / secrets.
        dump = binding.capability.model_dump(mode="json")
        assert "api_key" not in dump
        assert "base_url" not in dump
        assert "sk-SECRET-12345" not in str(dump)

    def test_capability_does_not_carry_base_url(self) -> None:
        registry = build_production_web_search_adapter_registry()
        cfg = _deepseek_model_config(base_url="https://secret.example.com/anthropic")
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        dump = binding.capability.model_dump(mode="json")
        assert "base_url" not in dump
        assert "secret.example.com" not in str(dump)


# ---------------------------------------------------------------------------
# Integration with resolve_reader_record_ask_execution
# ---------------------------------------------------------------------------


def _make_option(model_config: ResolvedModelConfig) -> ResolvedReaderAskModelOption:
    """Build a minimal resolved option carrying the model config."""
    from app.services.ai_usage.billing import DEFAULT_READER_ASK_BILLING_CONFIG

    return ResolvedReaderAskModelOption(
        key="test_option",
        label="Test Option",
        description=None,
        selection=None,  # type: ignore[arg-type]
        billing=DEFAULT_READER_ASK_BILLING_CONFIG,
        main_model_name=model_config.model_name,
        replan_model_name=None,
        is_default=False,
        used_fallback=False,
        runtime_budget=ReaderAskRuntimeBudgetConfig(
            max_input_tokens=24000,
            max_output_tokens=3200,
            max_turn_output_tokens=6400,
            prompt_buffer_tokens=0,
        ),
    )


class TestExecutionConfigIntegration:
    """``resolve_reader_record_ask_execution`` must populate
    ``web_search_backend`` (repr=False) when capability is enabled."""

    def test_execution_config_carries_backend_when_capability_enabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Patch build_model_for_route to return a Qwen-shaped config.
        from app.services.reader_record_ask import execution_config as ec

        def fake_build(cfg, route, selection):  # noqa: ANN001
            cfg = _qwen_model_config()
            return MagicMock(spec=["model_name"]), cfg

        monkeypatch.setattr(ec, "build_model_for_route", fake_build)
        option = _make_option(_qwen_model_config())
        execution = resolve_reader_record_ask_execution(
            option,
            web_search_mode="allowed",
        )
        assert execution.web_search_capability is not None
        assert execution.web_search_capability.enabled_for_turn is True
        assert execution.web_search_backend is not None
        assert isinstance(execution.web_search_backend, QwenDashscopeWebSearchBackend)
        # repr must NOT leak the backend (carries api_key).
        repr_str = repr(execution)
        assert "web_search_backend" not in repr_str
        assert "QwenDashscopeWebSearchBackend" not in repr_str

    def test_execution_config_backend_none_when_capability_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.services.reader_record_ask import execution_config as ec

        def fake_build(cfg, route, selection):  # noqa: ANN001
            cfg = _qwen_model_config()
            return MagicMock(spec=["model_name"]), cfg

        monkeypatch.setattr(ec, "build_model_for_route", fake_build)

        def should_not_resolve_web_search_binding(model_config):  # noqa: ANN001
            raise AssertionError(
                "disabled turns must not resolve or construct a Web Search binding"
            )

        monkeypatch.setattr(
            ec,
            "resolve_web_search_binding",
            should_not_resolve_web_search_binding,
        )
        option = _make_option(_qwen_model_config())
        execution = resolve_reader_record_ask_execution(
            option,
            web_search_mode="disabled",
        )
        assert execution.web_search_capability is None
        assert execution.web_search_backend is None

    def test_execution_config_backend_none_when_adapter_unverified(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.services.reader_record_ask import execution_config as ec

        def fake_build(cfg, route, selection):  # noqa: ANN001
            cfg = _unconfigured_model_config(provider="totally_unknown")
            return MagicMock(spec=["model_name"]), cfg

        monkeypatch.setattr(ec, "build_model_for_route", fake_build)
        option = _make_option(_unconfigured_model_config())
        execution = resolve_reader_record_ask_execution(
            option,
            web_search_mode="allowed",
        )
        # Capability may be returned (typed unavailable), but backend MUST be None.
        if execution.web_search_capability is not None:
            assert execution.web_search_capability.enabled_for_turn is False
        assert execution.web_search_backend is None


# ---------------------------------------------------------------------------
# Send / Retry symmetry: same persisted model option + web_search_mode
# rebuilds the same backend
# ---------------------------------------------------------------------------


class TestSendRetrySymmetry:
    def test_same_model_config_and_mode_produces_same_backend_identity_class(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.services.reader_record_ask import execution_config as ec

        cfg = _qwen_model_config()

        def fake_build(cfg_inner, route, selection):  # noqa: ANN001
            return MagicMock(spec=["model_name"]), cfg

        monkeypatch.setattr(ec, "build_model_for_route", fake_build)
        option = _make_option(cfg)
        send_exec = resolve_reader_record_ask_execution(
            option,
            web_search_mode="allowed",
        )
        retry_exec = resolve_reader_record_ask_execution(
            option,
            web_search_mode="allowed",
        )
        assert send_exec.web_search_capability is not None
        assert retry_exec.web_search_capability is not None
        # Same provider / protocol / model_name on both sides.
        assert (
            send_exec.web_search_capability.provider
            == retry_exec.web_search_capability.provider
        )
        assert (
            send_exec.web_search_capability.protocol
            == retry_exec.web_search_capability.protocol
        )
        # Backend class identity is stable (same adapter).
        assert type(send_exec.web_search_backend) is type(
            retry_exec.web_search_backend
        )


# ---------------------------------------------------------------------------
# G3- §I: Exact-model fail-closed readiness
# ---------------------------------------------------------------------------


class TestExactModelFailClosed:
    """G3-only probed, production-validated model names are enabled.

    All other models — including unprobed variants on the same provider —
    are unavailable. Readiness requires provider + adapter + model_name +
    credential + endpoint + backend constructibility all passing in the
    SAME registry resolution call.
    """

    @pytest.mark.parametrize(
        "model_name",
        ["qwen3.7-max", "qwen3.7-max-preview"],
        ids=["qwen3.7-max", "qwen3.7-max-preview"],
    )
    def test_qwen_probed_model_available(self, model_name: str) -> None:
        registry = WebSearchAdapterRegistry()
        registry.register_qwen(_stub_backend_factory("qwen"))
        cfg = _qwen_model_config(model_name=model_name)
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is True
        assert binding.backend is not None

    @pytest.mark.parametrize(
        "model_name",
        ["deepseek-v4-flash", "deepseek-v4-pro"],
        ids=["deepseek-v4-flash", "deepseek-v4-pro"],
    )
    def test_deepseek_probed_model_available(self, model_name: str) -> None:
        registry = WebSearchAdapterRegistry()
        registry.register_deepseek(_stub_backend_factory("deepseek"))
        cfg = _deepseek_model_config(model_name=model_name)
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is True
        assert binding.backend is not None

    @pytest.mark.parametrize(
        "model_name",
        [
            "deepseek-chat",
            "deepseek-coder",
            "deepseek-v4-flash-latest",
        ],
        ids=[
            "deepseek-chat",
            "deepseek-coder",
            "deepseek-v4-flash-latest",
        ],
    )
    def test_deepseek_unprobed_models_unavailable(self, model_name: str) -> None:
        """Unknown DeepSeek variants remain unavailable."""
        registry = WebSearchAdapterRegistry()
        registry.register_deepseek(_stub_backend_factory("deepseek"))
        cfg = _deepseek_model_config(model_name=model_name)
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is False
        assert binding.backend is None

    @pytest.mark.parametrize(
        "model_name",
        [
            "qwen3.5-max",
            "qwen-max",
            "qwen-plus",
            "qwen3.7-plus",
            "qwen2.5-72b-instruct",
        ],
        ids=[
            "qwen3.5-max",
            "qwen-max",
            "qwen-plus",
            "qwen3.7-plus",
            "qwen2.5-72b",
        ],
    )
    def test_qwen_unprobed_models_unavailable(self, model_name: str) -> None:
        """Same provider/adapter but unknown model → unavailable."""
        registry = WebSearchAdapterRegistry()
        registry.register_qwen(_stub_backend_factory("qwen"))
        cfg = _qwen_model_config(model_name=model_name)
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is False
        assert binding.backend is None

    def test_unknown_model_on_unknown_provider_unavailable(self) -> None:
        registry = WebSearchAdapterRegistry()
        cfg = _unconfigured_model_config(
            provider="totally_unknown",
            model_name="some-unknown-model",
        )
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is False
        assert binding.backend is None

    @pytest.mark.parametrize(
        "provider, adapter, base_url, api_key, model_name",
        [
            # Missing key (Qwen)
            ("dashscope", "openai_compatible",
             "https://dashscope.aliyuncs.com/compatible-mode/v1",
             "", "qwen3.7-max"),
            # Missing key (DeepSeek)
            ("deepseek", "openai_compatible",
             "https://api.deepseek.com/anthropic",
             "", "deepseek-v4-flash"),
            # Wrong adapter (Qwen model with dashscope_embedding — a valid
            # ModelAdapter that the Qwen registry does NOT match, since
            # the Qwen registry only accepts openai_compatible / dashscope_native)
            ("dashscope", "dashscope_embedding",
             "https://dashscope.aliyuncs.com/compatible-mode/v1",
             "sk-test", "qwen3.7-max"),
            # Wrong provider (Qwen model on unknown provider)
            ("totally_unknown", "openai_compatible",
             "https://dashscope.aliyuncs.com/compatible-mode/v1",
             "sk-test", "qwen3.7-max"),
        ],
        ids=[
            "qwen_missing_key",
            "deepseek_missing_key",
            "qwen_wrong_adapter",
            "qwen_wrong_provider",
        ],
    )
    def test_unavailable_conditions(
        self,
        provider: str,
        adapter: str,
        base_url: str,
        api_key: str,
        model_name: str,
    ) -> None:
        """Missing key, wrong adapter, wrong provider → unavailable."""
        registry = build_production_web_search_adapter_registry()
        cfg = ResolvedModelConfig(
            route="reader_ask",
            profile_name="test",
            provider=provider,
            adapter=adapter,  # type: ignore[arg-type]
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
        )
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is False
        assert binding.backend is None

    def test_backend_construction_failure_unavailable(self) -> None:
        """When the adapter factory raises, capability + backend both
        collapse to unavailable in the same resolution call.
        """

        def failing_factory(
            *, api_key: str, model_name: str, base_url: str
        ) -> WebSearchBackend:
            raise RuntimeError("simulated construction failure")

        registry = WebSearchAdapterRegistry()
        registry.register_qwen(failing_factory)
        cfg = _qwen_model_config()
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is False
        assert binding.backend is None


# ---------------------------------------------------------------------------
# G3- §II: Endpoint / credential boundary validation
# ---------------------------------------------------------------------------


class TestEndpointOriginValidation:
    """G3-credentials are NEVER sent to an unvalidated origin.

    The resolved ``base_url`` is validated before any adapter
    construction. Invalid origins → unavailable binding (no backend
    constructed, no credential passed).
    """

    # -- DeepSeek endpoint boundary ----------------------------------

    @pytest.mark.parametrize(
        "base_url",
        [
            "https://api.deepseek.com/anthropic",
            "https://api.deepseek.com/v1",
            "https://api.deepseek.com/",
            "https://api.deepseek.com",
        ],
        ids=[
            "anthropic_path",
            "v1_path",
            "trailing_slash",
            "bare_origin",
        ],
    )
    def test_deepseek_official_origin_available(self, base_url: str) -> None:
        registry = WebSearchAdapterRegistry()
        registry.register_deepseek(_stub_backend_factory("deepseek"))
        cfg = _deepseek_model_config(base_url=base_url)
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is True
        assert binding.backend is not None

    @pytest.mark.parametrize(
        "base_url",
        [
            # HTTP (not HTTPS)
            "http://api.deepseek.com/anthropic",
            # Custom proxy host
            "https://deepseek-proxy.internal.corp/anthropic",
            # Unknown host
            "https://example.com/anthropic",
            # Userinfo-bearing URL
            "https://user:pass@api.deepseek.com/anthropic",
            # Custom port
            "https://api.deepseek.com:8443/anthropic",
            # Malformed non-numeric port
            "https://api.deepseek.com:notaport/anthropic",
            # IP literal
            "https://127.0.0.1/anthropic",
            # Localhost
            "https://localhost/anthropic",
            # Malformed
            "not-a-url",
            "",
        ],
        ids=[
            "http_scheme",
            "custom_proxy",
            "unknown_host",
            "userinfo",
            "custom_port",
            "malformed_port",
            "ip_literal",
            "localhost",
            "malformed",
            "empty",
        ],
    )
    def test_deepseek_invalid_origin_unavailable(self, base_url: str) -> None:
        registry = WebSearchAdapterRegistry()
        registry.register_deepseek(_stub_backend_factory("deepseek"))
        cfg = _deepseek_model_config(base_url=base_url)
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is False
        assert binding.backend is None

    # -- Qwen endpoint boundary --------------------------------------

    def test_qwen_empty_base_url_maps_to_official_endpoint(self) -> None:
        """dashscope_native profile has empty base_url → official endpoint."""
        registry = WebSearchAdapterRegistry()
        registry.register_qwen(_stub_backend_factory("qwen"))
        cfg = _qwen_model_config(base_url="")
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is True
        assert binding.backend is not None

    @pytest.mark.parametrize(
        "base_url",
        [
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "https://dashscope.aliyuncs.com/",
        ],
        ids=["compatible_mode_v1", "trailing_slash"],
    )
    def test_qwen_official_origin_available(self, base_url: str) -> None:
        registry = build_production_web_search_adapter_registry()
        cfg = _qwen_model_config(base_url=base_url)
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is True
        assert isinstance(binding.backend, QwenDashscopeWebSearchBackend)
        assert (
            binding.backend.base_url
            == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )

    @pytest.mark.parametrize(
        "base_url",
        [
            # HTTP (not HTTPS)
            "http://dashscope.aliyuncs.com/compatible-mode/v1",
            # Custom proxy host
            "https://dashscope-proxy.internal.corp/compatible-mode/v1",
            # Unknown host
            "https://example.com/v1",
            # Userinfo-bearing URL
            "https://user:pass@dashscope.aliyuncs.com/compatible-mode/v1",
            # Custom port
            "https://dashscope.aliyuncs.com:8443/compatible-mode/v1",
            # Malformed non-numeric port
            "https://dashscope.aliyuncs.com:notaport/compatible-mode/v1",
            # IP literal
            "https://127.0.0.1/compatible-mode/v1",
            # Localhost
            "https://localhost/compatible-mode/v1",
            # Malformed
            "not-a-url",
        ],
        ids=[
            "http_scheme",
            "custom_proxy",
            "unknown_host",
            "userinfo",
            "custom_port",
            "malformed_port",
            "ip_literal",
            "localhost",
            "malformed",
        ],
    )
    def test_qwen_invalid_origin_unavailable(self, base_url: str) -> None:
        registry = WebSearchAdapterRegistry()
        registry.register_qwen(_stub_backend_factory("qwen"))
        cfg = _qwen_model_config(base_url=base_url)
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is False
        assert binding.backend is None

    def test_deepseek_credential_never_passed_to_unvalidated_origin(self) -> None:
        """When the origin is invalid, the factory MUST NOT be called
        (credential never reaches an unvalidated host).
        """

        def factory_spy(
            *, api_key: str, model_name: str, base_url: str
        ) -> WebSearchBackend:
            raise AssertionError(
                "Factory must not be called for unvalidated origin; "
                "credential would leak to unvalidated host"
            )

        registry = WebSearchAdapterRegistry()
        registry.register_deepseek(factory_spy)
        cfg = _deepseek_model_config(
            base_url="https://deepseek-proxy.internal.corp/anthropic",
        )
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is False
        assert binding.backend is None

    def test_qwen_credential_never_passed_to_unvalidated_origin(self) -> None:
        def factory_spy(
            *, api_key: str, model_name: str, base_url: str
        ) -> WebSearchBackend:
            raise AssertionError(
                "Factory must not be called for unvalidated origin; "
                "credential would leak to unvalidated host"
            )

        registry = WebSearchAdapterRegistry()
        registry.register_qwen(factory_spy)
        cfg = _qwen_model_config(
            base_url="https://dashscope-proxy.internal.corp/v1",
        )
        binding = registry.resolve(model_config=cfg)
        assert binding.capability is not None
        assert binding.capability.enabled_for_turn is False
        assert binding.backend is None


# ---------------------------------------------------------------------------
# G3- §III: Secret-safe adapter repr
# ---------------------------------------------------------------------------


class TestReprSecretSafety:
    """G3-``api_key`` must use ``field(repr=False)`` so that
    ``repr(adapter)``, exception messages, and log output never leak
    the credential. Capability / binding repr must not indirectly
    leak the backend credential either.
    """

    def test_qwen_backend_repr_does_not_leak_api_key(self) -> None:
        secret = "sk-qwen-SECRET-DO-NOT-LEAK-9f3a7c4e2b1d"
        backend = QwenDashscopeWebSearchBackend(
            api_key=secret,
            model_name="qwen3.7-max",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        repr_str = repr(backend)
        assert secret not in repr_str
        assert "api_key" not in repr_str
        assert "sk-qwen" not in repr_str

    def test_deepseek_backend_repr_does_not_leak_api_key(self) -> None:
        secret = "sk-deepseek-SECRET-DO-NOT-LEAK-9f3a7c4e2b1d"
        backend = DeepseekAnthropicWebSearchBackend(
            api_key=secret,
            model_name="deepseek-v4-flash",
            base_url="https://api.deepseek.com/anthropic",
        )
        repr_str = repr(backend)
        assert secret not in repr_str
        assert "api_key" not in repr_str
        assert "sk-deepseek" not in repr_str

    def test_qwen_binding_repr_does_not_leak_backend_credential(self) -> None:
        secret = "sk-qwen-SECRET-DO-NOT-LEAK-binding-9f3a7c4e2b1d"
        registry = build_production_web_search_adapter_registry()
        cfg = _qwen_model_config(api_key=secret)
        binding = registry.resolve(model_config=cfg)
        binding_repr = repr(binding)
        capability_repr = repr(binding.capability)
        assert secret not in binding_repr
        assert secret not in capability_repr
        assert "api_key" not in binding_repr
        assert "Authorization" not in binding_repr
        assert "Bearer" not in binding_repr

    def test_deepseek_binding_repr_does_not_leak_backend_credential(self) -> None:
        secret = "sk-deepseek-SECRET-DO-NOT-LEAK-binding-9f3a7c4e2b1d"
        registry = build_production_web_search_adapter_registry()
        cfg = _deepseek_model_config(api_key=secret)
        binding = registry.resolve(model_config=cfg)
        binding_repr = repr(binding)
        capability_repr = repr(binding.capability)
        assert secret not in binding_repr
        assert secret not in capability_repr
        assert "api_key" not in binding_repr
        assert "x-api-key" not in binding_repr

    def test_execution_config_repr_does_not_leak_backend(self) -> None:
        """``ReaderRecordAskExecutionConfig`` has ``web_search_backend``
        as ``field(repr=False)`` — the backend (carrying api_key)
        must not appear in the config's repr.
        """
        from app.services.reader_record_ask import execution_config as ec

        secret = "sk-qwen-SECRET-execution-config-9f3a7c4e2b1d"

        def fake_build(cfg, route, selection):  # noqa: ANN001
            cfg = _qwen_model_config(api_key=secret)
            return MagicMock(spec=["model_name"]), cfg

        # Use a fresh monkeypatch-free approach: patch the module attr.
        original = ec.build_model_for_route
        ec.build_model_for_route = fake_build  # type: ignore[assignment]
        try:
            option = _make_option(_qwen_model_config(api_key=secret))
            execution = resolve_reader_record_ask_execution(
                option,
                web_search_mode="allowed",
            )
            exec_repr = repr(execution)
            assert secret not in exec_repr
            assert "web_search_backend" not in exec_repr
            assert "QwenDashscopeWebSearchBackend" not in exec_repr
            assert "api_key" not in exec_repr
        finally:
            ec.build_model_for_route = original  # type: ignore[assignment]
