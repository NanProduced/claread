from __future__ import annotations

from claread_eval.runner.config_loader import RunnerFileConfig
from claread_eval.schemas.prompt_variant import (
    build_prompt_override_payload,
    load_prompt_variant_manifest,
    validate_prompt_variant,
)


def adapter_run_config(config: RunnerFileConfig) -> dict[str, object]:
    if config.prompt_variant_path is None:
        return {}
    if config.run_config.rag_mode != "off":
        raise ValueError("prompt_variant_path v1 requires rag_mode='off'")
    manifest = load_prompt_variant_manifest(config.prompt_variant_path)
    validate_prompt_variant(
        manifest,
        expected_variant_id=config.run_config.prompt_variant_id,
    )
    return {
        "prompt_override": build_prompt_override_payload(
            manifest,
            baseline_prompt_version=config.run_config.prompt_version,
        )
    }
