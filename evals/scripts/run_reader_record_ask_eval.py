"""Reader Record Ask real-LLM evaluation runner.

Usage (run from ``evals/``)::

    # Real LLM run (requires env gate)
    CLAREAD_ALLOW_REAL_LLM_TESTS=1 CLAREAD_R4_A3_RUN=1 CLAREAD_REAL_LLM_MODEL=deepseek-chat \\
        uv run python scripts/run_reader_record_ask_eval.py --phase 1 --run-id phase1-<ts>

    # The prior-run-id points at the initial run
    CLAREAD_ALLOW_REAL_LLM_TESTS=1 CLAREAD_R4_A3_RUN=1 CLAREAD_REAL_LLM_MODEL=deepseek-chat \\
        uv run python scripts/run_reader_record_ask_eval.py --phase 2 \\
        --run-id phase2-<ts> --prior-run-id phase1-<ts>

    # The next follow-up's prior-run-id points at the preceding run
    CLAREAD_ALLOW_REAL_LLM_TESTS=1 CLAREAD_R4_A3_RUN=1 CLAREAD_REAL_LLM_MODEL=deepseek-pro \\
        CLAREAD_R4_A3_PRO_PROFILE=<pro_profile> \\
        uv run python scripts/run_reader_record_ask_eval.py --phase 3 \\
        --run-id phase3-<ts> --prior-run-id phase2-<ts>

    # Aggregate: load artifacts, run 11 evaluators, generate report.
    uv run python scripts/run_reader_record_ask_eval.py --phase aggregate --run-id <id> \\
        --report-output ../evals/tmp/reader-record-ask-r4-a3/review/\\
TMP-reader-record-ask-r4-a3-eval-2026-07-17.md

Rework closure:

- ``--prior-run-id`` is required for follow-up stages and is passed to the
  harness via ``CLAREAD_R4_A3_PRIOR_RUN_ID`` env var. No more scanning
  the runs root for "latest".
- Aggregate uses :class:`RunSessionLayout` to resolve the artifact
  directory: ``<runs_root>/<run_id>/artifacts/``. This is the same
  resolver the harness uses to write artifacts, so writer and reader
  cannot diverge.
- Aggregate uses :func:`evaluate_artifact` as the single 11-dimension
  evaluator entrypoint — the runner no longer duplicates the
  evaluator list. This was the root cause of terminal-ok artifacts
  hiding content-quality failures.
- Aggregate handles ``budget_exhausted`` artifacts: they are NOT
  evaluated (no content to evaluate) and NOT treated as passes.
  The report shows the cap-triggered state.

All run stages invoke the in-process real-model harness at
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
from types import SimpleNamespace
from typing import Any

from claread_eval.reader_record_ask.evaluators.context_support_contract import (
    INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS as INSTRUMENTATION_INCOMPLETE_REASONS,
)
from claread_eval.reader_record_ask.evaluators.context_support_contract import (
    LEGACY_BLOCKER_CLASSIFICATIONS as LEGACY_BLOCKER_REASONS,
)

EVALS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = EVALS_ROOT.parent
# Dataset Git governance: the Reader Record Ask working dataset lives under
# ``evals/tmp/`` which is gitignored by the ``**/tmp/`` rule. The
# tracked ``evals/datasets/`` tree hosts canonical datasets like
# ``vocabulary-seed-v1`` only — the Reader Record Ask working dataset is local,
# env-bound, and never committed.
#
# Explicit dataset-dir binding: the previous ``DEFAULT_DATASET_DIR``
# was used as a silent fallback when neither ``--dataset-dir`` nor
# ``CLAREAD_R4_A3_DATASET_DIR`` was set. That allowed real runs to
# accidentally reuse a stale local working dataset. The constant is
# now ONLY a suggested path printed in help text — never used for
# automatic resolution.
SUGGESTED_DATASET_DIR = EVALS_ROOT / "tmp" / "reader-record-ask-r4-a3"
DEFAULT_RUNS_DIR = SUGGESTED_DATASET_DIR / "runs"
DEFAULT_REPORT_OUTPUT = (
    REPO_ROOT / "evals" / "tmp" / "reader-record-ask-r4-a3" / "review"
    / "TMP-reader-record-ask-r4-a3-eval-2026-07-17.md"
)
HARNESS_TEST_PATH = (
    REPO_ROOT / "services" / "api" / "tests"
    / "test_reader_record_ask_real_llm_eval.py"
)
HARNESS_CWD = REPO_ROOT / "services" / "api"

_TRACKER_PATH = (
    "evals/tmp/reader-record-ask-r4-a3/"
    "TMP-reader-record-ask-r4-a3-product-ready-tracker-2026-07-17.md"
)

# Dataset dir env var — shared with the in-process harness. Priority:
#   CLI ``--dataset-dir`` > env ``CLAREAD_R4_A3_DATASET_DIR``.
# Real runs MUST set one of these — the runner exits with code 2 before
# invoking the pytest subprocess when neither is provided. The harness
# also fail-closes before any paid call when the env is missing.
DATASET_DIR_ENV = "CLAREAD_R4_A3_DATASET_DIR"

# Provider request cap env var — SAME name the
# in-process harness reads (services/api/tests/test_reader_record_ask_
# real_llm_eval.py:_DEFAULT_MAX_REQUESTS + MAX_REQUESTS_ENV).
# The aggregate reads this env to surface ``request_cap`` in
# ``run_metadata.budget_semantics`` so the operator can see the
# configured cap alongside the planned logical runs and retry
# headroom. The aggregate NEVER falls back to a default — when the
# env is unset, ``request_cap`` is surfaced as ``None`` (the harness
# may have used its own default; the aggregate cannot know it
# authoritatively without a manifest field, which we deliberately do
# not add to avoid expanding the manifest schema in this round).
MAX_REQUESTS_ENV = "CLAREAD_R4_A3_MAX_REQUESTS"


# ---------------------------------------------------------------------------
# Planned logical runs vs provider request cap
# ---------------------------------------------------------------------------


def _resolve_request_cap_from_env() -> int | None:
    """Resolve the configured provider request cap from
    ``CLAREAD_R4_A3_MAX_REQUESTS``.

    Returns the int cap, or ``None`` when the env var is unset or
    whitespace-only (the harness may still apply its own default —
    the aggregate surfaces ``None`` rather than guessing).

    Raises ``ValueError`` when the env var is set but is not a valid
    non-negative int. This is fail-closed: a malformed cap should
    not silently produce wrong ``retry_headroom`` arithmetic.

    The same env var the in-process harness reads — keeping the
    same resolution rule ensures the aggregate's ``request_cap``
    matches what the harness actually used (when the operator set
    the env explicitly).
    """
    raw = os.environ.get(MAX_REQUESTS_ENV, "").strip()
    if not raw:
        return None
    try:
        cap = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{MAX_REQUESTS_ENV}={raw!r} is not a valid int"
        ) from exc
    if cap < 0:
        raise ValueError(
            f"{MAX_REQUESTS_ENV}={cap} must be non-negative"
        )
    return cap


def _compute_budget_semantics(
    *,
    manifest: Any | None,
    request_cap: int | None,
) -> SimpleNamespace:
    """Extract the five typed budget-semantics
    fields from a manifest (self-contained) + env-configured request
    cap (V1 fallback only).

    The current contract changes the earlier contract:

    - V2 manifests (``audit_contract_version == "r4-a4-2r3"``): the
      aggregate reads ``planned_logical_runs``, ``request_cap``,
      ``retry_headroom``, and ``retry_policy`` from the manifest
      ONLY — NO env fallback. The V2 contract (Rules 18d/18f/18g/18h)
      guarantees these fields are present and consistent. The
      ``request_cap`` parameter (env-derived) is IGNORED for V2
      manifests. ``retry_headroom`` is read directly from the
      manifest field (NOT recomputed) — V2 Rule 18h guarantees
      ``retry_headroom == request_cap - planned_logical_runs``.
    - V1 manifests (``audit_contract_version == "r4-a4-2r2"`` or
      ``None``): backwards-compat — the env-configured
      ``request_cap`` parameter is used as a fallback when the
      manifest field is absent. ``retry_headroom`` is recomputed as
      ``request_cap - planned_logical_runs`` (matching the V1
      behavior).

    Changes retained for V1:

    - ``planned_logical_runs`` is read from the manifest's
      ``planned_logical_runs`` field when present. Falls back to
      ``sum(len(planned_run_indices.values()))`` for older
      manifests.
    - ``request_cap`` is read from the manifest's ``request_cap``
      field when present. Falls back to the env-configured
      ``request_cap`` parameter for V1 backwards compat.
    - ``retry_headroom`` is ``request_cap - planned_logical_runs``
      (V1) or read from manifest (V2). When ``request_cap`` is None
      (V1 manifest + env both absent), ``retry_headroom`` is None.
    - ``retries_consumed`` = ``executed_requests - actual_completed_runs``
      (unchanged).

    Returns a :class:`types.SimpleNamespace` with:

    - ``planned_logical_runs`` (int): manifest field if present, else
      sum of list lengths in ``manifest.planned_run_indices``. Zero
      when manifest is None.
    - ``request_cap`` (int | None): V2 — manifest field (guaranteed
      non-null by Rule 18g). V1 — manifest field if present, else
      the env-configured ``request_cap`` parameter.
    - ``actual_completed_runs`` (int): sum of list lengths in
      ``manifest.completed_run_indices``. Zero when manifest is None.
    - ``retry_headroom`` (int | None): V2 — manifest field (guaranteed
      non-null by Rule 18f). V1 — ``request_cap - planned_logical_runs``,
      or ``None`` when ``request_cap`` is None.
    - ``retries_consumed`` (int): ``manifest.executed_requests -
      actual_completed_runs``. Zero when manifest is None. MUST be
      ≥ 0 — each completed run consumed at least 1 request, so
      ``executed_requests < actual_completed_runs`` indicates a
      corrupt manifest and raises ``ValueError`` (fail-closed).

    The previous report conflated
    ``planned_logical_runs`` (case universe size) with
    ``request_cap`` (provider call budget). An audit found
    "30 planned runs 共消耗 30 provider requests，但 3 次 output
    retry 导致只完成 27 runs" — the operator could not tell from
    the report whether 27/30 was a coverage gap (3 cases never ran)
    or a retry-overflow (3 cases ran but exceeded the cap on
    retries). The five typed fields make this distinction
    auditable.

    The earlier implementation still fell
    back to the CURRENT shell env for ``request_cap`` when the
    manifest field was absent. Re-running the aggregate against a
    historical V2 run with different env produced wrong
    ``retry_headroom`` arithmetic. The fix: V2 manifests are fully
    self-contained — the aggregate NEVER consults the env for V2.
    The env fallback is preserved ONLY for V1 backwards-compat.

    Informational only: ``_decide_final_verdict`` does NOT consult
    these fields — the verdict still falls to
    ``blocked_incomplete_real_model_run`` when
    ``actual_completed_runs < planned_logical_runs`` via the
    existing coverage gap precedence row. The budget semantics are
    surfaced in ``run_metadata.budget_semantics`` for operator
    observability, not for verdict routing.
    """
    if manifest is None:
        return SimpleNamespace(
            planned_logical_runs=0,
            request_cap=request_cap,
            actual_completed_runs=0,
            retry_headroom=request_cap,  # cap - 0 planned
            retries_consumed=0,
        )
    # Determine manifest version. V2 manifests are
    # fully self-contained — NO env fallback. V1 manifests retain
    # the env fallback for backwards-compat.
    audit_contract_version = getattr(manifest, "audit_contract_version", None)
    is_v2 = audit_contract_version == "r4-a4-2r3"

    # Prefer the manifest's planned_logical_runs field
    # (self-contained) over recomputing from planned_run_indices.
    # Falls back to recomputation for older manifests.
    planned_logical_runs = int(getattr(manifest, "planned_logical_runs", 0))
    if planned_logical_runs == 0:
        planned_logical_runs = sum(
            len(v) for v in manifest.planned_run_indices.values()
        )
    # request_cap resolution differs by version.
    manifest_request_cap = getattr(manifest, "request_cap", None)
    if is_v2:
        # V2: manifest-only — NO env fallback. V2 Rule 18g guarantees
        # ``request_cap`` is a non-negative int (NOT null). If the
        # manifest is V2 but ``request_cap`` is None, the manifest
        # is corrupt (Rule 18g should have rejected it at parse time).
        # Defense-in-depth: surface None rather than falling back to env.
        effective_request_cap: int | None = (
            int(manifest_request_cap) if manifest_request_cap is not None else None
        )
    else:
        # V1: prefer manifest's request_cap (self-contained) over the
        # env-configured fallback. This preserves the earlier behavior
        # for V1 manifests.
        if manifest_request_cap is not None:
            effective_request_cap = int(manifest_request_cap)
        else:
            effective_request_cap = request_cap
    actual_completed_runs = sum(
        len(v) for v in manifest.completed_run_indices.values()
    )
    executed_requests = int(manifest.executed_requests)
    if executed_requests < actual_completed_runs:
        raise ValueError(
            f"executed_requests={executed_requests} < "
            f"actual_completed_runs={actual_completed_runs}: corrupt "
            "manifest (each completed run consumes at least 1 request)"
        )
    retries_consumed = executed_requests - actual_completed_runs
    # retry_headroom resolution differs by version.
    manifest_retry_headroom = getattr(manifest, "retry_headroom", None)
    if is_v2:
        # V2: read directly from the manifest. V2 Rule 18f guarantees
        # ``retry_headroom`` is a non-negative int (NOT null). V2 Rule
        # 18h guarantees ``retry_headroom == request_cap -
        # planned_logical_runs``. We do NOT recompute — we trust the
        # manifest's persisted value (validated at write time).
        retry_headroom: int | None = (
            int(manifest_retry_headroom)
            if manifest_retry_headroom is not None
            else None
        )
    else:
        # V1: recompute as request_cap - planned_logical_runs
        # (matching the V1 behavior). ``None`` when request_cap
        # is None (manifest + env both absent).
        if effective_request_cap is None:
            retry_headroom = None
        else:
            retry_headroom = effective_request_cap - planned_logical_runs
    return SimpleNamespace(
        planned_logical_runs=planned_logical_runs,
        request_cap=effective_request_cap,
        actual_completed_runs=actual_completed_runs,
        retry_headroom=retry_headroom,
        retries_consumed=retries_consumed,
    )


# ---------------------------------------------------------------------------
# Dataset dir resolution (explicit binding — no silent fallback)
# ---------------------------------------------------------------------------


def _resolve_dataset_dir(cli_value: str | None) -> Path | None:
    """Resolve the evaluation dataset dir from CLI flag or env (no fallback).

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
    env_val = os.environ.get(DATASET_DIR_ENV, "").strip()
    if env_val:
        return Path(env_val).resolve()
    return None


