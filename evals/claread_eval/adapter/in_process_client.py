from __future__ import annotations

from typing import Any

from claread_eval.schemas.dataset import EvalCase
from claread_eval.schemas.run import EvalRunConfig


class InProcessArticleAnalysisAdapterClient:
    async def analyze(self, case: EvalCase, run_config: dict[str, Any]) -> dict[str, Any]:
        try:
            from app.eval_adapter import (  # type: ignore[import-not-found]
                ArticleAnalysisEvalRequest,
                PromptRuntimeOverride,
                run_article_analysis_eval,
            )
            from app.llm.types import ModelSelection  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "services/api is not importable. Run this client with services/api on PYTHONPATH "
                "or use FakeArticleAnalysisAdapterClient."
            ) from exc

        config = EvalRunConfig.model_validate(run_config)
        prompt_override_raw = run_config.get("prompt_override")
        prompt_override = (
            PromptRuntimeOverride.model_validate(prompt_override_raw)
            if prompt_override_raw
            else None
        )
        model_selection = (
            ModelSelection.model_validate(config.model_selection)
            if config.model_selection
            else None
        )
        request = ArticleAnalysisEvalRequest(
            case_id=case.id,
            run_id=config.run_id,
            text=case.text,
            reading_goal=case.reading_goal,
            reading_variant=case.reading_variant,
            source_type=case.source_type,
            extended=case.extended,
            model_selection=model_selection,
            rag_mode=config.rag_mode,
            prompt_variant_id=config.prompt_variant_id,
            prompt_override=prompt_override,
            trace_scope=config.trace_scope,
            trace_project=config.trace_project,
            timeout_seconds=config.timeout_seconds,
        )
        result = await run_article_analysis_eval(request)
        return result.model_dump(mode="json")
