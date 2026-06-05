from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from claread_eval.adapter.factory import AdapterKind
from claread_eval.schemas.run import EvalRunConfig


class RunConfigLoadError(ValueError):
    pass


class RunnerFileConfig(BaseModel):
    """Resolved runner config.

    Construct this via load_runner_config() so relative paths are resolved from
    the config file location instead of the process working directory.
    """

    run_config: EvalRunConfig
    adapter_kind: AdapterKind = "fake"
    runs_root: Path
    datasets_root: Path
    fake_latency_seconds: float = Field(default=0.0, ge=0.0)
    prompt_variant_path: Path | None = None
    prompt_override: dict[str, Any] | None = None

    @property
    def dataset_dir(self) -> Path:
        return self.datasets_root / self.run_config.dataset_id

    @property
    def run_dir(self) -> Path:
        return self.runs_root / self.run_config.run_id


def _resolve_path(base_dir: Path, raw: str | Path) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


_YAML_FALSE_AS_OFF_FIELDS = {"rag_mode", "trace_scope"}


def _normalize_yaml_literals(value: Any, *, key: str | None = None) -> Any:
    # PyYAML follows YAML 1.1 implicit bools, so unquoted `off` becomes False.
    # Only convert fields whose schema explicitly uses the string literal "off".
    if key in _YAML_FALSE_AS_OFF_FIELDS and value is False:
        return "off"
    if isinstance(value, dict):
        return {
            child_key: _normalize_yaml_literals(child, key=str(child_key))
            for child_key, child in value.items()
        }
    if isinstance(value, list):
        return [_normalize_yaml_literals(child) for child in value]
    return value


def load_runner_config(path: str | Path) -> RunnerFileConfig:
    config_path = Path(path).resolve()
    if not config_path.is_file():
        raise RunConfigLoadError(f"Run config not found: {config_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RunConfigLoadError("Run config must be a YAML object")

    payload: dict[str, Any] = dict(raw)
    base_dir = config_path.parent
    adapter_kind = payload.pop("adapter_kind", "fake")
    runs_root = _resolve_path(base_dir, payload.pop("runs_root", "../runs"))
    datasets_root = _resolve_path(base_dir, payload.pop("datasets_root", ""))
    fake_latency_seconds = payload.pop("fake_latency_seconds", 0.0)
    prompt_override = payload.pop("prompt_override", None)
    prompt_variant_path_raw = payload.pop("prompt_variant_path", None)
    prompt_variant_path = (
        _resolve_path(base_dir, prompt_variant_path_raw)
        if prompt_variant_path_raw is not None
        else None
    )

    return RunnerFileConfig(
        run_config=EvalRunConfig.model_validate(_normalize_yaml_literals(payload)),
        adapter_kind=adapter_kind,
        runs_root=runs_root,
        datasets_root=datasets_root,
        fake_latency_seconds=fake_latency_seconds,
        prompt_variant_path=prompt_variant_path,
        prompt_override=prompt_override,
    )