def _preflight_dataset_dir(dataset_dir: Path | None) -> None:
    """Fail-closed when the dataset dir is missing or has no dataset.yaml.

    Explicit dataset-dir binding: real runs MUST have an explicitly
    configured dataset dir (CLI or env). If neither is provided, or
    the resolved dir doesn't exist or doesn't contain ``dataset.yaml``,
    the runner exits BEFORE invoking the pytest harness subprocess —
    so no paid provider call can be made.
    """
    if dataset_dir is None:
        print(
            "ERROR: evaluation dataset dir not configured.\n"
            f"Set {DATASET_DIR_ENV}=<path> or pass --dataset-dir <path>. "
            f"Suggested local working dir: {SUGGESTED_DATASET_DIR} "
            "(gitignored; not used automatically).",
            file=sys.stderr,
        )
        sys.exit(2)
    if not dataset_dir.is_dir():
        print(
            f"ERROR: evaluation dataset dir not found: {dataset_dir}\n"
            f"Set {DATASET_DIR_ENV}=<path> or pass --dataset-dir <path>.",
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
    for follow-up stages) so the harness writes artifacts under
    ``<runs_dir>/<run_id>/artifacts/`` with the requested run_id and
    reads prior-phase artifacts from
    ``<runs_dir>/<prior_run_id>/artifacts/``.

    Explicit dataset-dir binding: ``dataset_dir`` is propagated to
    the subprocess via ``CLAREAD_R4_A3_DATASET_DIR`` env var so the
    in-process harness resolves the same dataset. ``main()`` calls
    ``_preflight_dataset_dir`` BEFORE this function, so ``dataset_dir``
    is always a resolved, validated ``Path`` here (never ``None``).
    The harness itself has NO silent fallback — when
    ``CLAREAD_R4_A3_DATASET_DIR`` is missing, the harness
    ``pytest.skip``s before any provider call.

    ``runs_dir`` is normalized to an absolute
    canonical path BEFORE propagating to the subprocess via
    ``CLAREAD_R4_A3_RUNS_DIR`` env var. The subprocess has
    ``cwd=services/api/`` — without normalization, a relative
    ``runs_dir`` would be re-resolved against the subprocess cwd,
    producing the historical ``services/services/api/tmp/...``
    double-resolution bug. After normalization the env var is
    absolute, so the subprocess cwd cannot re-resolve it. This
    normalization at the function entry protects BOTH ``main()``
    callers (which already pre-normalize) and direct programmatic
    callers (which may pass a relative path).
    """
    # Normalize at function entry — defense-in-depth
    # so direct callers (not just main()) are protected.
    runs_dir = runs_dir.resolve()
    env = {
        **os.environ,
        "CLAREAD_R4_A3_RUN_ID": run_id,
        "CLAREAD_R4_A3_RUNS_DIR": str(runs_dir),
    }
    if dataset_dir is not None:
        env[DATASET_DIR_ENV] = str(dataset_dir)
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
# AggregateReadinessAudit — single source of truth for normal-verdict
# readiness. Concentrates ALL conditions that must hold before the normal
# accepted/rework path may be entered. Replaces the parallel ``coverage_ok``
# check that previously lived in ``aggregate()`` + the duplicated coverage
# check inside ``_decide_final_verdict``.
# ---------------------------------------------------------------------------

# Classification reason tags
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
# the same: blocked_incomplete_real_model_run with both downstream gates
# disabled. Kept as a separate set
# (:data:`LEGACY_BLOCKER_CLASSIFICATIONS`) so the
# ``instrumentation_incomplete_count`` audit field stays semantically
# narrow (only the 3 instrumentation-incomplete reasons).


class AggregateReadinessAudit:
    """Typed audit result concentrating ALL normal-verdict preconditions.

    Spec: aggregate readiness contract.
    Requirement: Evaluation Completeness.

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
      Dataset case binding — a manifest planning unknown cases
      indicates the manifest was written against a different dataset
      version and MUST NOT be stitched together with the current run.
    - ``unknown_artifact_case_count``: count of valid artifacts whose
      ``case_id`` is NOT in the current dataset's ``cases_by_id``.
      Dataset case binding — an artifact referencing an unknown
      case cannot be evaluated and MUST NOT be silently skipped
      (the previous ``warn + continue`` path let ``case_results=[]``
      reach ``_decide_normal_verdict``, which returned ``accepted``
      via ``all([])``).
    - ``evaluated_case_result_count``: ``len(case_results)`` after
      the evaluator gate. Must equal ``planned_count`` AND be > 0.
    - ``instrumentation_incomplete_count``: Count of evaluated
      ``context_support`` dimensions whose
      typed ``classification`` falls in
      :data:`INSTRUMENTATION_INCOMPLETE_REASONS` (``baseline_unavailable``
      / ``runtime_exception`` / ``instrumentation_incomplete``). These
      are instrumentation/run-incomplete blockers — NOT model
      correctness failures. They MUST NOT enter rework, MUST NOT count
      as ``confirmed_model_failure``, and MUST NOT cluster as
      ``fact-not-grounded``. The verdict falls to
      ``blocked_incomplete_real_model_run`` with
      both downstream gates disabled via precedence row 9.5
      in :func:`_decide_final_verdict`. ``fact_not_supported`` /
      ``fact_not_cited`` classifications are NOT counted here — they
      are real model failures that DO enter rework.
    - ``legacy_artifact_count``: Count of legacy artifacts.
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
        # Instrumentation-incomplete
        # blocker count. See class docstring above.
        "instrumentation_incomplete_count",
        # Legacy-artifact blocker
        # count. Legacy artifacts (version=None, status=None) cannot
        # be authoritatively re-evaluated under the new contract —
        # the authoritative aggregate MUST block them and require new
        # artifacts. NOT counted under ``instrumentation_incomplete_count``
        # because the run did not fail — it predates the contract.
        "legacy_artifact_count",
        # Three-layer runtime fixture identity
        # mismatch count (dataset expected != manifest identity OR
        # manifest identity != artifact actual OR dataset expected !=
        # artifact actual). Artifacts failing this check are dropped
        # from the evaluable set in :func:`aggregate`; the verdict
        # falls to ``blocked_incomplete_real_model_run`` via
        # precedence row 5.6 in :func:`_decide_final_verdict`. The
        # mismatch is NOT display-only — the artifact is excluded
        # from evaluation and counted as a blocker.
        "runtime_fixture_identity_mismatch_count",
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
        runtime_fixture_identity_mismatch_count: int = 0,
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
        self.runtime_fixture_identity_mismatch_count = (
            runtime_fixture_identity_mismatch_count
        )

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
            # Instrumentation-incomplete
            # blockers (capture_status=unavailable/failed, fingerprint
            # mismatch, missing required observation, duplicate/unknown
            # observation, supporting handle not in model context) MUST
            # NOT reach the normal accepted/rework path. They are NOT
            # model correctness failures.
            and self.instrumentation_incomplete_count == 0
            # Legacy artifacts also
            # MUST NOT reach the normal accepted/rework path — they
            # cannot be authoritatively re-evaluated under the new
            # contract. The authoritative aggregate MUST block them
            # and require new artifacts (per user spec: "authoritative
            # aggregate 若 dataset identity 恰好匹配，也不得 accepted；
            # 应 blocked_incomplete_real_model_run").
            and self.legacy_artifact_count == 0
            # Three-layer runtime fixture identity
            # mismatch (dataset expected != manifest identity !=
            # artifact actual) is a typed blocker — the artifact
            # cannot be authoritatively evaluated because the runtime
            # baseline drifted from the dataset's committed identity.
            # The mismatch is NOT display-only: the artifact is
            # excluded from evaluation and counted as a blocker.
            and self.runtime_fixture_identity_mismatch_count == 0
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
    entrypoint. The runner no longer duplicates the evaluator
    list — divergence between the runner and the harness was the root
    cause of the divergence.
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
    # Prefer BudgetedUsageModel counters when agent_usage is None
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

    Defense-in-depth:
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
    # rework: the first downstream gate is conditionally allowed; the second is deferred regardless of
    # high-severity failure count.
    return "rework", True, False


