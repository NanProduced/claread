"""G3-Unified Web Search adapter registry (production wiring).

Single source of truth for translating a :class:`ResolvedModelConfig`
into a :class:`ResolvedWebSearchBinding` that carries BOTH the
:class:`ResolvedWebSearchCapability` AND the executable
:class:`WebSearchBackend`. The capability and the backend are produced
in the **same** resolution call — they must never be judged separately.

Contract
--------
- Capability is derived from ``ResolvedModelConfig`` fields only:
  ``provider``, ``adapter``, ``model_name``, ``base_url``, ``api_key``,
  and adapter readiness (factory success).
- ``web_search_mode="disabled"`` short-circuits at the resolver layer
  (returns ``None`` capability) — the registry is never consulted.
- When the model config does not match any registered adapter, the
  registry returns a :class:`ResolvedWebSearchBinding` with a non-None
  but disabled capability (``enabled_for_turn=False``) and ``None``
  backend. The capability's ``provider`` reflects the requested
  provider string (safe — not a secret); ``protocol`` is set to a
  non-``fake`` placeholder (``dashscope_responses`` for dashscope,
  ``deepseek_anthropic`` for deepseek, ``dashscope_responses`` for
  unknown providers — never ``fake`` in production).
- When the matching adapter factory raises (adapter unverified /
  unconstructible), the registry returns the same disabled binding
  shape — never propagates the factory exception.
- The capability never carries ``api_key`` / ``base_url`` / auth
  material. Those fields live only on the constructed backend (which
  is ``repr=False`` on :class:`ReaderRecordAskExecutionConfig`).

Exact-model fail-closed readiness (G3-)
-----------------------------------------
Only probed, production-validated model names are enabled. All other
models — including unprobed variants on the same provider — are
unavailable:

- Qwen: ``qwen3.7-max`` and the current product wire identity
  ``qwen3.7-max-preview`` are available.
- DeepSeek: ``deepseek-v4-flash`` and ``deepseek-v4-pro`` are
  available.
- Other Qwen/DeepSeek variants and unknown models are unavailable.

Endpoint / credential boundary (G3-)
---------------------------------------
Credentials are NEVER sent to an unvalidated origin. The resolved
``base_url`` is validated before any adapter construction:

- DeepSeek: the resolved ``base_url`` origin MUST be
  ``https://api.deepseek.com`` (HTTPS, exact host, no userinfo, no
  custom port). The Anthropic Web Search endpoint is constructed from
  the validated origin as ``https://api.deepseek.com/anthropic``.
  Custom proxies, unknown hosts, HTTP, and userinfo URLs are all
  unavailable.
- Qwen: an empty ``base_url`` (dashscope_native profile) maps to the
  official DashScope Responses endpoint
  ``https://dashscope.aliyuncs.com/compatible-mode/v1``. A non-empty
  ``base_url`` MUST be an HTTPS origin with host
  ``dashscope.aliyuncs.com`` (no userinfo). Unvalidated custom origins
  are unavailable.

Production wiring
-----------------
:func:`build_production_web_search_adapter_registry` returns a registry
pre-wired with the real :class:`QwenDashscopeWebSearchBackend` and
:class:`DeepseekAnthropicWebSearchBackend` factories. Tests inject
stub factories via :meth:`WebSearchAdapterRegistry.register_qwen` /
:meth:`WebSearchAdapterRegistry.register_deepseek`.

Provider matching rules
-----------------------
- Qwen (DashScope Responses API):
  * ``provider`` in ``{"dashscope", "dashscope_native"}``
  * ``adapter`` in ``{"openai_compatible", "dashscope_native"}``
  * ``model_name`` is one of the two explicit Qwen 3.7 Max wire
    identities (exact-model fail-closed)
  * ``api_key`` non-empty
  * ``base_url`` empty OR official HTTPS DashScope origin
    (``dashscope.aliyuncs.com``).
- DeepSeek (Anthropic-compat server-side Web Search):
  * ``provider == "deepseek"``
  * ``adapter == "openai_compatible"``
  * ``model_name`` is one of the two explicit DeepSeek V4 wire
    identities (exact-model fail-closed)
  * ``api_key`` non-empty
  * ``base_url`` origin MUST be ``https://api.deepseek.com`` (HTTPS,
    exact host). The Anthropic endpoint is constructed from this
    validated origin.
- Any other combination: typed unavailable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from app.llm.types import ResolvedModelConfig
from app.services.reader_record_ask.deepseek_anthropic_web_search_backend import (
    DeepseekAnthropicWebSearchBackend,
)
from app.services.reader_record_ask.qwen_dashscope_web_search_backend import (
    QwenDashscopeWebSearchBackend,
)
from app.services.reader_record_ask.web_search_contracts import (
    ResolvedWebSearchCapability,
    WebSearchProtocol,
)
from app.services.reader_record_ask.web_search_port import WebSearchBackend

logger = logging.getLogger(__name__)

# Policy version stamped on every capability resolved here. Bumped only
# when the resolution semantics change (new provider, new protocol, new
# max-calls / max-results mapping).
WEB_SEARCH_CAPABILITY_POLICY_VERSION: str = "reader_record_ask_web_search_v1"

# Frozen policy: at most two provider attempts, with the coordinator
# allowing the second only after the first outcome is ``no_results``.
_DEFAULT_MAX_CALLS: int = 2
_DEFAULT_MAX_RESULTS_PER_CALL: int = 5

# Canonical provider Web Search endpoints. These are the ONLY origins
# that credentials may be sent to.
_QWEN_DASHSCOPE_RESPONSES_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_DEEPSEEK_ANTHROPIC_BASE_URL: str = "https://api.deepseek.com/anthropic"

# Exact-model fail-closed whitelist (G3-). The DeepSeek V4 Flash and Pro
# product options share the same provider,
# Anthropic-compatible Web Search endpoint, and adapter contract. Unknown
# variants remain unavailable.
_QWEN_ALLOWED_MODELS: frozenset[str] = frozenset(
    {"qwen3.7-max", "qwen3.7-max-preview"}
)
_DEEPSEEK_ALLOWED_MODELS: frozenset[str] = frozenset(
    {"deepseek-v4-flash", "deepseek-v4-pro"}
)

# Official origin hosts for endpoint validation (G3-).
_QWEN_OFFICIAL_HOST: str = "dashscope.aliyuncs.com"
_DEEPSEEK_OFFICIAL_HOST: str = "api.deepseek.com"


# ---------------------------------------------------------------------------
# Backend factory Protocol
# ---------------------------------------------------------------------------


class WebSearchBackendFactory(Protocol):
    """Construct a :class:`WebSearchBackend` from resolved model config.

    Implementations MUST NOT make any network call during construction
    they only assemble the adapter object. Network calls happen in
    :meth:`WebSearchBackend.search_web`.

    Raising any exception signals that the adapter is unverified or
    unconstructible with the current config (e.g. missing required
    option, version mismatch). The registry catches the exception and
    returns a disabled binding — it never propagates.
    """

    def __call__(
        self,
        *,
        api_key: str,
        model_name: str,
        base_url: str,
    ) -> WebSearchBackend: ...


# ---------------------------------------------------------------------------
# Binding
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class ResolvedWebSearchBinding:
    """Result of one registry resolution.

    Carries BOTH the capability AND the backend produced by the same
    resolution call. Callers MUST NOT re-derive capability from
    ``model_config`` separately — they read it from this binding.

    - ``capability=None`` + ``backend=None`` → ``web_search_mode``
      was ``disabled`` (resolver short-circuits before calling the
      registry).
    - ``capability`` non-None + ``enabled_for_turn=False`` + ``backend=None``
      → adapter not registered / unverified / missing key / unsupported
      model. The runtime must NOT mount ``search_web``.
    - ``capability`` non-None + ``enabled_for_turn=True`` + ``backend`` non-None
      → adapter ready; runtime mounts ``search_web`` and injects the
      backend.
    """

    capability: ResolvedWebSearchCapability | None
    backend: WebSearchBackend | None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class WebSearchAdapterRegistry:
    """Provider-aware adapter registry.

    A single instance is constructed per process (or per test) and
    configured with zero or more provider-specific factories. The
    :meth:`resolve` method examines the :class:`ResolvedModelConfig`
    and dispatches to the matching factory.

    Thread-safety: factories are stored as plain attributes and read
    during :meth:`resolve`. Once :func:`build_production_web_search_adapter_registry`
    returns, the registry is effectively immutable for production use.
    """

    def __init__(self) -> None:
        self._qwen_factory: WebSearchBackendFactory | None = None
        self._deepseek_factory: WebSearchBackendFactory | None = None

    # -- registration --------------------------------------------------

    def register_qwen(self, factory: WebSearchBackendFactory) -> None:
        """Register the Qwen (DashScope Responses) adapter factory."""
        self._qwen_factory = factory

    def register_deepseek(self, factory: WebSearchBackendFactory) -> None:
        """Register the DeepSeek (Anthropic-compat) adapter factory."""
        self._deepseek_factory = factory

    # -- resolution ----------------------------------------------------

    def resolve(self, *, model_config: ResolvedModelConfig) -> ResolvedWebSearchBinding:
        """Resolve one binding from the model config.

        Always returns a :class:`ResolvedWebSearchBinding`. Never
        raises — adapter construction failures are caught and mapped
        to a disabled binding.

        G3-exact-model fail-closed. Only probed model names are
        enabled. Endpoint origin is validated before any adapter
        construction — credentials are never sent to unvalidated
        origins.
        """
        provider = model_config.provider
        adapter = model_config.adapter
        base_url = model_config.base_url
        api_key = model_config.api_key
        model_name = model_config.model_name

        # Dispatch by provider + adapter.
        # Qwen: dashscope OR dashscope_native provider, with either
        # openai_compatible or dashscope_native adapter. The Responses
        # API is an OpenAI-compatible surface; the dashscope_native
        # adapter is the native SDK transport for chat — but Web Search
        # always goes through the Responses endpoint.
        if (
            provider in ("dashscope", "dashscope_native")
            and adapter in ("openai_compatible", "dashscope_native")
            and self._qwen_factory is not None
        ):
            return self._resolve_qwen(
                factory=self._qwen_factory,
                api_key=api_key,
                model_name=model_name,
                base_url=base_url,
            )

        # DeepSeek: provider == "deepseek" with openai_compatible adapter.
        # The resolved base_url origin is validated (must be
        # https://api.deepseek.com), and the Anthropic Web Search
        # endpoint is constructed from that validated origin.
        if (
            provider == "deepseek"
            and adapter == "openai_compatible"
            and self._deepseek_factory is not None
        ):
            return self._resolve_deepseek(
                factory=self._deepseek_factory,
                api_key=api_key,
                model_name=model_name,
                base_url=base_url,
            )

        # No matching adapter registered → typed unavailable.
        # Capability is non-None (so callers can audit the requested
        # provider) but disabled; backend is None.
        return _unavailable_binding(
            provider=_safe_provider_name(provider),
            protocol=_protocol_for_provider(provider),
        )

    # -- per-provider resolution (private) -----------------------------

    def _resolve_qwen(
        self,
        *,
        factory: WebSearchBackendFactory,
        api_key: str,
        model_name: str,
        base_url: str,
    ) -> ResolvedWebSearchBinding:
        # G3-exact-model fail-closed.
        if model_name not in _QWEN_ALLOWED_MODELS:
            return _unavailable_binding(
                provider="dashscope",
                protocol="dashscope_responses",
            )
        if not api_key:
            return _unavailable_binding(
                provider="dashscope",
                protocol="dashscope_responses",
            )
        # G3-endpoint origin validation.
        # Empty base_url (dashscope_native) → official DashScope endpoint.
        # Non-empty base_url → must be official HTTPS DashScope origin.
        if base_url:
            validated_origin = _validate_https_origin(
                base_url,
                allowed_hosts=(_QWEN_OFFICIAL_HOST,),
            )
            if validated_origin is None:
                return _unavailable_binding(
                    provider="dashscope",
                    protocol="dashscope_responses",
                )
        effective_base_url = _QWEN_DASHSCOPE_RESPONSES_BASE_URL
        try:
            backend = factory(
                api_key=api_key,
                model_name=model_name,
                base_url=effective_base_url,
            )
        except Exception:  # noqa: BLE001 — fail-closed, no leakage
            logger.warning(
                "web_search_adapter qwen construction failed "
                "model_name=%s base_url_set=%s",
                model_name,
                bool(base_url),
            )
            return _unavailable_binding(
                provider="dashscope",
                protocol="dashscope_responses",
            )
        return _enabled_binding(
            provider="dashscope",
            protocol="dashscope_responses",
            backend=backend,
        )

    def _resolve_deepseek(
        self,
        *,
        factory: WebSearchBackendFactory,
        api_key: str,
        model_name: str,
        base_url: str,
    ) -> ResolvedWebSearchBinding:
        # G3-exact-model fail-closed.
        if model_name not in _DEEPSEEK_ALLOWED_MODELS:
            return _unavailable_binding(
                provider="deepseek",
                protocol="deepseek_anthropic",
            )
        if not api_key:
            return _unavailable_binding(
                provider="deepseek",
                protocol="deepseek_anthropic",
            )
        # G3-endpoint origin validation.
        # The resolved base_url origin MUST be https://api.deepseek.com.
        # The Anthropic Web Search endpoint is constructed from this
        # validated origin. Credentials are NEVER sent to unvalidated
        # origins (custom proxies, unknown hosts, HTTP, userinfo URLs).
        validated_origin = _validate_https_origin(
            base_url,
            allowed_hosts=(_DEEPSEEK_OFFICIAL_HOST,),
        )
        if validated_origin is None:
            return _unavailable_binding(
                provider="deepseek",
                protocol="deepseek_anthropic",
            )
        # Construct the Anthropic-compat endpoint from the validated
        # origin — do NOT blindly overwrite with a fixed constant.
        effective_base_url = f"{validated_origin}/anthropic"
        try:
            backend = factory(
                api_key=api_key,
                model_name=model_name,
                base_url=effective_base_url,
            )
        except Exception:  # noqa: BLE001 — fail-closed, no leakage
            logger.warning(
                "web_search_adapter deepseek construction failed "
                "model_name=%s base_url_set=%s",
                model_name,
                bool(base_url),
            )
            return _unavailable_binding(
                provider="deepseek",
                protocol="deepseek_anthropic",
            )
        return _enabled_binding(
            provider="deepseek",
            protocol="deepseek_anthropic",
            backend=backend,
        )


# ---------------------------------------------------------------------------
# Production factory
# ---------------------------------------------------------------------------


def build_production_web_search_adapter_registry() -> WebSearchAdapterRegistry:
    """Build the production registry pre-wired with real adapters.

    Returns a fresh registry with the Qwen (DashScope Responses) and
    DeepSeek (Anthropic-compat) factories registered. Production paths
    construct this once per request (cheap — no network I/O at
    construction time).
    """

    registry = WebSearchAdapterRegistry()

    def qwen_factory(
        *,
        api_key: str,
        model_name: str,
        base_url: str,
    ) -> WebSearchBackend:
        return QwenDashscopeWebSearchBackend(
            api_key=api_key,
            model_name=model_name,
            base_url=base_url,
            # ASK-WEB-backend budget MUST come from the same
            # configuration fact as the capability. The capability is
            # built with ``_DEFAULT_MAX_RESULTS_PER_CALL`` below; the
            # backend must mirror that exact value so the Coordinator's
            # effective execution upper bound matches the declared
            # capability. Never rely on the backend dataclass default
            # here — the default is a defensive fallback only.
            max_results_per_call=_DEFAULT_MAX_RESULTS_PER_CALL,
            timeout=18.0,
        )

    def deepseek_factory(
        *,
        api_key: str,
        model_name: str,
        base_url: str,
    ) -> WebSearchBackend:
        return DeepseekAnthropicWebSearchBackend(
            api_key=api_key,
            model_name=model_name,
            base_url=base_url,
            # ASK-WEB-backend budget MUST come from the same
            # configuration fact as the capability (see qwen_factory).
            max_results_per_call=_DEFAULT_MAX_RESULTS_PER_CALL,
            timeout=18.0,
        )

    registry.register_qwen(qwen_factory)
    registry.register_deepseek(deepseek_factory)
    return registry


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_https_origin(
    raw_url: str,
    *,
    allowed_hosts: tuple[str, ...],
) -> str | None:
    """Validate that ``raw_url`` is an HTTPS origin on an allowed host.

    Returns the canonical origin string (``"https://<host>"``) when the
    URL passes ALL of:
    - non-empty string
    - parses with ``urlsplit`` to scheme ``https`` (case-insensitive)
    - host (``hostname``) is non-empty and exactly matches one of
      ``allowed_hosts`` (case-insensitive)
    - no userinfo (``username`` / ``password`` empty)
    - no explicit port (default 443 only)

    Returns ``None`` for any other input — including HTTP, custom
    proxies, unknown hosts, IPs, localhost, userinfo-bearing URLs, and
    malformed strings. Callers treat ``None`` as fail-closed: the
    binding becomes unavailable and credentials are NEVER sent to the
    unvalidated origin.

    The path / query / fragment of ``raw_url`` are ignored — callers
    construct their own path on top of the validated origin.
    """
    if not raw_url or not isinstance(raw_url, str):
        return None
    try:
        parts = urlsplit(raw_url)
    except ValueError:
        return None
    if parts.scheme.lower() != "https":
        return None
    host = parts.hostname
    if not host:
        return None
    if host.lower() not in [h.lower() for h in allowed_hosts]:
        return None
    if parts.username or parts.password:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if port is not None:
        return None
    return f"https://{host.lower()}"


def _safe_provider_name(provider: str) -> str:
    """Normalise provider string for safe inclusion in capability.

    Returns the provider string as-is when non-empty, else ``"unwired"``.
    The value is safe — it is not a secret, not a URL, not an API key.
    """
    if not isinstance(provider, str) or not provider.strip():
        return "unwired"
    return provider.strip()[:64]


def _protocol_for_provider(provider: str) -> WebSearchProtocol:
    """Map a provider string to its reserved protocol placeholder.

    Used when constructing an unavailable capability for a provider
    that has no registered adapter. The protocol is the reserved
    placeholder (``dashscope_responses`` for dashscope,
    ``deepseek_anthropic`` for deepseek, ``dashscope_responses`` for
    any other unknown provider). Production paths NEVER resolve to
    ``fake`` protocol — the fake backend is test-only and injected
    directly via the stream constructor.
    """
    if provider == "deepseek":
        return "deepseek_anthropic"
    # dashscope and any unknown provider both map to dashscope_responses
    # as the reserved protocol placeholder. The capability's
    # ``enabled_for_turn=False`` ensures the runtime never executes
    # against this placeholder.
    return "dashscope_responses"


def _enabled_binding(
    *,
    provider: str,
    protocol: WebSearchProtocol,
    backend: WebSearchBackend,
) -> ResolvedWebSearchBinding:
    """Build an enabled binding (capability + backend)."""
    cap = ResolvedWebSearchCapability(
        enabled_for_turn=True,
        provider=provider,
        protocol=protocol,
        execution_mode="host_function",
        decision_mode="agent_auto",
        max_calls=_DEFAULT_MAX_CALLS,
        max_results_per_call=_DEFAULT_MAX_RESULTS_PER_CALL,
        policy_version=WEB_SEARCH_CAPABILITY_POLICY_VERSION,
    )
    return ResolvedWebSearchBinding(capability=cap, backend=backend)


def _unavailable_binding(
    *,
    provider: str,
    protocol: WebSearchProtocol,
) -> ResolvedWebSearchBinding:
    """Build a typed unavailable binding (disabled capability, no backend)."""
    cap = ResolvedWebSearchCapability(
        enabled_for_turn=False,
        provider=provider,
        protocol=protocol,
        execution_mode="host_function",
        decision_mode="agent_auto",
        max_calls=_DEFAULT_MAX_CALLS,
        max_results_per_call=_DEFAULT_MAX_RESULTS_PER_CALL,
        policy_version=WEB_SEARCH_CAPABILITY_POLICY_VERSION,
    )
    return ResolvedWebSearchBinding(capability=cap, backend=None)


__all__ = [
    "WEB_SEARCH_CAPABILITY_POLICY_VERSION",
    "ResolvedWebSearchBinding",
    "WebSearchAdapterRegistry",
    "WebSearchBackendFactory",
    "build_production_web_search_adapter_registry",
]
