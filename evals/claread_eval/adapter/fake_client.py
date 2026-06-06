from __future__ import annotations

import asyncio
from typing import Any

from claread_eval.schemas.dataset import EvalCase


class FakeArticleAnalysisAdapterClient:
    def __init__(self, *, latency_seconds: float = 0.01) -> None:
        self._latency = latency_seconds
        self.calls: list[dict[str, Any]] = []

    async def analyze(self, case: EvalCase, run_config: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"case_id": case.id, "run_config": run_config})
        if self._latency > 0:
            await asyncio.sleep(self._latency)

        is_academic = case.reading_goal == "academic"
        sentence_count = case.text.count(".") + case.text.count("!") + case.text.count("?")
        sentence_count = max(sentence_count, 1)

        translations = [
            {"sentence_id": f"s-{i}", "translation_zh": f"翻译{i}"}
            for i in range(sentence_count)
        ]

        inline_marks = [
            {
                "id": "mark-1",
                "annotation_type": "vocab_highlight",
                "anchor": {"kind": "text", "sentence_id": "s-0", "anchor_text": "example"},
                "render_type": "background",
                "visual_tone": "vocab",
                "clickable": True,
            }
        ]

        sentence_entries = [
            {
                "id": "entry-1",
                "sentence_id": "s-0",
                "entry_type": "grammar_note" if not is_academic else "term_note",
                "label": "示例标注",
                "title": "语法说明" if not is_academic else "术语说明",
                "content": "这是一个示例标注内容。",
            }
        ]

        schema_version = "3.0.0-academic" if is_academic else "3.0.0"
        prompt_override = run_config.get("prompt_override") or {}

        result: dict[str, Any] = {
            "status": "succeeded",
            "error": None,
            "workflow_identity": {
                "workflow_name": "article_analysis",
                "workflow_version": "3.0.0",
                "topology_mode": "academic" if is_academic else "learning",
            },
            "schema_identity": {
                "schema_version": "3.0.0",
                "render_schema_version": schema_version,
                "topology_mode": "academic" if is_academic else "learning",
            },
            "prompt_identity": {
                "prompt_version": run_config.get("prompt_version"),
                "prompt_variant_id": run_config.get("prompt_variant_id"),
                "prompt_snapshot_hash": prompt_override.get("prompt_snapshot_hash")
                if isinstance(prompt_override, dict)
                else None,
            },
            "render_scene": {
                "schema_version": schema_version,
                "request": {
                    "request_id": f"eval-fake-{case.id}",
                    "source_type": case.source_type,
                    "reading_goal": case.reading_goal,
                    "reading_variant": case.reading_variant,
                    "profile_id": "fake-profile",
                },
                "article": {
                    "source_type": case.source_type,
                    "source_text": case.text,
                    "render_text": case.text,
                    "paragraphs": [],
                    "sentences": [],
                },
                "user_facing_state": "normal",
                "translations": translations,
                "inline_marks": inline_marks,
                "sentence_entries": sentence_entries,
                "warnings": [],
            },
            "normalize_summary": {
                "mode": "academic" if is_academic else "learning",
                "translation_count": len(translations),
            },
            "drop_log_summary": {
                "available": True,
                "total_drop_count": 0,
                "quality_drop_count": 0,
            },
            "runtime_summary": {
                "usage_available": True,
                "aggregate": {"total_tokens": 1000},
                "per_agent": {"vocabulary": 300, "grammar": 400, "translation": 300},
                "latency_ms": int(self._latency * 1000),
            },
            "warnings": [],
            "model_identity": {
                "route": "annotation_generation",
                "profile_name": "fake-profile",
                "provider": "fake",
                "model_name": "fake-model",
            },
        }

        if is_academic:
            result["render_scene"]["content_summary"] = {
                "main_thesis": "示例主旨",
                "key_arguments": ["论点1"],
                "methodology": "示例方法",
            }
            result["render_scene"]["title"] = "示例学术文章标题"

        return result
