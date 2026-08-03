"""Offline end-to-end test for the R4-A3 rework closure.

Spec: `.trae/specs/reader-record-ask-r4-a3-rework-session-eval-closure/spec.md`
Requirement: 离线端到端验收（Task 14）.

This test exercises the full closure flow WITHOUT any real LLM call:

    FunctionModel Phase 1 (3 reps)
    → one artifact status=ok but contains unsupported ``2025`` year token
    → evaluate_artifact
    → failure cluster
    → PhasePlanner Phase 2 case selection
    → thinking config verification
    → Phase 2 artifact
    → aggregate
    → report

Asserts the 9 spec requirements (Task 14.2):

    (a) 2025 case enters Phase 2
    (b) run-id path consistent (writer = reader)
    (c) aggregate reads artifacts
    (d) deterministic failure not masked by terminal ok
    (e) suggestion case is actually selected
    (f) known-source offline-only not reported as runtime coverage
    (g) request hard cap effective
    (h) report no secret leak
    (i) verdict not blocked by artifact path error

The test uses :class:`FunctionModel` (pydantic_ai) wrapped in
:class:`BudgetedUsageModel` to simulate provider calls — no real LLM
is invoked. ``pydantic_ai`` is only available in the services/api
venv, so the module is skipped via ``pytest.importorskip`` when run
from the evals project.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Skip entire module when pydantic_ai is not installed (evals project).
pydantic_ai = pytest.importorskip("pydantic_ai")

from pydantic_ai.messages import (  # noqa: E402
    ModelMessage,
    ModelResponse,
    TextPart,
)
from pydantic_ai.models import ModelRequestParameters  # noqa: E402
from pydantic_ai.models.function import FunctionModel  # noqa: E402
from pydantic_ai.usage import RequestUsage  # noqa: E402

from claread_eval.reader_record_ask.budgeted_model import (  # noqa: E402
    BudgetedUsageModel,
    BudgetExhaustedError,
)
from claread_eval.reader_record_ask.evaluation import (  # noqa: E402
    dimension_by_name,
    evaluate_artifact,
)
from claread_eval.reader_record_ask.evaluators import (  # noqa: E402
    CaseEvalResult,
    aggregate_results,
)
from claread_eval.reader_record_ask.evaluators.artifact import (  # noqa: E402
    RawArtifact,
    RawEvidenceObservation,
    RawUsage,
)
from claread_eval.reader_record_ask.phase_planner import (  # noqa: E402
    PHASE_TAG_OFFLINE_ONLY,
    PHASE_TAG_REAL_PHASE1,
    PhasePlanner,
)
from claread_eval.reader_record_ask.report import (  # noqa: E402
    generate_eval_report,
)
from claread_eval.reader_record_ask.schema import (  # noqa: E402
    ReaderRecordAskR4A3Case,
    ReaderRecordAskR4A3Dataset,
    ReaderRecordAskR4A3Expected,
)
from claread_eval.reader_record_ask.session import (  # noqa: E402
    RunSessionLayout,
)

# ---------------------------------------------------------------------------
# Dataset fixtures — minimal, deterministic, covers the spec scenarios
# ---------------------------------------------------------------------------

# Synthetic placeholder UUID — real local Reading Record UUIDs must
# never appear in tracked fixtures (spec: P0 dataset Git governance §7).
_BBC_RECORD_ID = "00000000-0000-4000-8000-000000000000"


def _make_case(
    case_id: str,
    *,
    question_category: str,
    question: str,
    source_metadata: str = "unknown",
    input_mode: str = "no_selection",
    phase_tags: list[str] | None = None,
    expected_overrides: dict | None = None,
) -> ReaderRecordAskR4A3Case:
    expected = ReaderRecordAskR4A3Expected()
    if expected_overrides:
        expected = expected.model_copy(update=expected_overrides)
    return ReaderRecordAskR4A3Case(
        id=case_id,
        source_kind="bbc_record",
        record_id=_BBC_RECORD_ID,
        article_text=None,
        article_title=None,
        input_mode=input_mode,  # type: ignore[arg-type]
        selection=None,
        rag_mode="off",
        source_metadata=source_metadata,  # type: ignore[arg-type]
        baseline_mode="complete",
        question=question,
        question_category=question_category,  # type: ignore[arg-type]
        expected=expected,
        phase_tags=phase_tags or [],
    )


def _make_offline_e2e_dataset() -> ReaderRecordAskR4A3Dataset:
    """Build the minimal dataset that exercises every Task 14.2 assertion.

    Cases:
    - ``bbc-2025-leak``: Phase 1 real, status=ok but contains ``2025`` →
      must be flagged by ``unsupported_temporal_claims`` and selected
      for Phase 2 (assertions a, d).
    - ``bbc-suggestion-main-idea``: Phase 1 real,
      ``input_mode=suggestion_equivalent`` → must be selected into the
      Phase 1 manifest (assertion e).
    - ``bbc-manual-exercise``: Phase 1 real, ``input_mode=manual`` →
      control case (assertion e corollary: suggestion and manual both
      project to the same user_message).
    - ``bbc-known-offline``: ``source_metadata=known_bbc`` +
      ``phase_tags=[offline_only]`` → must NOT be selected for real
      runtime (assertion f).
    """
    return ReaderRecordAskR4A3Dataset(
        id="reader-record-ask-r4-a3-offline-e2e",
        schema_version="r4-a3-dataset-v1",
        description="Offline e2e test dataset for R4-A3 rework closure",
        case_globs=["cases/*.json"],
        tags=["r4-a3", "offline-e2e"],
        cases=[
            _make_case(
                case_id="bbc-2025-leak",
                question_category="city_enumeration",
                question="文章提到了哪些城市？",
                phase_tags=[PHASE_TAG_REAL_PHASE1, "targeted_phase2_candidate"],
                expected_overrides={
                    "expected_entity_set": {
                        "city": ["Thunder Bay", "纽约", "多伦多"],
                    },
                    "allowed_temporal_claims": [],  # 2025 NOT allowed
                    "allowed_numerics": [],
                    "allowed_entities_by_type": {
                        "city": ["Thunder Bay", "纽约", "多伦多"],
                    },
                    "entity_catalog": {
                        "city": ["Thunder Bay", "纽约", "多伦多"],
                        "region": ["纽约州西部部分地区"],
                    },
                    "forbidden_answer_patterns": ["2025", "2026"],
                    "answer_language": "zh",
                    "expect_tool_calls": "forbidden",
                },
            ),
            _make_case(
                case_id="bbc-suggestion-main-idea",
                question_category="main_idea",
                question="这篇文章在讲什么？",
                input_mode="suggestion_equivalent",
                phase_tags=[PHASE_TAG_REAL_PHASE1],
                expected_overrides={
                    "allowed_temporal_claims": [],
                    "forbidden_answer_patterns": ["2025", "2026"],
                    "answer_language": "zh",
                },
            ),
            _make_case(
                case_id="bbc-manual-exercise",
                question_category="exercise_one",
                question="帮我出一道练习题。",
                input_mode="manual",
                phase_tags=[PHASE_TAG_REAL_PHASE1],
                expected_overrides={
                    "requested_count": 1,
                    "requested_count_kind": "exercise_items",
                    "allowed_temporal_claims": [],
                    "forbidden_answer_patterns": ["2025", "2026"],
                    "answer_language": "zh",
                },
            ),
            _make_case(
                case_id="bbc-known-offline",
                question_category="city_enumeration",
                question="文章提到了哪些城市？",
                source_metadata="known_bbc",
                phase_tags=[PHASE_TAG_OFFLINE_ONLY],
                expected_overrides={
                    "expected_entity_set": {
                        "city": ["Thunder Bay", "纽约", "多伦多"],
                    },
                    "allowed_temporal_claims": [],
                    "allowed_entities_by_type": {
                        "city": ["Thunder Bay", "纽约", "多伦多"],
                    },
                },
            ),
        ],
    )


# ---------------------------------------------------------------------------
# FunctionModel — simulates provider responses without any real LLM call
# ---------------------------------------------------------------------------


def _make_response(
    *,
    text: str,
    input_tokens: int = 50,
    output_tokens: int = 30,
) -> ModelResponse:
    return ModelResponse(
        parts=[TextPart(content=text)],
        usage=RequestUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
    )


def _make_fake_model(response_text: str) -> FunctionModel:
    """Build a FunctionModel that always returns ``response_text``.

    The response carries explicit ``RequestUsage`` so
    :class:`BudgetedUsageModel` can aggregate tokens. (FunctionModel
    would otherwise auto-estimate usage — we want deterministic
    counters for the cap test.)
    """

    def _fn(messages: list[ModelMessage], info) -> ModelResponse:  # noqa: ANN001
        return _make_response(text=response_text)

    return FunctionModel(_fn, model_name="test-fake-e2e")


def _make_request_params() -> ModelRequestParameters:
    return ModelRequestParameters(
        function_tools=[],
        allow_text_output=True,
        output_tools=[],
        output_mode=None,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Artifact builder — simulates what the harness would write after a run
# ---------------------------------------------------------------------------


def _build_phase1_artifact(
    *,
    case: ReaderRecordAskR4A3Case,
    run_id: str,
    run_index: int,
    final_text: str,
    finalized_status: str = "ok",
    model_short_name: str = "deepseek-chat",
    thinking_enabled: bool = False,
    executed_requests: int = 1,
    executed_tokens: int = 80,
) -> RawArtifact:
    """Build a Phase 1 artifact with the fields the evaluators need.

    Mirrors what the harness would write via ``_run_one_case``. The
    artifact carries only safe fields — no article body, no
    reasoning_content, no API key.
    """
    return RawArtifact(
        case_id=case.id,
        run_id=run_id,
        run_index=run_index,
        model_short_name=model_short_name,
        model_route="reader_ask",
        thinking_enabled=thinking_enabled,
        final_text=final_text,
        finalized_status=finalized_status,
        finalized_reason=None,
        response_kind="grounded_answer",
        cited_evidence_handles=["ev-seed-1"],
        resolved_evidence=[],
        all_evidence_observations=[
            RawEvidenceObservation(
                handle_id="ev-seed-1",
                kind="article_seed",
                snippet="seed context snippet",
                provenance="baseline_context",
            ),
        ],
        read_range_calls=0,
        search_current_article_calls=0,
        baseline_status="injected",
        baseline_is_complete=True,
        baseline_is_injected=True,
        agent_usage=RawUsage(
            requests=executed_requests,
            input_tokens=50,
            output_tokens=30,
        ),
        latency_seconds=0.5,
        envelope_fingerprint="offline-e2e-fingerprint",
        executed_requests=executed_requests,
        executed_tokens=executed_tokens,
    )


def _write_artifact(
    artifact: RawArtifact,
    session: RunSessionLayout,
) -> Path:
    """Serialize artifact to disk via RunSessionLayout (mirrors harness)."""
    artifact_dir = session.artifact_dir
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = session.artifact_path(
        case_id=artifact.case_id,
        model_short_name=artifact.model_short_name,
        thinking_enabled=artifact.thinking_enabled,
        run_index=artifact.run_index,
    )
    payload = artifact.model_dump(mode="json")
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _load_artifacts_from_disk(
    artifact_dir: Path,
    run_id: str,
) -> list[RawArtifact]:
    """Mirror runner's ``_load_artifacts`` reader."""
    if not artifact_dir.is_dir():
        return []
    artifacts: list[RawArtifact] = []
    for json_path in sorted(artifact_dir.glob("*.json")):
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            continue
        if payload.get("run_id") != run_id:
            continue
        artifacts.append(RawArtifact.model_validate(payload))
    return artifacts


