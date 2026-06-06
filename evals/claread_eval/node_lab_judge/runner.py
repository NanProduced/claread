from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claread_eval.node_lab_judge.config_loader import load_node_lab_judge_catalog
from claread_eval.node_lab_judge.execute_client import (
    NodeLabJudgeExecuteClient,
    build_default_execute_client,
)
from claread_eval.node_lab_judge.packet_builder import (
    build_pairwise_packet,
    build_probe_packet,
    build_rubric_packet,
)
from claread_eval.node_lab_judge.prompt_assembler import (
    build_pairwise_prompts,
    build_probe_prompts,
    build_rubric_prompts,
)
from claread_eval.node_lab_judge.schemas import JudgePreset, NodeLabJudgeCatalog


@dataclass
class NodeLabJudgeRunConfig:
    judge_request_id: str
    session_id: str
    trial_id: str
    node_name: str
    judge_config_snapshot_json: dict[str, Any]
    artifact_path: str | None = None


def _write_json(file_path: Path, payload: Any) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n", encoding="utf-8")


def _response_payload(response: dict[str, Any], key: str) -> Any:
    if key not in response:
        raise RuntimeError(f"Judge execute response missing required key: {key}")
    return response.get(key)


def _error_payload(exc: Exception) -> dict[str, str]:
    return {
        "code": type(exc).__name__,
        "message": str(exc),
    }


def _step_run_from_response(response: dict[str, Any]) -> dict[str, Any]:
    status = response.get("status")
    if not status:
        status = "failed" if response.get("error") else "succeeded"
    return {
        "status": status,
        "runtime_summary": response.get("runtime_summary") or None,
        "model_identity": response.get("model_identity") or None,
        "error": response.get("error") or None,
        "output_mode": response.get("output_mode") or None,
        "output_schema_kind": response.get("output_schema_kind") or None,
    }


def _step_run_from_exception(exc: Exception, *, output_mode: str) -> dict[str, Any]:
    return {
        "status": "failed",
        "runtime_summary": None,
        "model_identity": None,
        "error": _error_payload(exc),
        "output_mode": output_mode,
        "output_schema_kind": None,
    }


def _trial_dir(evals_root: Path, session_id: str, trial_id: str) -> Path:
    return evals_root / "node-lab" / "sessions" / session_id / "trials" / trial_id


def _judge_dir(evals_root: Path, session_id: str, trial_id: str, judge_request_id: str) -> Path:
    return _trial_dir(evals_root, session_id, trial_id) / "judge" / judge_request_id


def _load_compare_payload(evals_root: Path, session_id: str, trial_id: str) -> dict[str, Any]:
    result_path = _trial_dir(evals_root, session_id, trial_id) / "result.json"
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "baseline" not in payload or "candidate" not in payload:
        raise RuntimeError("Node Lab judge requires a baseline compare artifact as evidence source.")
    return payload


def _resolve_reading(compare_payload: dict[str, Any]) -> tuple[str, str]:
    request_snapshot = compare_payload.get("request_snapshot") or {}
    reading_goal = str(request_snapshot.get("reading_goal") or "daily_reading")
    reading_variant = str(request_snapshot.get("reading_variant") or "intermediate_reading")
    return reading_goal, reading_variant


def _resolve_preset(catalog: NodeLabJudgeCatalog, config_snapshot: dict[str, Any], *, node_name: str) -> JudgePreset:
    preset_id = str(config_snapshot.get("preset_id") or "").strip()
    if not preset_id:
        raise RuntimeError("judge_config_snapshot_json.preset_id is required for Node Lab judge v1.")
    preset = catalog.presets.get(preset_id)
    if preset is None:
        raise RuntimeError(f"Node Lab judge preset not found: {preset_id}")
    if preset.node_name != node_name:
        raise RuntimeError("Judge preset node_name does not match compare trial node_name.")
    return preset


def _resolve_context(catalog: NodeLabJudgeCatalog, *, reading_goal: str, reading_variant: str):
    by_goal = catalog.contexts.get(reading_goal)
    if by_goal is None:
        raise RuntimeError(f"Resolved judge context not found for reading_goal={reading_goal}")
    context = by_goal.get(reading_variant)
    if context is None:
        raise RuntimeError(
            f"Resolved judge context not found for reading_variant={reading_variant} under reading_goal={reading_goal}"
        )
    return context


