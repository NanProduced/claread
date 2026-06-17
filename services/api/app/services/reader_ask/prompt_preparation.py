"""Prompt preparation: context compression, budget management, and compaction audit.

This module is responsible for preparing the prompt payload for the LLM by:
- Estimating token counts
- Computing the input budget from billing parameters
- Deciding whether context compaction is needed
- Applying progressive compression layers
- Injecting compaction audit information into traces

The prompt payload data assembly (building the dict structure) lives in
runtime_contract.py. This module handles the budget-aware preparation that
happens after the payload is assembled.
"""

from __future__ import annotations

import json
from typing import Any

from app.schemas.reader_ask import ReaderAskTraceSummary
from app.services.reader_ask import config as cfg
from app.services.reader_ask import utils


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def estimate_token_count(payload: dict[str, Any]) -> int:
    """Estimate the token count for a prompt payload.

    Uses a simple heuristic: 4 chars per token for non-CJK text,
    1.5 chars per token for CJK text. This is intentionally conservative
    and does not need to match any specific tokenizer exactly — it only
    needs to be consistent for budget comparisons.
    """
    serialized = json.dumps(payload, ensure_ascii=False)
    cjk_count = sum(1 for c in serialized if '\u4e00' <= c <= '\u9fff' or '\u3400' <= c <= '\u4dbf')
    non_cjk_len = len(serialized) - cjk_count
    return max(int(non_cjk_len / 4 + cjk_count / 1.5), 1)


# ---------------------------------------------------------------------------
# Budget calculation
# ---------------------------------------------------------------------------

def compute_max_input_budget(
    *,
    max_input_tokens: int | None = None,
    reserved_points: int | None = None,
    tokens_per_point: int | None = None,
    budget_buffer_tokens: int = cfg.PROMPT_BUDGET_BUFFER_TOKENS,
    min_max_output_tokens: int = cfg.MIN_MAX_OUTPUT_TOKENS,
    multiplier_output: int = 0,
) -> int:
    """Compute the maximum input token budget.

    Preferred mode is explicit ``max_input_tokens``. Legacy callers may still
    derive the budget from billing-like parameters.
    """
    if max_input_tokens is not None:
        return max_input_tokens
    if reserved_points is None or tokens_per_point is None:
        raise ValueError("Either max_input_tokens or reserved_points/tokens_per_point must be provided")
    weighted_budget = reserved_points * tokens_per_point
    return weighted_budget - budget_buffer_tokens - min_max_output_tokens * multiplier_output


# ---------------------------------------------------------------------------
# Compacting emission decision
# ---------------------------------------------------------------------------

def should_emit_compacting(payload: dict[str, Any], *, max_input_budget: int) -> bool:
    """Decide whether to emit a context.compacting SSE event.

    Returns True when the prompt payload exceeds the input budget,
    indicating that compression will be applied.
    """
    return estimate_token_count(payload) > max_input_budget


# ---------------------------------------------------------------------------
# Compaction audit injection
# ---------------------------------------------------------------------------

def inject_compaction_audit(
    trace_summary: ReaderAskTraceSummary | None,
    compaction_audit: list[str],
) -> ReaderAskTraceSummary | None:
    """Append compaction audit notes to trace_summary for eval / logging.

    The notes use internal layer names (not user-visible). This keeps
    technical details out of the UI but available in eval traces.
    """
    if not compaction_audit or trace_summary is None:
        return trace_summary
    audit_note = f"context_compaction:{','.join(compaction_audit)}"
    updated_notes = list(trace_summary.notes) + [audit_note]
    return trace_summary.model_copy(update={"notes": updated_notes})


# ---------------------------------------------------------------------------
# Initial (non-progressive) compaction
# ---------------------------------------------------------------------------

