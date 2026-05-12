from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from app.llm.types import ModelSelection, parse_model_selection


def get_model_selection(config: RunnableConfig | None) -> ModelSelection | None:
    configurable = (config or {}).get("configurable", {})
    return parse_model_selection(configurable.get("model_selection"))


def dump_model_selection(selection: ModelSelection | None) -> dict[str, object] | None:
    if selection is None:
        return None
    return selection.model_dump(exclude_none=True)
