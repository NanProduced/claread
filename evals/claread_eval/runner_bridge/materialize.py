from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from claread_eval.runner.config_loader import RunnerFileConfig
from claread_eval.schemas.run import EvalRunConfig

from .store import WorkflowRunRequest

RUNNER_WRAPPER_FIELDS = {
    "adapter_kind",
    "config_file",
    "dataset_root",
    "datasets_root",
    "execution_mode",
    "fake_latency_seconds",
    "preset_id",
    "prompt_override",
    "prompt_variant_path",
    "runner_bridge_request",
    "runs_root",
    "yaml_content",
}

YAML_FALSE_AS_OFF_FIELDS = {"rag_mode", "trace_scope"}


class RunnerConfigMaterializeError(ValueError):
    pass


def materialize_runner_config(
    request: WorkflowRunRequest,
    *,
    evals_root: str | Path,
) -> RunnerFileConfig:
    """Build an in-memory runner config from a queued Directus request.

    Directus may include generated YAML for manual execution, but the bridge
    does not write evals/run-configs in v1. It only reuses the YAML payload as
    a structured source of runner fields.
    """

    evals_root = Path(evals_root).resolve()
    raw_config = dict(request.config_json or {})
    yaml_config = _load_embedded_yaml(raw_config.get("yaml_content"))
    merged = {
        **yaml_config,
        **raw_config,
        "run_id": request.run_id,
        "dataset_id": request.dataset_id,
        "mode": "workflow",
    }

    adapter_kind = str(merged.get("adapter_kind") or request.adapter_kind or "fake")
    fake_latency_seconds = float(merged.get("fake_latency_seconds") or 0.0)
    prompt_variant_path = _resolve_prompt_variant_path(
        evals_root=evals_root,
        raw=merged.get("prompt_variant_path"),
    )
    prompt_override = merged.get("prompt_override")
    run_payload = {
        key: value
        for key, value in merged.items()
        if key not in RUNNER_WRAPPER_FIELDS
    }
    run_payload = _normalize_yaml_literals(run_payload)

    return RunnerFileConfig(
        run_config=EvalRunConfig.model_validate(run_payload),
        adapter_kind=adapter_kind,
        runs_root=evals_root / "runs",
        datasets_root=evals_root / "datasets",
        fake_latency_seconds=fake_latency_seconds,
        prompt_variant_path=prompt_variant_path,
        prompt_override=prompt_override if isinstance(prompt_override, dict) else None,
    )


def _load_embedded_yaml(yaml_content: Any) -> dict[str, Any]:
    if yaml_content is None:
        return {}
    if not isinstance(yaml_content, str):
        raise RunnerConfigMaterializeError("yaml_content must be a string when provided.")
    raw = yaml.safe_load(yaml_content)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise RunnerConfigMaterializeError("yaml_content must contain a YAML object.")
    return dict(raw)


def _resolve_prompt_variant_path(
    *,
    evals_root: Path,
    raw: Any,
) -> Path | None:
    if raw in (None, "", "null"):
        return None
    path = Path(str(raw))
    if path.is_absolute():
        return path
    return (evals_root / "run-configs" / path).resolve()


def _normalize_yaml_literals(value: Any, *, key: str | None = None) -> Any:
    if key in YAML_FALSE_AS_OFF_FIELDS and value is False:
        return "off"
    if isinstance(value, dict):
        return {
            child_key: _normalize_yaml_literals(child, key=str(child_key))
            for child_key, child in value.items()
        }
    if isinstance(value, list):
        return [_normalize_yaml_literals(child) for child in value]
    return value