def _compact_prompt_payload(
    payload: dict[str, Any],
    *,
    max_history: int = cfg.COMPACTION_MAX_HISTORY,
    max_record_assets: int = cfg.COMPACTION_MAX_RECORD_ASSETS,
    max_external_assets: int = cfg.COMPACTION_MAX_EXTERNAL_ASSETS,
    max_vocabulary: int = cfg.COMPACTION_MAX_VOCABULARY,
    max_insights: int = cfg.COMPACTION_MAX_INSIGHTS,
    max_sentence_windows: int = cfg.COMPACTION_MAX_SENTENCE_WINDOWS,
    max_source_excerpt: int = cfg.COMPACTION_MAX_SOURCE_EXCERPT,
    max_article_overview: int = cfg.COMPACTION_MAX_ARTICLE_OVERVIEW,
) -> dict[str, Any]:
    compact = json.loads(json.dumps(payload, ensure_ascii=False))
    history = compact.get("history")
    if isinstance(history, list) and len(history) > max_history:
        # Preserve system (summary) messages; only truncate user/assistant messages
        system_msgs = [m for m in history if isinstance(m, dict) and m.get("role") == "system"]
        conversation_msgs = [m for m in history if isinstance(m, dict) and m.get("role") != "system"]
        compact["history"] = system_msgs + conversation_msgs[-max_history:]

    record_assets = compact.get("record_assets")
    if isinstance(record_assets, list) and len(record_assets) > max_record_assets:
        compact["record_assets"] = record_assets[:max_record_assets]

    external_asset_contexts = compact.get("external_asset_contexts")
    if isinstance(external_asset_contexts, list) and len(external_asset_contexts) > max_external_assets:
        compact["external_asset_contexts"] = external_asset_contexts[:max_external_assets]
    if isinstance(external_asset_contexts, list):
        for item in external_asset_contexts:
            if not isinstance(item, dict):
                continue
            content_md = item.get("content_md")
            if isinstance(content_md, str) and len(content_md) > cfg.COMPACTION_EXTERNAL_ASSET_CONTENT_LIMIT:
                item["content_md"] = utils.truncate_text(content_md, cfg.COMPACTION_EXTERNAL_ASSET_CONTENT_LIMIT)

    vocabulary_items = compact.get("vocabulary_items")
    if isinstance(vocabulary_items, list) and len(vocabulary_items) > max_vocabulary:
        compact["vocabulary_items"] = vocabulary_items[:max_vocabulary]

    record_insights = compact.get("record_insights")
    if isinstance(record_insights, list) and len(record_insights) > max_insights:
        compact["record_insights"] = record_insights[:max_insights]

    record_context = compact.get("record_context")
    if isinstance(record_context, dict):
        sentence_windows = record_context.get("sentence_windows")
        if isinstance(sentence_windows, list) and len(sentence_windows) > max_sentence_windows:
            record_context["sentence_windows"] = sentence_windows[:max_sentence_windows]
        source_excerpt = record_context.get("source_excerpt")
        if isinstance(source_excerpt, str) and len(source_excerpt) > max_source_excerpt:
            record_context["source_excerpt"] = utils.truncate_text(source_excerpt, max_source_excerpt)
    article_overview = compact.get("article_overview")
    if isinstance(article_overview, str) and len(article_overview) > max_article_overview:
        compact["article_overview"] = utils.truncate_text(article_overview, max_article_overview)
    planning = compact.get("planning")
    if isinstance(planning, dict):
        trace_summary = planning.get("trace_summary")
        if isinstance(trace_summary, dict):
            notes = trace_summary.get("notes")
            if isinstance(notes, list) and len(notes) > 4:
                trace_summary["notes"] = notes[:4]
            tool_steps = trace_summary.get("tool_steps")
            if isinstance(tool_steps, list) and len(tool_steps) > 6:
                trace_summary["tool_steps"] = tool_steps[:6]
    return compact


# ---------------------------------------------------------------------------
# Progressive compaction: apply compression layers in priority order,
# re-estimate tokens after each layer, stop as soon as budget is met.
# ---------------------------------------------------------------------------

# Each layer is a (name, function) pair. Layers are applied in order from
# lowest priority (compressed first) to highest priority (compressed last).
# Each function mutates the payload dict in place and returns True if it
# actually changed anything (so we know to re-estimate tokens).

def _layer_trim_external_assets(payload: dict[str, Any], limit: int = 2) -> bool:
    """Trim external asset contexts — lowest priority, compressed first."""
    items = payload.get("external_asset_contexts")
    if not isinstance(items, list) or len(items) <= limit:
        return False
    payload["external_asset_contexts"] = items[:limit]
    return True


def _layer_trim_record_assets(payload: dict[str, Any], limit: int = 2) -> bool:
    """Trim record assets (annotations, notes)."""
    items = payload.get("record_assets")
    if not isinstance(items, list) or len(items) <= limit:
        return False
    payload["record_assets"] = items[:limit]
    return True


