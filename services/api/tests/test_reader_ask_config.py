"""Tests for Ask Claread config constants stability."""

from app.services.reader_ask import config as cfg


def test_prompt_budget_constants() -> None:
    assert cfg.DEFAULT_MAX_OUTPUT_TOKENS == 1600
    assert cfg.MIN_MAX_OUTPUT_TOKENS == 400
    assert cfg.PROMPT_BUDGET_BUFFER_TOKENS == 800


def test_history_and_context_limits() -> None:
    assert cfg.MAX_HISTORY_MESSAGES == 8
    assert cfg.MAX_CONTEXT_TEXT == 3200
    assert cfg.MAX_MESSAGE_TEXT == 1200
    assert cfg.MAX_PROMPT_ASSET_ITEMS == 5


def test_planner_settings() -> None:
    assert cfg.PLANNER_MAX_HISTORY_MESSAGES == 8
    assert cfg.DEFAULT_PLANNER_MAX_OUTPUT_TOKENS == 500
    assert cfg.PLANNER_TEMPERATURE == 0.1
    assert cfg.PLANNER_TIMEOUT_S == 25.0
    assert cfg.PLANNER_MAX_RETRIES == 2


def test_agent_settings() -> None:
    assert cfg.AGENT_TEMPERATURE == 0.3
    assert cfg.AGENT_TIMEOUT_S == 90.0


def test_checkpoint_thresholds() -> None:
    assert cfg.CHECKPOINT_MIN_FLUSH_INTERVAL_S == 0.8
    assert cfg.CHECKPOINT_MIN_CONTENT_CHARS == 48
    assert cfg.CHECKPOINT_MIN_REASONING_CHARS == 48


def test_compaction_defaults() -> None:
    assert cfg.COMPACTION_MAX_HISTORY == 6
    assert cfg.COMPACTION_MAX_RECORD_ASSETS == 3
    assert cfg.COMPACTION_MAX_EXTERNAL_ASSETS == 3
    assert cfg.COMPACTION_MAX_VOCABULARY == 3
    assert cfg.COMPACTION_MAX_INSIGHTS == 3
    assert cfg.COMPACTION_MAX_SENTENCE_WINDOWS == 5
    assert cfg.COMPACTION_MAX_SOURCE_EXCERPT == 2400
    assert cfg.COMPACTION_MAX_ARTICLE_OVERVIEW == 1200
    assert cfg.COMPACTION_EXTERNAL_ASSET_CONTENT_LIMIT == 900


def test_aggressive_layer_limits() -> None:
    assert cfg.AGGRESSIVE_HISTORY_LIMIT == 2
    assert cfg.AGGRESSIVE_SOURCE_EXCERPT_LIMIT == 800
    assert cfg.AGGRESSIVE_ARTICLE_OVERVIEW_LIMIT == 400
