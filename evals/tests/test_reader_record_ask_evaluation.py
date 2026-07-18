"""Tests for evaluate_artifact — single 11-dimension evaluator entrypoint.

Spec: `.trae/specs/reader-record-ask-r4-a3-rework-session-eval-closure/spec.md`
Requirement: evaluator-based Phase 2/3 失败选择（P0-3）.

Covers:
- ``evaluate_artifact`` returns exactly 11 dimensions in canonical order.
- Each dimension name matches the canonical list (defensive sync check).
- ``is_content_failure`` returns True for content-quality failures and
  False for usage_observability-only failures.
- ``has_usage_gap_only`` correctly distinguishes the two cases.
- The key P0-3 regression: a ``finalized_status='ok'`` artifact that
  contains an unsupported ``2025`` year token is flagged as a content
  failure (via ``unsupported_temporal_claims``) and would enter Phase 2.
"""

from __future__ import annotations

from claread_eval.reader_record_ask.evaluation import (
    CONTENT_QUALITY_DIMENSIONS,
    DIMENSION_ORDER,
    OBSERVABILITY_DIMENSIONS,
    dimension_by_name,
    evaluate_artifact,
    failed_dimensions,
    has_usage_gap_only,
    is_content_failure,
)
from claread_eval.reader_record_ask.evaluators.artifact import (
    RawArtifact,
    RawUsage,
)
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskR4A3Case,
    ReaderRecordAskR4A3Expected,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_case(
    *,
    case_id: str = "bbc_main_idea",
    question_category: str = "main_idea",
    question: str = "这篇文章主要说什么？",
    expected_overrides: dict | None = None,
) -> ReaderRecordAskR4A3Case:
    expected = ReaderRecordAskR4A3Expected()
    if expected_overrides:
        expected = expected.model_copy(update=expected_overrides)
    return ReaderRecordAskR4A3Case(
        id=case_id,
        source_kind="bbc_record",
        record_id="bbc-test-001",
        article_text="(article text placeholder)",
        article_title="BBC test article",
        input_mode="manual",
        selection=None,
        rag_mode="off",
        source_metadata="unknown",
        baseline_mode="complete",
        question=question,
        question_category=question_category,  # type: ignore[arg-type]
        expected=expected,
    )


def _make_artifact(
    *,
    final_text: str | None = "这是一篇关于加拿大野火的测试回答。",
    finalized_status: str | None = "ok",
    model_short_name: str | None = "deepseek-v4",
    model_route: str | None = "deepseek",
    thinking_enabled: bool = False,
    latency_seconds: float | None = 1.5,
    requests: int | None = 1,
) -> RawArtifact:
    return RawArtifact(
        case_id="bbc_main_idea",
        run_id="phase1-test",
        run_index=0,
        model_short_name=model_short_name,
        model_route=model_route,
        thinking_enabled=thinking_enabled,
        final_text=final_text,
        finalized_status=finalized_status,
        latency_seconds=latency_seconds,
        agent_usage=RawUsage(
            requests=requests,
            input_tokens=10,
            output_tokens=20,
        ),
    )


# ---------------------------------------------------------------------------
# Canonical list / order
# ---------------------------------------------------------------------------


def test_dimension_order_has_exactly_11_dimensions() -> None:
    assert len(DIMENSION_ORDER) == 11
    assert len(set(DIMENSION_ORDER)) == 11


def test_content_quality_dimensions_excludes_usage_observability() -> None:
    assert "usage_observability" not in CONTENT_QUALITY_DIMENSIONS
    assert "usage_observability" in OBSERVABILITY_DIMENSIONS
    assert len(CONTENT_QUALITY_DIMENSIONS) == 10


def test_evaluate_artifact_returns_11_results_in_canonical_order() -> None:
    case = _make_case()
    artifact = _make_artifact()
    results = evaluate_artifact(case, artifact)
    assert [r.dimension for r in results] == list(DIMENSION_ORDER)


def test_evaluate_artifact_results_are_eval_dimension_result_instances() -> None:
    from claread_eval.reader_record_ask.evaluators.result import (
        EvalDimensionResult,
    )

    case = _make_case()
    artifact = _make_artifact()
    results = evaluate_artifact(case, artifact)
    for r in results:
        assert isinstance(r, EvalDimensionResult)


# ---------------------------------------------------------------------------
# P0-3 regression: finalized_status=ok but 2025 unsupported claim
# ---------------------------------------------------------------------------


