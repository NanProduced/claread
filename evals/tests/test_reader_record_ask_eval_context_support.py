"""Tests for context_support evaluator (R4-A4-0 final closure contract).

Spec: `.trae/specs/reader-record-ask-r4-a3-rework-session-eval-closure/
spec.md` — Requirement: context_support atomic fact contract（P0-6）.

R4-A4-0 final closure — authoritative model-visible context binding
==================================================================

The previous (R4-A4-0 Task 1) implementation grounded each atomic fact
by matching ``fact.source_aliases`` against the truncated public
snippet, and bound every fact to ``cited_handles[0]``. R4-A4-0 final
closure replaces this with typed
:class:`ModelContextSupportObservation` entries computed at harness
run time against the ACTUAL model-visible context
(``result.baseline_context.model_context_chunks``).

This file covers two test groups:

1. **Updated contract tests** — the original Task-1 tests rewritten
   for the new contract. Tests that exercised the removed
   ``expected_baseline_fingerprint`` parameter and the removed
   ``cited_handle`` / ``baseline_fingerprint`` fields now exercise
   ``model_context_fingerprint`` / ``supporting_handle_ids`` /
   ``RawArtifact.model_context_fingerprint`` /
   ``RawArtifact.model_context_handle_ids``.

2. **R4-A4-0 final closure required tests (1..13)** — the 13
   scenarios mandated by the user spec for this rework round. They
   are written as standalone tests at the bottom of the file so they
   can be audited as a block during the delivery report.

Coverage map for the 13 required tests (see section header below):

  1. snapshot has alias, model_context_chunks truncated → support=False
  2. alias in second chunk → supporting_handle_ids only second handle
  3. answer cites only first chunk, fact in second chunk → fail
  4. answer cites correct second handle → pass
  5. one fact supported by two chunks → dedup handles, any citation passes
  6. cited handles empty but support=True → fail (fact_not_cited)
  7. runtime exception → empty observation/fingerprint/handle_ids
  8. duplicate/unknown fact observations → fail-closed
  9. fingerprint mismatch through real evaluate_artifact entry
  10. medium/long baseline >16 units → cannot use 17th unit text
  11. StrictBool coercion rejects "false" / "true" / 0 / 1 / 0.0 / 1.0
  12. two configs match Flash phase → fail-closed AMBIGUOUS (no first-pick)
  13. legacy artifact missing new fields → indeterminate (not model failure)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from claread_eval.reader_record_ask.evaluation import evaluate_artifact
from claread_eval.reader_record_ask.evaluators.aggregator import (
    AggregatedReport,
    aggregate_results,
)
from claread_eval.reader_record_ask.evaluators.artifact import (
    ModelContextSupportObservation,
    RawArtifact,
    RawEvidenceObservation,
    RawUsage,
)
from claread_eval.reader_record_ask.evaluators.context_support import (
    evaluate_context_support,
)
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.loader import (
    _migrate_legacy_required_article_facts,
)
from claread_eval.reader_record_ask.report import generate_r4_a3_report
from claread_eval.reader_record_ask.schema import (
    AtomicExpectedFact,
    ReaderRecordAskR4A3Case,
    ReaderRecordAskR4A3Dataset,
    ReaderRecordAskR4A3Expected,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Synthetic SHA-256 fingerprint for the model-visible baseline in tests.
#: 64 lowercase hex chars — matches the strict format validator on
#: :class:`ModelContextSupportObservation.model_context_fingerprint` and
#: :class:`RawArtifact.model_context_fingerprint`.
_TEST_FP = "a" * 64

#: A DIFFERENT synthetic fingerprint used to test mismatch detection.
_OTHER_FP = "b" * 64

#: Synthetic chunk handle_ids used in the new contract tests. The
#: evaluator only cares that they are non-empty strict strings present
#: in BOTH ``RawArtifact.model_context_handle_ids`` AND (when support=True)
#: in ``observation.supporting_handle_ids``.
_HANDLE_CHUNK_0 = "evh_" + "0" * 32
_HANDLE_CHUNK_1 = "evh_" + "1" * 32
_HANDLE_CHUNK_2 = "evh_" + "2" * 32
_HANDLE_UNKNOWN = "evh_" + "f" * 32  # not in model_context_handle_ids


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_case_with_atomic_facts(
    facts: list[AtomicExpectedFact],
    *,
    case_id: str = "t-context-support",
    question_category: str = "city_enumeration",
) -> ReaderRecordAskR4A3Case:
    return ReaderRecordAskR4A3Case(
        id=case_id,
        source_kind="synthetic_short",
        input_mode="manual",
        source_metadata="unknown",
        baseline_mode="complete",
        question="文章提到了哪些城市？",
        question_category=question_category,  # type: ignore[arg-type]
        expected=ReaderRecordAskR4A3Expected(atomic_facts=facts),
    )


def _make_case_with_legacy_facts(facts: list[str]) -> ReaderRecordAskR4A3Case:
    """Build a case using the deprecated ``required_article_facts`` field."""
    return ReaderRecordAskR4A3Case(
        id="t-context-support-legacy",
        source_kind="synthetic_short",
        input_mode="manual",
        source_metadata="unknown",
        baseline_mode="complete",
        question="文章提到了哪些城市？",
        question_category="city_enumeration",
        expected=ReaderRecordAskR4A3Expected(required_article_facts=facts),
    )


def _make_observation(
    *,
    fact_id: str,
    support: bool,
    fingerprint: str = _TEST_FP,
    supporting_handles: list[str] | None = None,
) -> ModelContextSupportObservation:
    """Build a typed support observation under the R4-A4-0 final contract.

    ``supporting_handles`` defaults to ``[_HANDLE_CHUNK_0]`` when
    ``support=True`` and to ``[]`` when ``support=False`` — this
    mirrors the harness contract (``support=True`` with empty handles
    is fail-closed ``instrumentation_incomplete``).
    """
    if supporting_handles is None:
        supporting_handles = [_HANDLE_CHUNK_0] if support else []
    return ModelContextSupportObservation(
        fact_id=fact_id,
        support=support,
        model_context_fingerprint=fingerprint,
        supporting_handle_ids=supporting_handles,
    )


def _make_artifact(
    *,
    final_text: str,
    resolved_snippets: list[str] | None = None,
    model_context_support: list[ModelContextSupportObservation] | None = None,
    cited_evidence_handles: list[str] | None = None,
    model_context_fingerprint: str | None = _TEST_FP,
    model_context_handle_ids: list[str] | None = None,
    case_id: str = "t-context-support",
    instrumentation_version: str | None = None,
    capture_status: str | None = None,
) -> RawArtifact:
    """Build an artifact with typed model-context support observations.

    Defaults align with the new authoritative contract:
    - ``model_context_fingerprint`` defaults to ``_TEST_FP`` so
      observations carrying the same fingerprint verify cleanly.
    - ``model_context_handle_ids`` defaults to ``[_HANDLE_CHUNK_0]``
      so a default observation with ``supporting_handles=[_HANDLE_CHUNK_0]``
      is in the model context.

    Pass ``model_context_fingerprint=None`` and
    ``model_context_handle_ids=[]`` for the legacy-artifact path
    (P0-4 backward compat).

    R4-A4-0 final gate closure (P0-1): the explicit lifecycle fields
    ``instrumentation_version`` / ``capture_status`` drive the 4-state
    classification. When BOTH are ``None`` (default), the lifecycle is
    INFERRED from ``fingerprint`` / ``handle_ids``:

    - If ``fingerprint is not None AND handle_ids is non-empty`` →
      ``("reader_record_ask_model_context_v1", "captured")`` (the
      authoritative per-fact classification path).
    - Otherwise → ``(None, None)`` (legacy — the evaluator returns
      coverage_incomplete with ``legacy_artifact_no_model_context_support``).

    Callers can explicitly override by passing both
    ``instrumentation_version`` and ``capture_status``. When overriding
    to a non-None state, the fingerprint / handle_ids / observations
    MUST satisfy the cross-field validator invariants for that state
    (see :class:`RawArtifact._validate_model_context_instrumentation_lifecycle`).
    """
    if model_context_handle_ids is None:
        model_context_handle_ids = [_HANDLE_CHUNK_0]
    # P0-1: infer lifecycle from fingerprint/handle_ids when not
    # explicitly specified. This keeps existing tests working without
    # forcing every caller to pass the lifecycle fields.
    if instrumentation_version is None and capture_status is None:
        if model_context_fingerprint is not None and model_context_handle_ids:
            instrumentation_version = "reader_record_ask_model_context_v1"
            capture_status = "captured"
        # else: leave both as None → legacy
    cited_handles = list(cited_evidence_handles or [])
    # Build resolved_evidence from explicit snippets, then ensure every
    # cited handle is present (so evidence_minimality's
    # "handles not in observations" check passes for the success-path
    # spec tests). Handles already provided via resolved_snippets are
    # not duplicated.
    resolved_evidence: list[RawEvidenceObservation] = [
        RawEvidenceObservation(
            handle_id=f"evh_{i:032x}",
            kind="article_seed",
            snippet=snippet,
            provenance="baseline_context",
        )
        for i, snippet in enumerate(resolved_snippets or [])
    ]
    existing_handles = {ev.handle_id for ev in resolved_evidence}
    for handle in cited_handles:
        if handle not in existing_handles:
            resolved_evidence.append(
                RawEvidenceObservation(
                    handle_id=handle,
                    kind="article_seed",
                    snippet="",
                    provenance="baseline_context",
                )
            )
            existing_handles.add(handle)
    return RawArtifact(
        case_id=case_id,
        run_id="run-1",
        finalized_status="ok",
        final_text=final_text,
        resolved_evidence=resolved_evidence,
        # ``all_evidence_observations`` is the field evidence_minimality
        # actually consults to verify cited handles are known. Mirror
        # resolved_evidence here so success-path spec tests don't trip
        # ``handles not in observations`` on the dimension under test.
        all_evidence_observations=list(resolved_evidence),
        cited_evidence_handles=cited_handles,
        model_context_support=model_context_support or [],
        model_context_fingerprint=model_context_fingerprint,
        model_context_handle_ids=model_context_handle_ids,
        model_context_instrumentation_version=instrumentation_version,
        model_context_capture_status=capture_status,
        # Observability defaults: usage_observability is excluded from
        # the accepted/rework ``all_passed`` check, but missing
        # observability still clusters as ``observability-missing`` in
        # the aggregated report. Setting sane defaults keeps the spec
        # tests focused on the dimension under test (context_support)
        # rather than tripping unrelated observability clusters.
        agent_usage=RawUsage(
            requests=1, input_tokens=10, output_tokens=5,
        ),
        model_route="deepseek-chat",
        latency_seconds=1.0,
    )


# ---------------------------------------------------------------------------
# Basic positive / negative cases (R4-A4-0 final closure contract)
# ---------------------------------------------------------------------------


def test_positive_fact_mentioned_and_grounded() -> None:
    """Required fact mentioned in answer AND support=True with cited handle → PASS."""
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
        AtomicExpectedFact(
            fact_id="city-toronto",
            answer_alias_groups=[["Toronto"]],
            source_aliases=["Toronto"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到的城市包括 Thunder Bay 和 Toronto。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(fact_id="city-thunder-bay", support=True),
            _make_observation(fact_id="city-toronto", support=True),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is True
    assert result.severity == "none"
    assert "all required atomic facts mentioned and grounded" in result.details


def test_negative_fact_mentioned_but_support_false() -> None:
    """Required fact in answer but support=False → FAIL (high).

    This is a real model failure: the answer cited a fact the model
    could not have seen in its baseline.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到的城市包括 Thunder Bay。",
        model_context_support=[
            _make_observation(fact_id="city-thunder-bay", support=False),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "city-thunder-bay" in result.details
    assert "fact_not_supported" in result.details


def test_negative_required_fact_not_mentioned() -> None:
    """Required fact not mentioned in answer → FAIL (high)."""
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到了多伦多。",  # no Thunder Bay mentioned
        model_context_support=[
            _make_observation(fact_id="city-thunder-bay", support=True),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "not mentioned" in result.details
    assert "city-thunder-bay" in result.details


def test_case_insensitive_match() -> None:
    """Aliases match case-insensitively."""
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["thunder bay"]],
            source_aliases=["thunder bay"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="The cities include THUNDER BAY.",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(fact_id="city-thunder-bay", support=True),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is True


# ---------------------------------------------------------------------------
# Source alias outside public snippet but inside model context
# ---------------------------------------------------------------------------


def test_source_alias_outside_snippet_but_inside_model_context_passes() -> None:
    """Spec: "source alias 位于 public snippet 外，但位于 model context 内 → pass".

    Under R4-A4-0 final closure the snippet is irrelevant — grounding
    uses ``model_context_support`` computed against the ACTUAL
    model-visible chunks. ``support=True`` with a valid cited
    supporting handle → PASS regardless of snippet contents.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="late-fact-egypt-port",
            answer_alias_groups=[["Egypt", "埃及"]],
            source_aliases=["Alexandria"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章后段提到 Egypt 港口城市 Alexandria。",
        # Snippet is truncated BEFORE "Alexandria" appears in the body.
        # Under the old contract this would fail; under R4-A4-0 final
        # closure the typed observation says support=True with a
        # supporting handle that IS in the cited set → PASS.
        resolved_snippets=["... earlier article body truncated before Alexandria ..."],
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(fact_id="late-fact-egypt-port", support=True),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is True


def test_source_alias_not_in_model_context_fails() -> None:
    """Spec: "source alias 不在 model context → fail".

    The harness verified the baseline and determined the source alias
    is NOT present. The answer's claim is unsupported — real failure.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-atlantis",
            answer_alias_groups=[["Atlantis"]],
            source_aliases=["Atlantis"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到了 Atlantis。",
        # Harness verified: "Atlantis" does NOT appear in baseline.
        # supporting_handle_ids is empty (correct shape for support=False).
        model_context_support=[
            _make_observation(fact_id="city-atlantis", support=False),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "city-atlantis" in result.details
    assert "fact_not_supported" in result.details


def test_supporting_handle_not_in_cited_set_fails() -> None:
    """Spec: "supporting_handle_ids not in cited_evidence_handles → fail".

    The observation's ``supporting_handle_ids`` are valid (in
    ``model_context_handle_ids``) and support=True, but none of them
    appear in the artifact's ``cited_evidence_handles``. The model saw
    the fact but did not cite the supporting chunk → FAIL
    (``fact_not_cited``).
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        # Cited a DIFFERENT handle than the supporting one.
        cited_evidence_handles=["evh_some_other_handle"],
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(
                fact_id="city-thunder-bay",
                support=True,
                supporting_handles=[_HANDLE_CHUNK_0],
            ),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert "fact_not_cited" in result.details


def test_fingerprint_mismatch_fails() -> None:
    """Spec: "observation.model_context_fingerprint ≠
    artifact.model_context_fingerprint → fail (instrumentation_incomplete)".

    The observation was computed against a different baseline than the
    artifact records — the observation is not authoritative for this
    artifact. Fail-closed.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        # Artifact fingerprint is _TEST_FP; observation fingerprint is
        # _OTHER_FP → mismatch.
        model_context_fingerprint=_TEST_FP,
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(
                fact_id="city-thunder-bay",
                support=True,
                fingerprint=_OTHER_FP,  # mismatch
                supporting_handles=[_HANDLE_CHUNK_0],
            ),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert "fingerprint_mismatch" in result.details


def test_missing_observation_for_required_fact_is_instrumentation_incomplete() -> None:
    """Spec (R4-A4-0 final closure P0-4): a NEW artifact (has fingerprint)
    that lacks an observation for a required fact with source_aliases is
    ``instrumentation_incomplete`` — fail-closed.

    Under the previous Task-1 contract this was a soft
    ``coverage_incomplete`` signal (passed=True). The new contract
    tightens this: a new-style artifact (carrying a fingerprint) MUST
    have an observation for every required fact with source_aliases.
    Absence means the harness could not determine support — fail-closed.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    # New-style artifact: has fingerprint, has model_context_handle_ids,
    # but no observation for the required fact.
    artifact = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        model_context_fingerprint=_TEST_FP,
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[],  # missing observation
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert "no_observation_for_required_fact" in result.details
    assert "city-thunder-bay" in result.details


def test_legacy_artifact_no_model_context_support_is_coverage_incomplete() -> None:
    """Spec (R4-A4-0 final closure P0-4 legacy compat): an artifact with
    NO fingerprint AND NO observations is a legacy artifact predating
    R4-A4-0 final closure. The evaluator surfaces a
    ``legacy_artifact_no_model_context_support`` signal and does NOT
    auto-pass or auto-fail.

    Old artifacts cannot be authoritatively re-evaluated under the new
    contract; they require a new run. The historical replay tool
    labels these as ``indeterminate_requires_new_artifact``.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    # Legacy artifact: no fingerprint, no handle_ids, no observations.
    artifact = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        resolved_snippets=["Thunder Bay"],
        model_context_fingerprint=None,
        model_context_handle_ids=[],
        model_context_support=[],
    )
    result = evaluate_context_support(case, artifact)
    # Coverage incomplete — not auto-pass, not auto-fail.
    assert result.passed is True  # soft signal, not failure
    assert "legacy_artifact_no_model_context_support" in result.details


def test_body_text_must_not_appear_in_observation() -> None:
    """Spec: "不把正文写入 artifact/report".

    The :class:`ModelContextSupportObservation` schema must NOT have
    any field that stores article body text. Only ``fact_id``,
    ``support``, ``model_context_fingerprint``, and
    ``supporting_handle_ids`` are allowed. ``extra="forbid"`` enforces
    this at the schema level.
    """
    # Attempting to add a "body" field must fail (extra="forbid").
    with pytest.raises(ValidationError):
        ModelContextSupportObservation.model_validate({
            "fact_id": "test-fact",
            "support": True,
            "model_context_fingerprint": _TEST_FP,
            "supporting_handle_ids": [_HANDLE_CHUNK_0],
            "body": "full article body that should not be stored",  # type: ignore[call-overload]
        })


# ---------------------------------------------------------------------------
# Regression: synonymous paraphrase PASSES
# ---------------------------------------------------------------------------


def test_synonymous_paraphrase_passes() -> None:
    """Spec: "正确同义改写 PASS".

    The previous implementation rejected synonymous paraphrases because
    it required the exact hand-written sentence. The new contract
    accepts any alias in the alias group. Grounding is via
    ``model_context_support``, independent of phrasing.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="snowfall-amount",
            answer_alias_groups=[[
                "36 inches of snow",
                "降雪量达到36英寸",
                "36英寸的雪",
                "snowfall reached 36 inches",
            ]],
            source_aliases=["36 inches"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章指出降雪量达到36英寸，受影响最严重。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(fact_id="snowfall-amount", support=True),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is True


def test_paraphrase_outside_alias_group_fails() -> None:
    """Paraphrase that does not match any alias → FAIL (not mentioned)."""
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="snowfall-amount",
            answer_alias_groups=[[
                "36 inches of snow",
                "降雪量达到36英寸",
            ]],
            source_aliases=["36 inches"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到大约一米的降雪。",  # no alias match
        model_context_support=[
            _make_observation(fact_id="snowfall-amount", support=True),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert "not mentioned" in result.details


# ---------------------------------------------------------------------------
# Multiple alias groups (AND) vs aliases within group (OR)
# ---------------------------------------------------------------------------


def test_multiple_alias_groups_require_all_groups_hit() -> None:
    """Multiple alias groups = AND across groups."""
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="compound-buffalo-snow",
            answer_alias_groups=[
                ["Buffalo", "布法罗"],  # group 1: city
                ["36 inches", "36英寸"],  # group 2: amount
            ],
            source_aliases=["Buffalo", "36 inches"],
            required=True,
            severity="high",
        ),
    ])
    # Answer mentions both groups → PASS
    artifact = _make_artifact(
        final_text="布法罗降雪量36英寸。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(fact_id="compound-buffalo-snow", support=True),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is True

    # Answer mentions only one group → FAIL
    artifact_missing_amount = _make_artifact(
        final_text="布法罗受到暴风雪影响。",  # no amount
        model_context_support=[
            _make_observation(fact_id="compound-buffalo-snow", support=True),
        ],
    )
    result_missing = evaluate_context_support(case, artifact_missing_amount)
    assert result_missing.passed is False
    assert "not mentioned" in result_missing.details


def test_aliases_within_group_are_or() -> None:
    """Aliases within a single group = OR."""
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-toronto",
            answer_alias_groups=[["Toronto", "多伦多", "T.O."]],
            source_aliases=["Toronto"],
            required=True,
            severity="high",
        ),
    ])
    for alias in ["Toronto", "多伦多", "T.O."]:
        artifact = _make_artifact(
            final_text=f"文章提到了 {alias}。",
            cited_evidence_handles=[_HANDLE_CHUNK_0],
            model_context_support=[
                _make_observation(fact_id="city-toronto", support=True),
            ],
        )
        result = evaluate_context_support(case, artifact)
        assert result.passed is True, f"failed for alias {alias!r}"


# ---------------------------------------------------------------------------
# Non-required facts and metadata-only facts
# ---------------------------------------------------------------------------


def test_non_required_fact_absent_passes() -> None:
    """Spec: "required=False 缺失不导致失败"."""
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="optional-context",
            answer_alias_groups=[["snowstorm warning"]],
            source_aliases=["warning"],
            required=False,  # informational only
            severity="low",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到了降雪量。",  # no mention of "snowstorm warning"
        model_context_support=[
            _make_observation(fact_id="optional-context", support=True),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is True


def test_metadata_only_fact_passes() -> None:
    """Fact with no answer aliases and no source aliases → metadata-only."""
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="metadata-no-year",
            answer_alias_groups=[],  # no answer constraint
            source_aliases=[],       # no grounding constraint
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章未提及具体年份。",
        model_context_support=[],  # no observation needed — vacuously grounded
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is True


# ---------------------------------------------------------------------------
# Capability boundary signal
# ---------------------------------------------------------------------------


def test_case_with_no_atomic_facts_signals_coverage_incomplete() -> None:
    """Spec: "明确报告 deterministic evaluator 的能力边界"."""
    case = _make_case_with_atomic_facts([])  # no atomic facts
    artifact = _make_artifact(
        final_text="文章提到了一些城市。",
        model_context_support=[],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is True
    assert "coverage_incomplete=true" in result.details
    assert "case has no atomic_facts" in result.details


# ---------------------------------------------------------------------------
# Legacy required_article_facts migration
# ---------------------------------------------------------------------------


def test_legacy_required_article_facts_migration() -> None:
    """Legacy ``required_article_facts`` is auto-converted to ``atomic_facts``."""
    case = _make_case_with_legacy_facts(["Thunder Bay", "Toronto"])
    # Before migration: atomic_facts is empty
    assert case.expected.atomic_facts == []
    # Run the loader's migration
    _migrate_legacy_required_article_facts(case)
    # After migration: two atomic facts with single-alias groups
    assert len(case.expected.atomic_facts) == 2
    assert case.expected.atomic_facts[0].fact_id == "legacy-0"
    assert case.expected.atomic_facts[0].answer_alias_groups == [["Thunder Bay"]]
    assert case.expected.atomic_facts[0].required is True
    assert case.expected.atomic_facts[0].severity == "high"

    # The evaluator should now work on the migrated case.
    artifact = _make_artifact(
        final_text="文章提到的城市包括 Thunder Bay 和 Toronto。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(fact_id="legacy-0", support=True),
            _make_observation(fact_id="legacy-1", support=True),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is True


def test_legacy_required_article_facts_skipped_when_atomic_facts_present() -> None:
    """When both fields are present, ``atomic_facts`` wins (new contract)."""
    case = ReaderRecordAskR4A3Case(
        id="t-both",
        source_kind="synthetic_short",
        input_mode="manual",
        source_metadata="unknown",
        baseline_mode="complete",
        question="...",
        question_category="main_idea",
        expected=ReaderRecordAskR4A3Expected(
            required_article_facts=["legacy sentence"],
            atomic_facts=[
                AtomicExpectedFact(
                    fact_id="new-contract-fact",
                    answer_alias_groups=[["new alias"]],
                    source_aliases=["new source"],
                    required=True,
                    severity="high",
                )
            ],
        ),
    )
    _migrate_legacy_required_article_facts(case)
    # atomic_facts unchanged — legacy field ignored.
    assert len(case.expected.atomic_facts) == 1
    assert case.expected.atomic_facts[0].fact_id == "new-contract-fact"


# ---------------------------------------------------------------------------
# Severity ordering
# ---------------------------------------------------------------------------


def test_highest_severity_among_failing_facts() -> None:
    """When multiple facts fail, the dimension severity is the highest."""
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="low-severity-fact",
            answer_alias_groups=[["missing-alias-low"]],
            source_aliases=["x"],
            required=True,
            severity="low",
        ),
        AtomicExpectedFact(
            fact_id="high-severity-fact",
            answer_alias_groups=[["missing-alias-high"]],
            source_aliases=["y"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到了一些城市。",  # neither alias present
        model_context_support=[
            _make_observation(fact_id="low-severity-fact", support=True),
            _make_observation(fact_id="high-severity-fact", support=True),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert result.severity == "high"


# ---------------------------------------------------------------------------
# Cited handle in artifact's cited set → PASS
# ---------------------------------------------------------------------------


def test_supporting_handle_in_cited_set_passes() -> None:
    """When the observation's ``supporting_handle_ids`` intersect
    ``cited_evidence_handles``, the observation is authoritative →
    PASS (if support=True and fingerprint matches).
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(
                fact_id="city-thunder-bay",
                support=True,
                supporting_handles=[_HANDLE_CHUNK_0],
            ),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is True


def test_support_true_with_empty_supporting_handles_fails() -> None:
    """Spec (P0-2): ``support=True`` with empty ``supporting_handle_ids``
    is ``instrumentation_incomplete`` — fail-closed. The harness must
    record which chunk(s) contained the alias hit; an empty list means
    the harness could not determine support.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(
                fact_id="city-thunder-bay",
                support=True,
                supporting_handles=[],  # empty — fail-closed
            ),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert "support_true_with_empty_supporting_handles" in result.details


def test_supporting_handle_not_in_model_context_fails() -> None:
    """Spec (P0-2): an observation naming a handle that is NOT in
    ``RawArtifact.model_context_handle_ids`` is forged / stale —
    fail-closed (``supporting_handle_not_in_model_context``).

    Note: the cross-field validator on :class:`RawArtifact` already
    rejects this at load time for ``captured`` artifacts. This test
    constructs a VALID captured artifact first, then uses
    ``model_copy(update=...)`` (which does NOT re-run validators) to
    swap in an observation referencing an unknown handle — verifying
    the evaluator ALSO catches it as defense-in-depth.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    # Build a VALID captured artifact first (supporting_handle_ids IS
    # in model_context_handle_ids).
    valid_artifact = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_UNKNOWN],
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(
                fact_id="city-thunder-bay",
                support=True,
                supporting_handles=[_HANDLE_CHUNK_0],  # IS in context
            ),
        ],
    )
    # Swap in a bad observation whose supporting_handle_id is NOT in
    # model_context_handle_ids. ``model_copy(update=...)`` does NOT
    # re-run validators.
    bad_observation = ModelContextSupportObservation(
        fact_id="city-thunder-bay",
        support=True,
        model_context_fingerprint=_TEST_FP,
        supporting_handle_ids=[_HANDLE_UNKNOWN],  # NOT in context
    )
    artifact = valid_artifact.model_copy(
        update={"model_context_support": [bad_observation]}
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert "supporting_handle_not_in_model_context" in result.details


# ===========================================================================
# R4-A4-0 FINAL CLOSURE — 13 REQUIRED TESTS
# ===========================================================================
#
# These tests are mandated by the user spec for this rework round. They
# are written as standalone tests so they can be audited as a block
# during the delivery report. Each test name maps 1:1 to a numbered
# item in the spec's "必须新增的测试" list.
# ===========================================================================


# --- Required test 1 -------------------------------------------------------
# snapshot 含某 alias，但实际 model_context_chunks 因预算截断不含：
# support=False。


def test_required_1_snapshot_has_alias_but_chunks_truncated_support_false() -> None:
    """Required test 1: snapshot contains an alias, but the actual
    ``model_context_chunks`` were budget-truncated and do NOT contain
    the alias → ``support=False``.

    This is the core R4-A4-0 final closure regression: the previous
    implementation computed support against ``snapshot.units`` (all
    units, no budget) and would mark the alias as "supported" even
    though the model never saw it. The new contract computes support
    against the ACTUAL chunks the model saw.

    We simulate this by giving the artifact an observation with
    ``support=False`` and empty ``supporting_handle_ids`` — the
    harness shape when no chunk contained the alias.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="late-fact-atlantis",
            answer_alias_groups=[["Atlantis"]],
            source_aliases=["Atlantis"],
            required=True,
            severity="high",
        ),
    ])
    # The artifact's resolved_evidence (snapshot) contains "Atlantis",
    # but the harness computed support=False against the actual
    # model_context_chunks (truncated before the alias). The evaluator
    # MUST trust the observation, not the snippet.
    artifact = _make_artifact(
        final_text="文章提到了 Atlantis。",
        resolved_snippets=["... Atlantis appears here in the snapshot ..."],
        model_context_fingerprint=_TEST_FP,
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(
                fact_id="late-fact-atlantis",
                support=False,
                supporting_handles=[],
            ),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert "fact_not_supported" in result.details
    assert "late-fact-atlantis" in result.details


# --- Required test 2 -------------------------------------------------------
# alias 位于第二个 chunk：supporting_handle_ids 只能包含第二个 chunk handle。


def test_required_2_alias_in_second_chunk_only_second_handle_recorded() -> None:
    """Required test 2: the alias appears only in the SECOND chunk.
    ``supporting_handle_ids`` MUST contain only the second chunk's
    handle (not the first chunk's handle).

    This is the regression for the previous ``cited_handles[0]``
    mis-binding bug — every fact was bound to the first handle
    regardless of which chunk actually contained the alias.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-toronto",
            answer_alias_groups=[["Toronto"]],
            source_aliases=["Toronto"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到了 Toronto。",
        cited_evidence_handles=[_HANDLE_CHUNK_1],
        model_context_handle_ids=[_HANDLE_CHUNK_0, _HANDLE_CHUNK_1],
        model_context_support=[
            _make_observation(
                fact_id="city-toronto",
                support=True,
                # ONLY the second chunk's handle — this is what the
                # harness records when only chunk 1 contained the alias.
                supporting_handles=[_HANDLE_CHUNK_1],
            ),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is True


# --- Required test 3 -------------------------------------------------------
# 回答只 cite 第一个 chunk，但事实只在第二个 chunk：context_support fail。


def test_required_3_cite_first_chunk_but_fact_in_second_chunk_fails() -> None:
    """Required test 3: the answer cites only the FIRST chunk's handle,
    but the fact is supported only by the SECOND chunk → FAIL
    (``fact_not_cited``).

    The previous contract bound every fact to ``cited_handles[0]`` so
    this would have passed silently. The new contract requires at
    least one of ``supporting_handle_ids`` to appear in
    ``cited_evidence_handles`` — citing chunk 0 when the fact lives in
    chunk 1 fails.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-toronto",
            answer_alias_groups=[["Toronto"]],
            source_aliases=["Toronto"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到了 Toronto。",
        # Model cited only chunk 0's handle.
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_handle_ids=[_HANDLE_CHUNK_0, _HANDLE_CHUNK_1],
        model_context_support=[
            _make_observation(
                fact_id="city-toronto",
                support=True,
                # Fact is supported only by chunk 1.
                supporting_handles=[_HANDLE_CHUNK_1],
            ),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert "fact_not_cited" in result.details


# --- Required test 4 -------------------------------------------------------
# 回答 cite 正确第二个 handle：pass。


def test_required_4_cite_correct_second_handle_passes() -> None:
    """Required test 4: the answer cites the SECOND chunk's handle,
    and the fact is supported by the second chunk → PASS.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-toronto",
            answer_alias_groups=[["Toronto"]],
            source_aliases=["Toronto"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到了 Toronto。",
        cited_evidence_handles=[_HANDLE_CHUNK_1],
        model_context_handle_ids=[_HANDLE_CHUNK_0, _HANDLE_CHUNK_1],
        model_context_support=[
            _make_observation(
                fact_id="city-toronto",
                support=True,
                supporting_handles=[_HANDLE_CHUNK_1],
            ),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is True


# --- Required test 5 -------------------------------------------------------
# 一个事实被两个 chunks 支持：supporting handles 去重保序，任一正确引用可通过。


def test_required_5_fact_supported_by_two_chunks_dedup_any_citation_passes() -> None:
    """Required test 5: a single fact is supported by TWO chunks.
    ``supporting_handle_ids`` contains both chunk handles (de-duplicated,
    order-preserving). Citing EITHER handle passes.

    Sub-case A: cite chunk 0 → PASS.
    Sub-case B: cite chunk 1 → PASS.
    Sub-case C: cite both → PASS.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-toronto",
            answer_alias_groups=[["Toronto"]],
            source_aliases=["Toronto"],
            required=True,
            severity="high",
        ),
    ])

    # Sub-case A: cite chunk 0
    artifact_a = _make_artifact(
        final_text="文章提到了 Toronto。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_handle_ids=[_HANDLE_CHUNK_0, _HANDLE_CHUNK_1],
        model_context_support=[
            _make_observation(
                fact_id="city-toronto",
                support=True,
                supporting_handles=[_HANDLE_CHUNK_0, _HANDLE_CHUNK_1],
            ),
        ],
    )
    result_a = evaluate_context_support(case, artifact_a)
    assert result_a.passed is True, "citing chunk 0 should pass"

    # Sub-case B: cite chunk 1
    artifact_b = _make_artifact(
        final_text="文章提到了 Toronto。",
        cited_evidence_handles=[_HANDLE_CHUNK_1],
        model_context_handle_ids=[_HANDLE_CHUNK_0, _HANDLE_CHUNK_1],
        model_context_support=[
            _make_observation(
                fact_id="city-toronto",
                support=True,
                supporting_handles=[_HANDLE_CHUNK_0, _HANDLE_CHUNK_1],
            ),
        ],
    )
    result_b = evaluate_context_support(case, artifact_b)
    assert result_b.passed is True, "citing chunk 1 should pass"

    # Sub-case C: cite both
    artifact_c = _make_artifact(
        final_text="文章提到了 Toronto。",
        cited_evidence_handles=[_HANDLE_CHUNK_0, _HANDLE_CHUNK_1],
        model_context_handle_ids=[_HANDLE_CHUNK_0, _HANDLE_CHUNK_1],
        model_context_support=[
            _make_observation(
                fact_id="city-toronto",
                support=True,
                supporting_handles=[_HANDLE_CHUNK_0, _HANDLE_CHUNK_1],
            ),
        ],
    )
    result_c = evaluate_context_support(case, artifact_c)
    assert result_c.passed is True, "citing both should pass"


# --- Required test 6 -------------------------------------------------------
# cited handles 为空但 observation support=True：不得通过。


def test_required_6_empty_cited_handles_with_support_true_fails() -> None:
    """Required test 6: ``cited_evidence_handles=[]`` but observation
    has ``support=True`` with valid supporting handles → FAIL
    (``fact_not_cited``).

    The model saw the fact in the baseline but did not cite any
    supporting chunk. The fact is ungrounded in the answer's citation
    set → FAIL.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[],  # empty
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(
                fact_id="city-thunder-bay",
                support=True,
                supporting_handles=[_HANDLE_CHUNK_0],
            ),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert "fact_not_cited" in result.details


# --- Required test 7 -------------------------------------------------------
# runtime exception：observation/fingerprint 为空。


def test_required_7_runtime_exception_empty_observation_and_fingerprint() -> None:
    """Required test 7: when ``run_reading_record_ask`` raises, the
    harness MUST NOT reconstruct model context from
    ``document_access.snapshot``. The artifact carries:
    - ``model_context_support=[]``
    - ``model_context_fingerprint=None``
    - ``model_context_handle_ids=[]``
    - ``model_context_instrumentation_version="reader_record_ask_model_context_v1"``
    - ``model_context_capture_status="failed"``

    R4-A4-0 final gate closure (P0-1): the explicit
    ``capture_status="failed"`` lifecycle marker distinguishes this
    new runtime-exception artifact from a legacy artifact (which has
    ``version=None, status=None``). The previous heuristic
    (``fingerprint=None + observations=[]`` → legacy OR exception)
    could NOT distinguish the two; the new contract uses the explicit
    lifecycle fields as the SINGLE source of truth.

    The evaluator classifies this as ``runtime_exception`` (an
    instrumentation_incomplete blocker) — NOT a model correctness
    failure, NOT rework-eligible.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    # Exception-path artifact: no fingerprint, no handle_ids, no
    # observations — BUT explicitly marked as a NEW artifact with
    # ``capture_status="failed"`` (NOT legacy). The cross-field
    # validator on RawArtifact enforces that ``failed`` requires
    # fingerprint=None, handle_ids=[], observations=[].
    artifact = _make_artifact(
        final_text=None,  # exception path: no final_text
        model_context_fingerprint=None,
        model_context_handle_ids=[],
        model_context_support=[],
        instrumentation_version="reader_record_ask_model_context_v1",
        capture_status="failed",
    )
    # Override finalized_status to mimic the exception path.
    artifact = artifact.model_copy(
        update={
            "finalized_status": "unavailable",
            "finalized_reason": "runtime_exception",
            "error": "RuntimeError",
        }
    )
    result = evaluate_context_support(case, artifact)
    # P0-1: explicit ``capture_status="failed"`` → classified as
    # ``runtime_exception`` (an instrumentation_incomplete blocker),
    # NOT as legacy (which would return coverage_incomplete with
    # ``legacy_artifact_no_model_context_support``).
    assert result.passed is False
    assert result.classification == "runtime_exception"
    assert "runtime_exception" in result.details
    assert "instrumentation_incomplete" in result.details
    assert "cannot authoritatively evaluate" in result.details
    # Critical: MUST NOT be classified as legacy.
    assert "legacy_artifact_no_model_context_support" not in result.details
    # Importantly: NO support observation was reconstructed from snapshot.
    assert "fact_not_supported" not in result.details
    assert "fact_not_cited" not in result.details


# --- Required test 8 -------------------------------------------------------
# duplicate/unknown fact observations：fail-closed。


def test_required_8a_duplicate_fact_id_observations_fail_closed() -> None:
    """Required test 8a: duplicate ``fact_id`` in observations →
    fail-closed (``duplicate_fact_id_in_observations``).
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(fact_id="city-thunder-bay", support=True),
            _make_observation(fact_id="city-thunder-bay", support=True),  # duplicate
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert "duplicate_fact_id_in_observations" in result.details
    assert "city-thunder-bay" in result.details


def test_required_8b_unknown_fact_id_observation_fail_closed() -> None:
    """Required test 8b: an observation whose ``fact_id`` is NOT in
    ``case.expected.atomic_facts`` → fail-closed
    (``observation_fact_id_not_in_case_atomic_facts``).
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(fact_id="city-thunder-bay", support=True),
            # Observation for a fact_id NOT in the case.
            _make_observation(fact_id="city-ghost", support=True),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert "observation_fact_id_not_in_case_atomic_facts" in result.details
    assert "city-ghost" in result.details


# --- Required test 9 -------------------------------------------------------
# fingerprint mismatch 必须穿过真实 evaluate_case/evaluate_artifact 入口触发。


def test_required_9_fingerprint_mismatch_through_real_evaluate_artifact_entry() -> None:
    """Required test 9: the fingerprint mismatch MUST fire through the
    real :func:`evaluate_artifact` entrypoint (which calls all 11
    evaluators in canonical order), not just
    :func:`evaluate_context_support` directly.

    This guards against the previous bug where
    ``expected_baseline_fingerprint`` was an optional parameter that
    ``evaluate_artifact`` never passed — making the fingerprint check
    dead code at the real entrypoint.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_fingerprint=_TEST_FP,
        model_context_support=[
            _make_observation(
                fact_id="city-thunder-bay",
                support=True,
                fingerprint=_OTHER_FP,  # mismatch
                supporting_handles=[_HANDLE_CHUNK_0],
            ),
        ],
    )
    # Use the real evaluate_artifact entrypoint — no fingerprint
    # parameter is passed by the caller. The evaluator must detect the
    # mismatch from the artifact's own fields.
    dimensions = evaluate_artifact(case, artifact)
    context_support_dim = next(
        d for d in dimensions if d.dimension == "context_support"
    )
    assert context_support_dim.passed is False
    assert "fingerprint_mismatch" in context_support_dim.details


# --- Required test 10 ------------------------------------------------------
# medium/long baseline 超过 16 units：evaluator 不能使用第 17 unit 的文字。


def test_required_10_evaluator_cannot_use_17th_unit_text() -> None:
    """Required test 10: when the medium/long baseline assembler
    truncates the article to 16 chunks (``MAX_BASELINE_CONTEXT_CHUNKS``),
    the evaluator MUST NOT use text from the 17th unit.

    The evaluator is data-only — it cannot read article text at all.
    It only sees what the harness recorded in
    ``model_context_handle_ids`` and ``model_context_support``. If the
    harness correctly excluded the 17th unit, the 17th unit's handle
    is NOT in ``model_context_handle_ids``, and any observation naming
    it would be rejected as ``supporting_handle_not_in_model_context``.

    This test simulates a buggy harness that recorded a supporting
    handle for the (non-existent) 17th chunk — the evaluator must
    reject it.

    Note: the cross-field validator on :class:`RawArtifact` already
    rejects this at load time for ``captured`` artifacts. This test
    constructs a VALID captured artifact first, then uses
    ``model_copy(update=...)`` (which does NOT re-run validators) to
    swap in an observation referencing the 17th handle — verifying
    the evaluator ALSO catches it as defense-in-depth.
    """
    # Build a model_context_handle_ids list with 16 chunk handles
    # (the cap). The 17th handle is NOT in the list.
    sixteen_handles = [f"evh_{i:032x}" for i in range(16)]
    handle_17 = "evh_" + "10" * 16  # 17th chunk handle, NOT in model context
    # Use one of the 16 valid handles for the initial valid artifact.
    valid_handle = sixteen_handles[0]

    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="fact-in-17th-unit",
            answer_alias_groups=[["seventeenth"]],
            source_aliases=["seventeenth"],
            required=True,
            severity="high",
        ),
    ])
    # Build a VALID captured artifact first (supporting_handle_ids IS
    # in model_context_handle_ids).
    valid_artifact = _make_artifact(
        final_text="文章提到了 seventeenth。",
        cited_evidence_handles=[valid_handle],
        model_context_handle_ids=sixteen_handles,  # 16 handles only
        model_context_support=[
            _make_observation(
                fact_id="fact-in-17th-unit",
                support=True,
                supporting_handles=[valid_handle],  # IS in context
            ),
        ],
    )
    # Swap in a buggy observation whose supporting_handle_id is the
    # 17th handle (NOT in model_context_handle_ids).
    bad_observation = ModelContextSupportObservation(
        fact_id="fact-in-17th-unit",
        support=True,
        model_context_fingerprint=_TEST_FP,
        supporting_handle_ids=[handle_17],  # NOT in context
    )
    artifact = valid_artifact.model_copy(
        update={"model_context_support": [bad_observation]}
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert "supporting_handle_not_in_model_context" in result.details


# --- Required test 11 ------------------------------------------------------
# StrictBool coercion 拒绝。


def test_required_11a_strictbool_rejects_string_false_true() -> None:
    """Required test 11a: ``StrictBool`` rejects ``"false"`` and ``"true"``."""
    with pytest.raises(ValidationError):
        AtomicExpectedFact(
            fact_id="f1",
            required="false",  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        AtomicExpectedFact(
            fact_id="f2",
            required="true",  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        ReaderRecordAskR4A3Expected(
            requires_exhaustive_entity_recall="false",  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        ReaderRecordAskR4A3Expected(
            requires_exhaustive_entity_recall="true",  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        ReaderRecordAskR4A3Expected(
            allow_subquestions="false",  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        ReaderRecordAskR4A3Expected(
            allow_subquestions="true",  # type: ignore[arg-type]
        )


def test_required_11b_strictbool_rejects_int_zero_one() -> None:
    """Required test 11b: ``StrictBool`` rejects ``0`` and ``1``."""
    with pytest.raises(ValidationError):
        AtomicExpectedFact(fact_id="f1", required=0)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        AtomicExpectedFact(fact_id="f2", required=1)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ReaderRecordAskR4A3Expected(
            requires_exhaustive_entity_recall=0,  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        ReaderRecordAskR4A3Expected(
            requires_exhaustive_entity_recall=1,  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        ReaderRecordAskR4A3Expected(allow_subquestions=0)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ReaderRecordAskR4A3Expected(allow_subquestions=1)  # type: ignore[arg-type]


def test_required_11c_strictbool_rejects_float_zero_one() -> None:
    """Required test 11c: ``StrictBool`` rejects ``0.0`` and ``1.0``."""
    with pytest.raises(ValidationError):
        AtomicExpectedFact(fact_id="f1", required=0.0)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        AtomicExpectedFact(fact_id="f2", required=1.0)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ReaderRecordAskR4A3Expected(
            requires_exhaustive_entity_recall=0.0,  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        ReaderRecordAskR4A3Expected(
            requires_exhaustive_entity_recall=1.0,  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        ReaderRecordAskR4A3Expected(allow_subquestions=0.0)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ReaderRecordAskR4A3Expected(allow_subquestions=1.0)  # type: ignore[arg-type]


# --- Required test 12 ------------------------------------------------------
# 两个配置同时命中 Flash phase：不得静默选择第一项。


def test_required_12_two_configs_match_flash_phase_ambiguous_fail_closed() -> None:
    """Required test 12: when two per_config keys match the same
    canonical phase regex (e.g. ``deepseek-v4-flash|thinking=False``
    and ``deepseek-chat|thinking=False`` both match the
    ``Flash non-thinking`` phase), the report MUST NOT silently pick
    the first match. It MUST surface an ``AMBIGUOUS`` row
    (fail-closed).

    This guards against dict insertion order accidentally determining
    which model's metrics get rendered.
    """
    # Build an AggregatedReport with two per_config keys that both
    # match the Flash non-thinking phase regex ``(?:flash|chat)``.
    case = _make_case_with_atomic_facts(
        [
            AtomicExpectedFact(
                fact_id="city-thunder-bay",
                answer_alias_groups=[["Thunder Bay"]],
                source_aliases=["Thunder Bay"],
                required=True,
                severity="high",
            )
        ],
        case_id="t-ambiguity",
    )
    dataset = ReaderRecordAskR4A3Dataset(
        id="reader-record-ask-r4-a3",
        schema_version="r4-a3-dataset-v1",
        description="R4-A3 ambiguity test dataset",
        case_globs=["cases/*.json"],
        tags=["r4-a3", "test"],
        cases=[case],
    )

    # Two artifacts, two different per_config keys, BOTH match the
    # Flash non-thinking phase regex. R4-A4-0 final gate closure
    # (P0-1): explicit lifecycle fields mark these as captured
    # artifacts (NOT legacy) so the context_support dimension actually
    # evaluates them.
    artifact_flash = RawArtifact(
        case_id="t-ambiguity",
        run_id="run-flash",
        run_index=0,
        thinking_enabled=False,
        model_short_name="deepseek-v4-flash",
        finalized_status="ok",
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_fingerprint=_TEST_FP,
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(fact_id="city-thunder-bay", support=True),
        ],
        model_context_instrumentation_version="reader_record_ask_model_context_v1",
        model_context_capture_status="captured",
        agent_usage=RawUsage(),
    )
    artifact_chat = RawArtifact(
        case_id="t-ambiguity",
        run_id="run-chat",
        run_index=0,
        thinking_enabled=False,
        model_short_name="deepseek-chat",
        finalized_status="ok",
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_fingerprint=_TEST_FP,
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(fact_id="city-thunder-bay", support=True),
        ],
        model_context_instrumentation_version="reader_record_ask_model_context_v1",
        model_context_capture_status="captured",
        agent_usage=RawUsage(),
    )

    # Run the real 11-evaluator suite on both artifacts, then wrap
    # each (case, artifact, dims) triple into a CaseEvalResult so the
    # aggregator can consume it. Passing tuples directly would raise
    # AttributeError — the aggregator expects CaseEvalResult objects.
    from claread_eval.reader_record_ask.evaluators.aggregator import (
        CaseEvalResult,
    )

    def _to_case_result(
        art: RawArtifact,
    ) -> CaseEvalResult:
        dims = evaluate_artifact(case, art)
        usage = art.agent_usage
        if usage is not None:
            in_tok = usage.input_tokens or 0
            out_tok = usage.output_tokens or 0
            total_tokens: int | None = in_tok + out_tok
            total_requests: int | None = usage.requests
        else:
            total_tokens = None
            total_requests = None
        return CaseEvalResult(
            case_id=art.case_id,
            run_id=art.run_id,
            run_index=art.run_index,
            model_short_name=art.model_short_name,
            thinking_enabled=art.thinking_enabled,
            dimensions=dims,
            latency_seconds=art.latency_seconds,
            total_tokens=total_tokens,
            total_requests=total_requests,
        )

    case_results = [
        _to_case_result(artifact_flash),
        _to_case_result(artifact_chat),
    ]
    aggregated: AggregatedReport = aggregate_results(case_results, {case.id: case})

    # Sanity: both per_config keys must be present and both must match
    # the Flash non-thinking phase regex ``(?:flash|chat)`` with
    # ``thinking=False``. If this sanity check fails, the test setup
    # itself is wrong (not the report).
    assert "deepseek-v4-flash|thinking=False" in aggregated.per_config
    assert "deepseek-chat|thinking=False" in aggregated.per_config

    # Generate the report — the Flash non-thinking phase row MUST be
    # AMBIGUOUS because two per_config keys matched the same phase.
    report = generate_r4_a3_report(
        aggregated=aggregated,
        dataset=dataset,
        artifacts=[artifact_flash, artifact_chat],
        start_head="abc1234",
        end_head="abc1234",
        parallel_dirty=[],
        harness_choice="real-llm-harness",
        rejected_harness="mock-harness",
        rejected_reason="not authoritative",
        real_model_blocked=False,
        real_model_block_reason=None,
        real_model_user_commands=None,
        deterministic_tests_passed=True,
        deterministic_tests_summary="13 required tests pass",
        verdict="accepted",
        allow_r4_a4=True,
        allow_r4_b1=False,
        modified_files=[
            "evals/tests/test_reader_record_ask_eval_context_support.py"
        ],
        task_label="R4-A4-0 final closure",
    )

    # The report MUST surface an AMBIGUOUS marker for the Flash
    # non-thinking phase. The exact wording is "AMBIGUOUS" + the match
    # count + the matched keys.
    assert "AMBIGUOUS" in report, (
        "expected AMBIGUOUS marker in report when two configs match the "
        "same canonical phase; report must fail-closed instead of silently "
        "picking the first match\n--- report excerpt ---\n"
        + report[report.find("## 9."):report.find("## 10.")]
    )


# --- Required test 13 ------------------------------------------------------
# 旧 artifact 缺新字段：indeterminate，不误报模型失败。


def test_required_13_legacy_artifact_indeterminate_not_model_failure() -> None:
    """Required test 13: an old artifact predating R4-A4-0 final
    closure (no ``model_context_fingerprint``, no
    ``model_context_support``, no ``model_context_handle_ids``) MUST
    be classified as ``indeterminate_requires_new_artifact`` (via the
    ``legacy_artifact_no_model_context_support`` coverage_incomplete
    signal), NOT as a model failure.

    The dimension returns ``passed=True`` with a coverage_incomplete
    signal — this is NOT a pass; it is an explicit "cannot
    authoritatively evaluate" verdict. The historical replay tool
    surfaces this as ``indeterminate_requires_new_artifact``.

    Critically: the dimension MUST NOT report ``fact_not_supported``
    or any other model-failure reason for legacy artifacts.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    # Legacy artifact: no fingerprint, no handle_ids, no observations.
    # The artifact DOES have final_text and resolved_evidence (legacy
    # snippet-based shape) — but the evaluator must NOT use them.
    artifact = RawArtifact(
        case_id="t-context-support",
        run_id="run-legacy",
        run_index=0,
        thinking_enabled=False,
        model_short_name="deepseek-chat",
        finalized_status="ok",
        final_text="文章提到了 Thunder Bay。",
        resolved_evidence=[
            RawEvidenceObservation(
                handle_id=_HANDLE_CHUNK_0,
                kind="article_seed",
                snippet="Thunder Bay appears in the legacy snippet.",
                provenance="baseline_context",
            )
        ],
        # Legacy: no new fields populated.
        model_context_fingerprint=None,
        model_context_handle_ids=[],
        model_context_support=[],
    )
    result = evaluate_context_support(case, artifact)

    # Coverage incomplete (legacy) — NOT a failure.
    assert result.passed is True  # soft signal
    assert "legacy_artifact_no_model_context_support" in result.details

    # Critical: MUST NOT report a model failure for legacy artifacts.
    assert "fact_not_supported" not in result.details
    assert "fact_not_cited" not in result.details
    assert "fingerprint_mismatch" not in result.details
    assert "instrumentation_incomplete" not in result.details.replace(
        "legacy_artifact_no_model_context_support", ""
    )


# ---------------------------------------------------------------------------
# Exception-path instrumentation_incomplete for new-style artifacts
# ---------------------------------------------------------------------------


def test_new_artifact_xor_fingerprint_observations_instrumentation_incomplete() -> None:
    """Spec (P0-4): a NEW-style artifact (not legacy) that has
    fingerprint XOR observations (one without the other) is
    ``instrumentation_incomplete`` — fail-closed.

    Sub-case A: has fingerprint, no observations (but observations
    expected because case has atomic_facts with source_aliases) →
    fail-closed via ``no_observation_for_required_fact`` per fact
    (covered by
    test_missing_observation_for_required_fact_is_instrumentation_incomplete).

    Sub-case B: has observations, no fingerprint → fail-closed via
    ``artifact_missing_model_context_fingerprint`` per fact.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    # Sub-case B: observations present, fingerprint missing.
    artifact = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_fingerprint=None,  # missing
        model_context_support=[
            _make_observation(fact_id="city-thunder-bay", support=True),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert "artifact_missing_model_context_fingerprint" in result.details


def test_new_artifact_no_handle_ids_instrumentation_incomplete() -> None:
    """Spec (P0-4): a new-style artifact with fingerprint +
    observations but empty ``model_context_handle_ids`` is
    ``instrumentation_incomplete`` — the evaluator cannot verify
    supporting_handle_ids membership.

    Note: the cross-field validator on :class:`RawArtifact` already
    rejects this at load time for ``captured`` artifacts (captured
    requires non-empty ``model_context_handle_ids``). This test
    constructs a VALID captured artifact first, then uses
    ``model_copy(update=...)`` (which does NOT re-run validators) to
    clear ``model_context_handle_ids`` — verifying the evaluator ALSO
    catches it as defense-in-depth.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    # Build a VALID captured artifact first (model_context_handle_ids
    # is non-empty).
    valid_artifact = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_fingerprint=_TEST_FP,
        model_context_support=[
            _make_observation(fact_id="city-thunder-bay", support=True),
        ],
    )
    # Clear model_context_handle_ids via model_copy (does NOT re-run
    # validators). The evaluator must catch this as
    # instrumentation_incomplete.
    artifact = valid_artifact.model_copy(
        update={"model_context_handle_ids": []}
    )
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert "instrumentation_incomplete" in result.details
    assert "cannot authoritatively evaluate" in result.details


# ---------------------------------------------------------------------------
# Sanity: EvalDimensionResult type is imported for type hints only
# ---------------------------------------------------------------------------


def test_dimension_result_is_context_support() -> None:
    """The evaluator always returns ``dimension="context_support"``."""
    case = _make_case_with_atomic_facts([])
    artifact = _make_artifact(final_text="文章提到了一些城市。")
    result = evaluate_context_support(case, artifact)
    assert isinstance(result, EvalDimensionResult)
    assert result.dimension == "context_support"


# ---------------------------------------------------------------------------
# Sanity: evaluate_artifact canonical list still includes context_support
# ---------------------------------------------------------------------------


def test_evaluate_artifact_returns_context_support_dimension() -> None:
    """The real :func:`evaluate_artifact` entrypoint includes
    ``context_support`` in its canonical 11-dimension list.
    """
    case = _make_case_with_atomic_facts([])
    artifact = _make_artifact(final_text="文章提到了一些城市。")
    dimensions = evaluate_artifact(case, artifact)
    dim_names = [d.dimension for d in dimensions]
    assert "context_support" in dim_names


# ===========================================================================
# R4-A4-0 final gate closure — 13 required end-to-end tests
#
# Spec (current round): the 13 scenarios mandated for P0-1/P0-2/P0-3
# closure. Each test exercises the FULL seam:
#
#   RawArtifact → evaluate_artifact → aggregate_results →
#   AggregateReadinessAudit → _decide_final_verdict
#
# These tests are written as standalone functions at the bottom of the
# file so they can be audited as a block during the delivery report.
# They do NOT replace the existing ``test_required_*`` tests from the
# previous round — those tested the per-fact classification logic
# directly. These new tests verify the end-to-end verdict precedence.
# ===========================================================================


# ---------------------------------------------------------------------------
# Module-level setup: load the runner script (hosts _decide_final_verdict +
# AggregateReadinessAudit + INSTRUMENTATION_INCOMPLETE_REASONS +
# LEGACY_BLOCKER_REASONS) and the harness test module (hosts
# _compute_model_context_support). Both are loaded via importlib so the
# test file does not depend on packaging layout.
# ---------------------------------------------------------------------------

import importlib.util as _importlib_util  # noqa: E402
import sys as _sys  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

_REPO_ROOT_FOR_RUNNER = _Path(__file__).resolve().parents[2]
_RUNNER_PATH = (
    _REPO_ROOT_FOR_RUNNER
    / "evals"
    / "scripts"
    / "run_reader_record_ask_r4_a3.py"
)
_HARNESS_PATH = (
    _REPO_ROOT_FOR_RUNNER
    / "services"
    / "api"
    / "tests"
    / "test_reader_record_ask_real_llm_eval.py"
)


def _load_runner_module():
    """Load the runner script as a module (it's not in a package)."""
    spec = _importlib_util.spec_from_file_location(
        "run_reader_record_ask_r4_a3", _RUNNER_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = _importlib_util.module_from_spec(spec)
    _sys.modules["run_reader_record_ask_r4_a3"] = module
    spec.loader.exec_module(module)
    return module


def _load_harness_module():
    """Load the harness test module (hosts ``_compute_model_context_support``).

    The harness module imports from ``app.services.*`` and
    ``app.llm.*`` — those imports require the ``services/api``
    environment (with ``pydantic_ai`` etc. installed). We add that
    directory to ``sys.path`` before loading so the transitive imports
    resolve. If the required third-party dependencies (e.g.
    ``pydantic_ai``) are NOT installed (e.g. when running from the
    ``evals`` environment), this function returns ``None`` and spec
    test 12 is skipped — the contract is still verified indirectly
    via spec test 3 (which constructs an ``unavailable`` artifact
    directly and verifies no empty SHA is produced).
    """
    services_api_dir = _REPO_ROOT_FOR_RUNNER / "services" / "api"
    if str(services_api_dir) not in _sys.path:
        _sys.path.insert(0, str(services_api_dir))
    try:
        import pydantic_ai  # noqa: F401 — transitive dep of app.llm.*
    except ImportError:
        return None
    spec = _importlib_util.spec_from_file_location(
        "test_reader_record_ask_real_llm_eval", _HARNESS_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = _importlib_util.module_from_spec(spec)
    _sys.modules["test_reader_record_ask_real_llm_eval"] = module
    spec.loader.exec_module(module)
    return module


_RUNNER = _load_runner_module()
_HARNESS = _load_harness_module()


# ---------------------------------------------------------------------------
# Shared helpers for the 13 end-to-end tests
# ---------------------------------------------------------------------------

from claread_eval.reader_record_ask.evaluators.aggregator import (  # noqa: E402
    CaseEvalResult as _CaseEvalResult,
)
from claread_eval.reader_record_ask.run_manifest import (  # noqa: E402
    CoverageAuditResult as _CoverageAuditResult,
)


def _make_coverage_audit_completed() -> _CoverageAuditResult:
    """Build a clean completed-manifest coverage audit for verdict tests."""
    return _CoverageAuditResult(
        manifest_present=True,
        manifest_status="completed",
        planned_count=1,
        completed_count=1,
        missing_count=0,
        duplicate_count=0,
        unexpected_count=0,
        identity_mismatch_count=0,
        evaluable_artifact_count=1,
        dataset_identity=("ds", "v1", "sha"),
        missing_run_indices={},
        duplicate_run_indices={},
        unexpected_run_indices={},
        manifest_state="valid",
        manifest_run_id_matches=True,
    )


def _build_case_result(
    case: ReaderRecordAskR4A3Case,
    artifact: RawArtifact,
) -> _CaseEvalResult:
    """Run the full 11-evaluator suite on ``artifact`` and wrap into a
    :class:`CaseEvalResult` ready for aggregation / verdict.
    """
    dims = evaluate_artifact(case, artifact)
    usage = artifact.agent_usage
    if usage is not None:
        in_tok = usage.input_tokens or 0
        out_tok = usage.output_tokens or 0
        total_tokens: int | None = in_tok + out_tok
        total_requests: int | None = usage.requests
    else:
        total_tokens = None
        total_requests = None
    return _CaseEvalResult(
        case_id=artifact.case_id,
        run_id=artifact.run_id,
        run_index=artifact.run_index,
        model_short_name=artifact.model_short_name,
        thinking_enabled=artifact.thinking_enabled,
        dimensions=dims,
        latency_seconds=artifact.latency_seconds,
        total_tokens=total_tokens,
        total_requests=total_requests,
    )


def _build_readiness_from_results(
    case_results: list[_CaseEvalResult],
    *,
    planned_count: int = 1,
) -> _RUNNER.AggregateReadinessAudit:
    """Build an :class:`AggregateReadinessAudit` from case_results.

    Computes ``instrumentation_incomplete_count`` and
    ``legacy_artifact_count`` from the ``context_support`` dimension's
    typed ``classification`` field — mirroring the runner's
    :func:`aggregate` logic.
    """
    instrumentation_incomplete_count = sum(
        1
        for cr in case_results
        for d in cr.dimensions
        if d.dimension == "context_support"
        and d.classification
        in _RUNNER.INSTRUMENTATION_INCOMPLETE_REASONS
    )
    legacy_artifact_count = sum(
        1
        for cr in case_results
        for d in cr.dimensions
        if d.dimension == "context_support"
        and d.classification in _RUNNER.LEGACY_BLOCKER_REASONS
    )
    return _RUNNER.AggregateReadinessAudit(
        artifact_load_clean=True,
        discovered_file_count=len(case_results),
        invalid_artifact_count=0,
        manifest_state="valid",
        manifest_present=True,
        manifest_run_id_matches=True,
        manifest_status="completed",
        manifest_is_complete=True,
        coverage_counts_clean=True,
        planned_count=planned_count,
        evaluable_artifact_count=len(case_results),
        unknown_planned_case_count=0,
        unknown_artifact_case_count=0,
        evaluated_case_result_count=len(case_results),
        instrumentation_incomplete_count=instrumentation_incomplete_count,
        legacy_artifact_count=legacy_artifact_count,
    )


def _decide_verdict(
    case_results: list[_CaseEvalResult],
) -> tuple[str, bool, bool]:
    """Run the full verdict seam: build readiness + coverage audit,
    then call :func:`_decide_final_verdict`.
    """
    readiness = _build_readiness_from_results(case_results)
    coverage_audit = _make_coverage_audit_completed()
    return _RUNNER._decide_final_verdict(
        case_results=case_results,
        coverage_audit=coverage_audit,
        identity_mismatched_count=0,
        real_model_blocked=False,
        has_budget_exhausted=False,
        total_artifacts_loaded=len(case_results),
        readiness=readiness,
    )


# ---------------------------------------------------------------------------
# Spec test 1: legacy artifact → replay indeterminate; authoritative
# aggregate → blocked incomplete.
# ---------------------------------------------------------------------------


def test_r4_a4_0_final_gate_01_legacy_artifact_blocked_in_aggregate() -> None:
    """Spec test 1: a legacy artifact (``version=None, status=None``)
    MUST be classified as ``legacy_artifact`` by the evaluator, and
    the authoritative aggregate MUST block it
    (``blocked_incomplete_real_model_run``) — NOT accept it even when
    dataset identity matches. The historical replay tool classifies
    it as ``indeterminate_requires_new_artifact``.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    # Legacy: no lifecycle fields, no fingerprint, no handle_ids, no
    # observations. The cross-field validator allows this (both None).
    artifact = RawArtifact(
        case_id="t-legacy",
        run_id="run-legacy",
        run_index=0,
        thinking_enabled=False,
        model_short_name="deepseek-chat",
        finalized_status="ok",
        final_text="文章提到了 Thunder Bay。",
        model_context_fingerprint=None,
        model_context_handle_ids=[],
        model_context_support=[],
        # lifecycle fields default to None → legacy
    )
    # Evaluator level: legacy → passed=True with coverage_incomplete
    # (NOT a model failure), classification=legacy_artifact.
    result = evaluate_context_support(case, artifact)
    assert result.passed is True  # soft signal
    assert result.classification == "legacy_artifact"
    assert "legacy_artifact_no_model_context_support" in result.details
    assert "fact_not_supported" not in result.details
    assert "fact_not_cited" not in result.details

    # Full seam: aggregate → verdict.
    case_results = [_build_case_result(case, artifact)]
    verdict, allow_a4, allow_b1 = _decide_verdict(case_results)
    # P0-2: legacy artifacts MUST block the authoritative aggregate.
    assert verdict == "blocked_incomplete_real_model_run", (
        f"legacy artifact must block the authoritative aggregate; "
        f"got verdict={verdict!r}"
    )
    assert allow_a4 is False
    assert allow_b1 is False

    # P0-2: the failure cluster MUST be ``legacy-artifact``, NOT
    # ``fact-not-grounded``.
    aggregated = aggregate_results(case_results, {case.id: case})
    cluster_patterns = {
        c.failure_pattern for c in aggregated.failure_clusters
    }
    assert "fact-not-grounded" not in cluster_patterns, (
        "legacy artifacts must NOT cluster as fact-not-grounded; "
        f"got clusters={cluster_patterns}"
    )


# ---------------------------------------------------------------------------
# Spec test 2: new runtime exception (failed) → blocked incomplete,
# NOT legacy.
# ---------------------------------------------------------------------------


def test_r4_a4_0_final_gate_02_runtime_exception_failed_not_legacy() -> None:
    """Spec test 2: a new artifact with ``capture_status="failed"``
    MUST be classified as ``runtime_exception`` (an instrumentation
    blocker), NOT as legacy. The authoritative aggregate MUST block
    it (``blocked_incomplete_real_model_run``). This is the key
    distinction from the previous heuristic which could not separate
    legacy from runtime exception.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text=None,
        model_context_fingerprint=None,
        model_context_handle_ids=[],
        model_context_support=[],
        instrumentation_version="reader_record_ask_model_context_v1",
        capture_status="failed",
    )
    # Evaluator level: failed → passed=False, classification=runtime_exception.
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert result.classification == "runtime_exception"
    assert "runtime_exception" in result.details
    # Critical: NOT legacy.
    assert result.classification != "legacy_artifact"
    assert "legacy_artifact_no_model_context_support" not in result.details

    # Full seam: aggregate → verdict.
    case_results = [_build_case_result(case, artifact)]
    verdict, allow_a4, allow_b1 = _decide_verdict(case_results)
    assert verdict == "blocked_incomplete_real_model_run"
    assert allow_a4 is False
    assert allow_b1 is False

    # Cluster: instrumentation-incomplete, NOT fact-not-grounded.
    aggregated = aggregate_results(case_results, {case.id: case})
    cluster_patterns = {
        c.failure_pattern for c in aggregated.failure_clusters
    }
    assert "instrumentation-incomplete" in cluster_patterns
    assert "fact-not-grounded" not in cluster_patterns
    assert "legacy-artifact" not in cluster_patterns


# ---------------------------------------------------------------------------
# Spec test 3: baseline unavailable → artifact persists, no illegal
# empty SHA, aggregate blocked.
# ---------------------------------------------------------------------------


def test_r4_a4_0_final_gate_03_baseline_unavailable_no_empty_sha() -> None:
    """Spec test 3: a new artifact with ``capture_status="unavailable"``
    (baseline produced no chunks — e.g. envelope_mismatch / no_units)
    MUST:
    - persist (Pydantic validation passes — no ``fingerprint=""``
      empty SHA is constructed),
    - have ``model_context_fingerprint=None``,
    - have ``model_context_handle_ids=[]``,
    - have ``model_context_support=[]``,
    - be classified as ``baseline_unavailable`` by the evaluator,
    - block the aggregate (``blocked_incomplete_real_model_run``).
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    # unavailable: fingerprint MUST be None, handle_ids MUST be [],
    # observations MUST be [] (enforced by cross-field validator).
    artifact = RawArtifact(
        case_id="t-unavailable",
        run_id="run-unavailable",
        run_index=0,
        thinking_enabled=False,
        model_short_name="deepseek-chat",
        finalized_status="ok",
        final_text="文章提到了 Thunder Bay。",
        model_context_fingerprint=None,
        model_context_handle_ids=[],
        model_context_support=[],
        model_context_instrumentation_version="reader_record_ask_model_context_v1",
        model_context_capture_status="unavailable",
    )
    # P0-3: no illegal empty SHA. The fingerprint is None (not "").
    assert artifact.model_context_fingerprint is None
    assert artifact.model_context_fingerprint != ""

    # Evaluator level: unavailable → passed=False,
    # classification=baseline_unavailable.
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert result.classification == "baseline_unavailable"
    assert "baseline_unavailable" in result.details

    # Full seam: aggregate → verdict.
    case_results = [_build_case_result(case, artifact)]
    verdict, allow_a4, allow_b1 = _decide_verdict(case_results)
    assert verdict == "blocked_incomplete_real_model_run"
    assert allow_a4 is False
    assert allow_b1 is False


# ---------------------------------------------------------------------------
# Spec test 4: captured + fingerprint mismatch → blocked incomplete.
# ---------------------------------------------------------------------------


def test_r4_a4_0_final_gate_04_captured_fingerprint_mismatch_blocked() -> None:
    """Spec test 4: a captured artifact whose observation carries a
    DIFFERENT fingerprint than the artifact's
    ``model_context_fingerprint`` MUST be classified as
    ``instrumentation_incomplete`` and block the aggregate.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    # Artifact fingerprint is _TEST_FP, but the observation carries
    # _OTHER_FP → fingerprint mismatch.
    artifact = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_fingerprint=_TEST_FP,
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(
                fact_id="city-thunder-bay",
                support=True,
                fingerprint=_OTHER_FP,  # mismatch!
            ),
        ],
    )
    # Evaluator level: fingerprint mismatch → instrumentation_incomplete.
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert result.classification == "instrumentation_incomplete"
    assert "fingerprint_mismatch" in result.details

    # Full seam: aggregate → verdict.
    case_results = [_build_case_result(case, artifact)]
    verdict, allow_a4, allow_b1 = _decide_verdict(case_results)
    assert verdict == "blocked_incomplete_real_model_run"
    assert allow_a4 is False
    assert allow_b1 is False


# ---------------------------------------------------------------------------
# Spec test 5: captured + missing required observation → blocked.
# ---------------------------------------------------------------------------


def test_r4_a4_0_final_gate_05_captured_missing_required_observation() -> None:
    """Spec test 5: a captured artifact that has a fingerprint and
    handle_ids but is MISSING the observation for a required atomic
    fact (with source_aliases) MUST be classified as
    ``instrumentation_incomplete`` and block the aggregate.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    # Captured artifact with fingerprint + handle_ids, but NO
    # observations for the required fact.
    artifact = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_fingerprint=_TEST_FP,
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[],  # missing observation!
    )
    # Evaluator level: missing observation → instrumentation_incomplete.
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert result.classification == "instrumentation_incomplete"
    assert "no_observation_for_required_fact" in result.details

    # Full seam: aggregate → verdict.
    case_results = [_build_case_result(case, artifact)]
    verdict, allow_a4, allow_b1 = _decide_verdict(case_results)
    assert verdict == "blocked_incomplete_real_model_run"
    assert allow_a4 is False
    assert allow_b1 is False


# ---------------------------------------------------------------------------
# Spec test 6: captured + duplicate/unknown observation → blocked.
# ---------------------------------------------------------------------------


def test_r4_a4_0_final_gate_06_captured_duplicate_unknown_observation() -> None:
    """Spec test 6: a captured artifact with a duplicate ``fact_id``
    in observations OR an observation whose ``fact_id`` is not in the
    case's atomic_facts MUST be classified as
    ``instrumentation_incomplete`` and block the aggregate.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    # Duplicate fact_id in observations.
    artifact_dup = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_fingerprint=_TEST_FP,
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(fact_id="city-thunder-bay", support=True),
            _make_observation(fact_id="city-thunder-bay", support=True),  # dup
        ],
    )
    result_dup = evaluate_context_support(case, artifact_dup)
    assert result_dup.passed is False
    assert result_dup.classification == "instrumentation_incomplete"
    assert "duplicate_fact_id_in_observations" in result_dup.details

    case_results_dup = [_build_case_result(case, artifact_dup)]
    verdict_dup, _, _ = _decide_verdict(case_results_dup)
    assert verdict_dup == "blocked_incomplete_real_model_run"

    # Unknown fact_id in observations.
    artifact_unknown = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_fingerprint=_TEST_FP,
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(fact_id="city-thunder-bay", support=True),
            _make_observation(fact_id="city-ghost", support=True),  # unknown
        ],
    )
    result_unknown = evaluate_context_support(case, artifact_unknown)
    assert result_unknown.passed is False
    assert result_unknown.classification == "instrumentation_incomplete"
    assert "observation_fact_id_not_in_case_atomic_facts" in result_unknown.details

    case_results_unknown = [_build_case_result(case, artifact_unknown)]
    verdict_unknown, _, _ = _decide_verdict(case_results_unknown)
    assert verdict_unknown == "blocked_incomplete_real_model_run"


# ---------------------------------------------------------------------------
# Spec test 7: captured + supporting handle not in model context → blocked.
# ---------------------------------------------------------------------------


def test_r4_a4_0_final_gate_07_captured_supporting_handle_not_in_model_context() -> None:
    """Spec test 7: a captured artifact whose observation carries a
    ``supporting_handle_id`` that is NOT in the artifact's
    ``model_context_handle_ids`` MUST be classified as
    ``instrumentation_incomplete`` and block the aggregate.

    Note: the cross-field validator on :class:`RawArtifact` already
    rejects this at load time for ``captured`` artifacts. This test
    constructs a VALID captured artifact first, then uses
    ``model_copy(update=...)`` (which does NOT re-run validators) to
    swap in an observation referencing an unknown handle — verifying
    the evaluator ALSO catches it as defense-in-depth.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    # Build a VALID captured artifact first (supporting_handle_ids
    # IS in model_context_handle_ids). This passes the cross-field
    # validator.
    valid_artifact = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_fingerprint=_TEST_FP,
        model_context_handle_ids=[_HANDLE_CHUNK_0],  # only chunk_0 in context
        model_context_support=[
            _make_observation(
                fact_id="city-thunder-bay",
                support=True,
                supporting_handles=[_HANDLE_CHUNK_0],  # IS in context
            ),
        ],
    )
    # Now swap in a bad observation whose supporting_handle_id is NOT
    # in model_context_handle_ids. ``model_copy(update=...)`` does NOT
    # re-run validators, so the cross-field validator won't catch this
    # — the evaluator must catch it as defense-in-depth.
    bad_observation = ModelContextSupportObservation(
        fact_id="city-thunder-bay",
        support=True,
        model_context_fingerprint=_TEST_FP,
        supporting_handle_ids=[_HANDLE_UNKNOWN],  # NOT in context!
    )
    artifact = valid_artifact.model_copy(
        update={"model_context_support": [bad_observation]}
    )
    # Evaluator level: supporting handle not in model context →
    # instrumentation_incomplete.
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert result.classification == "instrumentation_incomplete"
    assert "supporting_handle_not_in_model_context" in result.details

    # Full seam: aggregate → verdict.
    case_results = [_build_case_result(case, artifact)]
    verdict, allow_a4, allow_b1 = _decide_verdict(case_results)
    assert verdict == "blocked_incomplete_real_model_run"
    assert allow_a4 is False
    assert allow_b1 is False


# ---------------------------------------------------------------------------
# Spec test 8: captured + fact_not_supported → rework, allow_r4_a4=true.
# ---------------------------------------------------------------------------


def test_r4_a4_0_final_gate_08_captured_fact_not_supported_rework() -> None:
    """Spec test 8: a captured artifact where a required fact is
    mentioned in the answer but the observation says ``support=False``
    (the fact is NOT grounded in the model-visible baseline) MUST be
    classified as ``fact_not_supported`` (a real model correctness
    failure) and enter rework with ``allow_r4_a4=true``.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    # Captured artifact: fact mentioned, but observation says support=False.
    artifact = _make_artifact(
        final_text="文章提到了 Thunder Bay。",  # mentioned
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_fingerprint=_TEST_FP,
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(
                fact_id="city-thunder-bay",
                support=False,  # NOT grounded
                supporting_handles=[],
            ),
        ],
    )
    # Evaluator level: fact_not_supported → real model failure.
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert result.classification == "fact_not_supported"
    assert "fact_not_supported" in result.details

    # Full seam: aggregate → verdict. This is a real model failure →
    # rework (NOT blocked_incomplete_real_model_run).
    case_results = [_build_case_result(case, artifact)]
    verdict, allow_a4, allow_b1 = _decide_verdict(case_results)
    assert verdict == "rework", (
        "fact_not_supported is a real model failure that MUST enter "
        f"rework; got verdict={verdict!r}"
    )
    assert allow_a4 is True
    assert allow_b1 is False

    # Cluster: fact-not-grounded (real model failure cluster).
    aggregated = aggregate_results(case_results, {case.id: case})
    cluster_patterns = {
        c.failure_pattern for c in aggregated.failure_clusters
    }
    assert "fact-not-grounded" in cluster_patterns
    assert "instrumentation-incomplete" not in cluster_patterns


# ---------------------------------------------------------------------------
# Spec test 9: captured + fact_not_cited → rework, allow_r4_a4=true.
# ---------------------------------------------------------------------------


def test_r4_a4_0_final_gate_09_captured_fact_not_cited_rework() -> None:
    """Spec test 9: a captured artifact where a required fact IS
    grounded in the model context (``support=True`` with valid
    supporting handles) but the answer does NOT cite any of those
    handles MUST be classified as ``fact_not_cited`` (a real model
    correctness failure) and enter rework with ``allow_r4_a4=true``.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    # Captured artifact: fact supported by chunk_0, but answer cites
    # chunk_1 (a different handle) → fact_not_cited.
    artifact = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_1],  # cite the WRONG handle
        model_context_fingerprint=_TEST_FP,
        model_context_handle_ids=[_HANDLE_CHUNK_0, _HANDLE_CHUNK_1],
        model_context_support=[
            _make_observation(
                fact_id="city-thunder-bay",
                support=True,
                supporting_handles=[_HANDLE_CHUNK_0],  # supported by chunk_0
            ),
        ],
    )
    # Evaluator level: fact_not_cited → real model failure.
    result = evaluate_context_support(case, artifact)
    assert result.passed is False
    assert result.classification == "fact_not_cited"
    assert "fact_not_cited" in result.details

    # Full seam: aggregate → verdict. Real model failure → rework.
    case_results = [_build_case_result(case, artifact)]
    verdict, allow_a4, allow_b1 = _decide_verdict(case_results)
    assert verdict == "rework", (
        "fact_not_cited is a real model failure that MUST enter "
        f"rework; got verdict={verdict!r}"
    )
    assert allow_a4 is True
    assert allow_b1 is False

    # Cluster: fact-not-grounded.
    aggregated = aggregate_results(case_results, {case.id: case})
    cluster_patterns = {
        c.failure_pattern for c in aggregated.failure_clusters
    }
    assert "fact-not-grounded" in cluster_patterns
    assert "instrumentation-incomplete" not in cluster_patterns


# ---------------------------------------------------------------------------
# Spec test 10: captured + all success → accepted, allow_r4_a4=true,
# allow_r4_b1=true.
# ---------------------------------------------------------------------------


def test_r4_a4_0_final_gate_10_captured_all_success_accepted() -> None:
    """Spec test 10: a captured artifact where all required facts are
    mentioned, grounded, and cited MUST be classified as ``supported``
    and the aggregate verdict MUST be ``accepted`` with
    ``allow_r4_a4=true`` and ``allow_r4_b1=true``.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    # Captured artifact: fact mentioned, supported by chunk_0, cited.
    artifact = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],  # cite the supporting handle
        model_context_fingerprint=_TEST_FP,
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(
                fact_id="city-thunder-bay",
                support=True,
                supporting_handles=[_HANDLE_CHUNK_0],
            ),
        ],
    )
    # Evaluator level: all success → supported.
    result = evaluate_context_support(case, artifact)
    assert result.passed is True
    assert result.classification == "supported"

    # Full seam: aggregate → verdict. All pass → accepted.
    case_results = [_build_case_result(case, artifact)]
    verdict, allow_a4, allow_b1 = _decide_verdict(case_results)
    assert verdict == "accepted", (
        "all-success captured artifact MUST be accepted; "
        f"got verdict={verdict!r}"
    )
    assert allow_a4 is True
    assert allow_b1 is True


# ---------------------------------------------------------------------------
# Spec test 11: instrumentation failure cluster must NOT be named
# fact-not-grounded.
# ---------------------------------------------------------------------------


def test_r4_a4_0_final_gate_11_instrumentation_cluster_not_fact_not_grounded() -> None:
    """Spec test 11: when an artifact has an instrumentation failure
    (capture_status=unavailable/failed, fingerprint mismatch, missing
    required observation, duplicate/unknown observation, or supporting
    handle not in model context), the failure cluster in the
    aggregated report MUST be named ``instrumentation-incomplete`` (or
    ``legacy-artifact`` for legacy), NOT ``fact-not-grounded``.

    This test exercises all instrumentation blocker subtypes in a
    single aggregate to verify none of them leak into the
    ``fact-not-grounded`` cluster.
    """
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])

    # Build one artifact per instrumentation blocker subtype.
    artifacts: list[RawArtifact] = []

    # Observability fields shared across the direct RawArtifact
    # constructions below. The point of this test is to verify that
    # instrumentation blockers cluster as ``instrumentation-incomplete``
    # (NOT ``fact-not-grounded``) — observability defaults are set so
    # the aggregated report doesn't trip an unrelated
    # ``observability-missing`` cluster that would muddy the assertion.
    _obs_kwargs = dict(
        thinking_enabled=False,
        model_short_name="deepseek-chat",
        agent_usage=RawUsage(requests=1, input_tokens=10, output_tokens=5),
        model_route="deepseek-chat",
        latency_seconds=1.0,
    )

    # 1. unavailable
    artifacts.append(RawArtifact(
        case_id="t-unavailable",
        run_id="run-unavailable",
        run_index=0,
        finalized_status="ok",
        final_text="文章提到了 Thunder Bay。",
        model_context_fingerprint=None,
        model_context_handle_ids=[],
        model_context_support=[],
        model_context_instrumentation_version="reader_record_ask_model_context_v1",
        model_context_capture_status="unavailable",
        **_obs_kwargs,
    ))
    # 2. failed
    artifacts.append(RawArtifact(
        case_id="t-failed",
        run_id="run-failed",
        run_index=0,
        finalized_status="ok",
        final_text="文章提到了 Thunder Bay。",
        model_context_fingerprint=None,
        model_context_handle_ids=[],
        model_context_support=[],
        model_context_instrumentation_version="reader_record_ask_model_context_v1",
        model_context_capture_status="failed",
        **_obs_kwargs,
    ))
    # 3. fingerprint mismatch
    artifacts.append(_make_artifact(
        case_id="t-fp-mismatch",
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_fingerprint=_TEST_FP,
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(
                fact_id="city-thunder-bay",
                support=True,
                fingerprint=_OTHER_FP,  # mismatch
            ),
        ],
    ))
    # 4. missing required observation
    artifacts.append(_make_artifact(
        case_id="t-missing-obs",
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_fingerprint=_TEST_FP,
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[],  # missing
    ))
    # 5. duplicate fact_id
    artifacts.append(_make_artifact(
        case_id="t-dup",
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_fingerprint=_TEST_FP,
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(fact_id="city-thunder-bay", support=True),
            _make_observation(fact_id="city-thunder-bay", support=True),
        ],
    ))
    # 6. legacy
    artifacts.append(RawArtifact(
        case_id="t-legacy",
        run_id="run-legacy",
        run_index=0,
        finalized_status="ok",
        final_text="文章提到了 Thunder Bay。",
        model_context_fingerprint=None,
        model_context_handle_ids=[],
        model_context_support=[],
        # lifecycle fields default to None → legacy
        **_obs_kwargs,
    ))

    case_results = [_build_case_result(case, art) for art in artifacts]
    aggregated = aggregate_results(
        case_results,
        {case.id: case},
    )
    cluster_patterns = {
        c.failure_pattern for c in aggregated.failure_clusters
    }

    # P0-2: instrumentation blockers MUST NOT cluster as fact-not-grounded.
    assert "fact-not-grounded" not in cluster_patterns, (
        "instrumentation blockers must NOT cluster as fact-not-grounded; "
        f"got clusters={cluster_patterns}"
    )
    # They SHOULD cluster as instrumentation-incomplete. (Legacy
    # artifacts return ``passed=True`` with ``coverage_incomplete`` from
    # the evaluator, so they do NOT appear in ``failure_clusters`` —
    # the legacy blocker is enforced at the verdict seam via
    # ``legacy_artifact_count`` in :class:`AggregateReadinessAudit`,
    # verified below.)
    assert "instrumentation-incomplete" in cluster_patterns, (
        "expected instrumentation-incomplete cluster; "
        f"got clusters={cluster_patterns}"
    )

    # Verdict: blocked_incomplete_real_model_run (NOT rework, NOT accepted).
    verdict, allow_a4, allow_b1 = _decide_verdict(case_results)
    assert verdict == "blocked_incomplete_real_model_run"
    assert allow_a4 is False
    assert allow_b1 is False