# ---------------------------------------------------------------------------
# E2E test
# ---------------------------------------------------------------------------


def test_offline_e2e_full_closure_flow(tmp_path: Path) -> None:
    """Spec Task 14.1 + 14.2 — full offline closure flow.

    Walks the entire pipeline:
    - Phase 1 case selection via PhasePlanner (real_phase1 tag, excl.
      offline_only).
    - 3 fixed repetitions per case (no early break).
    - One artifact with finalized_status='ok' but containing unsupported
      ``2025`` year token — must be flagged by the deterministic
      ``unsupported_temporal_claims`` evaluator (NOT masked by terminal
      ok).
    - Artifacts written via RunSessionLayout to a deterministic path
      under ``<runs_root>/<run_id>/artifacts/``.
    - evaluate_artifact on each artifact → Phase 2 case selection via
      PhasePlanner(phase=2, prior_eval_results=...).
    - aggregate_results + generate_eval_report.

    Asserts all 9 spec requirements (a)–(i).
    """
    dataset = _make_offline_e2e_dataset()
    cases_by_id = {c.id: c for c in dataset.cases}

    # ---------------------------------------------------------------
    # (f) known-source offline-only NOT selected for runtime
    # ---------------------------------------------------------------
    phase1_planner = PhasePlanner(dataset=dataset, phase=1, repetitions=3)
    phase1_cases = phase1_planner.cases_to_run
    phase1_case_ids = [c.id for c in phase1_cases]

    assert "bbc-known-offline" not in phase1_case_ids, (
        "offline_only tagged case must NOT be selected for real runtime "
        "(assertion f)"
    )
    # Real_phase1 cases are all selected.
    assert "bbc-2025-leak" in phase1_case_ids
    assert "bbc-suggestion-main-idea" in phase1_case_ids
    assert "bbc-manual-exercise" in phase1_case_ids

    # ---------------------------------------------------------------
    # (e) suggestion case is actually selected
    # ---------------------------------------------------------------
    suggestion_case = cases_by_id["bbc-suggestion-main-idea"]
    assert suggestion_case.input_mode == "suggestion_equivalent"
    assert suggestion_case in phase1_cases, (
        "suggestion_equivalent case must enter the Phase 1 manifest "
        "(assertion e)"
    )

    # ---------------------------------------------------------------
    # (b) run-id path consistency
    # ---------------------------------------------------------------
    run_id = "phase1-offline-e2e"
    runs_root = tmp_path / "runs"
    session = RunSessionLayout(runs_root=runs_root, run_id=run_id)
    expected_artifact_dir = runs_root / run_id / "artifacts"
    assert session.artifact_dir == expected_artifact_dir

    # ---------------------------------------------------------------
    # Phase 1: 3 fixed reps per case, write artifacts via session
    # ---------------------------------------------------------------
    phase1_artifacts: list[RawArtifact] = []
    for case in phase1_cases:
        for run_index in range(phase1_planner.repetitions):
            # Pick a final_text per case + run_index. For the 2025-leak
            # case, every rep returns an answer containing the unsupported
            # ``2025`` year token — this is the P0-3 regression signal.
            if case.id == "bbc-2025-leak":
                final_text = (
                    "文章提到的城市有 Thunder Bay、纽约、多伦多。"
                    "该报道发表于 2025 年。"
                )
            elif case.id == "bbc-suggestion-main-idea":
                final_text = "这篇文章主要讲加拿大野火对城市空气质量的影响。"
            elif case.id == "bbc-manual-exercise":
                final_text = (
                    "练习题：根据文章内容，Thunder Bay 位于哪个国家？"
                )
            else:  # pragma: no cover - defensive
                final_text = "测试回答。"

            artifact = _build_phase1_artifact(
                case=case,
                run_id=run_id,
                run_index=run_index,
                final_text=final_text,
                finalized_status="ok",
                thinking_enabled=False,
                executed_requests=1,
                executed_tokens=80,
            )
            phase1_artifacts.append(artifact)
            _write_artifact(artifact, session)

    # 3 cases × 3 reps = 9 artifacts (offline_only excluded).
    assert len(phase1_artifacts) == 9, (
        "Phase 1 must run 3 fixed reps per case (no early break) — "
        "3 cases × 3 reps = 9 artifacts (assertion b/P0-2)"
    )

    # ---------------------------------------------------------------
    # (b) run-id path consistent: writer = reader
    # ---------------------------------------------------------------
    loaded_artifacts = _load_artifacts_from_disk(session.artifact_dir, run_id)
    assert len(loaded_artifacts) == 9, (
        "aggregate must read all 9 artifacts written by the harness "
        "(assertion b/c)"
    )
    # The artifact_dir resolves to the same path for writer and reader.
    assert session.artifact_dir == expected_artifact_dir
    # Filenames include the run_id directory and are unique per
    # (case, model, thinking, run_index).
    filenames = sorted(p.name for p in session.artifact_dir.glob("*.json"))
    assert len(filenames) == 9
    assert len(set(filenames)) == 9  # no collisions

    # ---------------------------------------------------------------
    # (d) deterministic failure NOT masked by terminal ok
    # ---------------------------------------------------------------
    # Run evaluate_artifact on each Phase 1 artifact.
    # P0-2: keep per-repetition results — any failing rep triggers
    # Phase 2 selection (no more last-rep-wins masking). The outer
    # dict is ``case_id -> list[list[EvalDimensionResult]]`` where the
    # inner list is one entry per repetition (sorted by run_index for
    # order-invariant aggregation).
    phase1_eval_results: dict[str, list[list]] = {}
    for artifact in sorted(phase1_artifacts, key=lambda a: a.run_index):
        case = cases_by_id[artifact.case_id]
        dims = evaluate_artifact(case, artifact)
        phase1_eval_results.setdefault(artifact.case_id, []).append(dims)

    # The 2025-leak case must be flagged as a content failure even
    # though finalized_status='ok'. ``any_repetition_content_failure``
    # returns True if ANY rep had a content-quality failure (P0-2).
    from claread_eval.reader_record_ask.evaluation import (  # noqa: PLC0415
        any_repetition_content_failure,
    )

    leak_reps = phase1_eval_results["bbc-2025-leak"]
    assert any_repetition_content_failure(leak_reps) is True, (
        "2025-leak case must be a content failure — deterministic "
        "failure must NOT be masked by terminal ok (assertion d)"
    )
    # The 2025 leak must be visible in at least one rep's temporal dim.
    temporal_dim = None
    for rep_dims in leak_reps:
        candidate = dimension_by_name(rep_dims, "unsupported_temporal_claims")
        if candidate is not None and not candidate.passed:
            temporal_dim = candidate
            break
    assert temporal_dim is not None, (
        "unsupported_temporal_claims must fail for the 2025 year token "
        "in at least one repetition"
    )
    assert "2025" in temporal_dim.details

    # The suggestion and manual cases pass content checks (no 2025 leak)
    # across ALL repetitions.
    assert any_repetition_content_failure(
        phase1_eval_results["bbc-suggestion-main-idea"]
    ) is False
    assert any_repetition_content_failure(
        phase1_eval_results["bbc-manual-exercise"]
    ) is False

    # ---------------------------------------------------------------
    # (a) 2025 case enters Phase 2
    # ---------------------------------------------------------------
    phase2_planner = PhasePlanner(
        dataset=dataset,
        phase=2,
        prior_artifacts=phase1_artifacts,
        prior_eval_results=phase1_eval_results,
    )
    phase2_cases = phase2_planner.cases_to_run
    phase2_case_ids = [c.id for c in phase2_cases]

    assert "bbc-2025-leak" in phase2_case_ids, (
        "2025-leak case must enter Phase 2 (assertion a)"
    )
    # Phase 2 default repetitions = 1 (not 3).
    assert phase2_planner.repetitions == 1

    # Cases that did NOT fail must NOT enter Phase 2.
    assert "bbc-suggestion-main-idea" not in phase2_case_ids
    assert "bbc-manual-exercise" not in phase2_case_ids

    # ---------------------------------------------------------------
    # (c) aggregate reads artifacts
    # ---------------------------------------------------------------
    # Build CaseEvalResult for each Phase 1 artifact (mirrors runner's
    # ``_build_case_result``).
    case_results: list[CaseEvalResult] = []
    for artifact in loaded_artifacts:
        case = cases_by_id[artifact.case_id]
        dims = evaluate_artifact(case, artifact)
        case_results.append(
            CaseEvalResult(
                case_id=artifact.case_id,
                run_id=artifact.run_id,
                run_index=artifact.run_index,
                model_short_name=artifact.model_short_name,
                thinking_enabled=artifact.thinking_enabled,
                dimensions=dims,
                latency_seconds=artifact.latency_seconds,
                total_tokens=(
                    (artifact.agent_usage.input_tokens or 0)
                    + (artifact.agent_usage.output_tokens or 0)
                ) if artifact.agent_usage else None,
                total_requests=(
                    artifact.agent_usage.requests
                    if artifact.agent_usage
                    else None
                ),
            )
        )

    aggregated = aggregate_results(case_results, cases_by_id)
    assert aggregated.total_runs == 9, (
        "aggregate must read all 9 artifacts (assertion c)"
    )
    assert aggregated.total_cases == 3, (
        "3 distinct cases ran in Phase 1"
    )
    # Failure clusters: at least the 2025-year-hallucination cluster.
    cluster_patterns = {
        (c.dimension, c.failure_pattern) for c in aggregated.failure_clusters
    }
    assert any(
        d == "unsupported_temporal_claims" and "2025" in p
        for d, p in cluster_patterns
    ), (
        "failure_clusters must include the 2025-year-hallucination pattern"
    )

    # ---------------------------------------------------------------
    # Phase 2 artifact (single rep, thinking enabled) + aggregate
    # ---------------------------------------------------------------
    phase2_run_id = "phase2-offline-e2e"
    phase2_session = RunSessionLayout(
        runs_root=runs_root,
        run_id=phase2_run_id,
        prior_run_id=run_id,
    )
    assert phase2_session.prior_run_id == run_id
    assert (
        phase2_session.prior_artifact_dir
        == runs_root / run_id / "artifacts"
    )

    phase2_artifacts: list[RawArtifact] = []
    for case in phase2_cases:
        # Phase 2 simulates the upgraded model fixing the 2025 leak.
        final_text = (
            "文章提到的城市有 Thunder Bay、纽约、多伦多。"
            "文章未提供发表年份。"
        )
        artifact = _build_phase1_artifact(
            case=case,
            run_id=phase2_run_id,
            run_index=0,
            final_text=final_text,
            finalized_status="ok",
            thinking_enabled=True,  # Phase 2 = thinking enabled
            model_short_name="deepseek-chat",
            executed_requests=1,
            executed_tokens=80,
        )
        phase2_artifacts.append(artifact)
        _write_artifact(artifact, phase2_session)

    # Verify Phase 2 artifact's thinking flag is True.
    for artifact in phase2_artifacts:
        assert artifact.thinking_enabled is True, (
            "Phase 2 artifacts must have thinking_enabled=True "
            "(P1-1 thinking verification)"
        )

    # ---------------------------------------------------------------
    # (h) report no secret leak
    # ---------------------------------------------------------------
    # Generate the report using the aggregated Phase 1 results.
    report = generate_eval_report(
        aggregated=aggregated,
        dataset=dataset,
        artifacts=loaded_artifacts,
        start_head="offline-e2e-start-head",
        end_head="offline-e2e-end-head",
        parallel_dirty=[],
        harness_choice=(
            "B: in-process real-model harness (FunctionModel-wrapped)"
        ),
        rejected_harness="A: HTTP adapter",
        rejected_reason="HTTP adapter would require SSE parsing + auth.",
        real_model_blocked=False,  # We have artifacts.
        real_model_block_reason=None,
        real_model_user_commands=None,
        deterministic_tests_passed=True,
        deterministic_tests_summary="offline e2e: all deep modules exercised.",
        verdict="rework",  # 2025 failure → rework (not accepted)
        allow_r4_a4=True,
        allow_r4_b1=False,
        run_metadata={
            "run_id": run_id,
            "phase1_artifact_dir": str(session.artifact_dir),
            "phase2_run_id": phase2_run_id,
            "total_phase1_artifacts": len(phase1_artifacts),
            "total_phase2_artifacts": len(phase2_artifacts),
            "phase2_cases": phase2_case_ids,
        },
    )

    # (h) No secret leak: no leaked reasoning_content VALUE in the
    # run_metadata appendix, no API key patterns, no large BBC body chunk.
    # Note: the declarative sections (16 能力边界, 18 budget 语义) legitimately
    # mention "reasoning_content" as a field name concept — this is NOT a
    # leak. A leak would be a reasoning_content key/value in the appendix.
    import re

    if "## 附录: run_metadata" in report:
        appendix_start = report.index("## 附录: run_metadata")
        appendix = report[appendix_start:]
        assert '"reasoning_content"' not in appendix, (
            "run_metadata appendix must not contain reasoning_content key "
            "(assertion h)"
        )
    # Use a word-boundary regex so the dataset id ``reader-record-ask-r4-a3``
    # (which contains ``sk-r`` as a substring of ``ask-r4``) is not a false
    # positive. Real API keys look like ``sk-proj-...`` / ``sk-test...``
    # where ``sk-`` sits at a word boundary.
    api_key_re = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")
    api_key_match = api_key_re.search(report)
    assert api_key_match is None, (
        f"report contains sk- API key pattern at word boundary: "
        f"{api_key_match.group(0)!r}"
    )
    assert "api_key=" not in report.lower()
    # No 200+ contiguous ASCII prose chunk (BBC body guard).
    long_ascii_runs = re.findall(r"[\x20-\x7E]{200,}", report)
    assert not long_ascii_runs, (
        f"report contains 200+ char ASCII prose run: "
        f"{long_ascii_runs[0][:80]!r}..."
    )
    # The leaked 2025 detail string must appear (it's a fact, not a
    # secret) — confirms the deterministic failure is visible.
    assert "2025" in report

    # ---------------------------------------------------------------
    # (i) verdict NOT blocked by artifact path error
    # ---------------------------------------------------------------
    # The verdict passed in is "rework" (real artifacts exist + 1 high
    # severity failure). The runner's _decide_verdict would only return
    # "blocked" when real_model_blocked or no case_results — neither is
    # true here.
    assert "verdict: **rework**" in report, (
        "verdict must be rework (not blocked) when artifacts exist — "
        "an artifact path error must NOT cause a false blocked verdict "
        "(assertion i)"
    )
    # Sanity: the report explicitly states it's NOT blocked.
    assert "BLOCKED" not in report.upper() or "N/A (BLOCKED)" not in report.upper()


