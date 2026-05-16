from __future__ import annotations

from typing import Final, Literal

UsageScope = Literal[
    "user_billed",
    "system_internal",
    "anonymous_trial",
    "eval_debug",
]

BillingMode = Literal[
    "user_points",
    "internal_only",
    "trial",
    "no_charge",
]

USAGE_SCOPE_USER_BILLED: Final[UsageScope] = "user_billed"
USAGE_SCOPE_SYSTEM_INTERNAL: Final[UsageScope] = "system_internal"
USAGE_SCOPE_ANONYMOUS_TRIAL: Final[UsageScope] = "anonymous_trial"
USAGE_SCOPE_EVAL_DEBUG: Final[UsageScope] = "eval_debug"

BILLING_MODE_USER_POINTS: Final[BillingMode] = "user_points"
BILLING_MODE_INTERNAL_ONLY: Final[BillingMode] = "internal_only"
BILLING_MODE_TRIAL: Final[BillingMode] = "trial"
BILLING_MODE_NO_CHARGE: Final[BillingMode] = "no_charge"

STATUS_SUCCEEDED: Final[str] = "succeeded"
STATUS_FAILED: Final[str] = "failed"
STATUS_FALLBACK: Final[str] = "fallback"
STATUS_SKIPPED: Final[str] = "skipped"

ALL_USAGE_SCOPES: Final[tuple[str, ...]] = (
    USAGE_SCOPE_USER_BILLED,
    USAGE_SCOPE_SYSTEM_INTERNAL,
    USAGE_SCOPE_ANONYMOUS_TRIAL,
    USAGE_SCOPE_EVAL_DEBUG,
)

ALL_BILLING_MODES: Final[tuple[str, ...]] = (
    BILLING_MODE_USER_POINTS,
    BILLING_MODE_INTERNAL_ONLY,
    BILLING_MODE_TRIAL,
    BILLING_MODE_NO_CHARGE,
)