# ---------------------------------------------------------------------------
# Spec test 12: _compute_model_context_support(case, []) does not throw.
# ---------------------------------------------------------------------------


def test_r4_a4_0_final_gate_12_compute_model_context_support_empty_chunks() -> None:
    """Spec test 12: calling
    :func:`_compute_model_context_support` with an EMPTY
    ``model_context_chunks`` list MUST NOT raise. It MUST return
    ``([], None, [])`` — no observations are constructed (so no
    ``fingerprint=""`` empty SHA can trigger a ValidationError), the
    fingerprint is ``None``, and the handle_ids list is empty.

    This is the P0-3 explicit empty-chunks handling. The caller is
    responsible for writing ``capture_status="unavailable"`` or
    ``"failed"`` based on the baseline/result state.

    This test requires the ``services/api`` environment (with
    ``pydantic_ai`` installed) because the harness module imports from
    ``app.llm.*``. When run from the ``evals`` environment, this test
    is skipped — the contract is still verified indirectly via spec
    test 3 (which constructs an ``unavailable`` artifact directly and
    verifies no empty SHA is produced).
    """
    if _HARNESS is None:
        pytest.skip(
            "harness module requires services/api environment "
            "(pydantic_ai not installed in evals env); the empty-chunks "
            "contract is verified indirectly via spec test 3"
        )
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    # P0-3: empty chunks → ([], None, []). Must NOT raise.
    observations, fingerprint, handle_ids = (
        _HARNESS._compute_model_context_support(case, [])
    )
    assert observations == []
    assert fingerprint is None
    assert handle_ids == []
    # Critical: fingerprint is None, NOT "" (which would be an illegal
    # empty SHA that triggers ValidationError on
    # ModelContextSupportObservation).
    assert fingerprint != ""


