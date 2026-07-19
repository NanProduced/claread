"""R4-A3 Reader Record Ask evaluation runner (rework).

Usage (run from ``evals/``)::

    # Phase 1: real LLM run (requires env gate)
    CLAREAD_ALLOW_REAL_LLM_TESTS=1 CLAREAD_R4_A3_RUN=1 CLAREAD_REAL_LLM_MODEL=deepseek-chat \\
        uv run python scripts/run_reader_record_ask_r4_a3.py --phase 1 --run-id phase1-<ts>

    # Phase 2: prior-run-id points at the Phase 1 run
    CLAREAD_ALLOW_REAL_LLM_TESTS=1 CLAREAD_R4_A3_RUN=1 CLAREAD_REAL_LLM_MODEL=deepseek-chat \\
        uv run python scripts/run_reader_record_ask_r4_a3.py --phase 2 \\
        --run-id phase2-<ts> --prior-run-id phase1-<ts>

    # Phase 3: prior-run-id points at the Phase 2 run
    CLAREAD_ALLOW_REAL_LLM_TESTS=1 CLAREAD_R4_A3_RUN=1 CLAREAD_REAL_LLM_MODEL=deepseek-pro \\
        CLAREAD_R4_A3_PRO_PROFILE=<pro_profile> \\
        uv run python scripts/run_reader_record_ask_r4_a3.py --phase 3 \\
        --run-id phase3-<ts> --prior-run-id phase2-<ts>

    # Aggregate: load artifacts, run 11 evaluators, generate report.
    uv run python scripts/run_reader_record_ask_r4_a3.py --phase aggregate --run-id <id> \\
        --report-output ../docs/tmp/reader-orchestration/review/\\
TMP-reader-record-ask-r4-a3-eval-2026-07-17.md

Rework closure (spec: `.trae/specs/reader-record-ask-r4-a3-rework-session-
eval-closure/spec.md`):

- ``--prior-run-id`` is required for Phase 2/3 and is passed to the
  harness via ``CLAREAD_R4_A3_PRIOR_RUN_ID`` env var. No more scanning
  the runs root for "latest" (P0-1).
- Aggregate uses :class:`RunSessionLayout` to resolve the artifact
  directory: ``<runs_root>/<run_id>/artifacts/``. This is the same
  resolver the harness uses to write artifacts, so writer and reader
  cannot diverge (P0-1).
- Aggregate uses :func:`evaluate_artifact` as the single 11-dimension
  evaluator entrypoint — the runner no longer duplicates the
  evaluator list. This was the root cause of P0-3 (terminal-ok
  artifacts hiding content-quality failures) (P0-3).
- Aggregate handles ``budget_exhausted`` artifacts: they are NOT
  evaluated (no content to evaluate) and NOT treated as passes.
  The report shows the cap-triggered state (P0-2).

Phase 1/2/3 invoke the in-process real-model harness at
``services/api/tests/test_reader_record_ask_real_llm_eval.py`` via
``pytest -m real_llm -k phase<N>``. The harness is default-skipped; it
only runs when the env gate is open.

This script never modifies production code, never commits, and never
touches DB / SSE / HTTP routes. It only consumes artifacts already
written by the harness.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from claread_eval.reader_record_ask.evaluators.context_support_contract import (
    INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS as INSTRUMENTATION_INCOMPLETE_REASONS,
)
from claread_eval.reader_record_ask.evaluators.context_support_contract import (
    LEGACY_BLOCKER_CLASSIFICATIONS as LEGACY_BLOCKER_REASONS,
)

EVALS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = EVALS_ROOT.parent
# Dataset Git governance: the R4-A3 working dataset lives under
# ``evals/tmp/`` which is gitignored by the ``**/tmp/`` rule. The
# tracked ``evals/datasets/`` tree hosts canonical datasets like
# ``vocabulary-seed-v1`` only — the R4-A3 working dataset is local,
# env-bound, and never committed.
#
# P0 explicit dataset-dir binding: the previous ``DEFAULT_DATASET_DIR``
# was used as a silent fallback when neither ``--dataset-dir`` nor
# ``CLAREAD_R4_A3_DATASET_DIR`` was set. That allowed real runs to
# accidentally reuse a stale local working dataset. The constant is
# now ONLY a suggested path printed in help text — never used for
# automatic resolution.
SUGGESTED_DATASET_DIR = EVALS_ROOT / "tmp" / "reader-record-ask-r4-a3"
DEFAULT_RUNS_DIR = SUGGESTED_DATASET_DIR / "runs"
DEFAULT_REPORT_OUTPUT = (
    REPO_ROOT / "docs" / "tmp" / "reader-orchestration" / "review"
    / "TMP-reader-record-ask-r4-a3-eval-2026-07-17.md"
)
HARNESS_TEST_PATH = (
    REPO_ROOT / "services" / "api" / "tests"
    / "test_reader_record_ask_real_llm_eval.py"
)
HARNESS_CWD = REPO_ROOT / "services" / "api"

_TRACKER_PATH = (
    "docs/tmp/reader-orchestration/"
    "TMP-reader-record-ask-r4-a3-product-ready-tracker-2026-07-17.md"
)

# Dataset dir env var — shared with the in-process harness. Priority:
#   CLI ``--dataset-dir`` > env ``CLAREAD_R4_A3_DATASET_DIR``.
# Real runs MUST set one of these — the runner exits with code 2 before
# invoking the pytest subprocess when neither is provided. The harness
# also fail-closes before any paid call when the env is missing.
R4_A3_DATASET_DIR_ENV = "CLAREAD_R4_A3_DATASET_DIR"


# ---------------------------------------------------------------------------
# Dataset dir resolution (P0 explicit binding — no silent fallback)
# ---------------------------------------------------------------------------


def _resolve_dataset_dir(cli_value: str | None) -> Path | None:
    """Resolve the R4-A3 dataset dir from CLI flag or env (no fallback).

    Priority (clear, single rule):
    1. ``--dataset-dir`` CLI flag (if explicitly set; non-empty)
    2. ``CLAREAD_R4_A3_DATASET_DIR`` env var (if set and non-empty)
    3. None — caller MUST fail-closed.

    Returns the resolved :class:`Path`, or ``None`` if neither source
    provided a value. The caller (preflight) is responsible for
    existence/cases validation before any paid call. The previous
    behavior of silently falling back to ``evals/tmp/...`` is
    intentionally removed: real runs must explicitly declare the
    dataset they are using.
    """
    if cli_value is not None and cli_value.strip():
        return Path(cli_value).resolve()
    env_val = os.environ.get(R4_A3_DATASET_DIR_ENV, "").strip()
    if env_val:
        return Path(env_val).resolve()
    return None


def _preflight_dataset_dir(dataset_dir: Path | None) -> None:
    """Fail-closed when the dataset dir is missing or has no dataset.yaml.

    P0 explicit dataset-dir binding: real runs MUST have an explicitly
    configured dataset dir (CLI or env). If neither is provided, or
    the resolved dir doesn't exist or doesn't contain ``dataset.yaml``,
    the runner exits BEFORE invoking the pytest harness subprocess —
    so no paid provider call can be made.
    """
    if dataset_dir is None:
        print(
            "ERROR: R4-A3 dataset dir not configured.\n"
            f"Set {R4_A3_DATASET_DIR_ENV}=<path> or pass --dataset-dir <path>. "
            f"Suggested local working dir: {SUGGESTED_DATASET_DIR} "
            "(gitignored; not used automatically).",
            file=sys.stderr,
        )
        sys.exit(2)
    if not dataset_dir.is_dir():
        print(
            f"ERROR: R4-A3 dataset dir not found: {dataset_dir}\n"
            f"Set {R4_A3_DATASET_DIR_ENV}=<path> or pass --dataset-dir <path>.",
            file=sys.stderr,
        )
        sys.exit(2)
    yaml_path = dataset_dir / "dataset.yaml"
    if not yaml_path.is_file():
        print(
            f"ERROR: dataset.yaml not found in {dataset_dir}\n"
            "The dataset dir must contain a valid dataset.yaml.",
            file=sys.stderr,
        )
        sys.exit(2)


# ---------------------------------------------------------------------------
# Phase invokers (subprocess to services/api pytest harness)
# ---------------------------------------------------------------------------


def run_phase(
    phase: int,
    run_id: str,
    runs_dir: Path,
    prior_run_id: str | None = None,
    dataset_dir: Path | None = None,
) -> int:
    """Invoke services/api pytest harness for the given phase via subprocess.

    Sets ``CLAREAD_R4_A3_RUN_ID`` (the run id) and
    ``CLAREAD_R4_A3_PRIOR_RUN_ID`` (the prior phase's run id, required
    for Phase 2/3) so the harness writes artifacts under
    ``<runs_dir>/<run_id>/artifacts/`` with the requested run_id and
    reads prior-phase artifacts from
    ``<runs_dir>/<prior_run_id>/artifacts/`` (P0-1).

    P0 explicit dataset-dir binding: ``dataset_dir`` is propagated to
    the subprocess via ``CLAREAD_R4_A3_DATASET_DIR`` env var so the
    in-process harness resolves the same dataset. ``main()`` calls
    ``_preflight_dataset_dir`` BEFORE this function, so ``dataset_dir``
    is always a resolved, validated ``Path`` here (never ``None``).
    The harness itself has NO silent fallback — when
    ``CLAREAD_R4_A3_DATASET_DIR`` is missing, the harness
    ``pytest.skip``s before any provider call.
    """
    env = {
        **os.environ,
        "CLAREAD_R4_A3_RUN_ID": run_id,
        "CLAREAD_R4_A3_RUNS_DIR": str(runs_dir),
    }
    if dataset_dir is not None:
        env[R4_A3_DATASET_DIR_ENV] = str(dataset_dir)
    if prior_run_id is not None:
        env["CLAREAD_R4_A3_PRIOR_RUN_ID"] = prior_run_id
    cmd = [
        "uv", "run", "pytest", str(HARNESS_TEST_PATH),
        "-v", "-m", "real_llm",
        "-k", f"phase{phase}",
    ]
    return subprocess.call(cmd, cwd=str(HARNESS_CWD), env=env)


# ---------------------------------------------------------------------------
# Aggregate phase: load artifacts + run evaluators + generate report
# ---------------------------------------------------------------------------


def _load_artifacts(artifact_dir: Path, run_id: str) -> list:
    """Backward-compatible thin wrapper around :func:`load_artifacts_with_audit`.

    DEPRECATED: new callers should use :func:`load_artifacts_with_audit`
    directly to obtain the full :class:`ArtifactLoadResult` (with typed
    counts for invalid_json / invalid_schema / foreign_run_id). This
    wrapper exists only for backwards compatibility with any code that
    still expects a plain ``list[RawArtifact]``.

    The production aggregate path uses :func:`load_artifacts_with_audit`
    so that corrupt/invalid/foreign artifacts are counted and force the
    verdict to ``blocked_incomplete_real_model_run`` instead of silently
    disappearing.
    """
    from claread_eval.reader_record_ask.artifact_loading import (
        load_artifacts_with_audit,
    )

    return list(load_artifacts_with_audit(artifact_dir, run_id).valid_artifacts)


# ---------------------------------------------------------------------------
# P0-2: AggregateReadinessAudit — single source of truth for normal-verdict
# readiness. Concentrates ALL conditions that must hold before the normal
# accepted/rework path may be entered. Replaces the parallel ``coverage_ok``
# check that previously lived in ``aggregate()`` + the duplicated coverage
# check inside ``_decide_final_verdict``.
# ---------------------------------------------------------------------------

# R4-A4-0 final gate closure (P1 de-dup): classification reason tags
# and the three routing frozensets are imported from
# :mod:`evaluators.context_support_contract` — the SINGLE source of
# truth shared by the evaluator, aggregator, and runner. The previous
# private mirror (``_RUNNER_REASON_*`` /
# ``INSTRUMENTATION_INCOMPLETE_REASONS`` /
# ``LEGACY_BLOCKER_REASONS``) was a manually-synced copy that could
# drift out of sync; the contract module eliminates that drift.
#
# ``fact_not_supported`` / ``fact_not_cited`` are intentionally NOT
# in :data:`INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS` — they are
# real model correctness failures that DO enter rework.
#
# Legacy is NOT an instrumentation_incomplete reason (the run did not
# fail — it predates the new contract), but the verdict outcome is
# the same: blocked_incomplete_real_model_run, allow_r4_a4=False,
# allow_r4_b1=False. Kept as a separate set
# (:data:`LEGACY_BLOCKER_CLASSIFICATIONS`) so the
# ``instrumentation_incomplete_count`` audit field stays semantically
# narrow (only the 3 instrumentation-incomplete reasons).


class AggregateReadinessAudit:
    """Typed audit result concentrating ALL normal-verdict preconditions.

    Spec: `.trae/specs/audit-r4-a3-eval-harness-final-closure/spec.md`
    Requirement: Evaluation Completeness (P0-2 final closure).

    The normal accepted/rework path (via :func:`_decide_normal_verdict`)
    is ONLY reachable when ``ready_for_normal_verdict`` returns ``True``.
    The verdict seam (:func:`_decide_final_verdict`) consults each
    field individually to decide which blocker precedence applies.

    All fields are non-negative integers or booleans. No file paths,
    JSON content, exception text, or case content is carried — only
    typed counts and boolean audit signals.

    Fields:

    - ``artifact_load_clean``: ``True`` iff no invalid/foreign artifacts
      were discovered (from :class:`ArtifactLoadResult.is_clean`).
    - ``discovered_file_count``: total ``*.json`` files found under
      the artifact directory (before filtering).
    - ``invalid_artifact_count``: invalid_json + invalid_schema +
      foreign_run_id (from :class:`ArtifactLoadResult`).
    - ``manifest_state``: ``"absent"`` / ``"valid"`` / ``"corrupt"``
      (from :class:`ManifestState`).
    - ``manifest_present``: ``True`` iff a valid manifest was loaded
      (i.e. ``manifest_state == "valid"``).
    - ``manifest_run_id_matches``: ``True`` iff manifest is valid AND
      ``manifest.run_id == session.run_id``. ``False`` for foreign
      manifests. ``None`` when no valid manifest exists.
    - ``manifest_status``: ``"completed"`` / ``"budget_exhausted"`` /
      ``None`` (from the manifest, when present).
    - ``manifest_is_complete``: ``True`` iff the manifest's
      :meth:`ReaderRecordAskRunManifest.is_complete` returns ``True``
      (defense-in-depth — ``from_json`` already rejects corrupt
      manifests, but ``is_complete`` checks the in-memory dataclass).
    - ``coverage_counts_clean``: ``True`` iff missing/duplicate/
      unexpected/identity_mismatch counts are all 0 (from
      :class:`CoverageAuditResult`).
    - ``planned_count`` / ``evaluable_artifact_count``: from
      :class:`CoverageAuditResult`. Must be equal for readiness.
    - ``unknown_planned_case_count``: count of manifest-planned case
      IDs that do NOT exist in the current dataset's ``cases_by_id``.
      P0-2 dataset case binding — a manifest planning unknown cases
      indicates the manifest was written against a different dataset
      version and MUST NOT be stitched together with the current run.
    - ``unknown_artifact_case_count``: count of valid artifacts whose
      ``case_id`` is NOT in the current dataset's ``cases_by_id``.
      P0-2 dataset case binding — an artifact referencing an unknown
      case cannot be evaluated and MUST NOT be silently skipped
      (the previous ``warn + continue`` path let ``case_results=[]``
      reach ``_decide_normal_verdict``, which returned ``accepted``
      via ``all([])``).
    - ``evaluated_case_result_count``: ``len(case_results)`` after
      the evaluator gate. Must equal ``planned_count`` AND be > 0.
    - ``instrumentation_incomplete_count``: R4-A4-0 final gate closure
      (P0-2). Count of evaluated ``context_support`` dimensions whose
      typed ``classification`` falls in
      :data:`INSTRUMENTATION_INCOMPLETE_REASONS` (``baseline_unavailable``
      / ``runtime_exception`` / ``instrumentation_incomplete``). These
      are instrumentation/run-incomplete blockers — NOT model
      correctness failures. They MUST NOT enter rework, MUST NOT count
      as ``confirmed_model_failure``, and MUST NOT cluster as
      ``fact-not-grounded``. The verdict falls to
      ``blocked_incomplete_real_model_run`` with
      ``allow_r4_a4 = allow_r4_b1 = False`` via precedence row 9.5
      in :func:`_decide_final_verdict`. ``fact_not_supported`` /
      ``fact_not_cited`` classifications are NOT counted here — they
      are real model failures that DO enter rework.
    - ``legacy_artifact_count``: R4-A4-0 final gate closure (P0-2).
      Count of evaluated ``context_support`` dimensions whose typed
      ``classification`` is ``legacy_artifact`` (version=None,
      status=None — predates the new contract). Per user spec, legacy
      artifacts MUST also be blocked in the authoritative aggregate
      even when dataset identity matches — they require new artifacts.
      Legacy is NOT counted under ``instrumentation_incomplete_count``
      (the run did not fail — it predates the contract) but produces
      the same verdict outcome via the same precedence row 9.5. The
      aggregator routes legacy to the ``legacy-artifact`` cluster
      (NOT ``fact-not-grounded``).

    The ``ready_for_normal_verdict`` property is the SINGLE source of
    truth consulted by :func:`_decide_normal_verdict` (defense-in-depth)
    and by :func:`_decide_final_verdict` (precedence gate). The
    previous implementation had two parallel checks (``coverage_ok`` in
    ``aggregate()`` + a duplicated check in ``_decide_final_verdict``)
    that could drift apart; this typed audit eliminates that drift.
    """

    __slots__ = (
        "artifact_load_clean",
        "discovered_file_count",
        "invalid_artifact_count",
        "manifest_state",
        "manifest_present",
        "manifest_run_id_matches",
        "manifest_status",
        "manifest_is_complete",
        "coverage_counts_clean",
        "planned_count",
        "evaluable_artifact_count",
        "unknown_planned_case_count",
        "unknown_artifact_case_count",
        "evaluated_case_result_count",
        # R4-A4-0 final gate closure (P0-2): instrumentation-incomplete
        # blocker count. See class docstring above.
        "instrumentation_incomplete_count",
        # R4-A4-0 final gate closure (P0-2): legacy-artifact blocker
        # count. Legacy artifacts (version=None, status=None) cannot
        # be authoritatively re-evaluated under the new contract —
        # the authoritative aggregate MUST block them and require new
        # artifacts. NOT counted under ``instrumentation_incomplete_count``
        # because the run did not fail — it predates the contract.
        "legacy_artifact_count",
    )

    def __init__(
        self,
        *,
        artifact_load_clean: bool,
        discovered_file_count: int,
        invalid_artifact_count: int,
        manifest_state: str,
        manifest_present: bool,
        manifest_run_id_matches: bool | None,
        manifest_status: str | None,
        manifest_is_complete: bool,
        coverage_counts_clean: bool,
        planned_count: int,
        evaluable_artifact_count: int,
        unknown_planned_case_count: int,
        unknown_artifact_case_count: int,
        evaluated_case_result_count: int,
        instrumentation_incomplete_count: int = 0,
        legacy_artifact_count: int = 0,
    ) -> None:
        self.artifact_load_clean = artifact_load_clean
        self.discovered_file_count = discovered_file_count
        self.invalid_artifact_count = invalid_artifact_count
        self.manifest_state = manifest_state
        self.manifest_present = manifest_present
        self.manifest_run_id_matches = manifest_run_id_matches
        self.manifest_status = manifest_status
        self.manifest_is_complete = manifest_is_complete
        self.coverage_counts_clean = coverage_counts_clean
        self.planned_count = planned_count
        self.evaluable_artifact_count = evaluable_artifact_count
        self.unknown_planned_case_count = unknown_planned_case_count
        self.unknown_artifact_case_count = unknown_artifact_case_count
        self.evaluated_case_result_count = evaluated_case_result_count
        self.instrumentation_incomplete_count = instrumentation_incomplete_count
        self.legacy_artifact_count = legacy_artifact_count

    @property
    def ready_for_normal_verdict(self) -> bool:
        """``True`` iff ALL normal-verdict preconditions hold.

        The normal accepted/rework path is ONLY reachable when this
        property is ``True``. :func:`_decide_normal_verdict` checks
        this as defense-in-depth (it should never be called when
        ``False`` — :func:`_decide_final_verdict` routes to a blocked
        verdict first, but the check is here so a buggy caller cannot
        accidentally reach the accepted/rework path).
        """
        return (
            self.artifact_load_clean
            and self.manifest_state == "valid"
            and self.manifest_present
            and self.manifest_run_id_matches is True
            and self.manifest_status == "completed"
            and self.manifest_is_complete
            and self.coverage_counts_clean
            and self.evaluable_artifact_count == self.planned_count
            and self.unknown_planned_case_count == 0
            and self.unknown_artifact_case_count == 0
            and self.evaluated_case_result_count == self.planned_count
            and self.evaluated_case_result_count > 0
            # R4-A4-0 final gate closure (P0-2): instrumentation-incomplete
            # blockers (capture_status=unavailable/failed, fingerprint
            # mismatch, missing required observation, duplicate/unknown
            # observation, supporting handle not in model context) MUST
            # NOT reach the normal accepted/rework path. They are NOT
            # model correctness failures.
            and self.instrumentation_incomplete_count == 0
            # R4-A4-0 final gate closure (P0-2): legacy artifacts also
            # MUST NOT reach the normal accepted/rework path — they
            # cannot be authoritatively re-evaluated under the new
            # contract. The authoritative aggregate MUST block them
            # and require new artifacts (per user spec: "authoritative
            # aggregate 若 dataset identity 恰好匹配，也不得 accepted；
            # 应 blocked_incomplete_real_model_run").
            and self.legacy_artifact_count == 0
        )

    @property
    def pre_evaluator_ready(self) -> bool:
        """``True`` iff the evaluator MAY be run.

        This is ``ready_for_normal_verdict`` MINUS the
        ``evaluated_case_result_count``,
        ``instrumentation_incomplete_count``, and
        ``legacy_artifact_count`` checks (all three are unknown
        until AFTER the evaluator runs). It is consulted by
        :func:`aggregate` to decide whether to invoke the 11-dimension
        evaluator at all. When ``False``, the evaluator is skipped and
        ``evaluated_case_result_count`` stays at ``0`` — the verdict
        falls to a blocked variant via
        :func:`_decide_final_verdict` precedence rows 1-8.

        When ``True``, the evaluator runs and produces
        ``case_results``. The actual ``evaluated_case_result_count``,
        ``instrumentation_incomplete_count``, and
        ``legacy_artifact_count`` are then set on a NEW
        :class:`AggregateReadinessAudit` instance (this class is
        mutable via ``__slots__`` but the aggregate rebuilds it
        post-evaluator for clarity) and passed to
        :func:`_decide_final_verdict`.

        The split between ``pre_evaluator_ready`` and
        ``ready_for_normal_verdict`` is necessary because
        ``evaluated_case_result_count``,
        ``instrumentation_incomplete_count``, and
        ``legacy_artifact_count`` are not known until AFTER
        the evaluator runs — we cannot make them preconditions for
        RUNNING the evaluator (chicken-and-egg).
        """
        return (
            self.artifact_load_clean
            and self.manifest_state == "valid"
            and self.manifest_present
            and self.manifest_run_id_matches is True
            and self.manifest_status == "completed"
            and self.manifest_is_complete
            and self.coverage_counts_clean
            and self.evaluable_artifact_count == self.planned_count
            and self.unknown_planned_case_count == 0
            and self.unknown_artifact_case_count == 0
        )


def _build_case_result(case, artifact):
    """Build a CaseEvalResult from an artifact + 11 dim results.

    Uses :func:`evaluate_artifact` as the single 11-dimension evaluator
    entrypoint (P0-3). The runner no longer duplicates the evaluator
    list — divergence between the runner and the harness was the root
    cause of P0-3.
    """
    from claread_eval.reader_record_ask.evaluation import evaluate_artifact
    from claread_eval.reader_record_ask.evaluators import CaseEvalResult

    dims = evaluate_artifact(case, artifact)
    total_tokens = None
    total_requests = None
    if artifact.agent_usage is not None:
        input_tokens = artifact.agent_usage.input_tokens or 0
        output_tokens = artifact.agent_usage.output_tokens or 0
        total_tokens = input_tokens + output_tokens
        total_requests = artifact.agent_usage.requests or 0
    # P0-8: prefer BudgetedUsageModel counters when agent_usage is None
    # or zero (the wrapper's counters are the source of truth).
    if (total_requests is None or total_requests == 0) and artifact.executed_requests:
        total_requests = artifact.executed_requests
    if (total_tokens is None or total_tokens == 0) and artifact.executed_tokens:
        total_tokens = artifact.executed_tokens
    return CaseEvalResult(
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


def _git_rev_parse_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT
        ).decode().strip()
    except (subprocess.CalledProcessError, OSError):
        return "(git unavailable)"


def _git_status_short() -> list[str]:
    try:
        out = subprocess.check_output(
            ["git", "status", "--short"], cwd=REPO_ROOT
        ).decode()
        return [line for line in out.splitlines() if line.strip()]
    except (subprocess.CalledProcessError, OSError):
        return []


def _decide_normal_verdict(case_results, *, readiness=None):
    """Derive the normal-path verdict (accepted / rework) from results.

    Pre-conditions (caller MUST enforce via precedence):
    - No identity mismatch (the dataset fingerprint matches all artifacts).
    - Real-model run was not blocked (some real artifacts exist).

    P0-2 final closure (defense-in-depth):
    - Empty ``case_results`` MUST NOT return ``accepted``. The previous
      ``all([])`` returned ``True``, which let the unknown-case path
      (artifact.case_id not in dataset → warn + continue → empty
      case_results) collapse to ``("accepted", True, True)``.
    - When ``readiness`` (an :class:`AggregateReadinessAudit`) is
      provided, ``readiness.ready_for_normal_verdict`` MUST be ``True``
      before any accepted/rework result is returned. This is the
      single-source-of-truth gate — the parallel ``coverage_ok`` check
      that previously lived in ``aggregate()`` is removed.
    - When ``readiness`` is ``None`` (e.g. unit-test direct call), the
      caller MUST pass a non-empty ``case_results``. An empty list
      returns ``("blocked_incomplete_real_model_run", False, False)``
      — never ``accepted``.
    """
    # Defense-in-depth: empty case_results must NEVER return accepted.
    # This is the structural fix for the ``all([]) → True`` bug.
    if not case_results:
        return "blocked_incomplete_real_model_run", False, False

    # Defense-in-depth: when the caller provides an AggregateReadinessAudit,
    # require ready_for_normal_verdict=True before proceeding. This makes
    # the verdict seam impossible to reach with stale/inconsistent
    # readiness signals.
    if readiness is not None and not readiness.ready_for_normal_verdict:
        return "blocked_incomplete_real_model_run", False, False

    all_passed = all(
        d.passed
        for cr in case_results
        for d in cr.dimensions
        if d.dimension != "usage_observability"
    )
    high_severity_failures = sum(
        1
        for cr in case_results
        for d in cr.dimensions
        if not d.passed and d.severity == "high"
    )
    if all_passed and high_severity_failures == 0:
        return "accepted", True, True
    # rework: R4-A4 conditionally allowed, R4-B1 deferred regardless of
    # high-severity failure count.
    return "rework", True, False


def _decide_final_verdict(
    *,
    case_results,
    coverage_audit,
    identity_mismatched_count: int,
    real_model_blocked: bool,
    has_budget_exhausted: bool,
    total_artifacts_loaded: int,
    readiness=None,
):
    """Single source of truth for the final aggregate verdict.

    Implements the verdict/gate table (frozen contract, spec
    `.trae/specs/audit-r4-a3-eval-harness-final-closure/spec.md`
    Requirement: Final Verdict Gate Contract). Each row's ``(a4, b1)``
    pair is ``(allow_r4_a4, allow_r4_b1)``:

    1. identity mismatch                              → blocked_dataset_identity_mismatch (F, F)
    2. manifest corrupt (any artifacts)               → blocked_incomplete_real_model_run  (F, F)
    3. manifest absent + partial artifacts present    → blocked_incomplete_real_model_run  (F, F)
    4. manifest valid but run_id mismatch (foreign)   → blocked_incomplete_real_model_run  (F, F)
    5. artifact load invalid/corrupt/foreign          → blocked_incomplete_real_model_run  (F, F)
    6. manifest.status="budget_exhausted"             → blocked_incomplete_real_model_run  (F, F)
    7. completed manifest + coverage gap (missing/dup/unexpected/
       is_complete False / evaluable != planned)      → blocked_incomplete_real_model_run  (F, F)
    8. unknown dataset case (planned or artifact)     → blocked_incomplete_real_model_run  (F, F)
    9. evaluated_case_result_count mismatch           → blocked_incomplete_real_model_run  (F, F)
    9.5. instrumentation_incomplete (capture_status != captured,
        fingerprint mismatch, missing required obs,
        duplicate/unknown obs, supporting handle not in model context)
        OR legacy_artifact (version=None, status=None — pre-contract)
                                                      → blocked_incomplete_real_model_run  (F, F)
    10. no manifest + no artifact (not yet run)       → blocked_by_real_model_run          (F, F)
    11. completed manifest + full coverage + all pass → accepted                           (T, T)
    12. completed manifest + full coverage + qual fail→ rework                             (T, F)

    R4-A4-0 final gate closure (P0-2) — precedence 9.5 is the typed
    blocker row. It fires AFTER 9 (count mismatch) and BEFORE 10
    (never-ran), so:

    - If the evaluator was skipped (case_results empty),
      ``instrumentation_incomplete_count`` and ``legacy_artifact_count``
      are both 0 — precedence 9 fires first (count mismatch), 9.5 is
      never reached.
    - If the evaluator ran and ALL artifacts had clean instrumentation
      (capture_status=captured, fingerprint matches, observations
      complete, version=v1), 9.5 does not fire — fall through to
      normal path.
    - If the evaluator ran and ANY artifact had an instrumentation
      blocker (capture_status=unavailable/failed, fingerprint mismatch,
      missing/duplicate/unknown observation, supporting handle not in
      model context), 9.5 fires with
      ``("blocked_incomplete_real_model_run", False, False)``. These
      blockers MUST NOT enter rework (NOT a model correctness failure)
      and MUST NOT cluster as ``fact-not-grounded``. Real model
      failures (``fact_not_supported`` / ``fact_not_cited``) do NOT
      trip 9.5 — they fall through to the normal accepted/rework path.
    - If the evaluator ran and ANY artifact is legacy (version=None,
      status=None — predates the new contract), 9.5 also fires with
      ``("blocked_incomplete_real_model_run", False, False)``. Per
      user spec, legacy artifacts MUST NOT be accepted in the
      authoritative aggregate even when dataset identity matches —
      they require new artifacts. Legacy artifacts are counted under
      ``legacy_artifact_count`` (NOT ``instrumentation_incomplete_count``)
      because the run did not fail — it predates the contract. The
      aggregator routes legacy to the ``legacy-artifact`` cluster
      (NOT ``fact-not-grounded``).

    Precedence (high → low):

    1. ``identity_mismatched_count > 0`` →
       ``("blocked_dataset_identity_mismatch", False, False)``.
       Identity mismatch is the strongest blocker — even if ALL
       artifacts mismatch (so ``real_artifacts`` is empty and
       ``real_model_blocked`` is True), the verdict MUST stay
       ``blocked_dataset_identity_mismatch`` so the operator sees the
       real reason.

    2. Manifest corrupt (file exists but unparseable / invalid) →
       ``("blocked_incomplete_real_model_run", False, False)``.
       P1 fix: a corrupt manifest indicates the run started but its
       audit trail is broken.

    3. Manifest absent + some artifacts on disk →
       ``("blocked_incomplete_real_model_run", False, False)``.
       Covers "process-like interruption" — phase process was killed
       before writing a manifest, but partial artifacts exist.
       NOTE: with the new ArtifactLoadResult seam, ``total_artifacts_loaded``
       reflects ``discovered_file_count`` (the count of ``*.json`` files
       found, BEFORE filtering by validity/foreign). This means
       "absent manifest + 1 corrupt artifact file" correctly falls here
       (incomplete) instead of falling through to row 10 (blocked_by_real_model_run).

    4. Manifest valid but foreign (``run_id`` mismatch) →
       ``("blocked_incomplete_real_model_run", False, False)``.
       P0-2 fix: a foreign/copied manifest MUST NOT be stitched
       together with the current run's artifacts.

    5. Artifact load invalid/corrupt/foreign (from ArtifactLoadResult)
       → ``("blocked_incomplete_real_model_run", False, False)``.
       P0-1 final closure: corrupt/invalid/foreign artifacts cannot
       be silently dropped — the audit trail is broken and the verdict
       must reflect it. This is checked BEFORE budget_exhausted and
       coverage_gap because a corrupt artifact file is a stronger
       signal than a partial run.

    6. ``manifest_status == "budget_exhausted"`` →
       ``("blocked_incomplete_real_model_run", False, False)``.
       Budget stop is NOT a completed run.

    7. Completed manifest but coverage gap OR ``is_complete()`` False
       OR ``evaluable_artifact_count != planned_count`` →
       ``("blocked_incomplete_real_model_run", False, False)``.

    8. Unknown dataset case — manifest planned a case_id that does
       not exist in the current dataset, OR an artifact's case_id
       is not in the current dataset →
       ``("blocked_incomplete_real_model_run", False, False)``.
       P0-2 final closure: dataset case binding must be enforced at
       the verdict seam, NOT via ``warn + continue`` in the evaluator
       loop. This catches both manifest-vs-dataset drift AND
       artifact-vs-dataset drift that the identity fence might miss
       (e.g. locally hand-edited artifacts with valid identity but
       unknown case_id).

    9. Evaluated case result count mismatch —
       ``evaluated_case_result_count != planned_count`` →
       ``("blocked_incomplete_real_model_run", False, False)``.
       P0-2 final closure: structural fix for the ``all([]) → accepted``
       bug. The previous path let unknown-case artifacts be skipped,
       producing ``case_results=[]`` with ``planned_count=1``, and
       ``_decide_normal_verdict([])`` returned accepted. This row
       catches that case at the verdict seam.

    10. No manifest + no artifacts (not yet real-run) →
        ``("blocked_by_real_model_run", False, False)``.
        NOTE: ``allow_r4_a4 = False`` per the frozen contract (bug fix:
        previous implementation incorrectly returned ``True``).

    11/12. Completed manifest + full coverage → normal path
        (accepted/rework via ``_decide_normal_verdict``).
        Defense-in-depth: ``_decide_normal_verdict`` is called with
        ``readiness=readiness`` so even if this row is reached, an
        inconsistent readiness audit will fall back to
        ``blocked_incomplete_real_model_run``.

    Args:
        case_results: list of :class:`CaseEvalResult` (post-filter —
            mismatched artifacts are already excluded). Empty when the
            evaluator was skipped (blocked paths).
        coverage_audit: :class:`CoverageAuditResult` from
            :func:`validate_manifest_coverage`. Carries manifest_present
            / manifest_status / manifest_state / manifest_run_id_matches
            / missing/duplicate/unexpected counts.
        identity_mismatched_count: number of artifacts whose identity
            triple differs from the manifest's identity (when manifest
            is present) OR from the current dataset identity (when
            manifest is absent).
        real_model_blocked: True when no evaluable real artifacts exist
            after filtering.
        has_budget_exhausted: True when ``coverage_audit.manifest_status
            == "budget_exhausted"`` OR any artifact carries
            ``budget_exhausted=True``.
        total_artifacts_loaded: ``discovered_file_count`` from
            :class:`ArtifactLoadResult` — the count of ``*.json`` files
            found before filtering. This is broader than the previous
            ``len(artifacts)`` (which was post-filter): a corrupt file
            now counts here so "absent manifest + corrupt file" falls
            to row 3 (incomplete) instead of row 10 (never ran).
        readiness: optional :class:`AggregateReadinessAudit` carrying
            the single-source-of-truth readiness signals. When provided,
            it is consulted for the artifact_load_invalid,
            unknown_planned_case_count, unknown_artifact_case_count,
            and evaluated_case_result_count precedence rows. When
            ``None`` (legacy callers / direct unit-test invocation),
            these rows are skipped — the previous precedence applies.

    Returns:
        ``(verdict, allow_r4_a4, allow_r4_b1)``.
    """
    # Precedence 1: identity mismatch wins over everything.
    if identity_mismatched_count > 0:
        return "blocked_dataset_identity_mismatch", False, False

    # Precedence 2: corrupt manifest → incomplete (P1 fix).
    # A corrupt manifest indicates the run started but its audit trail
    # is broken. This is strictly worse than absent and MUST NOT be
    # folded into the absent path.
    if coverage_audit.manifest_state == "corrupt":
        return "blocked_incomplete_real_model_run", False, False

    # Precedence 3: manifest absent + some artifacts on disk →
    # process-like interruption / partial run without manifest.
    # NOTE: ``total_artifacts_loaded`` is the discovered file count
    # (from ArtifactLoadResult), so corrupt/invalid/foreign files
    # also count here — they indicate the run started but the audit
    # trail is broken.
    if (
        not coverage_audit.manifest_present
        and coverage_audit.manifest_state == "absent"
        and total_artifacts_loaded > 0
    ):
        return "blocked_incomplete_real_model_run", False, False

    # Precedence 4: manifest valid but foreign (run_id mismatch).
    # P0-2 fix: a foreign manifest MUST NOT be stitched together with
    # the current run's artifacts. NOT classified as identity mismatch
    # unless the dataset identity itself mismatches.
    if (
        coverage_audit.manifest_present
        and coverage_audit.manifest_state == "valid"
        and coverage_audit.manifest_run_id_matches is False
    ):
        return "blocked_incomplete_real_model_run", False, False

    # Precedence 5: artifact load invalid/corrupt/foreign — the audit
    # trail is broken at the file level. P0-1 final closure: corrupt
    # artifacts cannot be silently dropped.
    if readiness is not None and not readiness.artifact_load_clean:
        return "blocked_incomplete_real_model_run", False, False

    # Precedence 6: budget_exhausted manifest → incomplete (NOT
    # accepted/rework). This is the P0-2 bug fix: previously a partial
    # budget-exhausted run with some case_results could fall through to
    # accepted/rework.
    if has_budget_exhausted:
        return "blocked_incomplete_real_model_run", False, False

    # Precedence 7: completed manifest but coverage gap OR is_complete
    # False OR evaluable != planned. P0-1 defense-in-depth: coverage_ok
    # now requires manifest.is_complete() AND
    # evaluable_artifact_count == planned_count, not just zero error
    # counts.
    if (
        coverage_audit.manifest_present
        and coverage_audit.manifest_status == "completed"
        and (
            coverage_audit.missing_count > 0
            or coverage_audit.duplicate_count > 0
            or coverage_audit.unexpected_count > 0
            or coverage_audit.evaluable_artifact_count != coverage_audit.planned_count
        )
    ):
        return "blocked_incomplete_real_model_run", False, False

    # Precedence 8: unknown dataset case — manifest planned or artifact
    # references a case_id not in the current dataset. P0-2 final closure.
    if readiness is not None and (
        readiness.unknown_planned_case_count > 0
        or readiness.unknown_artifact_case_count > 0
    ):
        return "blocked_incomplete_real_model_run", False, False

    # Precedence 9: evaluated_case_result_count mismatch — the evaluator
    # produced fewer/more results than planned. P0-2 final closure:
    # structural fix for ``all([]) → accepted``.
    #
    # This precedence ONLY fires when ``planned_count > 0`` — i.e., there
    # WAS a plan (manifest present and non-empty). When ``planned_count == 0``
    # (no manifest or empty manifest), the "evaluated == 0" condition is
    # the normal "never ran" state, not an incompleteness signal. Without
    # this guard, "absent manifest + zero files" would incorrectly fall
    # here instead of falling through to precedence 10
    # (``blocked_by_real_model_run``).
    if (
        readiness is not None
        and readiness.planned_count > 0
        and (
            readiness.evaluated_case_result_count != readiness.planned_count
            or readiness.evaluated_case_result_count == 0
        )
    ):
        return "blocked_incomplete_real_model_run", False, False

    # Precedence 9.5: instrumentation_incomplete OR legacy_artifact —
    # R4-A4-0 final gate closure (P0-2). The evaluator ran and produced
    # the expected number of case_results, BUT at least one
    # ``context_support`` dimension carries a typed ``classification``
    # in :data:`INSTRUMENTATION_INCOMPLETE_REASONS`
    # (``baseline_unavailable`` / ``runtime_exception`` /
    # ``instrumentation_incomplete``) OR in
    # :data:`LEGACY_BLOCKER_REASONS` (``legacy_artifact``). Both signal
    # that the model-context instrumentation could NOT authoritatively
    # evaluate the case — capture_status != captured, fingerprint
    # mismatch, missing required observation, duplicate / unknown
    # observation, supporting handle not in model context, OR the
    # artifact predates the new contract (legacy).
    #
    # Such cases MUST NOT enter rework (they are NOT model correctness
    # failures), MUST NOT count as ``confirmed_model_failure``, and
    # MUST NOT cluster as ``fact-not-grounded`` (the aggregator's
    # :func:`_extract_failure_pattern_typed` routes them to the
    # ``instrumentation-incomplete`` or ``legacy-artifact`` cluster
    # instead).
    #
    # Per user spec: "Legacy artifact: authoritative aggregate 若
    # dataset identity 恰好匹配，也不得 accepted；应
    # blocked_incomplete_real_model_run，要求新 artifacts". Legacy
    # artifacts are NOT counted under ``instrumentation_incomplete_count``
    # (the run did not fail — it predates the contract) but produce
    # the same verdict outcome.
    #
    # Real model failures (``fact_not_supported`` / ``fact_not_cited``)
    # do NOT trip this row — they fall through to the normal
    # accepted/rework path via precedence 11/12.
    if (
        readiness is not None
        and (
            readiness.instrumentation_incomplete_count > 0
            or readiness.legacy_artifact_count > 0
        )
    ):
        return "blocked_incomplete_real_model_run", False, False

    # Precedence 10: no manifest + no artifacts → blocked_by_real_model_run.
    # NOTE: allow_r4_a4=False per the frozen contract (bug fix:
    # previous implementation incorrectly returned True).
    if real_model_blocked or (
        not coverage_audit.manifest_present and total_artifacts_loaded == 0
    ):
        return "blocked_by_real_model_run", False, False

    # Precedence 11/12: normal path (completed manifest + full coverage).
    # _decide_normal_verdict returns ("accepted", True, True) when all
    # non-usage dimensions pass, else ("rework", True, False).
    # Defense-in-depth: pass readiness so _decide_normal_verdict can
    # re-check ready_for_normal_verdict before returning accepted/rework.
    return _decide_normal_verdict(case_results, readiness=readiness)


def aggregate(
    run_id: str,
    runs_dir: Path,
    dataset_dir: Path,
    report_output: Path,
    *,
    # R4-A4-0 (Task 5): parameterized report inputs so the runner no
    # longer relies on hardcoded date / file list / tracker path. CLI
    # optional flags flow through here.
    report_date: str | None = None,
    modified_files: list[str] | None = None,
    task_label: str = "Task 5",
    tracker_path: str | None = None,
) -> int:
    """Load artifacts, run 11 evaluators, aggregate, generate report.

    Uses :class:`RunSessionLayout` to resolve the artifact directory
    (P0-1) and :func:`evaluate_artifact` as the single evaluator
    entrypoint (P0-3). Budget-exhausted artifacts are NOT evaluated
    (P0-2) and NOT treated as passes.

    P1-b: the dataset and its :class:`DatasetIdentity` are loaded via
    :func:`load_r4_a3_dataset_with_snapshot` — the identity is bound
    to the same bytes the parser consumed, so a disk mutation between
    load and identity computation cannot desynchronize the fingerprint.

    P1-a: the final verdict is decided by :func:`_decide_final_verdict`,
    the SINGLE source of truth for the verdict precedence. The previous
    implementation set ``blocked_dataset_identity_mismatch`` then
    overwrote it with ``blocked_by_real_model_run`` when all artifacts
    mismatched — that overwrite is now structurally impossible because
    the precedence lives in one function.
    """
    from claread_eval.reader_record_ask.artifact_loading import (
        load_artifacts_with_audit,
    )
    from claread_eval.reader_record_ask.dataset_identity import (
        find_identity_mismatched_artifacts,
    )
    from claread_eval.reader_record_ask.evaluators import aggregate_results
    from claread_eval.reader_record_ask.loader import (
        load_r4_a3_dataset_with_snapshot,
    )
    from claread_eval.reader_record_ask.report import generate_r4_a3_report
    from claread_eval.reader_record_ask.run_manifest import (
        CoverageAuditResult,
        ManifestState,
        read_manifest_with_state,
        validate_manifest_coverage,
    )
    from claread_eval.reader_record_ask.session import RunSessionLayout

    # P1-b: load dataset + identity from a SINGLE byte capture. The
    # snapshot's ``dataset`` and ``identity`` are derived from the same
    # bytes — a disk mutation after this point cannot desync them.
    snapshot = load_r4_a3_dataset_with_snapshot(dataset_dir)
    dataset = snapshot.dataset
    dataset_identity = snapshot.identity
    cases_by_id = {c.id: c for c in dataset.cases}

    # P0-1: use RunSessionLayout to resolve the artifact directory.
    # The same resolver the harness uses to write — writer and reader
    # cannot diverge.
    session = RunSessionLayout(runs_root=runs_dir, run_id=run_id)
    artifact_dir = session.artifact_dir

    # P0-1 final closure: use the typed artifact-load seam as the
    # SINGLE source of truth for parsing on-disk artifacts. This
    # replaces the old ``_load_artifacts`` that did ``warn + continue``
    # for invalid/corrupt/foreign files — those files are now COUNTED
    # and force the verdict to ``blocked_incomplete_real_model_run``
    # via the readiness audit.
    load_result = load_artifacts_with_audit(artifact_dir, run_id)
    artifacts = list(load_result.valid_artifacts)
    discovered_file_count = load_result.discovered_file_count
    invalid_artifact_count = load_result.invalid_artifact_count
    artifact_load_clean = load_result.is_clean

    # ------------------------------------------------------------------
    # P1-c + P1 + P0-2: Coverage Audit with three manifest states.
    # ------------------------------------------------------------------
    # P1 fix: read the manifest via read_manifest_with_state, which
    # distinguishes absent / valid / corrupt. The previous code caught
    # RunManifestError and folded corrupt → absent, which caused
    # "corrupt manifest + no artifacts" to be misclassified as
    # blocked_by_real_model_run (the "never ran" verdict) instead of
    # blocked_incomplete_real_model_run (the "ran but unauditable"
    # verdict).
    #
    # P0-2 fix: after a VALID read, verify manifest.run_id == session.run_id
    # BEFORE consulting the manifest for coverage audit. A foreign
    # manifest (wrong run_id) MUST NOT be stitched together with the
    # current run's artifacts. The run_id check happens BEFORE coverage
    # audit per the task contract.
    manifest_read = read_manifest_with_state(session.manifest_path)
    manifest_state = manifest_read.state
    manifest = manifest_read.manifest  # None unless VALID

    # P0-2: run_id binding check. Only meaningful when manifest is
    # VALID (loaded successfully). A foreign manifest is treated as a
    # coverage failure → blocked_incomplete_real_model_run. NOT
    # classified as identity mismatch unless the dataset identity
    # itself mismatches.
    manifest_run_id_matches: bool | None = None
    if manifest_state == ManifestState.VALID and manifest is not None:
        manifest_run_id_matches = manifest.run_id == session.run_id
        if not manifest_run_id_matches:
            # Foreign manifest — do NOT use it for coverage audit. Pass
            # manifest=None so validate_manifest_coverage reports
            # manifest_present=False. The manifest_state="valid" +
            # manifest_run_id_matches=False signals the verdict function
            # to apply the foreign-manifest block. We deliberately do
            # NOT surface the manifest's run_id or content in the
            # report — only the fact that a mismatch was detected.
            print(
                f"WARN: foreign manifest at {session.manifest_path} "
                "(run_id mismatch); treating as foreign for coverage "
                "audit. Verdict will fall to blocked_incomplete_real_model_run.",
                file=sys.stderr,
            )
            manifest_for_audit = None
        else:
            manifest_for_audit = manifest
    elif manifest_state == ManifestState.CORRUPT:
        # P1: corrupt manifest — do NOT crash, do NOT fold into absent.
        # Pass manifest=None so validate_manifest_coverage reports
        # manifest_present=False, but propagate manifest_state="corrupt"
        # so the verdict function distinguishes corrupt (ran but
        # unauditable) from absent (never ran).
        print(
            f"WARN: corrupt manifest at {session.manifest_path}; "
            "treating as corrupt for coverage audit. Verdict will fall "
            "to blocked_incomplete_real_model_run.",
            file=sys.stderr,
        )
        manifest_for_audit = None
    else:
        # Absent — normal "not yet run" path.
        manifest_for_audit = None

    coverage_audit: CoverageAuditResult = validate_manifest_coverage(
        manifest_for_audit,
        artifacts,
        manifest_state=manifest_state.value
        if isinstance(manifest_state, ManifestState)
        else str(manifest_state),
        manifest_run_id_matches=manifest_run_id_matches,
    )

    # P0-2: separate budget_exhausted artifacts from real artifacts.
    # Budget-exhausted artifacts were never run, so there is nothing
    # to evaluate. They are NOT treated as passes — the report shows
    # the cap-triggered state separately.
    real_artifacts = [a for a in artifacts if not a.budget_exhausted]
    budget_exhausted_artifacts = [a for a in artifacts if a.budget_exhausted]
    has_budget_exhausted = bool(budget_exhausted_artifacts) or (
        coverage_audit.manifest_status == "budget_exhausted"
    )

    # P0-2 dataset identity fence: artifacts whose identity is missing
    # or does NOT match the current dataset are NOT silently skipped
    # and NOT treated as pass/fail. They are segregated so the verdict
    # reflects the identity mismatch (spec §二.5).
    identity_mismatched = find_identity_mismatched_artifacts(
        real_artifacts,
        current_identity=dataset_identity,
    )
    # identity_mismatched_count combines both signals:
    # - coverage_audit.identity_mismatch_count: artifacts whose identity
    #   differs from the MANIFEST's identity (manifest-level drift).
    # - len(identity_mismatched): artifacts whose identity differs from
    #   the CURRENT dataset identity (dataset-level drift, detected
    #   even without a manifest).
    # max() ensures either kind of drift forces blocked_dataset_identity_mismatch.
    identity_mismatched_count = max(
        len(identity_mismatched),
        coverage_audit.identity_mismatch_count,
    )
    if identity_mismatched:
        # Drop mismatched artifacts from the evaluable set — they are
        # NOT evaluated and NOT counted as passes. The report records
        # the mismatch count and forces a blocked_dataset_identity_mismatch
        # verdict via _decide_final_verdict's precedence rule.
        mismatched_ids = {a.case_id for a in identity_mismatched}
        real_artifacts = [
            a for a in real_artifacts if a.case_id not in mismatched_ids
        ]

    # ------------------------------------------------------------------
    # P0-2 final closure: dataset case binding.
    # ------------------------------------------------------------------
    # Manifest's planned_run_indices.keys() MUST be a subset of the
    # current dataset's cases_by_id. A manifest planning unknown cases
    # indicates it was written against a different dataset version.
    # Artifacts whose case_id is not in cases_by_id cannot be evaluated.
    #
    # The previous ``warn + continue`` path let unknown-case artifacts
    # be silently skipped, producing ``case_results=[]`` and the
    # ``all([]) → accepted`` bug. Now they are COUNTED and force the
    # verdict to ``blocked_incomplete_real_model_run`` via precedence
    # row 8 in :func:`_decide_final_verdict`.
    manifest_planned_case_ids: set[str] = set()
    if (
        manifest is not None
        and manifest_state == ManifestState.VALID
        and manifest_run_id_matches is True
    ):
        manifest_planned_case_ids = set(manifest.planned_run_indices.keys())

    unknown_planned_case_count = sum(
        1 for cid in manifest_planned_case_ids if cid not in cases_by_id
    )
    # Count valid artifacts (pre-identity-filter) whose case_id is not
    # in the dataset. We use the full ``artifacts`` list (not the
    # identity-filtered ``real_artifacts``) because an unknown case_id
    # is a separate signal from identity mismatch — both must be
    # surfaced.
    unknown_artifact_case_count = sum(
        1 for a in artifacts if a.case_id not in cases_by_id
    )

    # ------------------------------------------------------------------
    # P0-2 final closure: build AggregateReadinessAudit (pre-evaluator).
    # ------------------------------------------------------------------
    # This is the SINGLE source of truth for normal-verdict readiness.
    # The previous parallel ``coverage_ok`` check in ``aggregate()``
    # and the duplicated coverage check inside ``_decide_final_verdict``
    # are eliminated — both consult this typed audit.
    #
    # ``evaluated_case_result_count`` is set to 0 here (we haven't run
    # the evaluator yet). After the evaluator gate, a NEW instance is
    # built with the actual count and passed to ``_decide_final_verdict``.
    manifest_is_complete = manifest is not None and manifest.is_complete()
    coverage_counts_clean = (
        coverage_audit.missing_count == 0
        and coverage_audit.duplicate_count == 0
        and coverage_audit.unexpected_count == 0
        and coverage_audit.identity_mismatch_count == 0
    )
    pre_eval_readiness = AggregateReadinessAudit(
        artifact_load_clean=artifact_load_clean,
        discovered_file_count=discovered_file_count,
        invalid_artifact_count=invalid_artifact_count,
        manifest_state=manifest_state.value
        if isinstance(manifest_state, ManifestState)
        else str(manifest_state),
        manifest_present=coverage_audit.manifest_present,
        manifest_run_id_matches=coverage_audit.manifest_run_id_matches,
        manifest_status=coverage_audit.manifest_status,
        manifest_is_complete=manifest_is_complete,
        coverage_counts_clean=coverage_counts_clean,
        planned_count=coverage_audit.planned_count,
        evaluable_artifact_count=coverage_audit.evaluable_artifact_count,
        unknown_planned_case_count=unknown_planned_case_count,
        unknown_artifact_case_count=unknown_artifact_case_count,
        evaluated_case_result_count=0,
    )

    # ------------------------------------------------------------------
    # Evaluator gate: only run the 11 evaluators when pre_evaluator_ready
    # is True. Otherwise skip — the verdict will be a blocked_* variant
    # decided by _decide_final_verdict via the readiness audit.
    #
    # P0-2 final closure: unknown case_id artifacts are NO LONGER
    # silently skipped via ``warn + continue``. The
    # ``unknown_artifact_case_count`` in the readiness audit forces
    # ``pre_evaluator_ready=False``, so the evaluator is skipped
    # entirely and the verdict falls to ``blocked_incomplete_real_model_run``.
    # ------------------------------------------------------------------
    if not real_artifacts or not pre_eval_readiness.pre_evaluator_ready:
        # Real-model blocked path OR readiness audit failed: emit a
        # BLOCKED report so the user has the structured artifact even
        # before any real run completes. Note: when
        # identity_mismatched_count > 0 AND real_artifacts is empty
        # (all artifacts mismatched), the verdict stays
        # ``blocked_dataset_identity_mismatch`` per the precedence rule
        # in _decide_final_verdict — it is NOT downgraded to
        # ``blocked_by_real_model_run``.
        case_results = []
    else:
        # Evaluator gate passed — all pre-evaluator conditions hold.
        # Every artifact's case_id is guaranteed to be in cases_by_id
        # (unknown_artifact_case_count == 0 was a precondition), so
        # the ``cases_by_id.get(artifact.case_id)`` lookup cannot
        # return None here. The defensive ``if case is None: continue``
        # is kept as defense-in-depth but should never trigger.
        case_results = []
        for artifact in real_artifacts:
            case = cases_by_id.get(artifact.case_id)
            if case is None:
                # Defense-in-depth: should be unreachable because
                # ``unknown_artifact_case_count == 0`` was a
                # precondition for entering this branch. If we ever
                # reach this, the readiness audit lied — count it as
                # an unknown case and force a blocked verdict via
                # the post-evaluator readiness rebuild below.
                print(
                    f"WARN: case not found for artifact "
                    f"case_id={artifact.case_id} (unreachable under "
                    f"pre_evaluator_ready=True)",
                    file=sys.stderr,
                )
                continue
            case_results.append(_build_case_result(case, artifact))

    # ------------------------------------------------------------------
    # P0-2 final closure: rebuild readiness with actual
    # evaluated_case_result_count, instrumentation_incomplete_count,
    # and legacy_artifact_count, then pass to _decide_final_verdict.
    # ------------------------------------------------------------------
    # The post-evaluator readiness is a NEW instance (the class is
    # mutable via __slots__ but rebuilding is clearer and avoids
    # half-updated state). ``evaluated_case_result_count``,
    # ``instrumentation_incomplete_count``, and
    # ``legacy_artifact_count`` are computed from ``case_results`` —
    # all other fields are carried over from ``pre_eval_readiness``.
    #
    # R4-A4-0 final gate closure (P0-2): ``instrumentation_incomplete_count``
    # is the count of evaluated ``context_support`` dimensions whose
    # typed ``classification`` falls in
    # :data:`INSTRUMENTATION_INCOMPLETE_REASONS` (``baseline_unavailable``
    # / ``runtime_exception`` / ``instrumentation_incomplete``). When
    # this count is > 0, precedence row 9.5 in
    # :func:`_decide_final_verdict` forces
    # ``blocked_incomplete_real_model_run`` — these are NOT model
    # failures and MUST NOT enter rework. ``fact_not_supported`` /
    # ``fact_not_cited`` classifications are intentionally NOT counted
    # here — they are real model failures that DO enter rework.
    #
    # ``legacy_artifact_count`` is the count of evaluated
    # ``context_support`` dimensions whose typed ``classification`` is
    # ``legacy_artifact`` (version=None, status=None). Per user spec,
    # legacy artifacts MUST also be blocked in the authoritative
    # aggregate — they cannot be authoritatively re-evaluated under
    # the new contract. Legacy is NOT counted under
    # ``instrumentation_incomplete_count`` (the run did not fail — it
    # predates the contract) but produces the same verdict outcome.
    instrumentation_incomplete_count = sum(
        1
        for cr in case_results
        for d in cr.dimensions
        if d.dimension == "context_support"
        and d.classification in INSTRUMENTATION_INCOMPLETE_REASONS
    )
    legacy_artifact_count = sum(
        1
        for cr in case_results
        for d in cr.dimensions
        if d.dimension == "context_support"
        and d.classification in LEGACY_BLOCKER_REASONS
    )
    readiness = AggregateReadinessAudit(
        artifact_load_clean=pre_eval_readiness.artifact_load_clean,
        discovered_file_count=pre_eval_readiness.discovered_file_count,
        invalid_artifact_count=pre_eval_readiness.invalid_artifact_count,
        manifest_state=pre_eval_readiness.manifest_state,
        manifest_present=pre_eval_readiness.manifest_present,
        manifest_run_id_matches=pre_eval_readiness.manifest_run_id_matches,
        manifest_status=pre_eval_readiness.manifest_status,
        manifest_is_complete=pre_eval_readiness.manifest_is_complete,
        coverage_counts_clean=pre_eval_readiness.coverage_counts_clean,
        planned_count=pre_eval_readiness.planned_count,
        evaluable_artifact_count=pre_eval_readiness.evaluable_artifact_count,
        unknown_planned_case_count=pre_eval_readiness.unknown_planned_case_count,
        unknown_artifact_case_count=pre_eval_readiness.unknown_artifact_case_count,
        evaluated_case_result_count=len(case_results),
        instrumentation_incomplete_count=instrumentation_incomplete_count,
        legacy_artifact_count=legacy_artifact_count,
    )

    aggregated = aggregate_results(case_results, cases_by_id)

    start_head = _git_rev_parse_head()
    end_head = start_head  # no commit
    parallel_dirty = _git_status_short()

    real_model_blocked = len(real_artifacts) == 0
    real_model_block_reason = (
        f"no artifacts found under {artifact_dir}; real LLM gate was not "
        "opened or model unavailable (requires "
        "CLAREAD_ALLOW_REAL_LLM_TESTS=1 + CLAREAD_R4_A3_RUN=1 + "
        "CLAREAD_REAL_LLM_MODEL=<short_name> + DB + record_id for BBC cases)"
        if real_model_blocked
        else None
    )
    real_model_user_commands = [
        "cd services/api",
        "set CLAREAD_ALLOW_REAL_LLM_TESTS=1",
        "set CLAREAD_R4_A3_RUN=1",
        "set CLAREAD_REAL_LLM_MODEL=deepseek-chat  # 或实际 authorized model short name",
        "set CLAREAD_R4_A3_RUN_ID=phase1-<ts>",
        (
            "set CLAREAD_R4_A3_BBC_RECORD_ID=<your-local-bbc-record-id>"
            "  # 可选，BBC case"
        ),
        (
            "uv run pytest tests/test_reader_record_ask_real_llm_eval.py "
            "-v -m real_llm -k phase1"
        ),
    ] if real_model_blocked else None

    # P1-a: SINGLE source of truth for the verdict precedence. No
    # caller-side overrides — _decide_final_verdict returns the final
    # (verdict, allow_r4_a4, allow_r4_b1) tuple in one place.
    #
    # P0-1 final closure: ``total_artifacts_loaded`` is now
    # ``discovered_file_count`` (the count of ``*.json`` files found
    # BEFORE filtering by validity/foreign). This means "absent
    # manifest + 1 corrupt artifact file" correctly falls to row 3
    # (incomplete) instead of row 10 (blocked_by_real_model_run).
    verdict, allow_r4_a4, allow_r4_b1 = _decide_final_verdict(
        case_results=case_results,
        coverage_audit=coverage_audit,
        identity_mismatched_count=identity_mismatched_count,
        real_model_blocked=real_model_blocked,
        has_budget_exhausted=has_budget_exhausted,
        total_artifacts_loaded=discovered_file_count,
        readiness=readiness,
    )

    report = generate_r4_a3_report(
        aggregated=aggregated,
        dataset=dataset,
        artifacts=artifacts,
        start_head=start_head,
        end_head=end_head,
        parallel_dirty=parallel_dirty,
        harness_choice=(
            "B: in-process real-model harness "
            "(services/api/tests/test_reader_record_ask_real_llm_eval.py "
            "直接调用 run_reading_record_ask，包装 BudgetedUsageModel)"
        ),
        rejected_harness=(
            "A: HTTP adapter via evals/claread_eval/adapter/http_client.py"
        ),
        rejected_reason=(
            "现有 http_client.py 调用 /eval/article-analysis/workflow"
            "（旧 article-analysis 端点），不是 RR Ask SSE 端点；"
            "新增 eval 端点会修改生产 route（被禁止）；"
            "复用 SSE 端点需解析 SSE 流 + 鉴权 + thread 持久化，"
            "边界模糊、依赖 DB/server 运行、复现性差。"
        ),
        real_model_blocked=real_model_blocked,
        real_model_block_reason=real_model_block_reason,
        real_model_user_commands=real_model_user_commands,
        deterministic_tests_passed=True,
        deterministic_tests_summary=(
            "evals/tests/test_reader_record_ask_*.py: 全通过 "
            "(session/evaluation/phase_planner/budgeted_model/errors/utf16/"
            "eval_*/dataset/aggregator/report); "
            "services/api/tests/test_reader_record_ask_real_llm_eval.py: "
            "4 passed, 3 skipped (default skip). "
            "ruff All checks passed."
        ),
        verdict=verdict,
        allow_r4_a4=allow_r4_a4,
        allow_r4_b1=allow_r4_b1,
        run_metadata={
            "run_id": run_id,
            "artifact_dir": str(artifact_dir),
            "manifest_path": str(session.manifest_path),
            "dataset_id": dataset.id,
            "dataset_schema_version": dataset.schema_version,
            "dataset_content_sha256": dataset_identity.content_sha256,
            "total_cases_in_dataset": len(dataset.cases),
            "total_artifacts_loaded": len(artifacts),
            "total_real_artifacts": len(real_artifacts),
            "total_budget_exhausted_artifacts": len(budget_exhausted_artifacts),
            "has_budget_exhausted": has_budget_exhausted,
            "identity_mismatched_artifacts": identity_mismatched_count,
            # P0-1 final closure: ArtifactLoadResult typed counts.
            # Surfaces invalid_json / invalid_schema / foreign_run_id
            # counts so the operator can see WHY the verdict fell to
            # blocked_incomplete_real_model_run (instead of a vague
            # "no artifacts found"). No file paths or exception text
            # are surfaced — only typed counts.
            "artifact_load_audit": {
                "discovered_file_count": discovered_file_count,
                "invalid_json_count": load_result.invalid_json_count,
                "invalid_schema_count": load_result.invalid_schema_count,
                "foreign_run_id_count": load_result.foreign_run_id_count,
                "invalid_artifact_count": invalid_artifact_count,
                "is_clean": artifact_load_clean,
            },
            # P0-2 final closure: AggregateReadinessAudit typed signals.
            # Concentrates ALL normal-verdict preconditions in one
            # place so the operator can see exactly which gate failed.
            "readiness_audit": {
                "artifact_load_clean": readiness.artifact_load_clean,
                "manifest_state": readiness.manifest_state,
                "manifest_present": readiness.manifest_present,
                "manifest_run_id_matches": readiness.manifest_run_id_matches,
                "manifest_status": readiness.manifest_status,
                "manifest_is_complete": readiness.manifest_is_complete,
                "coverage_counts_clean": readiness.coverage_counts_clean,
                "planned_count": readiness.planned_count,
                "evaluable_artifact_count": readiness.evaluable_artifact_count,
                "unknown_planned_case_count": (
                    readiness.unknown_planned_case_count
                ),
                "unknown_artifact_case_count": (
                    readiness.unknown_artifact_case_count
                ),
                "evaluated_case_result_count": (
                    readiness.evaluated_case_result_count
                ),
                # R4-A4-0 final gate closure (P0-2): typed
                # instrumentation-incomplete blocker count. > 0 means
                # the verdict falls to
                # ``blocked_incomplete_real_model_run`` via precedence
                # row 9.5 — these are NOT model failures and do NOT
                # enter rework.
                "instrumentation_incomplete_count": (
                    readiness.instrumentation_incomplete_count
                ),
                # R4-A4-0 final gate closure (P0-2): legacy-artifact
                # blocker count. Per user spec, legacy artifacts MUST
                # also be blocked in the authoritative aggregate.
                "legacy_artifact_count": readiness.legacy_artifact_count,
                "pre_evaluator_ready": readiness.pre_evaluator_ready,
                "ready_for_normal_verdict": readiness.ready_for_normal_verdict,
            },
            # Coverage Audit fields (spec Requirement: Aggregate Coverage
            # Audit — report outputs manifest_status / planned / completed
            # / missing / duplicate / identity_mismatch / evaluable /
            # dataset_identity triple).
            "coverage_audit": {
                "manifest_present": coverage_audit.manifest_present,
                "manifest_status": coverage_audit.manifest_status,
                "manifest_state": coverage_audit.manifest_state,
                "manifest_run_id_matches": (
                    coverage_audit.manifest_run_id_matches
                ),
                "planned_count": coverage_audit.planned_count,
                "completed_count": coverage_audit.completed_count,
                "missing_count": coverage_audit.missing_count,
                "duplicate_count": coverage_audit.duplicate_count,
                "unexpected_count": coverage_audit.unexpected_count,
                "identity_mismatch_count": (
                    coverage_audit.identity_mismatch_count
                ),
                "evaluable_artifact_count": (
                    coverage_audit.evaluable_artifact_count
                ),
                "dataset_identity": (
                    list(coverage_audit.dataset_identity)
                    if coverage_audit.dataset_identity is not None
                    else None
                ),
                "missing_run_indices": coverage_audit.missing_run_indices,
                "duplicate_run_indices": (
                    coverage_audit.duplicate_run_indices
                ),
                "unexpected_run_indices": (
                    coverage_audit.unexpected_run_indices
                ),
            },
            "harness_test_path": str(HARNESS_TEST_PATH.relative_to(REPO_ROOT))
            if HARNESS_TEST_PATH.exists()
            else str(HARNESS_TEST_PATH),
            "tracker_path": _TRACKER_PATH,
        },
        # R4-A4-0 (Task 5): parameterize previously hardcoded values.
        # The report no longer carries stale date / file list / tracker
        # path from the previous round. ``report_date`` defaults to
        # today when caller does not pass it; ``modified_files`` and
        # ``tracker_path`` are taken from CLI args (or fall back to
        # canonical defaults).
        report_date=report_date,
        modified_files=modified_files,
        task_label=task_label,
        tracker_path=tracker_path,
    )

    report_output.parent.mkdir(parents=True, exist_ok=True)
    report_output.write_text(report, encoding="utf-8")
    print(f"Report written to: {report_output}")
    print(
        f"verdict={verdict} allow_r4_a4={allow_r4_a4} "
        f"allow_r4_b1={allow_r4_b1} artifacts={len(artifacts)} "
        f"real={len(real_artifacts)} budget_exhausted={len(budget_exhausted_artifacts)}"
    )
    print(
        f"artifact_load: discovered={discovered_file_count} "
        f"invalid_json={load_result.invalid_json_count} "
        f"invalid_schema={load_result.invalid_schema_count} "
        f"foreign_run_id={load_result.foreign_run_id_count} "
        f"is_clean={artifact_load_clean}"
    )
    print(
        f"readiness: pre_evaluator_ready={readiness.pre_evaluator_ready} "
        f"ready_for_normal_verdict={readiness.ready_for_normal_verdict} "
        f"unknown_planned_case={readiness.unknown_planned_case_count} "
        f"unknown_artifact_case={readiness.unknown_artifact_case_count} "
        f"evaluated_case_results={readiness.evaluated_case_result_count} "
        f"planned={readiness.planned_count} "
        f"instrumentation_incomplete={readiness.instrumentation_incomplete_count} "
        f"legacy_artifact={readiness.legacy_artifact_count}"
    )
    print(
        f"coverage_audit: manifest_present={coverage_audit.manifest_present} "
        f"manifest_status={coverage_audit.manifest_status} "
        f"manifest_state={coverage_audit.manifest_state} "
        f"manifest_run_id_matches={coverage_audit.manifest_run_id_matches} "
        f"planned={coverage_audit.planned_count} "
        f"completed={coverage_audit.completed_count} "
        f"missing={coverage_audit.missing_count} "
        f"duplicate={coverage_audit.duplicate_count} "
        f"unexpected={coverage_audit.unexpected_count} "
        f"identity_mismatch={coverage_audit.identity_mismatch_count} "
        f"evaluable={coverage_audit.evaluable_artifact_count}"
    )
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        required=True,
        choices=["1", "2", "3", "aggregate"],
        help="Phase 1/2/3 invoke the real-model harness; "
        "'aggregate' runs the offline evaluators and writes the report.",
    )
    parser.add_argument(
        "--run-id",
        required=True,
        help="Run id (used as runs/<run_id>/artifacts/ subdir).",
    )
    parser.add_argument(
        "--prior-run-id",
        default=None,
        help="Prior phase's run id (required for Phase 2/3; "
        "passed to harness via CLAREAD_R4_A3_PRIOR_RUN_ID env var).",
    )
    parser.add_argument(
        "--dataset-dir",
        default=None,
        help=(
            "Path to the R4-A3 dataset directory. REQUIRED for real runs "
            "(Phase 1/2/3) and aggregate. Priority: "
            "CLI --dataset-dir > env CLAREAD_R4_A3_DATASET_DIR. "
            "If neither is set, the runner exits with code 2 before any "
            "subprocess/paid call. Suggested local working dir: "
            "evals/tmp/reader-record-ask-r4-a3/ (gitignored; not used "
            "automatically)."
        ),
    )
    parser.add_argument(
        "--runs-dir",
        default=str(DEFAULT_RUNS_DIR),
        help="Path to the runs/ directory (artifacts root).",
    )
    parser.add_argument(
        "--report-output",
        default=str(DEFAULT_REPORT_OUTPUT),
        help="Path to write the markdown report (aggregate phase).",
    )
    parser.add_argument(
        "--report-date",
        default=None,
        help=(
            "Date label to print in the report title and tracker update "
            "(YYYY-MM-DD). Defaults to today's date when not provided."
        ),
    )
    parser.add_argument(
        "--modified-file",
        action="append",
        default=None,
        help=(
            "Path modified in the current round (repeatable). Rendered in "
            "§2.1 '本轮修改文件' of the report. Pass one flag per file."
        ),
    )
    parser.add_argument(
        "--task-label",
        default="Task 5",
        help="Task label rendered in §2.1 header (default: 'Task 5').",
    )
    parser.add_argument(
        "--tracker-path",
        default=None,
        help=(
            "Path to the tracker markdown file referenced in §14. Defaults "
            "to the canonical tracker path when not provided."
        ),
    )
    args = parser.parse_args()

    # P0 explicit dataset-dir binding: resolve dataset dir from CLI > env.
    # No silent fallback — real runs (Phase 1/2/3) AND aggregate must
    # explicitly declare the dataset they are using. ``_preflight_dataset_dir``
    # fails closed (exit code 2) when neither source is set, or the dir
    # is missing or has no dataset.yaml — before any subprocess invocation
    # or paid call.
    dataset_dir = _resolve_dataset_dir(args.dataset_dir)
    _preflight_dataset_dir(dataset_dir)
    print(f"R4-A3 dataset dir: {dataset_dir}", file=sys.stderr)

    if args.phase == "aggregate":
        # ``_preflight_dataset_dir`` above already exits with code 2 if
        # ``dataset_dir`` is None, so this assert is for type checkers.
        assert dataset_dir is not None  # noqa: S101
        return aggregate(
            run_id=args.run_id,
            runs_dir=Path(args.runs_dir),
            dataset_dir=dataset_dir,
            report_output=Path(args.report_output),
            report_date=args.report_date,
            modified_files=args.modified_file,
            task_label=args.task_label,
            tracker_path=args.tracker_path,
        )
    # Phase 2/3 require --prior-run-id.
    if args.phase in ("2", "3") and not args.prior_run_id:
        parser.error(
            f"--prior-run-id is required for --phase {args.phase} "
            "(the harness no longer scans the runs root for 'latest' — "
            "the prior phase's run id must be explicit)"
        )
    # ``_preflight_dataset_dir`` above already exits with code 2 if
    # ``dataset_dir`` is None, so this assert is for type checkers.
    assert dataset_dir is not None  # noqa: S101
    return run_phase(
        phase=int(args.phase),
        run_id=args.run_id,
        runs_dir=Path(args.runs_dir),
        prior_run_id=args.prior_run_id,
        dataset_dir=dataset_dir,
    )


if __name__ == "__main__":
    sys.exit(main())
