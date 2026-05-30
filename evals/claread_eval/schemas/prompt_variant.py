from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

FewShotMode = Literal["off", "baseline", "variant", "settings"]


class PromptVariantManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_id: str
    target: Literal["article_analysis"]
    description: str = ""
    few_shot_mode: FewShotMode = "settings"
    policies: dict[str, Any] = Field(default_factory=dict)
    examples: dict[str, Any] = Field(default_factory=dict)


class PromptVariantLoadError(ValueError):
    pass


def load_prompt_variant_manifest(path: str | Path) -> PromptVariantManifest:
    manifest_path = _manifest_path(path)
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise PromptVariantLoadError(f"Prompt variant manifest must be an object: {manifest_path}")
    raw = _normalize_yaml_literals(raw)
    return PromptVariantManifest.model_validate(raw)


def build_prompt_override_payload(
    manifest: PromptVariantManifest,
    *,
    baseline_prompt_version: str | None = None,
) -> dict[str, Any]:
    payload = manifest.model_dump(mode="json", exclude_none=True)
    payload["prompt_snapshot_hash"] = prompt_variant_snapshot_hash(
        manifest,
        baseline_prompt_version=baseline_prompt_version,
    )
    return payload


def validate_prompt_variant(
    manifest: PromptVariantManifest,
    *,
    expected_variant_id: str | None,
) -> None:
    if expected_variant_id and manifest.variant_id != expected_variant_id:
        raise PromptVariantLoadError(
            f"prompt_variant_id mismatch: {expected_variant_id} != {manifest.variant_id}"
        )
    if manifest.target != "article_analysis":
        raise PromptVariantLoadError(f"Unsupported prompt variant target: {manifest.target}")


def prompt_variant_snapshot_hash(
    manifest: PromptVariantManifest,
    *,
    baseline_prompt_version: str | None = None,
) -> str:
    payload = {
        "baseline_prompt_version": baseline_prompt_version,
        "manifest": manifest.model_dump(mode="json", exclude_none=True),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()[:16]


def _manifest_path(path: str | Path) -> Path:
    candidate = Path(path).resolve()
    if candidate.is_dir():
        candidate = candidate / "manifest.yaml"
    if not candidate.is_file():
        raise PromptVariantLoadError(f"Prompt variant manifest not found: {candidate}")
    return candidate


def _normalize_yaml_literals(value: Any, *, key: str | None = None) -> Any:
    if key == "few_shot_mode" and value is False:
        return "off"
    if isinstance(value, dict):
        return {
            child_key: _normalize_yaml_literals(child, key=str(child_key))
            for child_key, child in value.items()
        }
    if isinstance(value, list):
        return [_normalize_yaml_literals(child) for child in value]
    return value
