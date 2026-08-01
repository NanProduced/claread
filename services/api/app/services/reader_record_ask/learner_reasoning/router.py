"""Provider-authority routing for the learner-reasoning projector.

Exact host equality after URL normalization — no suffix/subdomain matches,
no substring guessing. Projector base URLs come only from the canonical
authority map. Missing mapping → fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from app.llm.thinking_capability import (
    ThinkingDialect,
    resolve_thinking_capability_from_config,
    resolve_thinking_dialect,
)
from app.llm.types import ResolvedModelConfig

ProjectorFamily = Literal["deepseek_flash", "qwen_flash"]

# Canonical host → (region_label | None, projector_base_url). Exact host only.
_DEEPSEEK_AUTHORITY: dict[str, tuple[str | None, str]] = {
    "api.deepseek.com": (None, "https://api.deepseek.com/v1"),
}

_DASHSCOPE_AUTHORITY: dict[str, tuple[str, str]] = {
    "dashscope.aliyuncs.com": (
        "cn-beijing",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    "dashscope-intl.aliyuncs.com": (
        "intl",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    ),
    "dashscope-us.aliyuncs.com": (
        "us",
        "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
    ),
}


@dataclass(frozen=True, slots=True)
class ProjectorRoute:
    """Resolved same-authority projector target (host-only)."""

    family: ProjectorFamily
    model_name: str
    base_url: str
    credential_domain: str
    main_dialect: ThinkingDialect
    region: str | None = None


def normalize_authority_host(endpoint: str) -> str | None:
    """Normalize an endpoint URL to a lowercase host for exact allowlist match.

    Rejects empty, non-http(s), userinfo, missing scheme, and empty host.
    Strips trailing dots. Port is ignored (hostname only).
    """
    raw = (endpoint or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        return None
    try:
        parsed = urlparse(raw)
    except Exception:  # noqa: BLE001
        return None
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return None
    return host


def _credential_domain(*, provider: str, host: str, has_key: bool) -> str:
    return f"{(provider or '').lower()}|{host}|{'keyed' if has_key else 'anonymous'}"


def _endpoint_for_config(main_config: ResolvedModelConfig) -> str:
    """Prefer transport base_url; fall back to server-only authority_endpoint."""
    base = (main_config.base_url or "").strip()
    if base:
        return base
    return (getattr(main_config, "authority_endpoint", None) or "").strip()


def resolve_projector_route(
    main_config: ResolvedModelConfig | None,
) -> ProjectorRoute | None:
    """Map main-model authority → same-authority cheap non-thinking profile."""
    if main_config is None:
        return None

    try:
        capability = resolve_thinking_capability_from_config(main_config)
        dialect: ThinkingDialect = capability.dialect
    except Exception:  # noqa: BLE001
        dialect = resolve_thinking_dialect(
            adapter=main_config.adapter,
            provider=main_config.provider,
            model_name=main_config.model_name,
            base_url=main_config.base_url
            or getattr(main_config, "authority_endpoint", ""),
            provider_options=main_config.provider_options,
            openai_profile=main_config.openai_profile,
        )

    endpoint = _endpoint_for_config(main_config)
    host = normalize_authority_host(endpoint)
    if host is None:
        return None
    if not (main_config.api_key or "").strip():
        return None

    cred = _credential_domain(
        provider=main_config.provider,
        host=host,
        has_key=True,
    )

    # DashScope regional hosts (incl. DashScope-hosted DeepSeek) → Qwen Flash.
    if host in _DASHSCOPE_AUTHORITY:
        region, projector_base = _DASHSCOPE_AUTHORITY[host]
        effective: ThinkingDialect = (
            dialect
            if dialect in ("dashscope_deepseek", "dashscope_qwen")
            else "dashscope_qwen"
        )
        return ProjectorRoute(
            family="qwen_flash",
            model_name="qwen-flash",
            base_url=projector_base,
            credential_domain=cred,
            main_dialect=effective,
            region=region,
        )

    # DeepSeek Direct exact hosts → DeepSeek Flash.
    if host in _DEEPSEEK_AUTHORITY:
        region, projector_base = _DEEPSEEK_AUTHORITY[host]
        return ProjectorRoute(
            family="deepseek_flash",
            model_name="deepseek-v4-flash",
            base_url=projector_base,
            credential_domain=cred,
            main_dialect="deepseek_direct",
            region=region,
        )

    return None


__all__ = [
    "ProjectorFamily",
    "ProjectorRoute",
    "normalize_authority_host",
    "resolve_projector_route",
]