def _decide_final_verdict(
    *,
    case_results,
    coverage_audit,
    identity_mismatched_count: int,
    runtime_identity_mismatch_count: int = 0,
    runtime_fixture_identity_mismatch_count: int = 0,
    real_model_blocked: bool,
    has_budget_exhausted: bool,
    total_artifacts_loaded: int,
    readiness=None,
):
    """Single source of truth for the final aggregate verdict.

    Implements the frozen verdict/gate table (Final Verdict Gate Contract).
    Each row's gate pair is shown as two booleans:

    1. identity mismatch                              → blocked_dataset_identity_mismatch (F, F)
    2. manifest corrupt (any artifacts)               → blocked_incomplete_real_model_run  (F, F)
    3. manifest absent + partial artifacts present    → blocked_incomplete_real_model_run  (F, F)
    4. manifest valid but run_id mismatch (foreign)   → blocked_incomplete_real_model_run  (F, F)
    5. artifact load invalid/corrupt/foreign          → blocked_incomplete_real_model_run  (F, F)
    5.5. runtime_identity_mismatch (artifact envelope_fingerprint
        does not match case's expected_envelope_fingerprint)
                                                      → blocked_incomplete_real_model_run  (F, F)
    5.6. runtime_fixture_identity_mismatch (three-layer check: dataset
        expected_runtime_fixture_fingerprint != manifest identity OR
        manifest identity != artifact runtime_fixture_fingerprint OR
        dataset expected != artifact actual; covers missing/empty/
        mismatch/foreign)
                                                      → blocked_incomplete_real_model_run  (F, F)
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

    Precedence 5.5 is the runtime fixture
    identity fence. It fires when at least one loaded artifact's
    runtime ``envelope_fingerprint`` does not match the case's
    declared ``expected_envelope_fingerprint`` (or is missing on a
    declared case). This is a STRICTER integrity signal than budget
    exhaustion or coverage gaps — the run produced artifacts, but
    they were generated against a runtime context the dataset author
    did not commit to. Per the task contract, the aggregate MUST NOT
    accept such artifacts and MUST NOT reverse-engineer expected
    facts from live article text. The artifacts are dropped from the
    evaluable set in :func:`aggregate` and the verdict falls here.

    Precedence 9.5 is the typed
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
       A corrupt manifest indicates the run started but its
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
       A foreign/copied manifest MUST NOT be stitched
       together with the current run's artifacts.

    5. Artifact load invalid/corrupt/foreign (from ArtifactLoadResult)
       → ``("blocked_incomplete_real_model_run", False, False)``.
       Corrupt/invalid/foreign artifacts cannot
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
       Dataset case binding must be enforced at
       the verdict seam, NOT via ``warn + continue`` in the evaluator
       loop. This catches both manifest-vs-dataset drift AND
       artifact-vs-dataset drift that the identity fence might miss
       (e.g. locally hand-edited artifacts with valid identity but
       unknown case_id).

    9. Evaluated case result count mismatch —
       ``evaluated_case_result_count != planned_count`` →
       ``("blocked_incomplete_real_model_run", False, False)``.
       Structural fix for the ``all([]) → accepted``
       bug. The previous path let unknown-case artifacts be skipped,
       producing ``case_results=[]`` with ``planned_count=1``, and
       ``_decide_normal_verdict([])`` returned accepted. This row
       catches that case at the verdict seam.

    10. No manifest + no artifacts (not yet real-run) →
        ``("blocked_by_real_model_run", False, False)``.
        NOTE: the first downstream gate remains disabled per the frozen contract (bug fix:
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
        The verdict plus two gate booleans.
    """
    # Precedence 1: identity mismatch wins over everything.
    if identity_mismatched_count > 0:
        return "blocked_dataset_identity_mismatch", False, False

    # Precedence 2: corrupt manifest → incomplete.
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
    # A foreign manifest MUST NOT be stitched together with
    # the current run's artifacts. NOT classified as identity mismatch
    # unless the dataset identity itself mismatches.
    if (
        coverage_audit.manifest_present
        and coverage_audit.manifest_state == "valid"
        and coverage_audit.manifest_run_id_matches is False
    ):
        return "blocked_incomplete_real_model_run", False, False

    # Precedence 5: artifact load invalid/corrupt/foreign — the audit
    # trail is broken at the file level. Corrupt
    # artifacts cannot be silently dropped.
    if readiness is not None and not readiness.artifact_load_clean:
        return "blocked_incomplete_real_model_run", False, False

    # Precedence 5.5: runtime fixture identity
    # mismatch. At least one loaded artifact's runtime
    # ``envelope_fingerprint`` does not match the case's declared
    # ``expected_envelope_fingerprint`` (or is missing on a declared
    # case). The artifact was generated against a runtime context the
    # dataset author did not commit to; it cannot be authoritatively
    # evaluated. The artifacts are already dropped from
    # ``real_artifacts`` in :func:`aggregate`, so this row surfaces
    # the reason as ``blocked_incomplete_real_model_run`` rather than
    # letting the verdict fall through to budget_exhausted or coverage
    # gap (which would misdiagnose the root cause).
    #
    # Per task contract: "artifact runtime identity mismatch →
    # aggregate blocked". Fail-closed — never accept, never rework.
    if runtime_identity_mismatch_count > 0:
        return "blocked_incomplete_real_model_run", False, False

    # Precedence 5.6: three-layer runtime fixture
    # identity mismatch. The three-layer contract is
    #   dataset expected == manifest identity == artifact actual.
    # Any deviation (missing/empty/mismatch/foreign identity in any
    # layer) is a typed blocker surfaced by
    # :func:`_runtime_fixture_identity_mismatches`. The artifacts are
    # already dropped from ``real_artifacts`` in :func:`aggregate`,
    # so this row surfaces the reason as
    # ``blocked_incomplete_real_model_run`` rather than letting the
    # verdict fall through to budget_exhausted or coverage gap (which
    # would misdiagnose the root cause).
    #
    # Per task contract: "missing/mixed/mismatch/foreign identity 均
    # 输出 typed blocker, 不进入 evaluator, 不得只是 display-only."
    # Fail-closed — never accept, never rework.
    if runtime_fixture_identity_mismatch_count > 0:
        return "blocked_incomplete_real_model_run", False, False

    # Precedence 6: budget_exhausted manifest → incomplete (NOT
    # accepted/rework). Previously, a partial
    # budget-exhausted run with some case_results could fall through to
    # accepted/rework.
    if has_budget_exhausted:
        return "blocked_incomplete_real_model_run", False, False

    # Precedence 7: completed manifest but coverage gap OR is_complete
    # False OR evaluable != planned. Defense-in-depth: coverage_ok
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
    # references a case_id not in the current dataset.
    if readiness is not None and (
        readiness.unknown_planned_case_count > 0
        or readiness.unknown_artifact_case_count > 0
    ):
        return "blocked_incomplete_real_model_run", False, False

    # Precedence 9: evaluated_case_result_count mismatch — the evaluator
    # produced fewer/more results than planned. Structural fix:
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
    # Typed blocker row. The evaluator ran and produced
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
    # NOTE: the first downstream gate remains disabled per the frozen contract (bug fix:
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