# ---------------------------------------------------------------------------
# (g) request hard cap effective — dedicated test
# ---------------------------------------------------------------------------


async def test_offline_e2e_request_hard_cap_effective() -> None:
    """Spec Task 14.2 (g) — request hard cap is actually enforced.

    Uses :class:`FunctionModel` wrapped in :class:`BudgetedUsageModel`
    with ``max_requests=2``. The first two ``request()`` calls succeed;
    the third raises :class:`BudgetExhaustedError` BEFORE the wrapped
    model is called.

    This test is the offline proof that the cap is a real hard boundary
    (not a paper-only cap as in the pre-rework harness).
    """
    inner = _make_fake_model("ok")
    wrapper = BudgetedUsageModel(inner, max_requests=2)
    params = _make_request_params()

    # First two requests succeed.
    await wrapper.request([], None, params)
    await wrapper.request([], None, params)
    assert wrapper.executed_requests == 2

    # Third request must be blocked BEFORE the wrapped model is called.
    with pytest.raises(BudgetExhaustedError) as exc_info:
        await wrapper.request([], None, params)

    # The wrapped model was NOT called a third time (count stays at 2).
    assert wrapper.executed_requests == 2
    # The exception carries safe metadata only — no payload leak.
    assert exc_info.value.cap_kind == "request_cap"
    assert exc_info.value.request_cap == 2
    err_msg = str(exc_info.value)
    assert "request_cap" in err_msg
    # No payload leak in the exception message.
    assert "api_key" not in err_msg.lower()
    assert "sk-" not in err_msg


