from __future__ import annotations

import json
from pathlib import Path

import pytest

from claread_eval.node_lab_judge.config_loader import load_node_lab_judge_catalog
from claread_eval.node_lab_judge.runner import NodeLabJudgeRunConfig, run_node_lab_judge
from claread_eval.node_lab_judge.store import InMemoryNodeLabJudgeRequestStore
from claread_eval.node_lab_judge.worker import NodeLabJudgeWorker


class FakeJudgeClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, payload: dict) -> dict:
        self.calls.append(payload)
        if payload["output_mode"] == "rubric_scoring":
            return {
                "status": "succeeded",
                "runtime_summary": {
                    "latency_ms": 1234,
                    "aggregate": {"input_tokens": 321, "output_tokens": 123, "total_tokens": 444},
                },
                "model_identity": {"model_name": "fake-judge-model"},
                "output_mode": "rubric_scoring",
                "output_schema_kind": "rubric_scoring",
                "rubric_scoring_result": {
                    "strategy": payload["judge_strategy"],
                    "method": payload["judge_method"],
                    "baseline": {
                        "items": [
                            {
                                "item_id": "grammar_note:s1:focus:Although",
                                "item_type": "grammar_note",
                                "sentence_id": "s1",
                                "label": "focus",
                                "source_excerpt": "Although the plan looked simple.",
                                "criteria": [
                                    {
                                        "criterion_id": "GN1",
                                        "score": 1,
                                        "reason": "解释准确。",
                                        "evidence": "Although 从句被正确定位。",
                                    }
                                ],
                                "item_summary": {"passed": 1, "failed": 0},
                            }
                        ],
                        "output_level_scores": [],
                        "aggregate": {
                            "item_count": 1,
                            "criteria_count": 1,
                            "passed": 1,
                            "failed": 0,
                            "pass_rate": 1.0,
                        },
                    },
                    "candidate": {
                        "items": [
                            {
                                "item_id": "grammar_note:s1:focus:Although",
                                "item_type": "grammar_note",
                                "sentence_id": "s1",
                                "label": "focus",
                                "source_excerpt": "Although the plan looked simple.",
                                "criteria": [
                                    {
                                        "criterion_id": "GN1",
                                        "score": 0,
                                        "reason": "解释偏离句内作用。",
                                        "evidence": "未说明从句在当前句中的关系。",
                                    }
                                ],
                                "item_summary": {"passed": 0, "failed": 1},
                            }
                        ],
                        "output_level_scores": [],
                        "aggregate": {
                            "item_count": 1,
                            "criteria_count": 1,
                            "passed": 0,
                            "failed": 1,
                            "pass_rate": 0.0,
                        },
                    },
                    "meta": {"preset_id": "grammar-default-v1"},
                }
            }
        if payload["output_mode"] == "pairwise":
            return {
                "status": "succeeded",
                "runtime_summary": {
                    "latency_ms": 789,
                    "aggregate": {"input_tokens": 111, "output_tokens": 45, "total_tokens": 156},
                },
                "model_identity": {"model_name": "fake-judge-model"},
                "output_mode": "pairwise",
                "output_schema_kind": "pairwise",
                "pairwise_result": {
                    "strategy": payload["judge_strategy"],
                    "method": payload["judge_method"],
                    "pairwise_review": {
                        "preferred_side": "baseline",
                        "overall_judgment": "Baseline 整体更稳。",
                        "baseline_strengths": ["讲解更聚焦。"],
                        "candidate_strengths": ["覆盖略多。"],
                        "baseline_risks": [],
                        "candidate_risks": ["Candidate 有关键条目未通过。"],
                        "manual_check_points": ["人工复看 candidate 的句内作用说明。"],
                    },
                    "meta": {"preset_id": "grammar-default-v1"},
                }
            }
        return {
            "status": "succeeded",
            "runtime_summary": {
                "latency_ms": 456,
                "aggregate": {"input_tokens": 88, "output_tokens": 22, "total_tokens": 110},
            },
            "model_identity": {"model_name": "fake-judge-model"},
            "output_mode": "probe_appendix",
            "output_schema_kind": "probe_appendix",
            "probe_appendix_result": {
                "probe_type": "anti_template_probe",
                "questions": [
                    {
                        "question_id": "AT1",
                        "detected": False,
                        "description": "未发现明显模板化。",
                        "evidence": [],
                    }
                ],
                "summary": "暂无显著问题。",
            }
        }