def _judger_profile(config_snapshot: dict[str, Any]) -> str:
    models = config_snapshot.get("judger_models_json") or []
    if isinstance(models, list):
        for item in models:
            if isinstance(item, dict) and item.get("profile_name"):
                return str(item["profile_name"])
    raise RuntimeError("At least one judger_models_json.profile_name is required.")


def _judge_settings(config_snapshot: dict[str, Any]) -> dict[str, Any]:
    raw = config_snapshot.get("parameters_json")
    return raw if isinstance(raw, dict) else {}


async def _execute_call(
    *,
    client: NodeLabJudgeExecuteClient,
    node_name: str,
    preset: JudgePreset,
    reading_goal: str,
    reading_variant: str,
    judger_model_profile: str,
    judger_model_settings: dict[str, Any],
    output_mode: str,
    output_schema_kind: str,
    system_prompt: str,
    user_prompt: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return await client.execute(
        {
            "eval_adapter_schema_version": "article-analysis-node-lab-judge-v1",
            "node_name": node_name,
            "judge_strategy": preset.strategy,
            "judge_method": preset.method,
            "reading_goal": reading_goal,
            "reading_variant": reading_variant,
            "judger_model_profile": judger_model_profile,
            "judger_model_settings": judger_model_settings,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "output_mode": output_mode,
            "output_schema_kind": output_schema_kind,
            "metadata": metadata,
        }
    )


async def run_node_lab_judge(
    config: NodeLabJudgeRunConfig,
    *,
    evals_root: Path,
    catalog: NodeLabJudgeCatalog | None = None,
    client: NodeLabJudgeExecuteClient | None = None,
) -> tuple[dict[str, Any], Path]:
    evals_root = Path(evals_root).resolve()
    catalog = catalog or load_node_lab_judge_catalog()
    client = client or build_default_execute_client()

    compare_payload = _load_compare_payload(evals_root, config.session_id, config.trial_id)
    reading_goal, reading_variant = _resolve_reading(compare_payload)
    preset = _resolve_preset(catalog, config.judge_config_snapshot_json, node_name=config.node_name)
    context = _resolve_context(catalog, reading_goal=reading_goal, reading_variant=reading_variant)
    strategy_spec = catalog.rubrics[preset.node_name]
    judger_model_profile = _judger_profile(config.judge_config_snapshot_json)
    judger_model_settings = _judge_settings(config.judge_config_snapshot_json)

    judge_dir = _judge_dir(evals_root, config.session_id, config.trial_id, config.judge_request_id)
    raw_dir = judge_dir / "raw-responses"

    _write_json(judge_dir / "judge-config.json", config.judge_config_snapshot_json)

    result_payload: dict[str, Any] = {
        "judge_request_id": config.judge_request_id,
        "trial_id": config.trial_id,
        "session_id": config.session_id,
        "preset_id": preset.preset_id,
        "node_name": config.node_name,
        "judge_method": preset.method,
        "judge_strategy": preset.strategy,
        "step_runs": {
            "rubric": None,
            "pairwise": None,
            "probe": None,
        },
    }
    judge_run_payload: dict[str, Any] = {
        "judge_request_id": config.judge_request_id,
        "trial_id": config.trial_id,
        "session_id": config.session_id,
        "node_name": config.node_name,
        "preset_id": preset.preset_id,
        "reading_goal": reading_goal,
        "reading_variant": reading_variant,
    }

    if preset.method == "anti_template_probe":
        probe_packet = build_probe_packet(
            compare_payload=compare_payload,
            preset=preset,
            context=context,
            reading_goal=reading_goal,
            reading_variant=reading_variant,
        )
        _write_json(judge_dir / "probe-packet.json", probe_packet.model_dump(mode="json"))
        system_prompt, user_prompt = build_probe_prompts(probe_packet)
        probe_response = await _execute_call(
            client=client,
            node_name=config.node_name,
            preset=preset,
            reading_goal=reading_goal,
            reading_variant=reading_variant,
            judger_model_profile=judger_model_profile,
            judger_model_settings=judger_model_settings,
            output_mode="probe_appendix",
            output_schema_kind="probe_appendix",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            metadata={
                "preset_id": preset.preset_id,
                "probe_type": preset.preset_id,
                "trial_id": config.trial_id,
                "judge_request_id": config.judge_request_id,
            },
        )
        _write_json(raw_dir / "probe.json", probe_response)
        result_payload["step_runs"]["probe"] = _step_run_from_response(probe_response)
        result_payload["probe_appendix_result"] = _response_payload(probe_response, "probe_appendix_result")
    else:
        rubric_packet = build_rubric_packet(
            compare_payload=compare_payload,
            preset=preset,
            context=context,
            strategy_spec=strategy_spec,
            reading_goal=reading_goal,
            reading_variant=reading_variant,
        )
        _write_json(judge_dir / "rubric-packet.json", rubric_packet.model_dump(mode="json"))
        system_prompt, user_prompt = build_rubric_prompts(rubric_packet)
        rubric_response = await _execute_call(
            client=client,
            node_name=config.node_name,
            preset=preset,
            reading_goal=reading_goal,
            reading_variant=reading_variant,
            judger_model_profile=judger_model_profile,
            judger_model_settings=judger_model_settings,
            output_mode="rubric_scoring",
            output_schema_kind=(
                "translation_output_scoring"
                if preset.strategy == "translation_output_review"
                else "vocabulary_item_scoring"
                if preset.strategy == "vocabulary_item_review"
                else "grammar_item_scoring"
            ),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            metadata={
                "preset_id": preset.preset_id,
                "trial_id": config.trial_id,
                "judge_request_id": config.judge_request_id,
            },
        )
        _write_json(raw_dir / "rubric.json", rubric_response)
        result_payload["step_runs"]["rubric"] = _step_run_from_response(rubric_response)
        rubric_result = _response_payload(rubric_response, "rubric_scoring_result")
        result_payload["rubric_scoring_result"] = rubric_result

        if preset.method == "rubric_plus_pairwise" and preset.pairwise and preset.pairwise.enabled:
            from claread_eval.node_lab_judge.schemas import NodeLabRubricScoringResult

            pairwise_packet = build_pairwise_packet(
                compare_payload=compare_payload,
                preset=preset,
                context=context,
                reading_goal=reading_goal,
                reading_variant=reading_variant,
                rubric_result=NodeLabRubricScoringResult.model_validate(rubric_result),
            )
            _write_json(judge_dir / "pairwise-packet.json", pairwise_packet.model_dump(mode="json"))
            system_prompt, user_prompt = build_pairwise_prompts(pairwise_packet)
            try:
                pairwise_response = await _execute_call(
                    client=client,
                    node_name=config.node_name,
                    preset=preset,
                    reading_goal=reading_goal,
                    reading_variant=reading_variant,
                    judger_model_profile=judger_model_profile,
                    judger_model_settings=judger_model_settings,
                    output_mode="pairwise",
                    output_schema_kind="pairwise_review",
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    metadata={
                        "preset_id": preset.preset_id,
                        "trial_id": config.trial_id,
                        "judge_request_id": config.judge_request_id,
                    },
                )
                _write_json(raw_dir / "pairwise.json", pairwise_response)
                result_payload["step_runs"]["pairwise"] = _step_run_from_response(pairwise_response)
                result_payload["pairwise_result"] = _response_payload(pairwise_response, "pairwise_result")
            except Exception as exc:
                result_payload["step_runs"]["pairwise"] = _step_run_from_exception(exc, output_mode="pairwise")
                result_payload["pairwise_error"] = _error_payload(exc)
                _write_json(judge_dir / "judge-run.json", judge_run_payload)
                _write_json(judge_dir / "result.json", result_payload)
                raise

    _write_json(judge_dir / "judge-run.json", judge_run_payload)
    _write_json(judge_dir / "result.json", result_payload)
    return result_payload, judge_dir