# ---------------------------------------------------------------------------
# Spec test 13: existing metadata-only / no-facts artifact NOT
# misreported as incomplete.
# ---------------------------------------------------------------------------


def test_r4_a4_0_final_gate_13_metadata_only_no_facts_not_incomplete() -> None:
    """Spec test 13: a captured artifact for a case with NO required
    source facts (metadata-only or no atomic_facts) MUST NOT be
    misreported as instrumentation_incomplete. The harness correctly
    emits zero observations for such cases (no fact requires
    grounding), so the artifact's empty ``model_context_support`` is
    the EXPECTED state, not an instrumentation gap.

    The verdict MUST be ``accepted`` (when all other dimensions pass),
    NOT ``blocked_incomplete_real_model_run``.
    """
    # Case with no atomic_facts at all.
    case_no_facts = _make_case_with_atomic_facts([])
    # Captured artifact: fingerprint + handle_ids + empty observations
    # (correct for no-facts cases).
    artifact_no_facts = _make_artifact(
        final_text="文章提到了一些城市。",  # no facts to ground
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_fingerprint=_TEST_FP,
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[],  # no facts → no observations
    )
    # Evaluator level: no facts → coverage_incomplete_no_facts (NOT
    # instrumentation_incomplete). passed=True (soft signal).
    result_no_facts = evaluate_context_support(case_no_facts, artifact_no_facts)
    assert result_no_facts.passed is True
    assert result_no_facts.classification != "instrumentation_incomplete"
    assert "coverage_incomplete=true" in result_no_facts.details
    assert "case has no atomic_facts" in result_no_facts.details

    # Full seam: aggregate → verdict. No instrumentation blocker →
    # accepted (when all other dimensions pass).
    case_results_no_facts = [_build_case_result(case_no_facts, artifact_no_facts)]
    verdict_no_facts, allow_a4_no_facts, allow_b1_no_facts = _decide_verdict(
        case_results_no_facts
    )
    assert verdict_no_facts == "accepted", (
        "metadata-only / no-facts captured artifact MUST NOT be "
        "blocked as instrumentation_incomplete; "
        f"got verdict={verdict_no_facts!r}"
    )
    assert allow_a4_no_facts is True
    assert allow_b1_no_facts is True

    # Case with metadata-only facts (atomic_facts present but none
    # have source_aliases — so no grounding is required).
    case_metadata_only = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="meta-only-fact",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=[],  # metadata-only — no source aliases
            required=True,
            severity="high",
        ),
    ])
    artifact_metadata_only = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_fingerprint=_TEST_FP,
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[],  # no source_aliases → no observations needed
    )
    result_metadata_only = evaluate_context_support(
        case_metadata_only, artifact_metadata_only
    )
    # Metadata-only facts do NOT require observations — the evaluator
    # MUST NOT flag this as instrumentation_incomplete.
    assert result_metadata_only.classification != "instrumentation_incomplete"
    assert "instrumentation_incomplete" not in result_metadata_only.details

    case_results_metadata = [
        _build_case_result(case_metadata_only, artifact_metadata_only)
    ]
    verdict_metadata, _, _ = _decide_verdict(case_results_metadata)
    assert verdict_metadata == "accepted", (
        "metadata-only facts MUST NOT trigger instrumentation_incomplete; "
        f"got verdict={verdict_metadata!r}"
    )