class TranslationJudgeClient(FakeJudgeClient):
    async def execute(self, payload: dict) -> dict:
        self.calls.append(payload)
        if payload["output_mode"] == "rubric_scoring":
            return {
                "status": "succeeded",
                "runtime_summary": {
                    "latency_ms": 901,
                    "aggregate": {"input_tokens": 210, "output_tokens": 77, "total_tokens": 287},
                },
                "model_identity": {"model_name": "fake-judge-model"},
                "output_mode": "rubric_scoring",
                "output_schema_kind": "rubric_scoring",
                "rubric_scoring_result": {
                    "strategy": payload["judge_strategy"],
                    "method": payload["judge_method"],
                    "baseline": {
                        "items": [],
                        "output_level_scores": [
                            {"criterion_id": "TT1", "score": 1, "reason": "核心意思准确。", "evidence": "原句主干被保留。"},
                            {"criterion_id": "TT2", "score": 1, "reason": "逻辑关系清晰。", "evidence": "转折关系未丢失。"},
                        ],
                        "aggregate": {"item_count": None, "criteria_count": 2, "passed": 2, "failed": 0, "pass_rate": 1.0},
                    },
                    "candidate": {
                        "items": [],
                        "output_level_scores": [
                            {"criterion_id": "TT1", "score": 1, "reason": "核心意思准确。", "evidence": "原句主干被保留。"},
                            {"criterion_id": "TT2", "score": 0, "reason": "逻辑关系略被抹平。", "evidence": "让步关系被弱化。"},
                        ],
                        "aggregate": {"item_count": None, "criteria_count": 2, "passed": 1, "failed": 1, "pass_rate": 0.5},
                    },
                    "meta": {"preset_id": "translation-default-v1"},
                }
            }
        return {
            "status": "succeeded",
            "runtime_summary": {
                "latency_ms": 654,
                "aggregate": {"input_tokens": 95, "output_tokens": 31, "total_tokens": 126},
            },
            "model_identity": {"model_name": "fake-judge-model"},
            "output_mode": "pairwise",
            "output_schema_kind": "pairwise",
            "pairwise_result": {
                "pairwise_review": {
                    "preferred_side": "baseline",
                    "overall_judgment": "Baseline 整体更利于当前用户理解原文。",
                    "baseline_strengths": ["整体逻辑更完整。"],
                    "candidate_strengths": ["表达更自然。"],
                    "baseline_risks": [],
                    "candidate_risks": ["让步关系弱化。"],
                    "manual_check_points": ["人工复看让步关系是否需要更显性。"],
                },
                "meta": {"preset_id": "translation-default-v1"},
            }
        }


class PairwiseFailureClient(FakeJudgeClient):
    async def execute(self, payload: dict) -> dict:
        self.calls.append(payload)
        if payload["output_mode"] == "rubric_scoring":
            return await super().execute(payload)
        raise RuntimeError("pairwise llm timeout")