def _runtime_identity_mismatches(artifact, cases_by_id: dict) -> bool:
    """Check whether an artifact's runtime envelope_fingerprint
    mismatches the case's declared ``expected_envelope_fingerprint``.

    Returns ``True`` when:
    - The case exists in ``cases_by_id`` AND declares a non-None
      ``expected_envelope_fingerprint`` AND the artifact's
      ``envelope_fingerprint`` is missing OR does not exactly match.

    Returns ``False`` when:
    - The case is not in ``cases_by_id`` (handled separately as
      ``unknown_artifact_case_count``).
    - The case does not declare ``expected_envelope_fingerprint``
      (backwards-compat — no check is performed).
    - The artifact's ``envelope_fingerprint`` exactly matches the
      declared value.

    Per the task contract: "artifact 必须携带实际 runtime identity" —
    a missing artifact ``envelope_fingerprint`` on a declared case is
    treated as a mismatch (fail-closed). The aggregate never reads
    live article text — it only compares the artifact's recorded
    runtime identity against the dataset's declared expected identity.

    This envelope-only check is RETAINED for defense-in-
    depth. The PRIMARY identity contract is now
    ``runtime_fixture_fingerprint`` (verified by
    :func:`_runtime_fixture_identity_mismatches`), which binds the
    actual model-visible chunks. The envelope fingerprint catches
    metadata drift (record_id / base_id / generation) that the chunk
    fingerprint would also catch, but catching it earlier in the
    chain produces a clearer error.
    """
    case = cases_by_id.get(artifact.case_id)
    if case is None:
        # Unknown case — handled by ``unknown_artifact_case_count``.
        return False
    expected = case.expected_envelope_fingerprint
    if expected is None:
        # Case does not declare an expected identity — no check.
        return False
    runtime = artifact.envelope_fingerprint
    if not runtime:
        # Declared but runtime fingerprint is missing — fail-closed.
        return True
    return runtime != expected


