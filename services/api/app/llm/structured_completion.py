"""Unified OpenAI-compatible structured JSON completion helper.

Resolves a model via the standard ``resolve_model_config`` pipeline (so callers
get the same base_url / api_key / model_name negotiation as pydantic_ai and
agent_runner), then issues a ``POST /chat/completions`` request with
``response_format=json_object`` and parses the response.

The helper does NOT replace pydantic_ai / agent_runner for typed output use
cases that need retries + tool_choice; it is intentionally minimal for
structured JSON callers that need to inspect the raw LLM response.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.config.settings import Settings
from app.llm.router import ModelSelectionError, resolve_model_config
from app.llm.routes import ModelRoute
from app.llm.types import ModelSelection, RunModelSettings


class StructuredCompletionError(RuntimeError):
    """Raised when a structured completion call cannot complete successfully."""


@dataclass(frozen=True)
class StructuredCompletionResult:
    """Outcome of a structured completion call.

    Attributes:
        parsed: JSON object parsed from the model response.
        raw_text: Original (un-trimmed) assistant message text. Useful for
            debug + audit.
        model_name: Resolved model identifier.
        profile_name: Resolved model profile name.
        base_url: Resolved base URL.
        usage: Token usage from the API response, if available.
            Contains prompt_tokens, completion_tokens, total_tokens.
    """

    parsed: dict[str, Any]
    raw_text: str
    model_name: str
    profile_name: str
    base_url: str
    usage: dict[str, int] | None = None


def _strip_code_fence(content: str) -> str:
    """Strip a single outer ```` ```json ... ``` ```` fence if present."""
    trimmed = content.strip()
    match = re.search(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", trimmed, re.DOTALL)
    if match is not None:
        return match.group(1).strip()
    return trimmed


def _parse_json_object(content: str) -> dict[str, Any]:
    """Parse a JSON object, tolerating markdown fences and surrounding prose."""
    stripped = _strip_code_fence(content)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        # Last-ditch attempt: locate the first balanced { ... } block.
        match = re.search(r"\{[\s\S]*\}", stripped)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("LLM response is not a JSON object")
    return parsed


def _build_payload(
    *,
    model_name: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float | None,
    max_tokens: int | None,
    model_settings: RunModelSettings | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    if temperature is not None:
        body["temperature"] = temperature
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    # Propagate profile-defined ``extra_body`` (e.g. ``enable_thinking: false``)
    # and ``extra_headers`` so non-pydantic_ai callers honour the same
    # model_settings that provider_factory passes to pydantic_ai. Without this
    # merge, profile-level overrides (thinking_mode, vendor params) are silently
    # dropped, leading to e.g. a Qwen model defaulting to thinking-mode and
    # blowing past the request timeout.
    if model_settings is not None:
        if model_settings.extra_body:
            body.update(model_settings.extra_body)
    return body


async def run_structured_completion(
    *,
    settings: Settings,
    route: ModelRoute,
    selection: ModelSelection | None = None,
    system_prompt: str,
    user_prompt: str,
    timeout_seconds: float = 30.0,
    temperature: float | None = 0.0,
    max_tokens: int | None = None,
) -> StructuredCompletionResult:
    """Resolve a model via ``resolve_model_config`` and run a JSON-object completion.

    Args:
        settings: Application settings.
        route: Model route to resolve (e.g. ``annotation_generation``).
        selection: Optional ``ModelSelection`` overriding the route's default
            profile. Pass ``ModelSelection(default_profile=...,
            routes={route: RouteModelSelection(profile=...)})`` to use a
            caller-supplied model profile.
        system_prompt: System message instructing the model.
        user_prompt: User message / packet.
        timeout_seconds: HTTP timeout in seconds.
        temperature: Optional sampling temperature (defaults to 0.0 for
            deterministic judge output).
        max_tokens: Optional cap on generated tokens.

    Returns:
        :class:`StructuredCompletionResult` with parsed JSON object, raw text,
        and resolved model identity.

    Raises:
        StructuredCompletionError: When the model is not configured, the HTTP
            request fails, the response is empty, or the response cannot be
            parsed as a JSON object.
    """
    try:
        config = resolve_model_config(settings, route, selection)
    except ModelSelectionError as exc:
        raise StructuredCompletionError(
            f"Model profile is not configured: {exc}"
        ) from exc
    if config is None:
        raise StructuredCompletionError(
            f"Model profile is not configured for route '{route}'."
        )
    if not config.base_url:
        raise StructuredCompletionError(
            f"Model profile '{config.profile_name}' is missing base_url."
        )
    if not config.api_key:
        raise StructuredCompletionError(
            f"Model profile '{config.profile_name}' is missing api_key."
        )

    body = _build_payload(
        model_name=config.model_name,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        model_settings=config.model_settings,
    )
    url = f"{config.base_url.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.api_key}",
    }
    if config.model_settings is not None and config.model_settings.extra_headers:
        headers.update(config.model_settings.extra_headers)

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        snippet = (exc.response.text or "")[:300]
        raise StructuredCompletionError(
            f"LLM HTTP {exc.response.status_code}: {snippet}"
        ) from exc
    except httpx.TimeoutException as exc:
        raise StructuredCompletionError(
            f"LLM request timed out after {timeout_seconds:.1f}s"
        ) from exc
    except httpx.RequestError as exc:
        raise StructuredCompletionError(f"LLM request failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise StructuredCompletionError(
            f"LLM response was not valid JSON: {exc}"
        ) from exc

    content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise StructuredCompletionError("LLM response content was empty")

    # Extract token usage if available
    usage = None
    raw_usage = payload.get("usage")
    if isinstance(raw_usage, dict):
        usage = {
            "prompt_tokens": raw_usage.get("prompt_tokens", 0),
            "completion_tokens": raw_usage.get("completion_tokens", 0),
            "total_tokens": raw_usage.get("total_tokens", 0),
        }

    try:
        parsed = _parse_json_object(content)
    except (ValueError, json.JSONDecodeError) as exc:
        raise StructuredCompletionError(
            f"LLM response could not be parsed as JSON object: {exc}"
        ) from exc

    return StructuredCompletionResult(
        parsed=parsed,
        raw_text=content,
        model_name=config.model_name,
        profile_name=config.profile_name,
        base_url=config.base_url,
        usage=usage,
    )
