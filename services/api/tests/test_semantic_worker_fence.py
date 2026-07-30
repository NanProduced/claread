"""Worker semantic fence: mismatch / disallowed → executor never called."""

from __future__ import annotations

import pytest

from app.services.reader_orchestration.automatic_layer_policy import (
    AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
    SemanticFenceError,
    validate_automatic_job_semantic_fence,
)
from app.services.reader_orchestration.semantic_classifier import SEMANTIC_CONTRACT_V1


def _meta(policy: dict, *, resolver: str = AUTOMATIC_LAYER_POLICY_RESOLVER_V1) -> dict:
    return {
        "semantic": {
            "contract_version": SEMANTIC_CONTRACT_V1,
            "resolver_version": resolver,
            "automatic_layer_policy": policy,
        }
    }


def test_validate_all_layers_disallowed_raises() -> None:
    all_off = {
        "translation": False,
        "vocabulary": False,
        "grammar_note": False,
        "sentence_analysis": False,
    }
    job = {
        "semantic_contract_version": SEMANTIC_CONTRACT_V1,
        "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
        "automatic_layer_name": "translation",
    }
    for layer in ("translation", "vocabulary", "grammar_note", "sentence_analysis"):
        job["automatic_layer_name"] = layer
        with pytest.raises(SemanticFenceError) as ei:
            validate_automatic_job_semantic_fence(
                job_input=job,
                layer=layer,
                unit_metadata_list=[_meta(all_off)],
            )
        assert ei.value.code == "semantic_automatic_layer_disallowed"


def test_validate_grammar_any_layers() -> None:
    t_only = {
        "translation": True,
        "vocabulary": False,
        "grammar_note": False,
        "sentence_analysis": False,
    }
    job = {
        "semantic_contract_version": SEMANTIC_CONTRACT_V1,
        "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
        "automatic_layer_name": "grammar_note",
    }
    with pytest.raises(SemanticFenceError):
        validate_automatic_job_semantic_fence(
            job_input=job,
            layer="grammar_note",
            layers_any=("grammar_note", "sentence_analysis"),
            unit_metadata_list=[_meta(t_only)],
        )


@pytest.mark.asyncio
async def test_fake_executor_spy_zero_calls_on_fence() -> None:
    """Simulate worker gate: fence failure before executor invocation."""

    class SpyExecutor:
        def __init__(self) -> None:
            self.calls = 0

        async def run(self) -> str:
            self.calls += 1
            return "ok"

    async def worker_attempt(job_input: dict, unit_meta: dict, spy: SpyExecutor) -> str:
        validate_automatic_job_semantic_fence(
            job_input=job_input,
            layer="vocabulary",
            unit_metadata_list=[unit_meta],
        )
        return await spy.run()

    spy = SpyExecutor()
    job = {
        "semantic_contract_version": SEMANTIC_CONTRACT_V1,
        "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
        "automatic_layer_name": "vocabulary",
    }
    unit = _meta(
        {
            "translation": True,
            "vocabulary": False,
            "grammar_note": False,
            "sentence_analysis": False,
        }
    )
    with pytest.raises(SemanticFenceError):
        await worker_attempt(job, unit, spy)
    assert spy.calls == 0
