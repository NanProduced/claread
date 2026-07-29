"""Ask Claread configuration constants.

All values are static defaults.  No env override or dynamic config.
Do not change values without updating corresponding tests.
"""

# ---------------------------------------------------------------------------
# Prompt budget / token limits
# ---------------------------------------------------------------------------
DEFAULT_MAX_OUTPUT_TOKENS: int = 1600
MIN_MAX_OUTPUT_TOKENS: int = 400
PROMPT_BUDGET_BUFFER_TOKENS: int = 8192
DEFAULT_RUNTIME_MAX_INPUT_TOKENS: int = 131072
DEFAULT_RUNTIME_MAX_OUTPUT_TOKENS: int = 8192
DEFAULT_RUNTIME_MAX_TURN_OUTPUT_TOKENS: int = 32768

# ---------------------------------------------------------------------------
# History / context limits
# ---------------------------------------------------------------------------
MAX_HISTORY_MESSAGES: int = 8
MAX_CONTEXT_TEXT: int = 3200
MAX_MESSAGE_TEXT: int = 1200
MAX_PROMPT_ASSET_ITEMS: int = 5

# ---------------------------------------------------------------------------
# Planner settings
# ---------------------------------------------------------------------------
PLANNER_MAX_HISTORY_MESSAGES: int = 8
DEFAULT_PLANNER_MAX_OUTPUT_TOKENS: int = 500
PLANNER_TEMPERATURE: float = 0.1
PLANNER_TIMEOUT_S: float = 25.0
PLANNER_MAX_RETRIES: int = 2

# ---------------------------------------------------------------------------
# Agent (main / replan) settings
# ---------------------------------------------------------------------------
AGENT_TEMPERATURE: float = 0.3
AGENT_TIMEOUT_S: float = 90.0

# ---------------------------------------------------------------------------
# Stream checkpoint thresholds
# ---------------------------------------------------------------------------
CHECKPOINT_MIN_FLUSH_INTERVAL_S: float = 0.8
CHECKPOINT_MIN_CONTENT_CHARS: int = 48
CHECKPOINT_MIN_REASONING_CHARS: int = 48

# ---------------------------------------------------------------------------
# Prompt compaction defaults (initial pass)
# ---------------------------------------------------------------------------
COMPACTION_MAX_HISTORY: int = 6
COMPACTION_MAX_RECORD_ASSETS: int = 3
COMPACTION_MAX_EXTERNAL_ASSETS: int = 3
COMPACTION_MAX_VOCABULARY: int = 3
COMPACTION_MAX_INSIGHTS: int = 3
COMPACTION_MAX_SENTENCE_WINDOWS: int = 5
COMPACTION_MAX_SOURCE_EXCERPT: int = 2400
COMPACTION_MAX_ARTICLE_OVERVIEW: int = 1200
COMPACTION_EXTERNAL_ASSET_CONTENT_LIMIT: int = 900

# ---------------------------------------------------------------------------
# Prompt compaction aggressive layer limits
# ---------------------------------------------------------------------------
AGGRESSIVE_HISTORY_LIMIT: int = 2
AGGRESSIVE_SOURCE_EXCERPT_LIMIT: int = 800
AGGRESSIVE_ARTICLE_OVERVIEW_LIMIT: int = 400

# ---------------------------------------------------------------------------
# Reference reranker settings (Phase 4 Round 7)
# ---------------------------------------------------------------------------
REFERENCE_RERANKER_ENABLED: bool = False
REFERENCE_RERANKER_TIMEOUT_S: float = 5.0
REFERENCE_RERANKER_MAX_CANDIDATES: int = 8