def _layer_trim_vocabulary(payload: dict[str, Any], limit: int = 2) -> bool:
    """Trim vocabulary items."""
    items = payload.get("vocabulary_items")
    if not isinstance(items, list) or len(items) <= limit:
        return False
    payload["vocabulary_items"] = items[:limit]
    return True


def _layer_trim_insights(payload: dict[str, Any], limit: int = 2) -> bool:
    """Trim record insights."""
    items = payload.get("record_insights")
    if not isinstance(items, list) or len(items) <= limit:
        return False
    payload["record_insights"] = items[:limit]
    return True


def _layer_trim_history(payload: dict[str, Any], limit: int = 4) -> bool:
    """Trim conversation history, preserving system summary messages."""
    history = payload.get("history")
    if not isinstance(history, list):
        return False
    system_msgs = [m for m in history if isinstance(m, dict) and m.get("role") == "system"]
    conv_msgs = [m for m in history if isinstance(m, dict) and m.get("role") != "system"]
    if len(conv_msgs) <= limit:
        return False
    payload["history"] = system_msgs + conv_msgs[-limit:]
    return True


def _layer_trim_sentence_windows(payload: dict[str, Any], limit: int = 3) -> bool:
    """Trim sentence windows — higher priority than history."""
    record_context = payload.get("record_context")
    if not isinstance(record_context, dict):
        return False
    windows = record_context.get("sentence_windows")
    if not isinstance(windows, list) or len(windows) <= limit:
        return False
    record_context["sentence_windows"] = windows[:limit]
    return True


def _layer_trim_source_excerpt(payload: dict[str, Any], limit: int = 1600) -> bool:
    """Trim source excerpt — second highest priority."""
    record_context = payload.get("record_context")
    if not isinstance(record_context, dict):
        return False
    excerpt = record_context.get("source_excerpt")
    if not isinstance(excerpt, str) or len(excerpt) <= limit:
        return False
    record_context["source_excerpt"] = utils.truncate_text(excerpt, limit)
    return True


def _layer_trim_article_overview(payload: dict[str, Any], limit: int = 800) -> bool:
    """Trim article overview — highest priority, compressed last."""
    overview = payload.get("article_overview")
    if not isinstance(overview, str) or len(overview) <= limit:
        return False
    payload["article_overview"] = utils.truncate_text(overview, limit)
    return True


# Compression layers ordered from lowest to highest priority.
# Lower priority = compressed first; higher priority = compressed last.
_COMPRESSION_LAYERS: list[tuple[str, object]] = [
    ("external_assets", _layer_trim_external_assets),
    ("record_assets", _layer_trim_record_assets),
    ("vocabulary", _layer_trim_vocabulary),
    ("insights", _layer_trim_insights),
    ("history", _layer_trim_history),
    ("sentence_windows", _layer_trim_sentence_windows),
    ("source_excerpt", _layer_trim_source_excerpt),
    ("article_overview", _layer_trim_article_overview),
]

# Aggressive follow-up layers that apply even tighter limits if the first
# pass wasn't enough. These are tried in order after the initial layers.
_AGGRESSIVE_LAYERS: list[tuple[str, object]] = [
    ("external_assets_drop", lambda p: _layer_trim_external_assets(p, limit=0)),
    ("record_assets_drop", lambda p: _layer_trim_record_assets(p, limit=0)),
    ("vocabulary_drop", lambda p: _layer_trim_vocabulary(p, limit=0)),
    ("insights_drop", lambda p: _layer_trim_insights(p, limit=0)),
    ("history_aggressive", lambda p: _layer_trim_history(p, limit=cfg.AGGRESSIVE_HISTORY_LIMIT)),
    ("sentence_windows_drop", lambda p: _layer_trim_sentence_windows(p, limit=0)),
    ("source_excerpt_aggressive", lambda p: _layer_trim_source_excerpt(p, limit=cfg.AGGRESSIVE_SOURCE_EXCERPT_LIMIT)),
    ("article_overview_aggressive", lambda p: _layer_trim_article_overview(p, limit=cfg.AGGRESSIVE_ARTICLE_OVERVIEW_LIMIT)),
]


