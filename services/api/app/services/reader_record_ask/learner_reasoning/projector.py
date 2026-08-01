"""Provider-local learner-reasoning projector agent.

Never call real providers from unit tests — inject ``run_fn``. Production
path builds a no-tool, thinking-disabled Agent with retries=0, explicit
structured-output mode, and dialect-specific model settings (DeepSeek vs
Qwen fields are never mixed in one extra_body).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.output import PromptedOutput
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from app.services.reader_record_ask.learner_reasoning.router import (
    ProjectorRoute,
)
from app.services.reader_record_ask.learner_reasoning.schemas import (
    PROJECTOR_MAX_OUTPUT_TOKENS,
    PROJECTOR_MODEL_TIMEOUT_SECONDS,
    PROJECTOR_TIMEOUT_SECONDS,
    LearnerReasoningDraft,
)
from app.services.reader_record_ask.learner_reasoning.scrub import (
    scrub_private_reasoning_for_projector,
)
from app.services.reader_record_ask.learner_reasoning.validator import (
    validate_learner_draft,
)

logger = logging.getLogger(__name__)

ProjectorRunFn = Callable[[str], Awaitable[str | None]]

_PROJECTOR_INSTRUCTIONS = (
    "你是 Claread 的回答思路摘要器。根据给定的私有分析片段，"
    "输出一句不超过 80 个中文字符的中性摘要，概括本轮 AI 回答正在如何分析和组织。"
    "不要把思路归因给用户或学习者本人。"
    "不要复述工具名、URL、密钥、模型名、步骤编号或原文长引用。"
    "只输出 JSON 字段 text_zh。"
)


def build_projector_prompt(
    *,
    scrubbed_window: str,
    previous_safe_summary: str | None,
) -> str:
    parts = [
        "请将以下私有分析片段概括为一句中文思路摘要，"
        "说明本轮 AI 回答正在如何分析和组织。",
        "",
        "【分析片段】",
        scrubbed_window,
    ]
    if previous_safe_summary:
        parts.extend(
            [
                "",
                "【上一版已发布摘要（可参考连贯，勿照抄内部词）】",
                previous_safe_summary,
            ]
        )
    parts.extend(
        [
            "",
            '以 JSON 返回：{"text_zh":"..."}',
        ]
    )
    return "\n".join(parts)


def build_projector_model_settings(route: ProjectorRoute) -> ModelSettings:
    """Dialect-specific settings: never mix DeepSeek and Qwen extra_body keys."""
    if route.family == "deepseek_flash":
        extra_body: dict[str, Any] = {
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
        }
    else:
        # qwen_flash / DashScope
        extra_body = {
            "enable_thinking": False,
            "response_format": {"type": "json_object"},
        }
    return ModelSettings(
        max_tokens=PROJECTOR_MAX_OUTPUT_TOKENS,
        timeout=PROJECTOR_MODEL_TIMEOUT_SECONDS,
        parallel_tool_calls=False,
        extra_body=extra_body,
    )


def _classify_exception(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):
        return "timeout"
    if isinstance(exc, UsageLimitExceeded):
        return "usage_limit"
    if isinstance(exc, ValidationError | UnexpectedModelBehavior):
        return "output_invalid"
    name = type(exc).__name__
    if "429" in str(exc) or "RateLimit" in name:
        return "rate_limited"
    return "provider_error"


def _disable_agent_instrumentation(agent: Agent[Any, Any]) -> None:
    for attr in ("instrument", "instrumentation_settings", "_instrument"):
        if hasattr(agent, attr):
            try:
                setattr(
                    agent,
                    attr,
                    False if attr != "instrumentation_settings" else None,
                )
            except Exception:  # noqa: BLE001
                pass


async def run_learner_reasoning_projector(
    *,
    raw_window: str,
    previous_safe_summary: str | None,
    route: ProjectorRoute | None,
    api_key: str,
    run_fn: ProjectorRunFn | None = None,
    model: Any | None = None,
    timeout_seconds: float = PROJECTOR_TIMEOUT_SECONDS,
    settings_out: dict[str, Any] | None = None,
) -> tuple[str | None, str]:
    """Run projector; return (validated_text_or_none, detail_code).

    When ``settings_out`` is provided, the final ModelSettings payload is
    copied into it for tests (never contains secrets beyond empty extra_body).
    """
    scrubbed = scrub_private_reasoning_for_projector(raw_window)
    if not scrubbed:
        return None, "empty_window"

    if run_fn is not None:
        try:
            text = await asyncio.wait_for(run_fn(scrubbed), timeout=timeout_seconds)
        except TimeoutError:
            return None, "timeout"
        except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
            raise
        except Exception:  # noqa: BLE001
            return None, "provider_error"
        if not text:
            return None, "output_invalid"
        try:
            draft = LearnerReasoningDraft.model_validate({"text_zh": text})
        except ValidationError:
            return None, "output_invalid"
        validated = validate_learner_draft(draft)
        return (validated, "ok") if validated else (None, "rejected_format")

    if route is None:
        return None, "route_missing"
    if not (api_key or "").strip() and model is None:
        return None, "route_missing"

    prompt = build_projector_prompt(
        scrubbed_window=scrubbed,
        previous_safe_summary=previous_safe_summary,
    )
    model_settings = build_projector_model_settings(route)
    if settings_out is not None:
        # Capture the actual settings dict used for Agent.run.
        try:
            settings_out.clear()
            settings_out.update(dict(model_settings))  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001
            settings_out["extra_body"] = getattr(
                model_settings, "extra_body", None
            )
            settings_out["max_tokens"] = getattr(
                model_settings, "max_tokens", None
            )

    if model is None:
        try:
            from pydantic_ai.models.openai import OpenAIChatModel
            from pydantic_ai.providers.openai import OpenAIProvider

            provider = OpenAIProvider(
                base_url=route.base_url,
                api_key=api_key or None,
            )
            model = OpenAIChatModel(
                route.model_name,
                provider=provider,
            )
        except Exception:  # noqa: BLE001
            return None, "model_unavailable"

    # Explicit PromptedOutput — do not rely on output_type auto profile
    # (DeepSeek documents json_object; Host still validates).
    output_type: Any = PromptedOutput(LearnerReasoningDraft)
    try:
        agent: Agent[None, Any] = Agent(
            model,
            output_type=output_type,
            name="reader_record_ask_learner_reasoning_projector",
            instructions=_PROJECTOR_INSTRUCTIONS,
            tools=[],
            retries={"tools": 0, "output": 0},
            instrument=False,
        )
    except TypeError:
        agent = Agent(
            model,
            output_type=output_type,
            name="reader_record_ask_learner_reasoning_projector",
            instructions=_PROJECTOR_INSTRUCTIONS,
            tools=[],
            retries={"tools": 0, "output": 0},
        )
        _disable_agent_instrumentation(agent)

    usage_limits = UsageLimits(
        request_limit=1,
        output_tokens_limit=PROJECTOR_MAX_OUTPUT_TOKENS,
    )

    try:
        result = await asyncio.wait_for(
            agent.run(
                prompt,
                model_settings=model_settings,
                usage_limits=usage_limits,
            ),
            timeout=timeout_seconds,
        )
        draft = result.output
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
        raise
    except Exception as exc:  # noqa: BLE001
        detail = _classify_exception(exc)
        logger.info(
            "reader_record_ask learner_reasoning projector failed detail=%s",
            detail,
        )
        return None, detail

    if not isinstance(draft, LearnerReasoningDraft):
        try:
            draft = LearnerReasoningDraft.model_validate(draft)
        except ValidationError:
            return None, "output_invalid"

    validated = validate_learner_draft(draft)
    if validated is None:
        return None, "rejected_format"
    return validated, "ok"


__all__ = [
    "ProjectorRunFn",
    "build_projector_model_settings",
    "build_projector_prompt",
    "run_learner_reasoning_projector",
]