# ===========================================================================
# R4-A4-0 P1 supplemental tests — shared classification contract
# ===========================================================================
#
# These 7 tests verify the P1 contract de-duplication work directly:
# the closed ``ContextSupportClassification`` Literal, the three
# routing frozensets, the Pydantic boundary rejection, and the
# single-source invariant. They supplement (not replace) the 13
# final-gate tests above — those tests exercise the full evaluator
# seam, while these target the contract module in isolation.
# ===========================================================================


from claread_eval.reader_record_ask.evaluators import (  # noqa: E402
    aggregator as _aggregator_module,
)
from claread_eval.reader_record_ask.evaluators import (  # noqa: E402
    context_support as _context_support_module,
)
from claread_eval.reader_record_ask.evaluators import (  # noqa: E402
    context_support_contract as _context_support_contract_module,
)


def test_contract_illegal_classification_rejected_at_pydantic_boundary() -> None:
    """P1 supplemental test 1: a classification string outside the
    closed :data:`ContextSupportClassification` Literal MUST be
    rejected at the Pydantic model boundary when constructing an
    :class:`EvalDimensionResult`.

    This is the core safety property introduced by the contract
    de-duplication: the vocabulary is closed at the type level, so a
    typo at any emit site (e.g. ``"instrumentation_incompletee"``)
    cannot silently leak through to the aggregator / runner.
    """
    # The 8 legal tags MUST construct cleanly.
    legal_tags = [
        "legacy_artifact",
        "captured",
        "baseline_unavailable",
        "runtime_exception",
        "instrumentation_incomplete",
        "fact_not_supported",
        "fact_not_cited",
        "supported",
    ]
    for tag in legal_tags:
        result = EvalDimensionResult(
            dimension="context_support",
            passed=True,
            classification=tag,  # type: ignore[arg-type]
        )
        assert result.classification == tag

    # Illegal tags MUST raise ValidationError.
    illegal_tags = [
        "bogus",
        "",
        "INSTRUMENTATION_INCOMPLETE",  # case-sensitive
        "instrumentation_incompletee",  # typo
        "fact_not_supported ",  # trailing space
        "legacy",  # abbreviation
        "supported ",
        None,  # None is allowed by `| None` — skip
    ]
    for tag in illegal_tags:
        if tag is None:
            continue
        with pytest.raises(ValidationError):
            EvalDimensionResult(
                dimension="context_support",
                passed=True,
                classification=tag,  # type: ignore[arg-type]
            )

    # None MUST be accepted (no classification → ordinary pass/fail).
    result_none = EvalDimensionResult(
        dimension="context_support",
        passed=True,
        classification=None,
    )
    assert result_none.classification is None


