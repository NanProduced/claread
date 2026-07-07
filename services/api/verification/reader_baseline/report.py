"""Structured comparison report between the new and legacy chains.

The report is a JSON-safe ``dict`` plus an optional Markdown render.
It is intentionally chain-agnostic so the same shape can be diffed
across golden samples and across runs of the same sample.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from verification.reader_baseline.golden_samples import GoldenSample
from verification.reader_baseline.new_chain import NewChainMetrics
from verification.reader_baseline.old_chain import (
    LegacyChainContract,
    LegacyChainRunOutcome,
    is_real_llm_runs_allowed,
)


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    sample_id: str
    shape: str
    char_count: int
    word_count: int
    generated_at: str
    new_chain: dict[str, Any]
    old_chain_contract: dict[str, Any]
    old_chain_outcome: dict[str, Any] | None
    old_chain_real_llm_allowed: bool
    reading_goal: str
    reading_variant: str
    completion_status: str
    is_complete: bool
    notes: str

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "shape": self.shape,
            "char_count": self.char_count,
            "word_count": self.word_count,
            "generated_at": self.generated_at,
            "new_chain": dict(self.new_chain),
            "old_chain_contract": dict(self.old_chain_contract),
            "old_chain_outcome": (
                dict(self.old_chain_outcome) if self.old_chain_outcome is not None else None
            ),
            "old_chain_real_llm_allowed": self.old_chain_real_llm_allowed,
            "reading_goal": self.reading_goal,
            "reading_variant": self.reading_variant,
            "completion_status": self.completion_status,
            "is_complete": self.is_complete,
            "notes": self.notes,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_jsonable(), ensure_ascii=False, indent=2, default=str)

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append(f"# Reader baseline comparison -- {self.sample_id}")
        lines.append("")
        lines.append(f"- shape: `{self.shape}`")
        lines.append(f"- chars: {self.char_count}")
        lines.append(f"- words: {self.word_count}")
        lines.append(f"- generated_at: {self.generated_at}")
        lines.append("")
        lines.append("## New orchestration chain")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(self.new_chain, ensure_ascii=False, indent=2, default=str))
        lines.append("```")
        lines.append("")
        lines.append("## Legacy article_analysis chain (contract)")
        lines.append("")
        lines.append("```json")
        lines.append(
            json.dumps(self.old_chain_contract, ensure_ascii=False, indent=2, default=str)
        )
        lines.append("```")
        lines.append("")
        if self.old_chain_real_llm_allowed:
            lines.append("## Legacy article_analysis chain (end-to-end)")
            lines.append("")
            if self.old_chain_outcome is None:
                lines.append("legacy chain run was requested but produced no outcome")
            else:
                lines.append("```json")
                lines.append(
                    json.dumps(
                        self.old_chain_outcome, ensure_ascii=False, indent=2, default=str
                    )
                )
                lines.append("```")
        else:
            lines.append("## Legacy article_analysis chain (end-to-end)")
            lines.append("")
            lines.append(
                "_Skipped: legacy chain end-to-end run is disabled. Set "
                "`READER_BASELINE_REAL_LLM=1` to enable._"
            )
        if self.notes:
            lines.append("")
            lines.append("## Notes")
            lines.append("")
            lines.append(self.notes)
        return "\n".join(lines) + "\n"


def build_report(
    *,
    sample: GoldenSample,
    new_metrics: NewChainMetrics,
    old_contract: LegacyChainContract,
    old_outcome: LegacyChainRunOutcome | None,
    notes: str = "",
    reading_goal: str | None = None,
    reading_variant: str | None = None,
) -> ComparisonReport:
    """Build a comparison report.

    ``reading_goal`` / ``reading_variant`` are the values the two
    chains actually received during the run. They win over the
    sample manifest defaults so the report's top-level metadata
    reflects the resolved values, not the static manifest entry.
    """
    resolved_goal = reading_goal or sample.reading_goal
    resolved_variant = reading_variant or sample.reading_variant
    return ComparisonReport(
        sample_id=sample.sample_id,
        shape=sample.shape,
        char_count=sample.char_count,
        word_count=sample.word_count,
        generated_at=datetime.now(timezone.utc).isoformat(),
        new_chain=new_metrics.to_jsonable(),
        old_chain_contract=old_contract.to_jsonable(),
        old_chain_outcome=old_outcome.to_jsonable() if old_outcome is not None else None,
        old_chain_real_llm_allowed=is_real_llm_runs_allowed(),
        reading_goal=resolved_goal,
        reading_variant=resolved_variant,
        completion_status=new_metrics.completion_status,
        is_complete=new_metrics.completion_status == "complete",
        notes=notes,
    )


__all__ = ["ComparisonReport", "build_report"]