def test_status_ok_with_2025_year_is_content_failure() -> None:
    """The key P0-3 regression.

    Before the rework, the harness would break on the first
    ``finalized_status='ok'`` artifact and never reach the
    ``unsupported_temporal_claims`` evaluator. A ``ok``-status artifact
    containing an unsupported ``2025`` year token must be flagged as a
    content failure and selected for Phase 2.
    """
    case = _make_case(
        expected_overrides={"allowed_temporal_claims": []},
    )
    artifact = _make_artifact(
        final_text="这篇文章发表于 2025 年，讨论了加拿大野火。",
        finalized_status="ok",
    )
    results = evaluate_artifact(case, artifact)
    assert is_content_failure(results) is True

    temporal = dimension_by_name(results, "unsupported_temporal_claims")
    assert temporal is not None
    assert temporal.passed is False
    assert "2025" in temporal.details


def test_status_ok_with_2025_year_does_not_show_usage_gap_only() -> None:
    case = _make_case(
        expected_overrides={"allowed_temporal_claims": []},
    )
    artifact = _make_artifact(
        final_text="这篇文章发表于 2025 年，讨论了加拿大野火。",
        finalized_status="ok",
    )
    results = evaluate_artifact(case, artifact)
    # 2025 fails unsupported_temporal_claims (content failure), so this
    # is NOT a usage-gap-only scenario even if usage_observability also
    # happened to fail.
    assert has_usage_gap_only(results) is False


def test_status_ok_with_allowed_year_passes_temporal() -> None:
    case = _make_case(
        expected_overrides={"allowed_temporal_claims": ["2025"]},
    )
    artifact = _make_artifact(
        final_text="这篇文章发表于 2025 年，讨论了加拿大野火。",
        finalized_status="ok",
    )
    results = evaluate_artifact(case, artifact)
    temporal = dimension_by_name(results, "unsupported_temporal_claims")
    assert temporal is not None
    assert temporal.passed is True


# ---------------------------------------------------------------------------
# usage_observability-only failure does NOT trigger content failure
# ---------------------------------------------------------------------------


def test_usage_gap_only_does_not_trigger_content_failure() -> None:
    """Spec: "默认不要仅因 usage 缺失升级模型".

    An artifact whose only failing dimension is ``usage_observability``
    must NOT be selected for Phase 2 — it is recorded as an
    observability gap instead.
    """
    case = _make_case()
    # Build an artifact where usage is missing but everything else is OK.
    artifact = RawArtifact(
        case_id="bbc_main_idea",
        run_id="phase1-test",
        run_index=0,
        model_short_name="deepseek-v4",
        model_route=None,  # missing → usage_observability fails
        thinking_enabled=False,
        final_text="这是一篇关于加拿大野火的测试回答。",
        finalized_status="ok",
        latency_seconds=1.5,
        agent_usage=None,  # missing → usage_observability fails
    )
    results = evaluate_artifact(case, artifact)
    assert is_content_failure(results) is False
    assert has_usage_gap_only(results) is True


def test_all_pass_returns_no_content_failure() -> None:
    case = _make_case()
    artifact = _make_artifact()
    results = evaluate_artifact(case, artifact)
    assert is_content_failure(results) is False
    assert has_usage_gap_only(results) is False
    assert failed_dimensions(results) == []


# ---------------------------------------------------------------------------
# Terminal failure is captured by answer_success → content failure
# ---------------------------------------------------------------------------


def test_terminal_failure_finalized_status_not_ok_is_content_failure() -> None:
    case = _make_case()
    artifact = _make_artifact(
        finalized_status="context_stale",
        final_text="",
    )
    results = evaluate_artifact(case, artifact)
    assert is_content_failure(results) is True
    answer = dimension_by_name(results, "answer_success")
    assert answer is not None
    assert answer.passed is False


def test_terminal_failure_empty_final_text_is_content_failure() -> None:
    case = _make_case()
    artifact = _make_artifact(
        finalized_status="ok",
        final_text="",
    )
    results = evaluate_artifact(case, artifact)
    assert is_content_failure(results) is True


