from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .schemas import (
    JudgeOutputSchemaDefinition,
    JudgePreset,
    NodeLabJudgeCatalog,
    ResolvedJudgeContext,
    StrategyRubricSpec,
)


def _config_root() -> Path:
    return Path(__file__).resolve().parent / "config"


def _load_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return raw


@lru_cache(maxsize=1)
def load_node_lab_judge_catalog() -> NodeLabJudgeCatalog:
    root = _config_root()
    contexts_raw = _load_json(root / "contexts" / "judge_resolved_context_v1_zh.json")
    rubrics_raw = _load_json(root / "rubrics" / "judge_rubric_presets_v1_zh.json")
    presets_raw = _load_json(root / "presets" / "judge_presets_v1_zh.json")
    schemas_raw = _load_json(root / "schemas" / "judge_output_schemas_v1.json")

    contexts = {
        goal: {
            variant: ResolvedJudgeContext.model_validate(payload)
            for variant, payload in variants.items()
        }
        for goal, variants in (contexts_raw.get("contexts") or {}).items()
    }

    rubrics = {
        key: StrategyRubricSpec.model_validate(payload)
        for key, payload in rubrics_raw.items()
        if key in {"grammar", "vocabulary", "translation"}
    }
    presets = {
        preset["preset_id"]: JudgePreset.model_validate(preset)
        for preset in (presets_raw.get("presets") or [])
    }
    output_schemas = {
        key: JudgeOutputSchemaDefinition.model_validate(payload)
        for key, payload in (schemas_raw.get("schema_kinds") or {}).items()
    }

    return NodeLabJudgeCatalog(
        version=str(presets_raw.get("version") or contexts_raw.get("version") or "v1"),
        contexts=contexts,
        rubrics=rubrics,
        presets=presets,
        output_schemas=output_schemas,
        root=root,
    )