def test_contract_instrumentation_blockers_route_to_blocked_incomplete() -> None:
    """P1 supplemental test 2: the three instrumentation blocker
    classifications (``baseline_unavailable`` /
    ``runtime_exception`` / ``instrumentation_incomplete``) MUST all
    be members of
    :data:`INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS` and MUST all
    route the authoritative aggregate to
    ``blocked_incomplete_real_model_run`` (precedence row 9.5).
    """
    from claread_eval.reader_record_ask.evaluators.context_support_contract import (
        CLASSIFICATION_BASELINE_UNAVAILABLE,
        CLASSIFICATION_INSTRUMENTATION_INCOMPLETE,
        CLASSIFICATION_RUNTIME_EXCEPTION,
        INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS,
    )

    # Membership invariant — the contract frozenset contains exactly
    # the three instrumentation blocker tags.
    assert INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS == frozenset({
        CLASSIFICATION_BASELINE_UNAVAILABLE,
        CLASSIFICATION_RUNTIME_EXCEPTION,
        CLASSIFICATION_INSTRUMENTATION_INCOMPLETE,
    })

    # Each tag, when emitted by the evaluator, MUST route the
    # authoritative aggregate to blocked_incomplete_real_model_run.
    # We exercise each via the appropriate artifact shape.
    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])

    # baseline_unavailable: capture_status="unavailable".
    artifact_unavailable = _make_artifact(
        final_text=None,
        model_context_fingerprint=None,
        model_context_handle_ids=[],
        model_context_support=[],
        instrumentation_version="reader_record_ask_model_context_v1",
        capture_status="unavailable",
    )
    result_unavailable = evaluate_context_support(case, artifact_unavailable)
    assert result_unavailable.classification == CLASSIFICATION_BASELINE_UNAVAILABLE
    assert result_unavailable.classification in INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS

    # runtime_exception: capture_status="failed".
    artifact_failed = _make_artifact(
        final_text=None,
        model_context_fingerprint=None,
        model_context_handle_ids=[],
        model_context_support=[],
        instrumentation_version="reader_record_ask_model_context_v1",
        capture_status="failed",
    )
    result_failed = evaluate_context_support(case, artifact_failed)
    assert result_failed.classification == CLASSIFICATION_RUNTIME_EXCEPTION
    assert result_failed.classification in INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS

    # instrumentation_incomplete: captured artifact with a fingerprint
    # mismatch (observation fingerprint != artifact fingerprint).
    artifact_incomplete = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_fingerprint=_TEST_FP,
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(
                fact_id="city-thunder-bay",
                support=True,
                fingerprint=_OTHER_FP,  # mismatch → instrumentation_incomplete
            ),
        ],
    )
    result_incomplete = evaluate_context_support(case, artifact_incomplete)
    assert result_incomplete.classification == CLASSIFICATION_INSTRUMENTATION_INCOMPLETE
    assert result_incomplete.classification in INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS

    # Verdict seam: all three MUST block the aggregate.
    for artifact in (artifact_unavailable, artifact_failed, artifact_incomplete):
        case_results = [_build_case_result(case, artifact)]
        verdict, allow_a4, allow_b1 = _decide_verdict(case_results)
        assert verdict == "blocked_incomplete_real_model_run", (
            f"instrumentation blocker {artifact.model_context_capture_status!r} "
            f"must block the aggregate; got verdict={verdict!r}"
        )
        assert allow_a4 is False
        assert allow_b1 is False