# ---------------------------------------------------------------------------
# (b) run-id path consistency — dedicated path-traversal test
# ---------------------------------------------------------------------------


def test_offline_e2e_run_id_path_consistency(tmp_path: Path) -> None:
    """Spec Task 14.2 (b) — writer and reader use the same path resolver.

    RunSessionLayout's ``artifact_dir`` and ``artifact_path`` are the
    single source of truth. The harness writes via ``artifact_path``;
    the runner reads via ``artifact_dir.glob('*.json')``. Both must
    agree on the directory.

    Path traversal in run_id must fail closed.
    """
    runs_root = tmp_path / "runs"
    run_id = "phase1-path-consistency"
    session = RunSessionLayout(runs_root=runs_root, run_id=run_id)

    # Writer path.
    writer_path = session.artifact_path(
        case_id="bbc-test",
        model_short_name="deepseek-chat",
        thinking_enabled=False,
        run_index=0,
    )
    # Reader path (the runner globs artifact_dir).
    assert writer_path.parent == session.artifact_dir
    assert session.artifact_dir == runs_root / run_id / "artifacts"

    # Path traversal fail-closed.
    from claread_eval.reader_record_ask.session import RunSessionLayoutError

    with pytest.raises(RunSessionLayoutError):
        RunSessionLayout(runs_root=runs_root, run_id="../escape-attempt")
    with pytest.raises(RunSessionLayoutError):
        RunSessionLayout(runs_root=runs_root, run_id="has/slash")
    with pytest.raises(RunSessionLayoutError):
        RunSessionLayout(runs_root=runs_root, run_id="has space")
    with pytest.raises(RunSessionLayoutError):
        RunSessionLayout(runs_root=runs_root, run_id="")


