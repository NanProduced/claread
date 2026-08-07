"""Ask Claread runtime budget defaults.

All values are static defaults.  No env override or dynamic config.
Do not change values without updating corresponding tests.
"""

# ---------------------------------------------------------------------------
# Runtime token budget (consumed by ``model_options.py``)
# ---------------------------------------------------------------------------
PROMPT_BUDGET_BUFFER_TOKENS: int = 8192
DEFAULT_RUNTIME_MAX_INPUT_TOKENS: int = 131072
DEFAULT_RUNTIME_MAX_OUTPUT_TOKENS: int = 8192
DEFAULT_RUNTIME_MAX_TURN_OUTPUT_TOKENS: int = 32768