def test_contract_legacy_routes_to_authoritative_aggregate_blocked() -> None:
    """P1 supplemental test 3: the ``legacy_artifact`` classification
    MUST be a member of :data:`LEGACY_BLOCKER_CLASSIFICATIONS` and
    MUST route the authoritative aggregate to
    ``blocked_incomplete_real_model_run`` (precedence row 9.5, kept
    separate from instrumentation_incomplete so the audit field
    ``instrumentation_incomplete_count`` stays semantically narrow).
    """
    from claread_eval.reader_record_ask.evaluators.context_support_contract import (
        CLASSIFICATION_LEGACY,
        INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS,
        LEGACY_BLOCKER_CLASSIFICATIONS,
    )

    # Membership invariant.
    assert LEGACY_BLOCKER_CLASSIFICATIONS == frozenset({CLASSIFICATION_LEGACY})

    # Legacy is intentionally NOT in the instrumentation_incomplete set —
    # legacy artifacts did not fail at run time, they predate the
    # contract. The audit field stays semantically narrow.
    assert CLASSIFICATION_LEGACY not in INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS

    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    artifact = RawArtifact(
        case_id="t-legacy-contract",
        run_id="run-legacy-contract",
        run_index=0,
        thinking_enabled=False,
        model_short_name="deepseek-chat",
        finalized_status="ok",
        final_text="文章提到了 Thunder Bay。",
        model_context_fingerprint=None,
        model_context_handle_ids=[],
        model_context_support=[],
        # lifecycle fields default to None → legacy
    )
    result = evaluate_context_support(case, artifact)
    assert result.classification == CLASSIFICATION_LEGACY
    assert result.classification in LEGACY_BLOCKER_CLASSIFICATIONS

    # Verdict seam: legacy MUST block the authoritative aggregate.
    case_results = [_build_case_result(case, artifact)]
    verdict, allow_a4, allow_b1 = _decide_verdict(case_results)
    assert verdict == "blocked_incomplete_real_model_run", (
        f"legacy must block the authoritative aggregate; got verdict={verdict!r}"
    )
    assert allow_a4 is False
    assert allow_b1 is False