def test_terminal_failure_with_exception_error_field() -> None:
    """An artifact with ``error`` set but ``finalized_status='ok'`` and
    non-empty final_text is NOT automatically a content failure — the
    error field alone does not flip the verdict. (The harness uses
    ``error`` for diagnostics; the evaluators look at content.)
    """
    case = _make_case()
    artifact = _make_artifact(final_text="正常回答内容。")
    artifact.error = "some non-fatal warning"
    results = evaluate_artifact(case, artifact)
    # No content-quality dim failed → not a content failure.
    assert is_content_failure(results) is False


# ---------------------------------------------------------------------------
# Defensive: dimension name sync
# ---------------------------------------------------------------------------


def test_evaluate_artifact_raises_if_evaluator_returns_wrong_dimension_name(
    monkeypatch,
) -> None:
    """If an evaluator module is refactored to return a different
    dimension name, ``evaluate_artifact`` must fail loudly rather than
    silently producing a list whose order disagrees with DIMENSION_ORDER.
    """
    from claread_eval.reader_record_ask import evaluation as evaluation_mod

    original = evaluation_mod.evaluate_answer_success

    def _renamed(case, artifact):  # noqa: ANN001
        result = original(case, artifact)
        # Simulate a bad rename inside the evaluator module.
        return result.model_copy(update={"dimension": "wrong_name"})

    # Patch the binding inside the evaluation module (not the source
    # evaluator module) because ``evaluation.py`` already imported the
    # function reference at module load time.
    monkeypatch.setattr(evaluation_mod, "evaluate_answer_success", _renamed)
    case = _make_case()
    artifact = _make_artifact()
    try:
        evaluate_artifact(case, artifact)
    except ValueError as exc:
        assert "answer_success" in str(exc)
        assert "wrong_name" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected ValueError for renamed dimension")


# ---------------------------------------------------------------------------
# LLM judge hook is passed through to entity_precision (no-op here)
# ---------------------------------------------------------------------------


def test_llm_judge_hook_is_passed_through_but_cannot_override_failure() -> None:
    """The ``llm_judge`` hook may only supplement; it cannot flip a
    deterministic ``passed=False`` to ``True``.

    Per the existing ``entity_precision`` contract, the judge is only
    invoked when there are no deterministic failures (so it may add a
    note). When there IS a deterministic failure (region leaked as city),
    the judge is NOT called and ``llm_judge_used`` stays ``False`` —
    the deterministic verdict stands on its own.
    """
    case = _make_case(
        question_category="city_enumeration",
        question="文章提到了哪些城市？",
        expected_overrides={
            "expected_entity_set": {"city": ["Thunder Bay", "纽约"]},
            "allowed_entities_by_type": {
                "city": ["Thunder Bay", "纽约"],
                "region": ["纽约州西部部分地区"],
            },
        },
    )
    artifact = _make_artifact(
        final_text="文章提到的城市有：Thunder Bay、纽约、纽约州西部部分地区。",
    )

    captured: dict = {}

    def judge(text: str, ctx: dict) -> dict:
        captured["text"] = text
        captured["ctx"] = ctx
        return {"note": "judge says fine, but determinism wins"}

    results = evaluate_artifact(case, artifact, llm_judge=judge)
    entity = dimension_by_name(results, "entity_precision")
    assert entity is not None
    # Deterministic failure (region leaked as city) — judge cannot override.
    assert entity.passed is False
    # Judge was NOT called because there was a deterministic failure.
    assert entity.llm_judge_used is False
    assert captured == {}  # judge hook never invoked


def test_llm_judge_hook_invoked_when_no_deterministic_failure() -> None:
    """When there are no deterministic failures, the judge IS invoked
    and may record a supplementary note. It still cannot flip ``passed``
    (which is already ``True`` at that point) — it only adds a note.
    """
    case = _make_case(
        question_category="city_enumeration",
        question="文章提到了哪些城市？",
        expected_overrides={
            "expected_entity_set": {"city": ["Thunder Bay", "纽约"]},
            "allowed_entities_by_type": {
                "city": ["Thunder Bay", "纽约"],
            },
        },
    )
    artifact = _make_artifact(
        final_text="文章提到的城市有：Thunder Bay 和 纽约。",
    )

    def judge(text: str, ctx: dict) -> dict:
        return {"note": "all entities look type-correct"}

    results = evaluate_artifact(case, artifact, llm_judge=judge)
    entity = dimension_by_name(results, "entity_precision")
    assert entity is not None
    assert entity.passed is True
    assert entity.llm_judge_used is True
    assert "type-correct" in (entity.llm_judge_note or "")
