from __future__ import annotations

from typing import Any

from pydantic_ai.usage import RunUsage


def build_workflow_root_tags(workflow_name: str, model_names: list[str] | None = None) -> list[str]:
    tags = ["workflow", workflow_name]
    if model_names:
        tags.extend(model_names)
    return tags


def build_workflow_root_metadata(
    *,
    workflow_name: str,
    workflow_version: str,
    schema_version: str,
    request_id: str,
    source_type: str,
    reading_goal: str,
    reading_variant: str,
    profile_id: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "workflow_name": workflow_name,
        "workflow_version": workflow_version,
        "schema_version": schema_version,
        "request_id": request_id,
        "source_type": source_type,
        "reading_goal": reading_goal,
        "reading_variant": reading_variant,
        "profile_id": profile_id,
    }
    if extra:
        metadata.update(extra)
    return {key: value for key, value in metadata.items() if value is not None}


def build_llm_trace_metadata(
    *,
    workflow_name: str,
    workflow_version: str,
    request_id: str,
    source_type: str,
    reading_goal: str,
    reading_variant: str,
    profile_id: str,
    model_name: str,
    model_provider: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "workflow_name": workflow_name,
        "workflow_version": workflow_version,
        "request_id": request_id,
        "source_type": source_type,
        "reading_goal": reading_goal,
        "reading_variant": reading_variant,
        "profile_id": profile_id,
        "model_provider": model_provider,
        "model_name": model_name,
        "ls_provider": model_provider,
        "ls_model_name": model_name,
    }
    if extra:
        metadata.update(extra)
    return metadata


def build_usage_metadata(usage: RunUsage) -> dict[str, object]:
    usage_metadata: dict[str, object] = {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.input_tokens + usage.output_tokens,
    }
    if usage.cache_read_tokens:
        usage_metadata["cache_read_tokens"] = usage.cache_read_tokens
    if usage.cache_write_tokens:
        usage_metadata["cache_write_tokens"] = usage.cache_write_tokens
    for key, value in usage.details.items():
        if value:
            usage_metadata[key] = value
    return usage_metadata