def test_contract_fact_not_supported_and_not_cited_route_to_rework() -> None:
    """P1 supplemental test 4: the two real model correctness failure
    classifications (``fact_not_supported`` / ``fact_not_cited``)
    MUST both be members of
    :data:`MODEL_FAILURE_CLASSIFICATIONS` and MUST both route the
    authoritative aggregate to ``rework`` (precedence row 12).
    """
    from claread_eval.reader_record_ask.evaluators.context_support_contract import (
        CLASSIFICATION_FACT_NOT_CITED,
        CLASSIFICATION_FACT_NOT_SUPPORTED,
        INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS,
        MODEL_FAILURE_CLASSIFICATIONS,
    )

    # Membership invariant.
    assert MODEL_FAILURE_CLASSIFICATIONS == frozenset({
        CLASSIFICATION_FACT_NOT_SUPPORTED,
        CLASSIFICATION_FACT_NOT_CITED,
    })

    # Real model failures are intentionally NOT in the
    # instrumentation_incomplete set — they DO enter rework.
    for tag in (CLASSIFICATION_FACT_NOT_SUPPORTED, CLASSIFICATION_FACT_NOT_CITED):
        assert tag not in INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS

    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])

    # fact_not_supported: support=False.
    artifact_not_supported = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_fingerprint=_TEST_FP,
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(
                fact_id="city-thunder-bay",
                support=False,
                supporting_handles=[],
            ),
        ],
    )
    result_not_supported = evaluate_context_support(case, artifact_not_supported)
    assert result_not_supported.classification == CLASSIFICATION_FACT_NOT_SUPPORTED
    assert result_not_supported.classification in MODEL_FAILURE_CLASSIFICATIONS

    # fact_not_cited: support=True but answer cites the wrong handle.
    artifact_not_cited = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_1],  # wrong handle
        model_context_fingerprint=_TEST_FP,
        model_context_handle_ids=[_HANDLE_CHUNK_0, _HANDLE_CHUNK_1],
        model_context_support=[
            _make_observation(
                fact_id="city-thunder-bay",
                support=True,
                supporting_handles=[_HANDLE_CHUNK_0],  # supported by chunk_0
            ),
        ],
    )
    result_not_cited = evaluate_context_support(case, artifact_not_cited)
    assert result_not_cited.classification == CLASSIFICATION_FACT_NOT_CITED
    assert result_not_cited.classification in MODEL_FAILURE_CLASSIFICATIONS

    # Verdict seam: both MUST enter rework (real model failures).
    for artifact in (artifact_not_supported, artifact_not_cited):
        case_results = [_build_case_result(case, artifact)]
        verdict, allow_a4, allow_b1 = _decide_verdict(case_results)
        assert verdict == "rework", (
            f"real model failure must enter rework; got verdict={verdict!r}"
        )
        assert allow_a4 is True
        assert allow_b1 is False


