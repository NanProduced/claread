from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.artifact import (
    RawArtifact,
    RawEvidenceObservation,
)
from claread_eval.reader_record_ask.evaluators.evidence_minimality import (
    evaluate_evidence_minimality,
)
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskR4A3Case,
    ReaderRecordAskR4A3Expected,
)


def _make_case() -> ReaderRecordAskR4A3Case:
    return ReaderRecordAskR4A3Case(
        id="t-evidence-min",
        source_kind="synthetic_short",
        input_mode="manual",
        source_metadata="unknown",
        baseline_mode="complete",
        question="这篇文章主要说了什么？",
        question_category="main_idea",
        expected=ReaderRecordAskR4A3Expected(),
    )


def _obs(handle_id: str, kind: str = "article_seed") -> RawEvidenceObservation:
    return RawEvidenceObservation(
        handle_id=handle_id,
        kind=kind,
        snippet="文章片段",
        provenance="baseline_context" if kind == "article_seed" else "search_current_article",
    )


def _make_artifact(
    *,
    handles: list[str],
    observations: list[RawEvidenceObservation],
    baseline_is_complete: bool | None = True,
) -> RawArtifact:
    return RawArtifact(
        case_id="t-evidence-min",
        run_id="run-1",
        finalized_status="ok",
        final_text="回答",
        cited_evidence_handles=handles,
        all_evidence_observations=observations,
        baseline_is_complete=baseline_is_complete,
    )


def test_positive_within_limit_no_duplicates() -> None:
    handles = ["evh_a" + "0" * 29, "evh_b" + "0" * 29, "evh_c" + "0" * 29]
    artifact = _make_artifact(handles=handles, observations=[_obs(h) for h in handles])
    result = evaluate_evidence_minimality(_make_case(), artifact)
    assert result.passed is True
    assert result.severity == "none"


def test_negative_too_many_handles_high_severity() -> None:
    handles = [f"evh_{i:032x}" for i in range(7)]
    artifact = _make_artifact(handles=handles, observations=[_obs(h) for h in handles])
    result = evaluate_evidence_minimality(_make_case(), artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "too many handles" in result.details


def test_negative_duplicate_handles() -> None:
    h = "evh_a" + "0" * 29
    artifact = _make_artifact(handles=[h, h], observations=[_obs(h)])
    result = evaluate_evidence_minimality(_make_case(), artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "duplicate" in result.details


def test_negative_unknown_handle_not_in_observations() -> None:
    known = "evh_a" + "0" * 29
    unknown = "evh_z" + "0" * 29
    artifact = _make_artifact(handles=[known, unknown], observations=[_obs(known)])
    result = evaluate_evidence_minimality(_make_case(), artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "not in observations" in result.details


def test_soft_failure_all_search_hit_when_baseline_complete() -> None:
    handles = ["evh_a" + "0" * 29, "evh_b" + "0" * 29]
    obs = [_obs(h, kind="search_hit") for h in handles]
    artifact = _make_artifact(
        handles=handles, observations=obs, baseline_is_complete=True
    )
    result = evaluate_evidence_minimality(_make_case(), artifact)
    assert result.passed is False
    assert result.severity == "medium"
    assert "search_hit" in result.details


def test_no_soft_failure_when_baseline_incomplete() -> None:
    handles = ["evh_a" + "0" * 29, "evh_b" + "0" * 29]
    obs = [_obs(h, kind="search_hit") for h in handles]
    artifact = _make_artifact(
        handles=handles, observations=obs, baseline_is_complete=False
    )
    result = evaluate_evidence_minimality(_make_case(), artifact)
    assert result.passed is True
