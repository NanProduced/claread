from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from claread_eval.adapter.factory import create_adapter_client
from claread_eval.adapter.fake_client import FakeArticleAnalysisAdapterClient
from claread_eval.adapter.in_process_client import InProcessArticleAnalysisAdapterClient
from claread_eval.schemas.dataset import EvalCase
from claread_eval.schemas.run import EvalRunConfig


def test_create_adapter_client_fake() -> None:
    adapter = create_adapter_client("fake")
    assert isinstance(adapter, FakeArticleAnalysisAdapterClient)


@pytest.mark.asyncio
async def test_in_process_client_maps_case_and_run_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeModelSelection:
        @classmethod
        def model_validate(cls, payload: dict[str, Any]) -> dict[str, Any]:
            captured["model_selection_payload"] = payload
            return {"validated": payload}

    class FakeArticleAnalysisEvalRequest:
        def __init__(self, **kwargs: Any) -> None:
            captured["request_kwargs"] = kwargs

    class FakePromptRuntimeOverride:
        @classmethod
        def model_validate(cls, payload: dict[str, Any]) -> dict[str, Any]:
            captured["prompt_override_payload"] = payload
            return {"validated_prompt_override": payload}

    class FakeResult:
        def model_dump(self, *, mode: str) -> dict[str, Any]:
            assert mode == "json"
            return {"status": "succeeded", "render_scene": {}}

    async def fake_run_article_analysis_eval(request: object) -> FakeResult:
        captured["request_object"] = request
        return FakeResult()

    app_module = types.ModuleType("app")
    app_eval_adapter_module = types.ModuleType("app.eval_adapter")
    app_eval_adapter_module.ArticleAnalysisEvalRequest = FakeArticleAnalysisEvalRequest
    app_eval_adapter_module.PromptRuntimeOverride = FakePromptRuntimeOverride
    app_eval_adapter_module.run_article_analysis_eval = fake_run_article_analysis_eval
    app_llm_module = types.ModuleType("app.llm")
    app_llm_types_module = types.ModuleType("app.llm.types")
    app_llm_types_module.ModelSelection = FakeModelSelection

    monkeypatch.setitem(sys.modules, "app", app_module)
    monkeypatch.setitem(sys.modules, "app.eval_adapter", app_eval_adapter_module)
    monkeypatch.setitem(sys.modules, "app.llm", app_llm_module)
    monkeypatch.setitem(sys.modules, "app.llm.types", app_llm_types_module)

    case = EvalCase(
        id="case-1",
        text="Sentence one.",
        reading_goal="academic",
        reading_variant="academic_general",
        extended=True,
    )
    run_config = EvalRunConfig(
        run_id="run-1",
        dataset_id="dataset-1",
        prompt_variant_id="variant-a",
        model_selection={"default_profile": "eval-profile"},
        rag_mode="off",
        trace_scope="isolated",
        trace_project="claread-eval-test",
        timeout_seconds=3.0,
    )
    run_payload = {
        **run_config.model_dump(mode="json"),
        "prompt_override": {
            "variant_id": "variant-a",
            "few_shot_mode": "off",
            "prompt_snapshot_hash": "hash-a",
        },
    }

    result = await InProcessArticleAnalysisAdapterClient().analyze(
        case,
        run_payload,
    )

    assert result["status"] == "succeeded"
    assert captured["model_selection_payload"] == {"default_profile": "eval-profile"}
    assert captured["prompt_override_payload"] == {
        "variant_id": "variant-a",
        "few_shot_mode": "off",
        "prompt_snapshot_hash": "hash-a",
    }
    assert captured["request_kwargs"] == {
        "case_id": "case-1",
        "run_id": "run-1",
        "text": "Sentence one.",
        "reading_goal": "academic",
        "reading_variant": "academic_general",
        "source_type": "user_input",
        "extended": True,
        "model_selection": {"validated": {"default_profile": "eval-profile"}},
        "rag_mode": "off",
        "prompt_variant_id": "variant-a",
        "prompt_override": {
            "validated_prompt_override": {
                "variant_id": "variant-a",
                "few_shot_mode": "off",
                "prompt_snapshot_hash": "hash-a",
            }
        },
        "trace_scope": "isolated",
        "trace_project": "claread-eval-test",
        "timeout_seconds": 3.0,
    }
