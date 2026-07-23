"""R4-A5-8A1: skip-by-default real-LLM thinking probes.

Does **not** run unless the existing real_llm gate is enabled by the
operator. Never logs raw reasoning — report only provider/model/phase
order and character counts.

This module is a scaffold only; do not invoke real providers from CI or
from agent sessions without explicit human authorization.
"""

from __future__ import annotations

import os

import pytest

# Reuse the project’s existing real-LLM gate convention when present.
_REAL_LLM = os.environ.get("CLAREAD_REAL_LLM", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

pytestmark = pytest.mark.skipif(
    not _REAL_LLM,
    reason="real-LLM thinking probes disabled (set CLAREAD_REAL_LLM=1)",
)


def _phase_report(
    *,
    provider: str,
    model: str,
    phases: list[str],
    reasoning_chars: int,
    answer_chars: int,
) -> dict[str, object]:
    """Build a privacy-safe report (no raw reasoning text)."""
    return {
        "provider": provider,
        "model": model,
        "phases": list(phases),
        "reasoning_char_count": int(reasoning_chars),
        "answer_char_count": int(answer_chars),
    }


@pytest.mark.asyncio
async def test_probe_deepseek_direct_flash_thinking_tool_echo() -> None:
    """Scaffold: Direct DeepSeek Flash — thinking + minimal echo tool.

    Implementation body intentionally omitted until authorized real runs.
    When enabled, must assert: ≥1 reasoning delta, tool round continues,
    final answer present; report via ``_phase_report`` only.
    """
    pytest.skip("scaffold only — await explicit authorization for real call")


@pytest.mark.asyncio
async def test_probe_dashscope_deepseek_flash_thinking_tool_echo() -> None:
    """Scaffold: DashScope DeepSeek Flash — thinking + minimal echo tool."""
    pytest.skip("scaffold only — await explicit authorization for real call")


@pytest.mark.asyncio
async def test_probe_dashscope_qwen_flash_thinking_tool_echo() -> None:
    """Scaffold: DashScope Qwen Flash — enable_thinking + minimal echo tool."""
    pytest.skip("scaffold only — await explicit authorization for real call")