def _progressive_compact(
    payload: dict[str, Any], *, budget_tokens: int
) -> tuple[dict[str, Any], list[str]]:
    """Apply compression layers progressively until the payload fits the budget.

    Each layer is applied in priority order (lowest first). After each layer,
    we re-estimate token count. If the payload fits, we stop. If not, we
    apply the next layer. This ensures high-priority fields are preserved
    as long as possible.

    Returns (compacted_payload, applied_layers) where applied_layers is a
    list of layer names that were actually applied (for audit / trace).
    """
    compact = json.loads(json.dumps(payload, ensure_ascii=False))
    current_tokens = estimate_token_count(compact)
    applied: list[str] = []

    if current_tokens <= budget_tokens:
        return compact, applied

    # Pass 1: apply each compression layer in priority order
    for layer_name, layer_fn in _COMPRESSION_LAYERS:
        changed = layer_fn(compact)
        if changed:
            applied.append(layer_name)
            current_tokens = estimate_token_count(compact)
            if current_tokens <= budget_tokens:
                return compact, applied

    # Pass 2: aggressive layers — drop low-priority fields entirely
    for layer_name, layer_fn in _AGGRESSIVE_LAYERS:
        changed = layer_fn(compact)
        if changed:
            applied.append(layer_name)
            current_tokens = estimate_token_count(compact)
            if current_tokens <= budget_tokens:
                return compact, applied

    return compact, applied


# ---------------------------------------------------------------------------
# Budget-aware prompt preparation (main entry point)
# ---------------------------------------------------------------------------

def prepare_prompt_payload(
    payload: dict[str, Any],
    *,
    max_input_tokens: int | None = None,
    reserved_points: int | None = None,
    tokens_per_point: int | None = None,
    multiplier_output: int = 0,
    budget_buffer_tokens: int,
    default_max_output_tokens: int,
    min_max_output_tokens: int,
) -> tuple[dict[str, Any], int, list[str], bool]:
    """Prepare prompt payload with budget-aware compaction.

    Returns (prompt_payload, budgeted_output_tokens, compaction_audit, context_too_large)
    where compaction_audit is a list of applied compression layer names and
    context_too_large is True when the payload still exceeds the input budget
    after all compaction layers or when explicit attachments would be lost.
    """
    prompt_payload = payload
    estimated_input_tokens = estimate_token_count(prompt_payload)
    compaction_audit: list[str] = []

    # Record original attachment count for preservation check
    original_attachment_count = len(
        payload.get("canonical_context", {}).get("attachments", [])
    )

    # Preferred mode: use an explicit runtime input budget. Legacy callers can
    # still derive a budget from billing-like parameters until all call sites
    # are migrated.
    max_input_budget = compute_max_input_budget(
        max_input_tokens=max_input_tokens,
        reserved_points=reserved_points,
        tokens_per_point=tokens_per_point,
        budget_buffer_tokens=budget_buffer_tokens,
        min_max_output_tokens=min_max_output_tokens,
        multiplier_output=multiplier_output,
    )

    if estimated_input_tokens > max_input_budget:
        # Use progressive compaction to fit within the real input budget
        prompt_payload, compaction_audit = _progressive_compact(payload, budget_tokens=max_input_budget)
        estimated_input_tokens = estimate_token_count(prompt_payload)

    # Check if compaction still can't fit the budget
    context_too_large = estimated_input_tokens > max_input_budget

    # Check if explicit attachments were lost during compaction
    if not context_too_large and original_attachment_count > 0:
        compacted_attachment_count = len(
            prompt_payload.get("canonical_context", {}).get("attachments", [])
        )
        if compacted_attachment_count < original_attachment_count:
            context_too_large = True

    if max_input_tokens is not None:
        budgeted_output_tokens = default_max_output_tokens
    else:
        assert reserved_points is not None
        assert tokens_per_point is not None
        weighted_budget = reserved_points * tokens_per_point
        weighted_remaining = max(weighted_budget - estimated_input_tokens - budget_buffer_tokens, 0)
        budgeted_output_tokens = max(
            min_max_output_tokens,
            min(default_max_output_tokens, weighted_remaining // multiplier_output if weighted_remaining else 0),
        )
    return prompt_payload, budgeted_output_tokens, compaction_audit, context_too_large
