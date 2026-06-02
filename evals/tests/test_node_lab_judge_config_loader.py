from __future__ import annotations

from claread_eval.node_lab_judge.config_loader import load_node_lab_judge_catalog


def test_node_lab_judge_catalog_loads_runtime_assets() -> None:
    catalog = load_node_lab_judge_catalog()

    assert catalog.version
    assert "daily_reading" in catalog.contexts
    assert "grammar" in catalog.rubrics
    assert "grammar-default-v1" in catalog.presets
    assert "translation-default-v1" in catalog.presets
    assert "probe_appendix" in catalog.output_schemas