def _runtime_fixture_identity_mismatches(
    artifact,
    cases_by_id: dict,
    manifest: Any | None,
) -> tuple[bool, str | None]:
    """Three-layer runtime fixture identity check.

    Verifies the contract:

        dataset expected == manifest preflight == artifact actual

    Returns ``(mismatch: bool, reason: str | None)``:

    - ``(False, None)`` when the case does not declare
      ``expected_runtime_fixture_fingerprint`` AND is not a
      ``real_phase1`` case (backwards-compat — no check is performed
      for non-real_phase1 cases).
    - ``(True, reason)`` when any of the following holds:

      1. ``missing_dataset_expected`` — case is a ``real_phase1``
         case (BBC OR synthetic) but ``expected_runtime_fixture_fingerprint``
         is None or empty. This is a hard contract violation: ALL
         real_phase1 cases MUST declare the field (harness preflight
         fail-closes on this; the aggregate re-checks defense-in-depth).
         The strict contract expands this from BBC-only to ALL real_phase1
         cases (including synthetic).
      2. ``missing_artifact_actual`` — case declares expected but
         artifact's ``runtime_fixture_fingerprint`` is None or empty.
         The artifact was written by an older harness, the
         runtime raised an exception (capture_status="failed"), or the
         baseline assembly yielded 0 chunks (capture_status=
         "unavailable") — fail-closed.
      3. ``dataset_artifact_mismatch`` — artifact actual does not
         match dataset expected. The runtime baseline (status /
         is_complete / chunks) drifted from the dataset author's
         committed identity (e.g. monkeypatched assembler, DB mutation
         between preflight and run, different chunk truncation).
      4. ``manifest_artifact_mismatch`` — manifest preflight identity
         does not match artifact actual. The harness wrote a different
         fingerprint to the manifest than to the artifact (corrupt
         manifest or harness bug).
      5. ``dataset_manifest_mismatch`` — manifest preflight identity
         does not match dataset expected. The harness wrote a different
         fingerprint to the manifest than the dataset declared (corrupt
         manifest or harness bug).
      6. ``manifest_identity_missing`` — case declares expected and
         artifact carries actual, but the manifest's
         ``runtime_fixture_identities`` does not contain the case.
         For V2 manifests this is a contract violation (already caught
         by manifest validation Rule 18b, but defense-in-depth here).
      7. ``manifest_identity_foreign`` — manifest's identity is a
         valid SHA-256 but does not match either dataset expected
         or artifact actual (a "foreign" identity from another run).

    Manifest version strategy:

    - V2 (``audit_contract_version == "r4-a4-2r3"``): the three-layer
      check is MANDATORY. The manifest's ``runtime_fixture_identities``
      MUST cover all planned cases (Rule 18a/18b). An empty/missing
      identity map is corrupt (NOT legacy) — the manifest reader
      rejects it at parse time, so we never reach this function with
      a V2 manifest that has an empty identity map. Defense-in-depth:
      if we do, ``manifest_identity_missing`` is returned.
    - V1 (``audit_contract_version == "r4-a4-2r2"`` or ``None``):
      backwards-compat — checks 4-7 are skipped (the manifest may
      carry an empty or partial identity map). Only checks 1-3 apply.
      V1 is selected by EXPLICIT version, NOT by empty-dict guessing.

    The case is treated as a ``real_phase1`` case when
    ``"real_phase1" in case.phase_tags``. This covers BOTH BBC and
    synthetic real_phase1 cases; all of them are required
    to declare ``expected_runtime_fixture_fingerprint``.
    """
    case = cases_by_id.get(artifact.case_id)
    if case is None:
        # Unknown case — handled by ``unknown_artifact_case_count``.
        return False, None
    expected = case.expected_runtime_fixture_fingerprint
    is_real_phase1 = "real_phase1" in (case.phase_tags or [])

    if expected is None or not str(expected).strip():
        if is_real_phase1:
            # Check 1: ALL real_phase1 cases (BBC + synthetic) MUST
            # declare the field. The strict contract expands this from
            # BBC-only to ALL real_phase1 cases.
            return True, "missing_dataset_expected"
        # Non-real_phase1 case without declaration — backwards-compat.
        return False, None
    expected = str(expected)

    actual = getattr(artifact, "runtime_fixture_fingerprint", None)
    if not actual or not str(actual).strip():
        # Check 2: artifact missing the field — fail-closed. This
        # covers runtime exceptions (capture_status="failed") and
        # unavailable baselines (capture_status="unavailable") where
        # the actual fingerprint is intentionally None.
        return True, "missing_artifact_actual"
    actual = str(actual)

    if actual != expected:
        # Check 3: artifact actual != dataset expected.
        return True, "dataset_artifact_mismatch"

    # Three-layer manifest checks use EXPLICIT
    # ``audit_contract_version`` to decide whether to perform the
    # three-layer check — NOT the empty-dict heuristic. V2 manifests
    # MUST carry the identity map (Rule 18a); V1 manifests may have
    # an empty/partial map (backwards-compat).
    if manifest is None:
        return False, None
    audit_contract_version = getattr(manifest, "audit_contract_version", None)
    if audit_contract_version != "r4-a4-2r3":
        # V1 (legacy) manifest — backwards-compat, skip three-layer
        # checks. Selected by EXPLICIT version, NOT by empty-dict
        # guessing.
        return False, None
    # V2 manifest — three-layer check is mandatory.
    manifest_identities = getattr(manifest, "runtime_fixture_identities", None)
    if not manifest_identities:
        # V2 manifest with empty identity map — corrupt (Rule 18a
        # should have rejected this at parse time). Defense-in-depth.
        return True, "manifest_identity_missing"
    manifest_identity = manifest_identities.get(artifact.case_id)
    if manifest_identity is None or not str(manifest_identity).strip():
        # Check 6: manifest missing the case's identity.
        return True, "manifest_identity_missing"
    manifest_identity = str(manifest_identity)
    if manifest_identity != actual:
        # Check 4: manifest preflight != artifact actual.
        return True, "manifest_artifact_mismatch"
    if manifest_identity != expected:
        # Check 5: manifest preflight != dataset expected.
        return True, "dataset_manifest_mismatch"
    return False, None