def test_contract_supported_routes_to_accepted() -> None:
    """P1 supplemental test 5: the ``supported`` classification MUST
    NOT be a member of any blocker frozenset
    (:data:`INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS` /
    :data:`LEGACY_BLOCKER_CLASSIFICATIONS` /
    :data:`MODEL_FAILURE_CLASSIFICATIONS`) and MUST route the
    authoritative aggregate to ``accepted``.
    """
    from claread_eval.reader_record_ask.evaluators.context_support_contract import (
        CLASSIFICATION_SUPPORTED,
        INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS,
        LEGACY_BLOCKER_CLASSIFICATIONS,
        MODEL_FAILURE_CLASSIFICATIONS,
    )

    # `supported` is the success path — must NOT be in any blocker set.
    assert CLASSIFICATION_SUPPORTED not in INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS
    assert CLASSIFICATION_SUPPORTED not in LEGACY_BLOCKER_CLASSIFICATIONS
    assert CLASSIFICATION_SUPPORTED not in MODEL_FAILURE_CLASSIFICATIONS

    case = _make_case_with_atomic_facts([
        AtomicExpectedFact(
            fact_id="city-thunder-bay",
            answer_alias_groups=[["Thunder Bay"]],
            source_aliases=["Thunder Bay"],
            required=True,
            severity="high",
        ),
    ])
    artifact = _make_artifact(
        final_text="文章提到了 Thunder Bay。",
        cited_evidence_handles=[_HANDLE_CHUNK_0],
        model_context_fingerprint=_TEST_FP,
        model_context_handle_ids=[_HANDLE_CHUNK_0],
        model_context_support=[
            _make_observation(
                fact_id="city-thunder-bay",
                support=True,
                supporting_handles=[_HANDLE_CHUNK_0],
            ),
        ],
    )
    result = evaluate_context_support(case, artifact)
    assert result.classification == CLASSIFICATION_SUPPORTED

    # Verdict seam: supported → accepted.
    case_results = [_build_case_result(case, artifact)]
    verdict, allow_a4, allow_b1 = _decide_verdict(case_results)
    assert verdict == "accepted", (
        f"supported must route to accepted; got verdict={verdict!r}"
    )
    assert allow_a4 is True
    assert allow_b1 is True


def test_contract_aggregator_failure_pattern_uses_shared_classification_set() -> None:
    """P1 supplemental test 6: the aggregator's failure-pattern
    router MUST consume the shared classification frozensets from
    :mod:`evaluators.context_support_contract` — not a private mirror
    copy. This is verified by introspecting the aggregator module's
    imports and by exercising the failure-pattern function directly
    with each classification tag.
    """
    from claread_eval.reader_record_ask.evaluators.aggregator import (
        _extract_failure_pattern_typed,
    )
    from claread_eval.reader_record_ask.evaluators.context_support_contract import (
        CLASSIFICATION_BASELINE_UNAVAILABLE,
        CLASSIFICATION_FACT_NOT_CITED,
        CLASSIFICATION_FACT_NOT_SUPPORTED,
        CLASSIFICATION_INSTRUMENTATION_INCOMPLETE,
        CLASSIFICATION_LEGACY,
        CLASSIFICATION_RUNTIME_EXCEPTION,
        CLASSIFICATION_SUPPORTED,
        INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS,
        LEGACY_BLOCKER_CLASSIFICATIONS,
        MODEL_FAILURE_CLASSIFICATIONS,
    )

    # The aggregator module MUST import the shared frozensets from
    # the contract module (not define its own mirror). We verify by
    # checking that the module-level frozenset objects the aggregator
    # references are the SAME objects as the contract module's
    # frozensets (identity check via `is`).
    # ``_INSTRUMENTATION_INCOMPLETE_REASONS`` and
    # ``_MODEL_FAILURE_REASONS`` are the aggregator's aliased imports.
    assert (
        _aggregator_module._INSTRUMENTATION_INCOMPLETE_REASONS
        is INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS
    )
    assert (
        _aggregator_module._MODEL_FAILURE_REASONS
        is MODEL_FAILURE_CLASSIFICATIONS
    )
    # The aggregator also imports the legacy tag constant (used to
    # detect the ``legacy-artifact`` cluster pattern).
    assert _aggregator_module._REASON_LEGACY == CLASSIFICATION_LEGACY
    assert _aggregator_module._REASON_SUPPORTED == CLASSIFICATION_SUPPORTED

    # Direct exercise of the typed failure-pattern router: each
    # instrumentation blocker tag → ``instrumentation-incomplete``.
    for tag in (
        CLASSIFICATION_BASELINE_UNAVAILABLE,
        CLASSIFICATION_RUNTIME_EXCEPTION,
        CLASSIFICATION_INSTRUMENTATION_INCOMPLETE,
    ):
        pattern = _extract_failure_pattern_typed(
            "context_support", "details-irrelevant", tag
        )
        assert pattern == "instrumentation-incomplete", (
            f"tag {tag!r} must cluster as instrumentation-incomplete; "
            f"got pattern={pattern!r}"
        )

    # Real model failure tags → ``fact-not-grounded``.
    for tag in (CLASSIFICATION_FACT_NOT_SUPPORTED, CLASSIFICATION_FACT_NOT_CITED):
        pattern = _extract_failure_pattern_typed(
            "context_support", "details-irrelevant", tag
        )
        assert pattern == "fact-not-grounded", (
            f"tag {tag!r} must cluster as fact-not-grounded; "
            f"got pattern={pattern!r}"
        )

    # Legacy tag → ``legacy-artifact``.
    pattern_legacy = _extract_failure_pattern_typed(
        "context_support", "details-irrelevant", CLASSIFICATION_LEGACY
    )
    assert pattern_legacy == "legacy-artifact"

    # `supported` → not a failure (defense-in-depth: returns
    # ``supported`` only if the function is somehow reached for a
    # passed dimension; the aggregator normally skips passed dims).
    pattern_supported = _extract_failure_pattern_typed(
        "context_support", "details-irrelevant", CLASSIFICATION_SUPPORTED
    )
    # ``supported`` is in the contract but the function falls back to
    # ``_extract_failure_pattern`` for non-failure tags. The string
    # fallback returns ``fact-not-grounded`` for ``context_support``.
    # What matters here is that the function does NOT raise and does
    # NOT cluster ``supported`` as ``instrumentation-incomplete``.
    assert pattern_supported != "instrumentation-incomplete"

    # The legacy tag is NOT in the instrumentation_incomplete set
    # (this is the partition invariant that keeps the audit field
    # semantically narrow).
    assert CLASSIFICATION_LEGACY not in INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS
    assert CLASSIFICATION_LEGACY in LEGACY_BLOCKER_CLASSIFICATIONS


def test_contract_single_source_no_mirror_definitions() -> None:
    """P1 supplemental test 7 (the ``rg`` invariant, as a runtime
    test): the closed :data:`ContextSupportClassification` Literal
    and the three routing frozensets
    (:data:`INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS` /
    :data:`LEGACY_BLOCKER_CLASSIFICATIONS` /
    :data:`MODEL_FAILURE_CLASSIFICATIONS`) MUST be defined exactly
    ONCE across the entire ``evals/`` tree — in
    :mod:`evaluators.context_support_contract`. No mirror copy may
    exist in ``context_support.py``, ``aggregator.py``, or the
    runner script.

    This test scans the relevant source files at runtime and asserts
    that the defining assignment patterns appear ONLY in the contract
    module. A second copy anywhere else would let a typo drift
    silently and undermine the closed-vocabulary safety property.
    """
    import re

    # The four modules that previously held mirror copies.
    candidate_files = [
        _context_support_module.__file__,
        _aggregator_module.__file__,
        _RUNNER_PATH,
        # The contract module itself — MUST be the ONLY file with
        # these definitions.
        _context_support_contract_module.__file__,
    ]
    assert all(p is not None for p in candidate_files), (
        "all candidate modules must have a resolvable __file__"
    )

    # Patterns that uniquely identify the contract vocabulary
    # definitions. We match the assignment LHS — these are the
    # SINGLE-SOURCE markers.
    literal_pattern = re.compile(
        r"^ContextSupportClassification\s*=\s*Literal\[",
        re.MULTILINE,
    )
    instr_set_pattern = re.compile(
        r"^INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS\s*:\s*frozenset\[str\]\s*=\s*frozenset\(",
        re.MULTILINE,
    )
    legacy_set_pattern = re.compile(
        r"^LEGACY_BLOCKER_CLASSIFICATIONS\s*:\s*frozenset\[str\]\s*=\s*frozenset\(",
        re.MULTILINE,
    )
    model_failure_set_pattern = re.compile(
        r"^MODEL_FAILURE_CLASSIFICATIONS\s*:\s*frozenset\[str\]\s*=\s*frozenset\(",
        re.MULTILINE,
    )

    contract_path = str(_context_support_contract_module.__file__)

    for pattern, label in [
        (literal_pattern, "ContextSupportClassification Literal"),
        (instr_set_pattern, "INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS frozenset"),
        (legacy_set_pattern, "LEGACY_BLOCKER_CLASSIFICATIONS frozenset"),
        (model_failure_set_pattern, "MODEL_FAILURE_CLASSIFICATIONS frozenset"),
    ]:
        matches: list[str] = []
        for file_path in candidate_files:
            assert file_path is not None
            with open(file_path, "r", encoding="utf-8") as f:
                source = f.read()
            if pattern.search(source):
                matches.append(file_path)
        # Each definition MUST appear in EXACTLY one file — the
        # contract module. No mirror copies allowed.
        assert matches == [contract_path], (
            f"{label} must be defined exactly ONCE (in the contract "
            f"module {contract_path}); found definitions in: {matches}"
        )

    # Aliased imports (``as REASON_LEGACY`` etc.) are allowed — they
    # re-export the contract constants without redefining the
    # vocabulary. Verify the contract module itself does NOT import
    # from any of the three call sites (no circular dependency).
    with open(contract_path, "r", encoding="utf-8") as f:
        contract_source = f.read()
    forbidden_imports = [
        "from claread_eval.reader_record_ask.evaluators.context_support import",
        "from claread_eval.reader_record_ask.evaluators.aggregator import",
        "from claread_eval.reader_record_ask.evaluators.result import",
        "from claread_eval.reader_record_ask.evaluators.artifact import",
        "from claread_eval.reader_record_ask.evaluators import result",
        "from claread_eval.reader_record_ask.evaluators import artifact",
    ]
    for forbidden in forbidden_imports:
        assert forbidden not in contract_source, (
            f"contract module must NOT import from call sites "
            f"(leaf-module invariant); found: {forbidden!r}"
        )
