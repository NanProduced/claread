"""T5.6b — translation execution lane classifier (null-safe SQL fragments).

is_section_lane  ⇔  origin = 'section_v1'
is_ordinary_lane ⇔  origin IS DISTINCT FROM 'section_v1'

Never use NOT (origin = 'section_v1') — missing keys yield NULL and drop
historical ordinary rows from WHERE clauses.
"""

from __future__ import annotations

SECTION_REQUEST_ORIGIN = "section_v1"
TRANSLATION_SECTION_OPERATION_FINGERPRINT = "translation_article_section_v1"
TRANSLATION_SECTION_POLICY_VERSION = "reader_translation_section_bootstrap_v1"

# SQL predicates on reader_jobs.input_json (or aliased job.input_json).
# Callers must substitute the column expression if the alias differs.
SQL_IS_SECTION_LANE = (
    "(input_json->>'request_origin') = 'section_v1'"
)
SQL_IS_ORDINARY_LANE = (
    "(input_json->>'request_origin') IS DISTINCT FROM 'section_v1'"
)


def sql_is_ordinary_lane(column_expr: str = "input_json") -> str:
    """Null-safe ordinary-lane predicate for a jsonb column expression."""
    return f"({column_expr}->>'request_origin') IS DISTINCT FROM 'section_v1'"


def sql_is_section_lane(column_expr: str = "input_json") -> str:
    return f"({column_expr}->>'request_origin') = 'section_v1'"


def is_section_request_origin(value: object) -> bool:
    return value == SECTION_REQUEST_ORIGIN


def is_ordinary_request_origin(value: object) -> bool:
    """Python-side mirror of IS DISTINCT FROM 'section_v1'."""
    return value != SECTION_REQUEST_ORIGIN


__all__ = [
    "SECTION_REQUEST_ORIGIN",
    "SQL_IS_ORDINARY_LANE",
    "SQL_IS_SECTION_LANE",
    "TRANSLATION_SECTION_OPERATION_FINGERPRINT",
    "TRANSLATION_SECTION_POLICY_VERSION",
    "is_ordinary_request_origin",
    "is_section_request_origin",
    "sql_is_ordinary_lane",
    "sql_is_section_lane",
]
