from __future__ import annotations

import json
from pathlib import Path

import pytest

from claread_eval.judge import (
    JudgePacketWriteError,
    build_rubric_case_input,
    write_run_rubric_inputs,
)
from claread_eval.schemas.rubric import load_rubric
from claread_eval.schemas.run import EvalCaseArtifact, EvalRunConfig
from claread_eval.writer.artifact_writer import init_run_dir, write_case_artifact


def _artifact() -> EvalCaseArtifact:
    return EvalCaseArtifact(
        case_id="case-1",
        run_id="run-1",
        input_snapshot={
            "text": "This is a source sentence. " * 120,
            "reading_goal": "daily_reading",
            "reading_variant": "intermediate_reading",
        },
        prompt_identity={
            "prompt_version": "prompt-a",
            "prompt_variant_id": "no-few-shot-v1",
            "prompt_snapshot_hash": "hash-a",
        },
        model_identity={
            "provider": "fake",
            "model_name": "fake-model",
        },
        user_facing_state="normal",
        translations=[{"sentence_id": "s-1", "translation_zh": "译文"}],
        sentence_entries=[
            {
                "id": "entry-1",
                "sentence_id": "s-1",
                "entry_type": "grammar_note",
                "label": "结构说明",
                "content": "这是一个具体说明。",
            }
        ],
    )


def test_load_language_quality_rubric() -> None:
    rubric_path = (
        Path(__file__).parent.parent
        / "rubrics"
        / "article-analysis-language-quality-v1.yaml"
    )

    rubric = load_rubric(rubric_path)

    assert rubric.id == "article-analysis-language-quality-v1"
    assert rubric.target == "article_analysis"
    assert len(rubric.criteria) >= 5


def test_build_rubric_case_input_truncates_source_text() -> None:
    rubric_path = (
        Path(__file__).parent.parent
        / "rubrics"
        / "article-analysis-language-quality-v1.yaml"
    )
    rubric = load_rubric(rubric_path)

    packet = build_rubric_case_input(
        rubric=rubric,
        artifact=_artifact(),
        source_text_char_limit=80,
    )

    assert packet.case_id == "case-1"
    assert packet.prompt_identity["prompt_variant_id"] == "no-few-shot-v1"
    assert len(packet.source_text_excerpt) == 80
    assert packet.source_text_excerpt.endswith("...")
    assert packet.output_excerpt["sentence_entries"][0]["content"] == "这是一个具体说明。"


def test_write_run_rubric_inputs_is_immutable(tmp_path: Path) -> None:
    rubric_path = (
        Path(__file__).parent.parent
        / "rubrics"
        / "article-analysis-language-quality-v1.yaml"
    )
    rubric = load_rubric(rubric_path)
    run_dir = init_run_dir(tmp_path, EvalRunConfig(run_id="run-1", dataset_id="dataset-1"))
    write_case_artifact(run_dir, _artifact())

    output_dir, index_path = write_run_rubric_inputs(rubric=rubric, run_dir=run_dir)

    assert output_dir == run_dir / "judge-inputs" / rubric.id
    assert index_path.is_file()
    packet_path = output_dir / "case-1.json"
    assert packet_path.is_file()
    data = json.loads(packet_path.read_text(encoding="utf-8"))
    assert data["rubric_id"] == rubric.id
    assert data["output_excerpt"]["translations"][0]["translation_zh"] == "译文"

    with pytest.raises(JudgePacketWriteError, match="already exists"):
        write_run_rubric_inputs(rubric=rubric, run_dir=run_dir)