def aggregate(
    run_id: str,
    runs_dir: Path,
    dataset_dir: Path,
    report_output: Path,
    *,
    # Parameterized report inputs so the runner no
    # longer relies on hardcoded date / file list / tracker path. CLI
    # optional flags flow through here.
    report_date: str | None = None,
    modified_files: list[str] | None = None,
    evaluation_heading: str = "current evaluation",
    tracker_path: str | None = None,
) -> int:
    """Load artifacts, run 11 evaluators, aggregate, generate report.

    Uses :class:`RunSessionLayout` to resolve the artifact directory
    and :func:`evaluate_artifact` as the single evaluator
    entrypoint. Budget-exhausted artifacts are NOT evaluated
    and NOT treated as passes.

    The dataset and its :class:`DatasetIdentity` are loaded via
    the snapshot-loading entrypoint — the identity is bound
    to the same bytes the parser consumed, so a disk mutation between
    load and identity computation cannot desynchronize the fingerprint.

    The final verdict is decided by :func:`_decide_final_verdict`,
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
        load_reader_record_ask_dataset_with_snapshot,
    )
    from claread_eval.reader_record_ask.report import generate_eval_report
    from claread_eval.reader_record_ask.run_manifest import (
        CoverageAuditResult,
        ManifestState,
        read_manifest_with_state,
        validate_manifest_coverage,
    )
    from claread_eval.reader_record_ask.session import RunSessionLayout

    # Load dataset + identity from a SINGLE byte capture. The
    # snapshot's ``dataset`` and ``identity`` are derived from the same
    # bytes — a disk mutation after this point cannot desync them.
    snapshot = load_reader_record_ask_dataset_with_snapshot(dataset_dir)
    dataset = snapshot.dataset
    dataset_identity = snapshot.identity
    cases_by_id = {c.id: c for c in dataset.cases}

    # Use RunSessionLayout to resolve the artifact directory.
    # The same resolver the harness uses to write — writer and reader
    # cannot diverge.
    session = RunSessionLayout(runs_root=runs_dir, run_id=run_id)
    artifact_dir = session.artifact_dir

    # Use the typed artifact-load seam as the
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
    # Coverage Audit with three manifest states.
    # ------------------------------------------------------------------
    # Read the manifest via read_manifest_with_state, which
    # distinguishes absent / valid / corrupt. The previous code caught
    # RunManifestError and folded corrupt → absent, which caused
    # "corrupt manifest + no artifacts" to be misclassified as
    # blocked_by_real_model_run (the "never ran" verdict) instead of
    # blocked_incomplete_real_model_run (the "ran but unauditable"
    # verdict).
    #
    # After a VALID read, verify manifest.run_id == session.run_id
    # BEFORE consulting the manifest for coverage audit. A foreign
    # manifest (wrong run_id) MUST NOT be stitched together with the
    # current run's artifacts. The run_id check happens BEFORE coverage
    # audit per the task contract.
    manifest_read = read_manifest_with_state(session.manifest_path)
    manifest_state = manifest_read.state
    manifest = manifest_read.manifest  # None unless VALID

    # run_id binding check. Only meaningful when manifest is
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
        # Corrupt manifest — do NOT crash, do NOT fold into absent.
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

    # Separate budget_exhausted artifacts from real artifacts.
    # Budget-exhausted artifacts were never run, so there is nothing
    # to evaluate. They are NOT treated as passes — the report shows
    # the cap-triggered state separately.
    real_artifacts = [a for a in artifacts if not a.budget_exhausted]
    budget_exhausted_artifacts = [a for a in artifacts if a.budget_exhausted]
    has_budget_exhausted = bool(budget_exhausted_artifacts) or (
        coverage_audit.manifest_status == "budget_exhausted"
    )

    # Compute the five typed budget-semantics
    # fields for operator observability. The manifest is the
    # authoritative source for planned/completed counts and
    # executed_requests; the env-configured request cap is read via
    # ``_resolve_request_cap_from_env``. ``manifest_for_audit`` is
    # the manifest object to use for budget semantics — when the
    # manifest is foreign (wrong run_id) or corrupt, we surface zeros
    # (the verdict falls to a blocked_* variant via existing
    # precedence rows; budget semantics are informational only).
    budget_manifest_for_semantics = (
        manifest
        if (
            manifest is not None
            and manifest_state == ManifestState.VALID
            and manifest_run_id_matches is True
        )
        else None
    )
    try:
        budget_semantics = _compute_budget_semantics(
            manifest=budget_manifest_for_semantics,
            request_cap=_resolve_request_cap_from_env(),
        )
    except ValueError as exc:
        # Corrupt manifest executed_requests < completed_runs. This
        # is a hard data integrity violation — fail-closed with a
        # visible error so the operator can investigate. The verdict
        # will fall to ``blocked_incomplete_real_model_run`` via the
        # existing coverage/manifest_state precedence rows; we do
        # not need a new precedence row here.
        print(
            f"WARN: budget_semantics computation failed: {exc}; "
            "surface as None in report.",
            file=sys.stderr,
        )
        budget_semantics = SimpleNamespace(
            planned_logical_runs=0,
            request_cap=None,
            actual_completed_runs=0,
            retry_headroom=None,
            retries_consumed=0,
        )

    # Dataset identity fence: artifacts whose identity is missing
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
    # Runtime fixture identity fence.
    # ------------------------------------------------------------------
    # For each artifact whose case declares ``expected_envelope_fingerprint``,
    # verify the artifact's runtime ``envelope_fingerprint`` matches
    # EXACTLY. Mismatch (or missing runtime fingerprint on a declared
    # case) means the runtime drifted from the dataset's declared
    # identity — the artifact cannot be authoritatively evaluated
    # because the model saw a different base/generation/record than
    # the dataset author committed to.
    #
    # This is the post-call counterpart to the harness's pre-call
    # ``_verify_runtime_identity`` check. The pre-call check prevents
    # the run from starting; this post-call check prevents an
    # already-written artifact (e.g. from a prior phase whose dataset
    # later drifted) from being accepted.
    #
    # Per the task contract: "artifact 必须携带实际 runtime identity；
    # aggregate 必须校验 artifact、dataset expectation、manifest/run
    # 一致" and "不得在 aggregate 时从实时正文反推 expected facts 或
    # temporal allowset" — the aggregate compares the artifact's
    # recorded runtime identity against the dataset's declared expected
    # identity, and never reads live article text.
    runtime_identity_mismatched = [
        a for a in real_artifacts
        if _runtime_identity_mismatches(a, cases_by_id)
    ]
    runtime_identity_mismatch_count = len(runtime_identity_mismatched)
    if runtime_identity_mismatched:
        # Drop mismatched artifacts from the evaluable set — they are
        # NOT evaluated and NOT counted as passes. The verdict falls
        # to ``blocked_incomplete_real_model_run`` via a new precedence
        # row in ``_decide_final_verdict``.
        runtime_mismatched_ids = {a.case_id for a in runtime_identity_mismatched}
        real_artifacts = [
            a for a in real_artifacts
            if a.case_id not in runtime_mismatched_ids
        ]
        print(
            f"WARN: {runtime_identity_mismatch_count} artifact(s) failed "
            "  runtime identity verification — "
            "artifact envelope_fingerprint does not match the case's "
            "declared expected_envelope_fingerprint. These artifacts "
            "are NOT evaluated and NOT counted as passes. Verdict will "
            "fall to blocked_incomplete_real_model_run.",
            file=sys.stderr,
        )

    # ------------------------------------------------------------------
    # Three-layer runtime fixture identity check.
    # ------------------------------------------------------------------
    # For each artifact, verify:
    #   dataset expected == manifest identity == artifact actual
    # Missing / mixed / mismatch / foreign identity → typed blocker,
    # NOT display-only. The artifact is dropped from the evaluable
    # set (NOT evaluated, NOT counted as a pass), and the verdict
    # falls to ``blocked_incomplete_real_model_run`` via the
    # existing precedence row.
    #
    # This is the post-call counterpart to the harness preflight's
    # ``_compute_preflight_runtime_fixture_fingerprint`` (which
    # verifies dataset expected == preflight actual BEFORE any paid
    # call). The post-call check verifies the SAME contract after
    # the run, plus the manifest identity layer (which the preflight
    # cannot check because the manifest has not been written yet).
    #
    # Per the task contract: "artifact 持久化实际 runtime_fixture_
    # fingerprint; manifest 持久化本 run 每个 case 的 fixture
    # identity; aggregate 校验：dataset expected == manifest
    # identity == artifact actual; missing/mixed/mismatch/foreign
    # identity 均输出 typed blocker, 不进入 evaluator, 不得只是
    # display-only."
    runtime_fixture_mismatched: list = []
    runtime_fixture_mismatch_reasons: dict[str, str] = {}
    for a in real_artifacts:
        mismatch, reason = _runtime_fixture_identity_mismatches(
            a, cases_by_id, manifest
        )
        if mismatch:
            runtime_fixture_mismatched.append(a)
            if reason is not None:
                runtime_fixture_mismatch_reasons[a.case_id] = reason
    runtime_fixture_identity_mismatch_count = len(runtime_fixture_mismatched)
    if runtime_fixture_mismatched:
        runtime_fixture_mismatched_ids = {
            a.case_id for a in runtime_fixture_mismatched
        }
        real_artifacts = [
            a for a in real_artifacts
            if a.case_id not in runtime_fixture_mismatched_ids
        ]
        # Surface the typed reasons so the operator can see WHICH
        # layer failed (missing_dataset_expected / missing_artifact_actual
        # / dataset_artifact_mismatch / manifest_artifact_mismatch /
        # dataset_manifest_mismatch / manifest_identity_missing).
        reasons_summary = ", ".join(
            f"{cid}={reason}"
            for cid, reason in sorted(
                runtime_fixture_mismatch_reasons.items()
            )
        )
        print(
            f"WARN: {runtime_fixture_identity_mismatch_count} artifact(s) "
            "failed three-layer runtime fixture identity "
            "verification (dataset expected == manifest identity == "
            "artifact actual). Typed reasons: " + reasons_summary + ". "
            "These artifacts are NOT evaluated and NOT counted as "
            "passes. Verdict will fall to "
            "blocked_incomplete_real_model_run.",
            file=sys.stderr,
        )

    # ------------------------------------------------------------------
    # Dataset case binding.
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
    # Build AggregateReadinessAudit (pre-evaluator).
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
        runtime_fixture_identity_mismatch_count=(
            runtime_fixture_identity_mismatch_count
        ),
    )

    # ------------------------------------------------------------------
    # Evaluator gate: only run the 11 evaluators when pre_evaluator_ready
    # is True. Otherwise skip — the verdict will be a blocked_* variant
    # decided by _decide_final_verdict via the readiness audit.
    #
    # Unknown case_id artifacts are NO LONGER
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
    # Rebuild readiness with actual
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
    # ``instrumentation_incomplete_count``
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
        runtime_fixture_identity_mismatch_count=(
            pre_eval_readiness.runtime_fixture_identity_mismatch_count
        ),
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

    # SINGLE source of truth for the verdict precedence. No
    # caller-side overrides — _decide_final_verdict returns the final
    # verdict and two gate booleans in one place.
    #
    # ``total_artifacts_loaded`` is now
    # ``discovered_file_count`` (the count of ``*.json`` files found
    # BEFORE filtering by validity/foreign). This means "absent
    # manifest + 1 corrupt artifact file" correctly falls to row 3
    # (incomplete) instead of row 10 (blocked_by_real_model_run).
    verdict, allow_correctness_followup, allow_streaming_provider_followup = _decide_final_verdict(
        case_results=case_results,
        coverage_audit=coverage_audit,
        identity_mismatched_count=identity_mismatched_count,
        runtime_identity_mismatch_count=runtime_identity_mismatch_count,
        runtime_fixture_identity_mismatch_count=(
            runtime_fixture_identity_mismatch_count
        ),
        real_model_blocked=real_model_blocked,
        has_budget_exhausted=has_budget_exhausted,
        total_artifacts_loaded=discovered_file_count,
        readiness=readiness,
    )

    report = generate_eval_report(
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
            "aggregate 路径不调用 pytest；离线确定性套件以本轮开发/"
            "CI 实际命令结果为准（session/evaluation/phase_planner/"
            "budgeted_model/evaluators/dataset/aggregator/report 等）。"
            " real-LLM harness 默认 gated skip，本摘要不声明精确 "
            "passed/skipped 计数。"
        ),
        verdict=verdict,
        allow_correctness_followup=allow_correctness_followup,
        allow_streaming_provider_followup=allow_streaming_provider_followup,
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
            # Runtime fixture identity mismatch
            # count. > 0 means at least one loaded artifact's runtime
            # ``envelope_fingerprint`` does not match the case's
            # declared ``expected_envelope_fingerprint``. Those
            # artifacts are dropped from the evaluable set and the
            # verdict falls to ``blocked_incomplete_real_model_run``
            # via precedence row 5.5 in :func:`_decide_final_verdict`.
            # No file paths or fingerprint values are surfaced — only
            # the typed count.
            "runtime_identity_mismatched_artifacts": (
                runtime_identity_mismatch_count
            ),
            # ArtifactLoadResult typed counts.
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
            # AggregateReadinessAudit typed signals.
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
                # Typed
                # instrumentation-incomplete blocker count. > 0 means
                # the verdict falls to
                # ``blocked_incomplete_real_model_run`` via precedence
                # row 9.5 — these are NOT model failures and do NOT
                # enter rework.
                "instrumentation_incomplete_count": (
                    readiness.instrumentation_incomplete_count
                ),
                # Legacy-artifact
                # blocker count. Per user spec, legacy artifacts MUST
                # also be blocked in the authoritative aggregate.
                "legacy_artifact_count": readiness.legacy_artifact_count,
                "pre_evaluator_ready": readiness.pre_evaluator_ready,
                "ready_for_normal_verdict": readiness.ready_for_normal_verdict,
                # Three-layer runtime fixture identity
                # mismatch count. > 0 means at least one loaded
                # artifact's runtime_fixture_fingerprint (or the
                # manifest's runtime_fixture_identities entry) does
                # not match the case's declared
                # expected_runtime_fixture_fingerprint. Typed reasons
                # are surfaced via stderr WARN; here we surface only
                # the typed count. The artifacts are dropped from
                # the evaluable set and the verdict falls to
                # ``blocked_incomplete_real_model_run`` via precedence
                # row 5.6 in :func:`_decide_final_verdict`.
                "runtime_fixture_identity_mismatch_count": (
                    readiness.runtime_fixture_identity_mismatch_count
                ),
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
            # Planned logical runs vs provider
            # request cap semantics. The five typed fields let the
            # operator distinguish a coverage gap (planned >
            # actual_completed) from a retry-overflow
            # (retries_consumed > 0). An earlier audit found "30
            # planned, 30 requests, 27 completed" was ambiguous —
            # these fields make the cause auditable. ``request_cap``
            # is ``None`` when the env var is unset (the harness may
            # have used its own default; the aggregate does not
            # guess). Informational only — ``_decide_final_verdict``
            # does NOT consult these fields.
            "budget_semantics": {
                "planned_logical_runs": budget_semantics.planned_logical_runs,
                "request_cap": budget_semantics.request_cap,
                "actual_completed_runs": (
                    budget_semantics.actual_completed_runs
                ),
                "retry_headroom": budget_semantics.retry_headroom,
                "retries_consumed": budget_semantics.retries_consumed,
            },
            "harness_test_path": str(HARNESS_TEST_PATH.relative_to(REPO_ROOT))
            if HARNESS_TEST_PATH.exists()
            else str(HARNESS_TEST_PATH),
            "tracker_path": _TRACKER_PATH,
        },
        # Parameterize previously hardcoded values.
        # The report no longer carries stale date / file list / tracker
        # path from the previous round. ``report_date`` defaults to
        # today when caller does not pass it; ``modified_files`` and
        # ``tracker_path`` are taken from CLI args (or fall back to
        # canonical defaults).
        report_date=report_date,
        modified_files=modified_files,
        evaluation_heading=evaluation_heading,
        tracker_path=tracker_path,
    )

    report_output.parent.mkdir(parents=True, exist_ok=True)
    report_output.write_text(report, encoding="utf-8")
    print(f"Report written to: {report_output}")
    print(
        f"verdict={verdict} allow_correctness_followup={allow_correctness_followup} "
        f"allow_streaming_provider_followup={allow_streaming_provider_followup} artifacts={len(artifacts)} "
        f"real={len(real_artifacts)} budget_exhausted={len(budget_exhausted_artifacts)} "
        f"runtime_identity_mismatched={runtime_identity_mismatch_count}"
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
    # Planned logical runs vs provider request cap.
    # ``planned`` is the (case × repetition) universe; ``cap`` is the
    # configured provider call budget; ``headroom`` = cap − planned
    # (negative = structurally insufficient); ``retries_consumed`` =
    # executed_requests − actual_completed (output retries / multi-turn
    # tool loops above the one-request-per-completed-case baseline).
    print(
        f"budget_semantics: planned_logical_runs={budget_semantics.planned_logical_runs} "
        f"request_cap={budget_semantics.request_cap} "
        f"actual_completed_runs={budget_semantics.actual_completed_runs} "
        f"retry_headroom={budget_semantics.retry_headroom} "
        f"retries_consumed={budget_semantics.retries_consumed}"
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
        help="Stages 1/2/3 invoke the real-model harness; "
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
        help="Prior stage run id (required for stages 2/3; "
        "passed to harness via CLAREAD_R4_A3_PRIOR_RUN_ID env var).",
    )
    parser.add_argument(
        "--dataset-dir",
        default=None,
        help=(
            "Path to the evaluation dataset directory. REQUIRED for real runs "
            "(stages 1/2/3) and aggregate. Priority: "
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
        dest="evaluation_heading",
        default="current evaluation",
        help="Task label rendered in §2.1 header (default: 'current evaluation').",
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

    # Explicit dataset-dir binding: resolve dataset dir from CLI > env.
    # No silent fallback — real runs and aggregate must
    # explicitly declare the dataset they are using. ``_preflight_dataset_dir``
    # fails closed (exit code 2) when neither source is set, or the dir
    # is missing or has no dataset.yaml — before any subprocess invocation
    # or paid call.
    dataset_dir = _resolve_dataset_dir(args.dataset_dir)
    _preflight_dataset_dir(dataset_dir)
    print(f"Evaluation dataset dir: {dataset_dir}", file=sys.stderr)

    if args.phase == "aggregate":
        # ``_preflight_dataset_dir`` above already exits with code 2 if
        # ``dataset_dir`` is None, so this assert is for type checkers.
        assert dataset_dir is not None
        # Normalize ``--runs-dir`` to an absolute
        # canonical path BEFORE the aggregate function consumes it.
        # Aggregate runs in this main process (cwd=``evals/`` when
        # invoked from the typical workflow). Without normalization,
        # a relative ``--runs-dir`` would resolve against the main
        # process cwd here, while the harness subprocess (cwd=
        # ``services/api/``) would resolve the SAME relative path
        # against its own cwd — producing the historical
        # ``services/services/api/tmp/...`` double-resolution bug
        # where aggregate could not find the artifacts written by
        # the harness.
        return aggregate(
            run_id=args.run_id,
            runs_dir=Path(args.runs_dir).resolve(),
            dataset_dir=dataset_dir,
            report_output=Path(args.report_output),
            report_date=args.report_date,
            modified_files=args.modified_file,
            evaluation_heading=args.evaluation_heading,
            tracker_path=args.tracker_path,
        )
    # Follow-up stages require --prior-run-id.
    if args.phase in ("2", "3") and not args.prior_run_id:
        parser.error(
            f"--prior-run-id is required for --phase {args.phase} "
            "(the harness no longer scans the runs root for 'latest' — "
            "the prior phase's run id must be explicit)"
        )
    # ``_preflight_dataset_dir`` above already exits with code 2 if
    # ``dataset_dir`` is None, so this assert is for type checkers.
    assert dataset_dir is not None
    # Normalize ``--runs-dir`` to an absolute
    # canonical path BEFORE propagating it to the pytest subprocess
    # via ``CLAREAD_R4_A3_RUNS_DIR`` env var. The subprocess cwd is
    # ``services/api/`` (HARNESS_CWD). Without normalization, a
    # relative ``--runs-dir`` would be re-resolved against the
    # subprocess cwd, producing the historical
    # ``services/services/api/tmp/...`` double-resolution bug.
    # After normalization the env var is absolute, so the subprocess
    # cwd cannot re-resolve it.
    return run_phase(
        phase=int(args.phase),
        run_id=args.run_id,
        runs_dir=Path(args.runs_dir).resolve(),
        prior_run_id=args.prior_run_id,
        dataset_dir=dataset_dir,
    )


if __name__ == "__main__":
    sys.exit(main())
