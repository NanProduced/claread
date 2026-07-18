"""P0-3: Evidence kind/provenance cross-field invariant tests.

Spec: R4-A3 Eval Harness 最终微补丁——Evidence Kind/Provenance 组合不变量.

This module locks down the cross-field invariant on
:class:`RawEvidenceObservation` that was missing in the previous round:
``kind`` and ``provenance`` were each individually validated as legal
Literals, but illegal COMBINATIONS (e.g. ``article_seed +
search_current_article``) were silently accepted.

Production contract source:
``services/api/app/services/reader_record_ask/evidence.py:64-77``
(``LEGAL_EVIDENCE_KIND_SOURCE``). The evals-side copy is
:data:`LEGAL_EVIDENCE_KIND_PROVENANCE` in
``claread_eval/reader_record_ask/evaluators/artifact.py``.

Total combinations: 5 kinds × 4 provenances = 20 cartesian pairs.
Legal: exactly 7. Illegal: exactly 13.

No real LLM / provider calls. All tests are deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from claread_eval.reader_record_ask.artifact_loading import (
    load_artifacts_with_audit,
)
from claread_eval.reader_record_ask.evaluators.artifact import (
    LEGAL_EVIDENCE_KIND_PROVENANCE,
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

# ---------------------------------------------------------------------------
# Constants and fixtures
# ---------------------------------------------------------------------------

_VALID_SHA = "a" * 64

# The complete 20-combination cartesian product, split into legal (7)
# and illegal (13) pairs. These are the SINGLE source of truth for the
# parametrized tests below — any change to LEGAL_EVIDENCE_KIND_PROVENANCE
# must be reflected here (and the counts asserted in the mapping-shape
# tests).

LEGAL_PAIRS: list[tuple[str, str]] = [
    ("initial_anchor", "initial_anchor"),
    ("read_range", "read_range"),
    ("search_hit", "search_current_article"),
    ("observation", "initial_anchor"),
    ("observation", "read_range"),
    ("observation", "search_current_article"),
    ("article_seed", "baseline_context"),
]

ILLEGAL_PAIRS: list[tuple[str, str]] = [
    # initial_anchor: only "initial_anchor" provenance is legal
    ("initial_anchor", "read_range"),
    ("initial_anchor", "search_current_article"),
    ("initial_anchor", "baseline_context"),
    # read_range: only "read_range" provenance is legal
    ("read_range", "initial_anchor"),
    ("read_range", "search_current_article"),
    ("read_range", "baseline_context"),
    # search_hit: only "search_current_article" provenance is legal
    ("search_hit", "initial_anchor"),
    ("search_hit", "read_range"),
    ("search_hit", "baseline_context"),
    # observation: "baseline_context" is illegal (article_seed owns it)
    ("observation", "baseline_context"),
    # article_seed: only "baseline_context" provenance is legal
    ("article_seed", "initial_anchor"),
    ("article_seed", "read_range"),
    ("article_seed", "search_current_article"),
]

# Sanity assertion at module load time — catches test-vs-mapping drift
# early (before any test function runs).
assert len(LEGAL_PAIRS) == 7, f"expected 7 legal pairs, got {len(LEGAL_PAIRS)}"
assert len(ILLEGAL_PAIRS) == 13, (
    f"expected 13 illegal pairs, got {len(ILLEGAL_PAIRS)}"
)
_ALL_PAIRS = LEGAL_PAIRS + ILLEGAL_PAIRS
assert len(_ALL_PAIRS) == 20, (
    f"expected 20 total pairs, got {len(_ALL_PAIRS)}"
)
assert len(set(_ALL_PAIRS)) == 20, "duplicate pairs detected"


def _make_case() -> ReaderRecordAskR4A3Case:
    return ReaderRecordAskR4A3Case(
        id="t-evidence-contract",
        source_kind="synthetic_short",
        input_mode="manual",
        source_metadata="unknown",
        baseline_mode="complete",
        question="这篇文章主要说了什么？",
        question_category="main_idea",
        expected=ReaderRecordAskR4A3Expected(),
    )


def _make_valid_artifact() -> RawArtifact:
    """A schema-valid RawArtifact with no evidence observations."""
    return RawArtifact(
        case_id="case-a",
        run_id="phase1-test",
        run_index=0,
        dataset_id="test-dataset",
        dataset_schema_version="test-schema-v1",
        dataset_content_sha256=_VALID_SHA,
        budget_exhausted=False,
    )


def _write_artifact_json(
    artifact_dir: Path, filename: str, payload: object
) -> Path:
    """Write a JSON file (potentially invalid) to ``artifact_dir``."""
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / filename
    if isinstance(payload, bytes):
        path.write_bytes(payload)
    elif isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ===========================================================================
# SECTION 1: Mapping shape tests
# ===========================================================================


class TestLegalEvidenceKindProvenanceMappingShape:
    """Lock down the shape of :data:`LEGAL_EVIDENCE_KIND_PROVENANCE`."""

    def test_mapping_contains_exactly_five_kinds(self) -> None:
        """The mapping MUST have exactly 5 kind keys (one per EvidenceKind)."""
        assert len(LEGAL_EVIDENCE_KIND_PROVENANCE) == 5
        assert set(LEGAL_EVIDENCE_KIND_PROVENANCE.keys()) == {
            "initial_anchor",
            "read_range",
            "search_hit",
            "observation",
            "article_seed",
        }

    def test_mapping_values_are_frozensets(self) -> None:
        """All values MUST be ``frozenset`` (immutable, hashable)."""
        for value in LEGAL_EVIDENCE_KIND_PROVENANCE.values():
            assert isinstance(value, frozenset), (
                f"expected frozenset, got {type(value).__name__}"
            )

    def test_total_legal_combinations_exactly_seven(self) -> None:
        """The total number of legal (kind, provenance) pairs MUST be 7."""
        total = sum(len(v) for v in LEGAL_EVIDENCE_KIND_PROVENANCE.values())
        assert total == 7, f"expected 7 legal pairs, got {total}"

    def test_mapping_matches_test_legal_pairs(self) -> None:
        """The mapping's enumerated legal pairs MUST match the test's
        LEGAL_PAIRS list — single source of truth.
        """
        mapping_pairs: set[tuple[str, str]] = set()
        for kind, provenances in LEGAL_EVIDENCE_KIND_PROVENANCE.items():
            for prov in provenances:
                mapping_pairs.add((kind, prov))
        assert mapping_pairs == set(LEGAL_PAIRS)


# ===========================================================================
# SECTION 2: 7 legal combinations accepted
# ===========================================================================


class TestLegalCombinationsAccepted:
    """All 7 legal (kind, provenance) pairs MUST construct successfully."""

    @pytest.mark.parametrize(
        "kind, provenance",
        LEGAL_PAIRS,
        ids=[f"{k}__{p}" for k, p in LEGAL_PAIRS],
    )
    def test_legal_pair_accepted(
        self, kind: str, provenance: str
    ) -> None:
        """Each legal pair MUST construct without raising."""
        obs = RawEvidenceObservation(
            handle_id="h1",
            kind=kind,  # type: ignore[arg-type]
            snippet="s",
            provenance=provenance,  # type: ignore[arg-type]
        )
        assert obs.kind == kind
        assert obs.provenance == provenance


# ===========================================================================
# SECTION 3: 13 illegal combinations rejected
# ===========================================================================


class TestIllegalCombinationsRejected:
    """All 13 illegal (kind, provenance) pairs MUST raise ValidationError."""

    @pytest.mark.parametrize(
        "kind, provenance",
        ILLEGAL_PAIRS,
        ids=[f"{k}__{p}" for k, p in ILLEGAL_PAIRS],
    )
    def test_illegal_pair_rejected(
        self, kind: str, provenance: str
    ) -> None:
        """Each illegal pair MUST raise ``ValidationError`` at construction."""
        with pytest.raises(ValidationError):
            RawEvidenceObservation(
                handle_id="h1",
                kind=kind,  # type: ignore[arg-type]
                snippet="s",
                provenance=provenance,  # type: ignore[arg-type]
            )

    # ------------------------------------------------------------------
    # Spec-named specific illegal combinations (defensive duplication
    # in case the parametrized list above is accidentally edited)
    # ------------------------------------------------------------------

    def test_observation_baseline_context_rejected(self) -> None:
        """``observation + baseline_context`` is ILLEGAL — ``baseline_context``
        provenance is exclusively owned by ``article_seed``.
        """
        with pytest.raises(ValidationError):
            RawEvidenceObservation(
                handle_id="h1",
                kind="observation",
                provenance="baseline_context",
            )

    def test_article_seed_read_range_rejected(self) -> None:
        """``article_seed + read_range`` is ILLEGAL — article_seed is
        exclusively produced by ``baseline_context``.
        """
        with pytest.raises(ValidationError):
            RawEvidenceObservation(
                handle_id="h1",
                kind="article_seed",
                provenance="read_range",
            )

    def test_article_seed_search_current_article_rejected(self) -> None:
        """``article_seed + search_current_article`` is ILLEGAL — this
        is the specific spoofing vector called out in the spec: search
        evidence disguised as article_seed could bypass the
        ``evidence_minimality`` soft-failure check.
        """
        with pytest.raises(ValidationError):
            RawEvidenceObservation(
                handle_id="h1",
                kind="article_seed",
                provenance="search_current_article",
            )

    def test_search_hit_baseline_context_rejected(self) -> None:
        """``search_hit + baseline_context`` is ILLEGAL — search hits
        are exclusively produced by ``search_current_article``.
        """
        with pytest.raises(ValidationError):
            RawEvidenceObservation(
                handle_id="h1",
                kind="search_hit",
                provenance="baseline_context",
            )

    def test_initial_anchor_search_current_article_rejected(self) -> None:
        """``initial_anchor + search_current_article`` is ILLEGAL."""
        with pytest.raises(ValidationError):
            RawEvidenceObservation(
                handle_id="h1",
                kind="initial_anchor",
                provenance="search_current_article",
            )


# ===========================================================================
# SECTION 4: Artifact loader fail-closed on illegal pairs
# ===========================================================================


class TestIllegalPairsRejectedByArtifactLoader:
    """Illegal (kind, provenance) pairs in an artifact FILE must be
    classified as ``invalid_schema_count`` by the production load
    boundary (:func:`load_artifacts_with_audit`). They MUST NOT reach
    the evaluator.
    """

    def _write_and_load(
        self, tmp_path: Path, observations: list[dict]
    ) -> object:
        artifact_dir = tmp_path / "artifacts"
        payload = _make_valid_artifact().model_dump()
        payload["all_evidence_observations"] = observations
        _write_artifact_json(artifact_dir, "case-a__0.json", payload)
        return load_artifacts_with_audit(artifact_dir, "phase1-test")

    @pytest.mark.parametrize(
        "kind, provenance",
        ILLEGAL_PAIRS,
        ids=[f"{k}__{p}" for k, p in ILLEGAL_PAIRS],
    )
    def test_illegal_pair_rejected_at_load_boundary(
        self, tmp_path: Path, kind: str, provenance: str
    ) -> None:
        """Each illegal pair in a JSON artifact file MUST increment
        ``invalid_schema_count`` and produce zero valid artifacts.
        """
        result = self._write_and_load(
            tmp_path,
            [
                {
                    "handle_id": "h1",
                    "kind": kind,
                    "snippet": "s",
                    "provenance": provenance,
                }
            ],
        )
        assert result.invalid_schema_count == 1, (
            f"expected invalid_schema_count=1 for illegal pair "
            f"({kind}, {provenance}), got {result.invalid_schema_count}"
        )
        assert len(result.valid_artifacts) == 0

    def test_illegal_pair_loader_result_does_not_leak_sensitive_fields(
        self, tmp_path: Path
    ) -> None:
        """The :class:`ArtifactLoadResult` MUST NOT carry the handle,
        snippet, or exception text — only typed counts.
        """
        # Embed a fake "sensitive" snippet and handle to prove they
        # don't leak into the result dataclass.
        result = self._write_and_load(
            tmp_path,
            [
                {
                    "handle_id": "SECRET_HANDLE_DO_NOT_LEAK",
                    "kind": "article_seed",
                    "snippet": "SECRET_SNIPPET_DO_NOT_LEAK",
                    "provenance": "search_current_article",  # illegal
                }
            ],
        )
        assert result.invalid_schema_count == 1
        # The result dataclass has NO field that could carry text.
        assert not hasattr(result, "error_text")
        assert not hasattr(result, "exception")
        assert not hasattr(result, "raw_bytes")
        assert not hasattr(result, "file_paths")
        # Verify by serializing the result to its repr — no secrets.
        result_repr = repr(result)
        assert "SECRET_HANDLE_DO_NOT_LEAK" not in result_repr
        assert "SECRET_SNIPPET_DO_NOT_LEAK" not in result_repr

    def test_legal_pair_loads_successfully(
        self, tmp_path: Path
    ) -> None:
        """Non-regression: a legal evidence observation loads as a
        valid artifact (``invalid_schema_count=0``).
        """
        result = self._write_and_load(
            tmp_path,
            [
                {
                    "handle_id": "h1",
                    "kind": "article_seed",
                    "snippet": "s",
                    "provenance": "baseline_context",  # legal
                }
            ],
        )
        assert result.invalid_schema_count == 0
        assert len(result.valid_artifacts) == 1


# ===========================================================================
# SECTION 5: Legal pairs through real evidence_minimality evaluator
# ===========================================================================


class TestLegalPairsThroughRealEvaluator:
    """Spec section 四: prove legal pairs traverse the real
    :func:`evaluate_evidence_minimality` evaluator with the expected
    verdicts, and that the cross-field invariant does NOT change
    content-quality scoring.
    """

    def _handle(self, suffix: str = "a") -> str:
        return "evh_" + suffix + "0" * 28

    def test_legal_search_hit_soft_fails_when_baseline_complete(
        self
    ) -> None:
        """A legal ``search_hit + search_current_article`` observation
        MUST enter the evaluator. When ``baseline_is_complete=True``
        and ALL cited evidence is ``search_hit``, the evaluator's
        soft-failure branch MUST fire (``passed=False, severity="medium"``).
        """
        h1 = self._handle("a")
        h2 = self._handle("b")
        artifact = RawArtifact(
            case_id="case-a",
            run_id="phase1-test",
            finalized_status="ok",
            final_text="answer",
            cited_evidence_handles=[h1, h2],
            all_evidence_observations=[
                RawEvidenceObservation(
                    handle_id=h1,
                    kind="search_hit",
                    snippet="search result 1",
                    provenance="search_current_article",
                ),
                RawEvidenceObservation(
                    handle_id=h2,
                    kind="search_hit",
                    snippet="search result 2",
                    provenance="search_current_article",
                ),
            ],
            baseline_is_complete=True,
        )
        result = evaluate_evidence_minimality(_make_case(), artifact)
        assert result.passed is False
        assert result.severity == "medium"
        assert "search_hit" in result.details

    def test_legal_article_seed_does_not_trigger_search_hit_soft_failure(
        self
    ) -> None:
        """A legal ``article_seed + baseline_context`` observation
        MUST enter the evaluator and MUST NOT trigger the search-hit-only
        soft-failure branch (because ``all(k == "search_hit")`` is False).
        """
        h1 = self._handle("a")
        h2 = self._handle("b")
        artifact = RawArtifact(
            case_id="case-a",
            run_id="phase1-test",
            finalized_status="ok",
            final_text="answer",
            cited_evidence_handles=[h1, h2],
            all_evidence_observations=[
                RawEvidenceObservation(
                    handle_id=h1,
                    kind="article_seed",
                    snippet="baseline seed",
                    provenance="baseline_context",
                ),
                RawEvidenceObservation(
                    handle_id=h2,
                    kind="article_seed",
                    snippet="baseline seed 2",
                    provenance="baseline_context",
                ),
            ],
            baseline_is_complete=True,
        )
        result = evaluate_evidence_minimality(_make_case(), artifact)
        # 2 handles, no duplicates, all resolved, NOT all search_hit.
        assert result.passed is True
        assert result.severity == "none"

    def test_legal_observation_all_three_provenances_pass(
        self
    ) -> None:
        """All three legal ``observation + <provenance>`` pairs MUST
        construct and pass through the evaluator without raising.
        """
        h1 = self._handle("a")
        h2 = self._handle("b")
        h3 = self._handle("c")
        artifact = RawArtifact(
            case_id="case-a",
            run_id="phase1-test",
            finalized_status="ok",
            final_text="answer",
            cited_evidence_handles=[h1, h2, h3],
            all_evidence_observations=[
                RawEvidenceObservation(
                    handle_id=h1,
                    kind="observation",
                    snippet="obs from initial_anchor",
                    provenance="initial_anchor",
                ),
                RawEvidenceObservation(
                    handle_id=h2,
                    kind="observation",
                    snippet="obs from read_range",
                    provenance="read_range",
                ),
                RawEvidenceObservation(
                    handle_id=h3,
                    kind="observation",
                    snippet="obs from search",
                    provenance="search_current_article",
                ),
            ],
            baseline_is_complete=True,
        )
        result = evaluate_evidence_minimality(_make_case(), artifact)
        # 3 handles, no duplicates, all resolved, NOT all search_hit.
        assert result.passed is True
        assert result.severity == "none"


# ===========================================================================
# SECTION 6: Content-quality errors preserved for evaluator
# ===========================================================================


class TestContentQualityPreservedForEvaluator:
    """Spec section 四: content-quality errors (duplicate handles,
    unknown handles) MUST be preserved for the evaluator to fail.
    The cross-field invariant MUST NOT reject these — it only rejects
    contract corruption (illegal kind/provenance pairs).
    """

    def _handle(self, suffix: str = "a") -> str:
        return "evh_" + suffix + "0" * 28

    def test_duplicate_cited_handles_preserved_for_evaluator_fail(
        self
    ) -> None:
        """Duplicate cited handles are CONTENT quality — schema
        preserves them, evaluator fails.
        """
        h = self._handle("a")
        artifact = RawArtifact(
            case_id="case-a",
            run_id="phase1-test",
            finalized_status="ok",
            final_text="answer",
            cited_evidence_handles=[h, h],  # duplicate
            all_evidence_observations=[
                RawEvidenceObservation(
                    handle_id=h,
                    kind="article_seed",
                    snippet="s",
                    provenance="baseline_context",
                )
            ],
            baseline_is_complete=True,
        )
        result = evaluate_evidence_minimality(_make_case(), artifact)
        assert result.passed is False
        assert result.severity == "high"
        assert "duplicate" in result.details

    def test_unknown_cited_handle_preserved_for_evaluator_fail(
        self
    ) -> None:
        """Unknown cited handles are CONTENT quality — schema
        preserves them, evaluator fails.
        """
        known = self._handle("a")
        unknown = self._handle("z")
        artifact = RawArtifact(
            case_id="case-a",
            run_id="phase1-test",
            finalized_status="ok",
            final_text="answer",
            cited_evidence_handles=[known, unknown],  # unknown
            all_evidence_observations=[
                RawEvidenceObservation(
                    handle_id=known,
                    kind="article_seed",
                    snippet="s",
                    provenance="baseline_context",
                )
            ],
            baseline_is_complete=True,
        )
        result = evaluate_evidence_minimality(_make_case(), artifact)
        assert result.passed is False
        assert result.severity == "high"
        assert "not in observations" in result.details


# ===========================================================================
# SECTION 7: Non-regression — normal complete artifact
# ===========================================================================


class TestNormalArtifactNonRegression:
    """A normal, schema-valid artifact with mixed legal evidence kinds
    MUST pass through the full evaluator path without regression.
    """

    def test_normal_mixed_evidence_artifact_evaluates_cleanly(
        self
    ) -> None:
        """An artifact with one ``article_seed`` and one ``search_hit``
        — both legal pairs — evaluates cleanly through
        :func:`evaluate_evidence_minimality`.
        """
        h1 = "evh_a" + "0" * 29
        h2 = "evh_b" + "0" * 29
        artifact = RawArtifact(
            case_id="case-a",
            run_id="phase1-test",
            run_index=0,
            dataset_id="test-dataset",
            dataset_schema_version="test-schema-v1",
            dataset_content_sha256=_VALID_SHA,
            budget_exhausted=False,
            finalized_status="ok",
            response_kind="grounded_answer",
            baseline_status="injected",
            baseline_is_complete=True,
            baseline_is_injected=True,
            read_range_calls=1,
            search_current_article_calls=1,
            cited_evidence_handles=[h1, h2],
            all_evidence_observations=[
                RawEvidenceObservation(
                    handle_id=h1,
                    kind="article_seed",
                    snippet="article snippet",
                    provenance="baseline_context",
                ),
                RawEvidenceObservation(
                    handle_id=h2,
                    kind="search_hit",
                    snippet="search result",
                    provenance="search_current_article",
                ),
            ],
        )
        result = evaluate_evidence_minimality(_make_case(), artifact)
        assert result.passed is True
        assert result.severity == "none"


# ===========================================================================
# SECTION 8: All 20 combinations accounted for (exhaustive guard)
# ===========================================================================


class TestAllCombinationsAccountedFor:
    """Exhaustive guard: the union of LEGAL_PAIRS and ILLEGAL_PAIRS
    MUST cover the entire 5×4 cartesian product. This catches drift
    if a new kind or provenance is added to the Literals without
    updating the test lists.
    """

    def test_all_20_cartesian_pairs_are_classified(self) -> None:
        """Every (kind, provenance) cartesian pair MUST appear in
        either LEGAL_PAIRS or ILLEGAL_PAIRS — no orphans.
        """
        all_kinds = {
            "initial_anchor",
            "read_range",
            "search_hit",
            "observation",
            "article_seed",
        }
        all_provenances = {
            "initial_anchor",
            "read_range",
            "search_current_article",
            "baseline_context",
        }
        cartesian = {
            (k, p) for k in all_kinds for p in all_provenances
        }
        classified = set(LEGAL_PAIRS) | set(ILLEGAL_PAIRS)
        assert cartesian == classified, (
            f"orphan pairs: {cartesian - classified}; "
            f"extra pairs: {classified - cartesian}"
        )

    def test_legal_and_illegal_lists_are_disjoint(self) -> None:
        """No pair may appear in both LEGAL_PAIRS and ILLEGAL_PAIRS."""
        overlap = set(LEGAL_PAIRS) & set(ILLEGAL_PAIRS)
        assert overlap == set(), f"overlap: {overlap}"
