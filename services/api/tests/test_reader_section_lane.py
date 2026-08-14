"""lane classifier + SQL null-safety."""

from __future__ import annotations

from pathlib import Path

from app.services.reader_orchestration.section_lane import (
    SECTION_REQUEST_ORIGIN,
    SQL_IS_ORDINARY_LANE,
    SQL_IS_SECTION_LANE,
    is_ordinary_request_origin,
    is_section_request_origin,
    sql_is_ordinary_lane,
    sql_is_section_lane,
)

_REPO_ORCH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "reader_orchestration"
)


def test_lane_predicates_python() -> None:
    assert is_section_request_origin("section_v1")
    assert not is_section_request_origin(None)
    assert not is_section_request_origin("ordinary")
    assert is_ordinary_request_origin(None)
    assert is_ordinary_request_origin("ordinary")
    assert not is_ordinary_request_origin(SECTION_REQUEST_ORIGIN)


def test_sql_ordinary_uses_is_distinct_from_not_not_equals() -> None:
    assert "IS DISTINCT FROM" in SQL_IS_ORDINARY_LANE
    assert "IS DISTINCT FROM" in sql_is_ordinary_lane("job.input_json")
    # Forbidden pattern must not appear as ordinary predicate.
    assert "NOT (" not in SQL_IS_ORDINARY_LANE
    assert SQL_IS_SECTION_LANE == "(input_json->>'request_origin') = 'section_v1'"
    assert sql_is_section_lane("job.input_json").endswith("= 'section_v1'")


def test_repository_and_worker_loop_and_supersede_use_null_safe_ordinary() -> None:
    repo = (_REPO_ORCH / "repository.py").read_text(encoding="utf-8")
    loop = (_REPO_ORCH / "worker_loop.py").read_text(encoding="utf-8")
    boot = (_REPO_ORCH / "job_bootstrap.py").read_text(encoding="utf-8")
    needle = "IS DISTINCT FROM 'section_v1'"
    assert needle in repo
    assert needle in loop
    assert needle in boot
    # Must not use the NULL-unsafe form in supersede/count paths.
    assert "NOT ((input_json->>'request_origin') = 'section_v1')" not in boot
    assert "NOT ((input_json->>'request_origin') = 'section_v1')" not in repo