def _write_compare_artifact(root: Path, *, session_id: str, trial_id: str) -> None:
    trial_dir = root / "node-lab" / "sessions" / session_id / "trials" / trial_id
    trial_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "node_name": "grammar",
        "request_snapshot": {
            "reading_goal": "daily_reading",
            "reading_variant": "intermediate_reading",
        },
        "compare_summary": {
            "prompt_changed": True,
            "token_delta": -12,
        },
        "baseline": {
            "prepared_sentences": [
                {"sentence_id": "s1", "text": "Although the plan looked simple, it required careful coordination."}
            ],
            "node_output": {
                "grammar_notes": [
                    {
                        "sentence_id": "s1",
                        "label": "让步从句",
                        "note_zh": "解释正确。",
                        "spans": [{"text": "Although", "role": "subordinator"}],
                    }
                ],
                "sentence_analyses": [],
            },
        },
        "candidate": {
            "prepared_sentences": [
                {"sentence_id": "s1", "text": "Although the plan looked simple, it required careful coordination."}
            ],
            "node_output": {
                "grammar_notes": [
                    {
                        "sentence_id": "s1",
                        "label": "让步从句",
                        "note_zh": "解释一般。",
                        "spans": [{"text": "Although", "role": "subordinator"}],
                    }
                ],
                "sentence_analyses": [],
            },
        },
    }
    (trial_dir / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_translation_compare_artifact(root: Path, *, session_id: str, trial_id: str) -> None:
    trial_dir = root / "node-lab" / "sessions" / session_id / "trials" / trial_id
    trial_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "node_name": "translation",
        "request_snapshot": {
            "reading_goal": "exam",
            "reading_variant": "cet",
        },
        "compare_summary": {
            "prompt_changed": True,
            "token_delta": -4,
        },
        "baseline": {
            "prepared_sentences": [
                {"sentence_id": "s1", "text": "Although the plan looked simple, it required careful coordination."}
            ],
            "node_output": {
                "sentence_translations": [
                    {"sentence_id": "s1", "translation_zh": "虽然这个计划看起来简单，但它需要非常细致的协调。"}
                ],
            },
        },
        "candidate": {
            "prepared_sentences": [
                {"sentence_id": "s1", "text": "Although the plan looked simple, it required careful coordination."}
            ],
            "node_output": {
                "sentence_translations": [
                    {"sentence_id": "s1", "translation_zh": "这个计划看似简单，却仍然需要仔细协调。"}
                ],
            },
        },
    }
    (trial_dir / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.mark.asyncio
async def test_node_lab_judge_runner_writes_artifacts(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    _write_compare_artifact(evals_root, session_id="session-1", trial_id="trial-1")
    client = FakeJudgeClient()

    result, artifact_dir = await run_node_lab_judge(
        NodeLabJudgeRunConfig(
            judge_request_id="judge-1",
            session_id="session-1",
            trial_id="trial-1",
            node_name="grammar",
            judge_config_snapshot_json={
                "preset_id": "grammar-default-v1",
                "judger_models_json": [{"profile_name": "eval-profile"}],
                "parameters_json": {"temperature": 0},
            },
        ),
        evals_root=evals_root,
        catalog=load_node_lab_judge_catalog(),
        client=client,
    )

    assert result["rubric_scoring_result"]["baseline"]["aggregate"]["passed"] == 1
    assert result["pairwise_result"]["pairwise_review"]["preferred_side"] == "baseline"
    assert result["step_runs"]["rubric"]["status"] == "succeeded"
    assert result["step_runs"]["pairwise"]["status"] == "succeeded"
    assert result["step_runs"]["rubric"]["runtime_summary"]["aggregate"]["total_tokens"] >= 0
    assert len(client.calls) == 2
    assert "raw_item" not in client.calls[0]["user_prompt"]
    assert "raw_item" not in client.calls[1]["user_prompt"]
    assert "source_sentence" in client.calls[1]["user_prompt"]
    assert "selected_annotations" in client.calls[1]["user_prompt"]
    assert (artifact_dir / "judge-config.json").is_file()
    assert (artifact_dir / "rubric-packet.json").is_file()
    assert (artifact_dir / "pairwise-packet.json").is_file()
    assert (artifact_dir / "raw-responses" / "rubric.json").is_file()
    assert (artifact_dir / "raw-responses" / "pairwise.json").is_file()
    assert (artifact_dir / "result.json").is_file()


@pytest.mark.asyncio
async def test_node_lab_judge_runner_translation_uses_output_level_and_light_pairwise_packet(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    _write_translation_compare_artifact(evals_root, session_id="session-2", trial_id="trial-2")
    client = TranslationJudgeClient()

    result, artifact_dir = await run_node_lab_judge(
        NodeLabJudgeRunConfig(
            judge_request_id="judge-2",
            session_id="session-2",
            trial_id="trial-2",
            node_name="translation",
            judge_config_snapshot_json={
                "preset_id": "translation-default-v1",
                "judger_models_json": [{"profile_name": "eval-profile"}],
                "parameters_json": {"temperature": 0},
            },
        ),
        evals_root=evals_root,
        catalog=load_node_lab_judge_catalog(),
        client=client,
    )

    assert result["rubric_scoring_result"]["baseline"]["items"] == []
    assert result["rubric_scoring_result"]["baseline"]["output_level_scores"][0]["criterion_id"] == "TT1"
    assert result["step_runs"]["rubric"]["status"] == "succeeded"
    assert result["step_runs"]["pairwise"]["status"] == "succeeded"
    assert "translation_zh" not in client.calls[1]["user_prompt"]
    assert "baseline_translation" in client.calls[1]["user_prompt"]
    assert "candidate_translation" in client.calls[1]["user_prompt"]
    assert "TT2" in client.calls[1]["user_prompt"]
    assert "逻辑关系略被抹平" in client.calls[1]["user_prompt"]
    assert (artifact_dir / "pairwise-packet.json").is_file()


@pytest.mark.asyncio
async def test_node_lab_judge_runner_probe_only_does_not_trigger_rubric_call(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    _write_compare_artifact(evals_root, session_id="session-3", trial_id="trial-3")
    client = FakeJudgeClient()

    result, artifact_dir = await run_node_lab_judge(
        NodeLabJudgeRunConfig(
            judge_request_id="judge-3",
            session_id="session-3",
            trial_id="trial-3",
            node_name="grammar",
            judge_config_snapshot_json={
                "preset_id": "grammar-anti-template-v1",
                "judger_models_json": [{"profile_name": "eval-profile"}],
                "parameters_json": {"temperature": 0},
            },
        ),
        evals_root=evals_root,
        catalog=load_node_lab_judge_catalog(),
        client=client,
    )

    assert "probe_appendix_result" in result
    assert "rubric_scoring_result" not in result
    assert result["step_runs"]["probe"]["status"] == "succeeded"
    assert len(client.calls) == 1
    assert (artifact_dir / "probe-packet.json").is_file()


@pytest.mark.asyncio
async def test_node_lab_judge_worker_preserves_rubric_result_when_pairwise_fails(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    _write_compare_artifact(evals_root, session_id="session-4", trial_id="trial-4")
    store = InMemoryNodeLabJudgeRequestStore()
    store.add_request(
        judge_request_id="judge-4",
        session_id="session-4",
        trial_id="trial-4",
        node_name="grammar",
        judge_config_snapshot_json={
            "preset_id": "grammar-default-v1",
            "judger_models_json": [{"profile_name": "eval-profile"}],
            "parameters_json": {"temperature": 0},
        },
    )
    worker = NodeLabJudgeWorker(
        store=store,
        evals_root=evals_root,
        worker_id="test-worker",
        poll_interval=0.01,
        lease_seconds=60,
        heartbeat_interval=60.0,
    )

    async def fake_run_node_lab_judge(*args, **kwargs):
        return await run_node_lab_judge(*args, **kwargs, catalog=load_node_lab_judge_catalog(), client=PairwiseFailureClient())

    from claread_eval.node_lab_judge import worker as worker_module

    original = worker_module.run_node_lab_judge
    worker_module.run_node_lab_judge = fake_run_node_lab_judge
    try:
        claimed = await worker.run_once()
    finally:
        worker_module.run_node_lab_judge = original

    assert claimed is True
    request = store.requests[0]
    assert request.status == "failed"
    assert request.artifact_path is not None
    result_path = evals_root / "node-lab" / "sessions" / "session-4" / "trials" / "trial-4" / "judge" / "judge-4" / "result.json"
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["rubric_scoring_result"]["baseline"]["aggregate"]["passed"] == 1
    assert payload["step_runs"]["rubric"]["status"] == "succeeded"
    assert payload["step_runs"]["pairwise"]["status"] == "failed"
    assert payload["step_runs"]["pairwise"]["error"]["message"] == "pairwise llm timeout"
    assert payload["pairwise_error"]["message"] == "pairwise llm timeout"