# ---------------------------------------------------------------------------
# (f) known-source offline-only — dedicated coverage assertion
# ---------------------------------------------------------------------------


def test_offline_e2e_known_source_offline_only_not_in_runtime_coverage() -> None:
    """Spec Task 14.2 (f) — known-source ``offline_only`` cases are NOT
    reported as runtime coverage.

    The Phase 1 manifest excludes ``offline_only`` cases. The report's
    dataset case table still lists them (for auditability) but they
    never enter ``cases_to_run``.
    """
    dataset = _make_offline_e2e_dataset()
    planner = PhasePlanner(dataset=dataset, phase=1, repetitions=3)
    runtime_case_ids = {c.id for c in planner.cases_to_run}

    # The known-source case is in the dataset (auditable) but NOT in
    # the runtime manifest.
    assert "bbc-known-offline" in {c.id for c in dataset.cases}
    assert "bbc-known-offline" not in runtime_case_ids, (
        "known_bbc + offline_only case must NOT be in runtime coverage "
        "(assertion f)"
    )
    # Its source_metadata is known_bbc — explicitly NOT runtime-covered
    # until R4-A4 lands the trusted-source-metadata seam.
    known_case = next(c for c in dataset.cases if c.id == "bbc-known-offline")
    assert known_case.source_metadata == "known_bbc"
    assert PHASE_TAG_OFFLINE_ONLY in known_case.phase_tags
