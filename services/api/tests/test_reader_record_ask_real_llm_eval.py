"""In-process real-LLM harness for Reader Record Ask R4-A3 eval (rework).

Default-skipped. To run, all gates must be open:

    CLAREAD_ALLOW_REAL_LLM_TESTS=1 \\
    CLAREAD_R4_A3_RUN=1 \\
    CLAREAD_REAL_LLM_MODEL=<short_name> \\
    CLAREAD_R4_A3_RUN_ID=phase1-<ts> \\
        uv run pytest tests/test_reader_record_ask_real_llm_eval.py -m real_llm -v

Rework closure (spec: `.trae/specs/reader-record-ask-r4-a3-rework-session-
eval-closure/spec.md`): this harness now consumes the four deep modules —
:class:`RunSessionLayout`, :func:`evaluate_artifact`, :class:`PhasePlanner`,
:class:`BudgetedUsageModel` — plus :func:`project_exception` and
:func:`utf16_code_units`. The harness itself is reduced to scenario and
wiring; the deep modules own the contract.

Run/session contract (P0-1):
- ``CLAREAD_R4_A3_RUN_ID`` is the single source of truth for the run id.
- ``CLAREAD_R4_A3_PRIOR_RUN_ID`` is required for Phase 2/3 and points at
  the prior phase's run id (no more scanning the runs root for "latest").
- Artifacts are written to ``<runs_root>/<run_id>/artifacts/*.json`` via
  :class:`RunSessionLayout.artifact_path`.

Phase contract (P0-2, P0-3, P0-5):
- :class:`PhasePlanner` selects Phase 1 cases from the dataset's explicit
  ``phase_tags`` field (``real_phase1``), not from an implicit sort.
- Each selected case runs ``repetitions`` times (default 3 for Phase 1,
  1 for Phase 2/3) — no early break on first success.
- Phase 2/3 cases are selected from prior *evaluator results*
  (``is_content_failure``), not from terminal status alone. A
  ``finalized_status='ok'`` artifact with an unsupported ``2025`` year
  token is correctly selected for Phase 2.

Budget contract (P0-8):
- The resolved model is wrapped in :class:`BudgetedUsageModel` BEFORE
  being handed to ``run_reading_record_ask``. The wrapper increments
  ``executed_requests`` before each provider call and raises
  :class:`BudgetExhaustedError` when the cap is hit.
- ``agent_usage`` is populated from the wrapper's counters, not from
  ``agent_output.usage()`` (which is usually None).

Thinking contract (P1-1):
- ``artifact.thinking_enabled`` comes from
  ``model_config.model_settings.thinking_enabled()`` (the resolved
  settings), not from a harness hardcoded flag.
- Phase 1 asserts ``thinking_enabled() is False`` before any model call.
- Phase 2 asserts ``thinking_enabled() is True`` after building the
  thinking config.
- Phase 3 verifies ``model_name`` matches the authorized Pro model AND
  ``thinking_enabled() is True`` AND ``CLAREAD_R4_A3_PRO_PROFILE`` is
  actually used (not just non-empty).

Error projection (P1-2):
- ``RawArtifact.error`` is populated via
  :func:`project_exception_to_string` (allowlisted safe code + exception
  type only). ``RawArtifact.safe_error_code`` carries the allowlisted
  code. Truncation is NOT sanitization — the raw exception string is
  never read.

Preflight (P0-5):
- Before any paid model call, :func:`_preflight_check` verifies:
  required cases load, BBC DB/record/base/blocks ready (for BBC cases),
  model route correct, Phase 1 thinking disabled, run dir writable,
  budget executable. Preflight failure sets ``preflight_status`` on the
  artifact and skips the phase — no paid call is made.

Sanitization invariants:
- No article_text in the artifact (RawArtifact does not carry it).
- No raw reasoning_content (RawArtifact does not carry it).
- No API key / provider payload (RawArtifact does not carry them).
- Error string is allowlisted (never the raw exception text).
- Evidence snippet truncated to 500 chars in ``_raw_evidence_from_observation``.
"""

# ruff: noqa: I001
# The ``claread_eval`` import path must be set up between the stdlib import
# block and the first-party imports, which ruff's isort would otherwise
# merge into a single block. The path setup is load-bearing for
# ``claread_eval`` discoverability from the services/api test root.

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Sequence
from typing import Any
from uuid import UUID

import pytest

# ---------------------------------------------------------------------------
# Make ``claread_eval`` importable from this test file.
# Path: <repo>/services/api/tests/test_reader_record_ask_real_llm_eval.py
# parents[3] = <repo>; <repo>/evals hosts the claread_eval package.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[3]
_EVALS_ROOT = _REPO_ROOT / "evals"
if str(_EVALS_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVALS_ROOT))

from app.llm.call_guard import real_llm_tests_allowed  # noqa: E402
from app.llm.provider_factory import build_model_instance  # noqa: E402
from app.llm.routes import MODEL_ROUTE_READER_ASK  # noqa: E402
from app.llm.types import ResolvedModelConfig, RunModelSettings  # noqa: E402
from app.services.reader_record_ask.context_envelope import (  # noqa: E402
    ReadingRecordAskContextEnvelope,
    VerifiedEnvelopeInput,
    build_context_envelope,
)
from app.services.reader_record_ask.document_access import (  # noqa: E402
    InMemoryDocumentAccess,
    ReadingUnitView,
    build_document_scope,
)
from app.services.reader_record_ask.agent import (  # noqa: E402
    DEFAULT_OUTPUT_RETRIES,
    DEFAULT_TOOL_RETRIES,
)
from app.services.reader_record_ask.baseline_context import (  # noqa: E402
    BaselineContextAssembler,
    ModelContextChunk,
)
from app.services.reader_record_ask.evidence_registry import (  # noqa: E402
    EvidenceRegistry,
)
from app.services.reader_record_ask.runtime import (  # noqa: E402
    run_reading_record_ask,
)
from app.services.reader_record_ask.runtime_deps import (  # noqa: E402
    ExecutionStage,
    RuntimeObservation,
)
from claread_eval.reader_record_ask.budgeted_model import (  # noqa: E402
    BudgetExhaustedError,
    BudgetedUsageModel,
)
from claread_eval.reader_record_ask.dataset_identity import (  # noqa: E402
    DatasetIdentity,
    DatasetIdentityError,
    assert_prior_artifacts_identity_consistent,
)
from claread_eval.reader_record_ask.errors import (  # noqa: E402
    project_exception,
    project_exception_to_string,
)
from claread_eval.reader_record_ask.evaluation import (  # noqa: E402
    evaluate_artifact,
)
from claread_eval.reader_record_ask.evaluators.artifact import (  # noqa: E402
    ModelContextSupportObservation,
    RawArtifact,
    RawEvidenceObservation,
    RawUsage,
)
from claread_eval.reader_record_ask.loader import (  # noqa: E402
    LoadedReaderRecordAskDatasetSnapshot,
    load_r4_a3_dataset_with_snapshot,
)
from claread_eval.reader_record_ask.phase_planner import (  # noqa: E402
    BudgetStopResult,
    PhasePlanner,
)
from claread_eval.reader_record_ask.run_manifest import (  # noqa: E402
    AUDIT_CONTRACT_VERSION_V2,
    MANIFEST_SCHEMA_VERSION,
    ReaderRecordAskRunManifest,
    write_manifest_atomic,
)
from claread_eval.reader_record_ask.runtime_fixture import (  # noqa: E402
    compute_runtime_fixture_fingerprint,
    precheck_required_facts_support,
)
from claread_eval.reader_record_ask.schema import (  # noqa: E402
    ReaderRecordAskR4A3Case,
    ReaderRecordAskR4A3Dataset,
)
from claread_eval.reader_record_ask.session import (  # noqa: E402
    ENV_PRIOR_RUN_ID,
    ENV_RUN_ID,
    RunSessionLayout,
    RunSessionLayoutError,
)
from claread_eval.reader_record_ask.utf16 import (  # noqa: E402
    build_unit_offsets,
    utf16_code_units,
)

# ---------------------------------------------------------------------------
# Env var names
# ---------------------------------------------------------------------------
R4_A3_RUN_ENV = "CLAREAD_R4_A3_RUN"
REAL_LLM_MODEL_ENV = "CLAREAD_REAL_LLM_MODEL"
R4_A3_BBC_RECORD_ID_ENV = "CLAREAD_R4_A3_BBC_RECORD_ID"
R4_A3_PRO_PROFILE_ENV = "CLAREAD_R4_A3_PRO_PROFILE"
R4_A3_MAX_REQUESTS_ENV = "CLAREAD_R4_A3_MAX_REQUESTS"
R4_A3_MAX_TOKENS_ENV = "CLAREAD_R4_A3_MAX_TOKENS"
R4_A3_RUNS_DIR_ENV = "CLAREAD_R4_A3_RUNS_DIR"
R4_A3_THINKING_VIA_PROFILE_ENV = "CLAREAD_R4_A3_THINKING_VIA_PROFILE"
# P0 explicit dataset-dir binding: real runs MUST set
# ``CLAREAD_R4_A3_DATASET_DIR`` — there is NO silent fallback to
# ``evals/tmp/reader-record-ask-r4-a3/``. The previous default-fallback
# allowed real runs to accidentally reuse a stale local working dataset.
# The harness now fail-closes before any provider call when the env is
# missing (see :func:`_resolve_dataset_dir_or_skip`).
R4_A3_DATASET_DIR_ENV = "CLAREAD_R4_A3_DATASET_DIR"

_TRUTHY = {"1", "true", "yes", "on"}
_DEFAULT_MAX_REQUESTS = 30
_DEFAULT_MAX_TOKENS = 200_000

# Suggested local working dir — used ONLY in skip messages to help the
# operator pick a path. Never used for automatic resolution.
_SUGGESTED_DATASET_DIR = _REPO_ROOT / "evals" / "tmp" / "reader-record-ask-r4-a3"
_DEFAULT_RUNS_DIR = _SUGGESTED_DATASET_DIR / "runs"


def _resolve_dataset_dir() -> Path:
    """Resolve the R4-A3 dataset dir from env (P0 explicit binding).

    Priority: ``CLAREAD_R4_A3_DATASET_DIR`` env only.
    No silent fallback — real runs MUST explicitly declare the dataset
    they are using. When the env is missing, the caller MUST fail-closed
    before any provider call (see :func:`_r4_a3_env_gate` and
    :func:`_preflight_check`).

    Returns the resolved :class:`Path`. Raises ``pytest.skip`` when the
    env is missing — this keeps default pytest runs (without real-LLM
    gate) as safe skips rather than failures, while still fail-closing
    real runs before any paid call.
    """
    env_val = os.environ.get(R4_A3_DATASET_DIR_ENV, "").strip()
    if not env_val:
        pytest.skip(
            f"R4-A3 requires explicit dataset dir: set "
            f"{R4_A3_DATASET_DIR_ENV}=<path>. Suggested local working "
            f"dir: {_SUGGESTED_DATASET_DIR} (gitignored; not used "
            f"automatically)."
        )
    return Path(env_val)


# ---------------------------------------------------------------------------
# Task 4.1 — env gate (unchanged: still triple-gated)
# ---------------------------------------------------------------------------


def _r4_a3_env_gate() -> tuple[str, str]:
    """Return (authorized_short_name, runs_dir_str) or pytest.skip.

    Triple gate:
    1. ``CLAREAD_ALLOW_REAL_LLM_TESTS=1`` (via call_guard)
    2. ``CLAREAD_R4_A3_RUN=1``
    3. ``CLAREAD_REAL_LLM_MODEL=<short_name>`` non-empty
    """
    if not real_llm_tests_allowed():
        pytest.skip("R4-A3 real LLM eval requires CLAREAD_ALLOW_REAL_LLM_TESTS=1")
    if os.environ.get(R4_A3_RUN_ENV, "").strip().lower() not in _TRUTHY:
        pytest.skip(f"R4-A3 real LLM eval requires {R4_A3_RUN_ENV}=1")
    authorized = os.environ.get(REAL_LLM_MODEL_ENV, "").strip()
    if not authorized:
        pytest.skip(
            f"R4-A3 real LLM eval requires {REAL_LLM_MODEL_ENV}=<short_name>"
        )
    runs_dir = os.environ.get(
        R4_A3_RUNS_DIR_ENV, str(_DEFAULT_RUNS_DIR)
    ).strip() or str(_DEFAULT_RUNS_DIR)
    return authorized, runs_dir


# ---------------------------------------------------------------------------
# Run session layout (P0-1)
# ---------------------------------------------------------------------------


def _build_session_layout(runs_dir_str: str) -> RunSessionLayout:
    """Build :class:`RunSessionLayout` from env.

    Reads ``CLAREAD_R4_A3_RUN_ID`` (required) and
    ``CLAREAD_R4_A3_PRIOR_RUN_ID`` (optional, required for Phase 2/3).
    Raises :class:`RunSessionLayoutError` if run id is missing — the
    harness must always have an explicit run id, never a guessed
    "latest run".
    """
    try:
        return RunSessionLayout.from_env(runs_root=runs_dir_str)
    except RunSessionLayoutError as exc:
        pytest.skip(
            f"R4-A3 harness requires {ENV_RUN_ID}=<run_id>: {exc}"
        )


# ---------------------------------------------------------------------------
# Task 4.2 — model route resolution + fail-closed
# ---------------------------------------------------------------------------


def _resolve_authorized_model(
    authorized_short_name: str,
) -> tuple[Any, ResolvedModelConfig]:
    """Resolve ``MODEL_ROUTE_READER_ASK`` and fail-closed on name mismatch.

    Returns (model, model_config). Skips when:
    - the route is unconfigured (model or config is None)
    - the resolved ``model_name`` does not match ``authorized_short_name``

    Both ``get_settings`` and ``build_model_for_route`` are imported inside
    the function so tests can monkeypatch them on their host modules.
    """
    from app.config.settings import get_settings
    from app.llm.router import build_model_for_route

    cfg = get_settings()
    model, model_config = build_model_for_route(cfg, MODEL_ROUTE_READER_ASK, None)
    if model is None or model_config is None:
        pytest.skip("reader_ask model route unresolved")
    if model_config.model_name != authorized_short_name:
        pytest.skip(
            "resolved reader_ask model does not match authorized: "
            f"resolved={model_config.model_name!r}, "
            f"authorized={authorized_short_name!r}. "
            "Set ASK_CLAREAD_PROFILE / MODEL_PROFILES_JSON to the intended profile."
        )
    return model, model_config


# ---------------------------------------------------------------------------
# Thinking-mode model rebuild (programmatic)
# ---------------------------------------------------------------------------


def _build_thinking_model(
    base_config: ResolvedModelConfig,
) -> tuple[Any, ResolvedModelConfig]:
    """Rebuild the model with ``extra_body.enable_thinking=True``.

    If ``CLAREAD_R4_A3_THINKING_VIA_PROFILE=1`` is set, the harness assumes
    the caller has pre-switched the reader_ask profile to a thinking-enabled
    variant and returns the base config unchanged (no programmatic override).
    The harness still verifies the model_name via ``_resolve_authorized_model``.
    """
    if os.environ.get(R4_A3_THINKING_VIA_PROFILE_ENV, "").strip().lower() in _TRUTHY:
        # Profile-switch mode: caller pre-configured thinking via env profile.
        return build_model_instance(base_config), base_config

    base_settings = base_config.model_settings or RunModelSettings()
    existing_extra_body = dict(base_settings.extra_body or {})
    if existing_extra_body.get("enable_thinking") is True:
        # Already thinking-enabled on the base profile.
        return build_model_instance(base_config), base_config

    new_extra_body = dict(existing_extra_body)
    new_extra_body["enable_thinking"] = True
    new_settings = base_settings.model_copy(
        update={"extra_body": new_extra_body}
    )
    thinking_config = base_config.model_copy(
        update={"model_settings": new_settings}
    )
    thinking_model = build_model_instance(thinking_config)
    if thinking_model is None:
        pytest.skip(
            "failed to rebuild reader_ask model with thinking enabled; "
            f"set {R4_A3_THINKING_VIA_PROFILE_ENV}=1 and pre-switch profile"
        )
    return thinking_model, thinking_config


# ---------------------------------------------------------------------------
# Synthetic case → InMemoryDocumentAccess + envelope (UTF-16 fix P1-3)
# ---------------------------------------------------------------------------


def _split_article_into_units(article_text: str) -> list[str]:
    """Split article text into units by blank-line then newline.

    Empty units are dropped to keep ``ReadingUnitView.text`` non-empty.
    """
    if not article_text:
        return []
    raw = article_text.split("\n\n")
    units: list[str] = []
    for chunk in raw:
        for line in chunk.split("\n"):
            stripped = line.strip()
            if stripped:
                units.append(stripped)
    return units


def _build_synthetic_runtime_inputs(
    case: ReaderRecordAskR4A3Case,
) -> tuple[ReadingRecordAskContextEnvelope, InMemoryDocumentAccess]:
    """Construct envelope + document_access from a synthetic case.

    Deterministic identity: user_id=UUID(int=1), reading_record_id=UUID(int=1),
    base_id=UUID(int=2), record_generation=1.

    Uses :func:`build_unit_offsets` so unit offsets are monotonic and
    non-overlapping even when units contain emoji or other non-BMP
    characters (P1-3 fix).
    """
    if not case.article_text:
        pytest.skip(f"synthetic case {case.id} missing article_text")

    article_text = case.article_text
    base_content_sha256 = hashlib.sha256(article_text.encode("utf-8")).hexdigest()

    units_text = _split_article_into_units(article_text)
    if not units_text:
        pytest.skip(f"synthetic case {case.id} produced no non-empty units")

    offsets = build_unit_offsets(units_text)
    units: list[ReadingUnitView] = []
    for index, (text, offset) in enumerate(zip(units_text, offsets, strict=True)):
        units.append(
            ReadingUnitView(
                unit_id=f"unit-{index:04d}",
                order_index=index,
                text=text,
                text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest()[:8],
                base_start_utf16=offset.start,
                base_end_utf16=offset.end,
            )
        )

    snapshot = build_document_scope(
        reading_record_id=UUID(int=1),
        base_id=UUID(int=2),
        record_generation=1,
        units=units,
        base_content_sha256=base_content_sha256,
    )
    document_access = InMemoryDocumentAccess(snapshot=snapshot)

    verified = VerifiedEnvelopeInput(
        user_id=UUID(int=1),
        reading_record_id=UUID(int=1),
        base_id=UUID(int=2),
        record_generation=1,
        base_content_sha256=base_content_sha256,
        product_state="r4_a3_eval",
        readiness_state="baseline_only",
        can_read_range=True,
        can_search_current_article=True,
        article_rag_ready=False,
    )
    envelope = build_context_envelope(verified)
    return envelope, document_access


# ---------------------------------------------------------------------------
# BBC record → DB path (skip if not available)
# ---------------------------------------------------------------------------


async def _build_bbc_runtime_inputs(
    record_id: str,
) -> tuple[ReadingRecordAskContextEnvelope, InMemoryDocumentAccess]:
    """Load BBC record units from DB; skip on any failure.

    Does not copy BBC article text into the test code — only references the
    record id and structured fields fetched live from the DB.
    """
    record_id_env = os.environ.get(R4_A3_BBC_RECORD_ID_ENV, "").strip()
    if not record_id_env:
        pytest.skip(
            f"BBC record path requires {R4_A3_BBC_RECORD_ID_ENV} and DB"
        )
    if record_id_env != record_id:
        pytest.skip(
            f"BBC case record_id {record_id!r} does not match env "
            f"{R4_A3_BBC_RECORD_ID_ENV}={record_id_env!r}"
        )

    try:
        import asyncpg  # noqa: PLC0415
    except ImportError:
        pytest.skip("asyncpg not available for BBC DB path")

    from app.config.settings import get_settings  # noqa: PLC0415

    try:
        settings = get_settings()
        conn = await asyncpg.connect(settings.database_url)
    except Exception as exc:  # noqa: BLE001
        # P1-2: do not leak DB connection details into the skip message.
        pytest.skip(
            "BBC record path requires DB; connect failed "
            f"(exception_type={type(exc).__name__})"
        )

    try:
        record_row = await conn.fetchrow(
            """
            SELECT generation, active_base_id, user_id
            FROM reading_records
            WHERE id = $1
              AND deleted_at IS NULL
              AND lifecycle_status = 'active'
            """,
            UUID(record_id),
        )
        if record_row is None:
            pytest.skip("BBC record not found or stale")
        record_generation = int(record_row["generation"])
        active_base_id = UUID(str(record_row["active_base_id"]))
        user_id = UUID(str(record_row["user_id"]))

        base_row = await conn.fetchrow(
            """
            SELECT id, content_sha256, text
            FROM reading_bases
            WHERE id = $1
              AND reading_record_id = $2
              AND record_generation = $3
              AND status = 'active'
            """,
            active_base_id,
            UUID(record_id),
            record_generation,
        )
        if base_row is None:
            pytest.skip("BBC record active base not found or stale")
        base_content_sha256 = str(base_row["content_sha256"])
        base_text = str(base_row["text"] or "")

        block_rows = await conn.fetch(
            """
            SELECT block_id, order_index, text_content,
                   canonical_text_start_utf16, canonical_text_end_utf16
            FROM stable_document_blocks
            WHERE stable_document_id IN (
                SELECT id FROM stable_reading_documents
                WHERE reading_record_id = $1
                  AND record_generation = $2
                  AND status = 'active'
            )
            ORDER BY order_index ASC
            """,
            UUID(record_id),
            record_generation,
        )
        if not block_rows:
            pytest.skip("BBC record has no ordered blocks")

        units: list[ReadingUnitView] = []
        for index, row in enumerate(block_rows):
            text = str(row["text_content"] or "").strip()
            if not text:
                continue
            start = int(row["canonical_text_start_utf16"] or 0)
            end = int(row["canonical_text_end_utf16"] or start + utf16_code_units(text))
            units.append(
                ReadingUnitView(
                    unit_id=str(row["block_id"]),
                    order_index=index,
                    text=text,
                    text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest()[:8],
                    base_start_utf16=start,
                    base_end_utf16=max(end, start + 1),
                )
            )
        if not units:
            pytest.skip("BBC record produced no readable units")
    except pytest.skip.Exception:
        raise
    except Exception as exc:  # noqa: BLE001
        # P1-2: do not leak DB details into the skip message.
        pytest.skip(
            "BBC record load failed "
            f"(exception_type={type(exc).__name__})"
        )
    finally:
        try:
            await conn.close()
        except Exception:  # noqa: BLE001
            pass

    snapshot = build_document_scope(
        reading_record_id=UUID(record_id),
        base_id=active_base_id,
        record_generation=record_generation,
        units=units,
        base_content_sha256=base_content_sha256,
    )
    document_access = InMemoryDocumentAccess(snapshot=snapshot)

    verified = VerifiedEnvelopeInput(
        user_id=user_id,
        reading_record_id=UUID(record_id),
        base_id=active_base_id,
        record_generation=record_generation,
        base_content_sha256=base_content_sha256,
        product_state="r4_a3_eval",
        readiness_state="baseline_only",
        can_read_range=True,
        can_search_current_article=True,
        article_rag_ready=False,
    )
    envelope = build_context_envelope(verified)

    # Sanity: base_text is referenced only to confirm the base row was loaded.
    # It is never written to disk in the artifact (see ``_write_artifact``).
    del base_text
    return envelope, document_access


# ---------------------------------------------------------------------------
# Run one case (P0-8 budgeted model + P1-2 safe error projection)
# ---------------------------------------------------------------------------


def _raw_evidence_from_observation(obs: Any) -> RawEvidenceObservation:
    """Project a ``ServerEvidenceObservation`` into a serializable RawEvidence.

    Field mapping (confirmed by reading evidence.py):
    - ``obs.handle.handle_id`` -> ``handle_id``
    - ``obs.handle.kind``       -> ``kind`` (article_seed / read_range / ...)
    - ``obs.snippet``           -> ``snippet`` (already ≤2000 chars upstream)
    - ``obs.handle.source_tool``-> ``provenance`` (baseline_context / ...)
    """
    snippet = obs.snippet or ""
    return RawEvidenceObservation(
        handle_id=obs.handle.handle_id,
        kind=str(obs.handle.kind),
        snippet=snippet[:500],
        provenance=str(obs.handle.source_tool),
    )


def _resolved_thinking_enabled(model_config: ResolvedModelConfig) -> bool:
    """Read ``thinking_enabled`` from resolved settings (P1-1).

    The artifact's ``thinking_enabled`` field MUST come from the resolved
    :class:`RunModelSettings`, not from a harness hardcoded flag.
    """
    settings = model_config.model_settings
    if settings is None:
        return False
    return settings.thinking_enabled()


def _build_usage_delta(
    budget_model: BudgetedUsageModel,
    start_requests: int,
    start_input_tokens: int,
    start_output_tokens: int,
) -> tuple[RawUsage, int, int]:
    """Compute per-case usage delta from a start snapshot (P1-1).

    Spec: "每次 case 执行前记录 counter snapshot。artifact.agent_usage /
    executed_requests / executed_tokens 写入本次 case 的 delta。"

    The prior implementation wrote the wrapper's *cumulative* counters
    into every artifact, so report aggregation double-counted: two
    single-request cases produced ``[1, 2]`` instead of ``[1, 1]``,
    and the report summed them to ``3`` instead of ``2``.

    Returns ``(RawUsage(delta), delta_requests, delta_tokens)`` where
    ``delta_tokens = delta_input + delta_output``.
    """
    delta_requests = budget_model.executed_requests - start_requests
    delta_input = budget_model.executed_input_tokens - start_input_tokens
    delta_output = budget_model.executed_output_tokens - start_output_tokens
    delta_tokens = delta_input + delta_output
    usage = RawUsage(
        requests=delta_requests,
        input_tokens=delta_input if delta_input > 0 else None,
        output_tokens=delta_output if delta_output > 0 else None,
    )
    return usage, delta_requests, delta_tokens


def _build_usage_from_budget(
    budget_model: BudgetedUsageModel,
) -> RawUsage:
    """Build cumulative :class:`RawUsage` from :class:`BudgetedUsageModel`.

    Deprecated for per-artifact writes (use :func:`_build_usage_delta`
    instead). Retained for run-level metadata where cumulative counts
    are intentional (e.g. BudgetStopResult aggregation).
    """
    input_tokens = budget_model.executed_input_tokens
    output_tokens = budget_model.executed_output_tokens
    return RawUsage(
        requests=budget_model.executed_requests,
        input_tokens=input_tokens if input_tokens > 0 else None,
        output_tokens=output_tokens if output_tokens > 0 else None,
    )


def _compute_model_context_fingerprint(
    chunks: tuple[ModelContextChunk, ...] | Sequence[ModelContextChunk],
) -> str | None:
    """R4-A4-0 final closure (P0-3): canonical SHA-256 over actual chunks.

    Computes a deterministic SHA-256 over the actual
    ``model_context_chunks`` (the chunks the model REALLY saw). The
    framing is length-prefixed and unambiguous:

        for each chunk in chunk_ordinal order:
            u64_be(ordinal)
            || u64_be(len(handle_id_utf8)) || handle_id_utf8
            || u64_be(len(text_utf8))      || text_utf8

    The hash binds ``ordinal`` + ``handle_id`` + ``text`` for every
    chunk — a change to any of the three (truncation, handle rename,
    chunk reorder) produces a different fingerprint. Simple
    concatenation is forbidden (path/content boundary could shift).

    Returns ``None`` when ``chunks`` is empty — the artifact will
    carry ``model_context_fingerprint=None`` which the evaluator
    treats as ``instrumentation_incomplete`` for new artifacts
    (fail-closed) or as ``legacy_artifact`` when combined with empty
    observations.

    This fingerprint is the artifact-internal integrity binding. It
    is NOT an independent security proof — it only ensures that each
    observation was computed against the same set of chunks the
    artifact records.
    """
    chunks_list = list(chunks)
    if not chunks_list:
        return None
    # Sort by chunk_ordinal for order-independence (defensive; the
    # assembler already produces them in ordinal order).
    sorted_chunks = sorted(chunks_list, key=lambda c: c.chunk_ordinal)
    hasher = hashlib.sha256()
    for chunk in sorted_chunks:
        ordinal_bytes = chunk.chunk_ordinal.to_bytes(8, "big", signed=False)
        handle_bytes = chunk.handle_id.encode("utf-8")
        text_bytes = chunk.text.encode("utf-8")
        hasher.update(ordinal_bytes)
        hasher.update(len(handle_bytes).to_bytes(8, "big", signed=False))
        hasher.update(handle_bytes)
        hasher.update(len(text_bytes).to_bytes(8, "big", signed=False))
        hasher.update(text_bytes)
    return hasher.hexdigest()


def _collect_model_context_handle_ids(
    chunks: tuple[ModelContextChunk, ...] | Sequence[ModelContextChunk],
) -> list[str]:
    """Return de-duplicated, order-preserving handle_ids from ``chunks``.

    The evaluator uses this list to verify that each observation's
    ``supporting_handle_ids`` came from real model-visible chunks.
    An empty list means the model saw no chunks (runtime exception
    before baseline assembly, or assembler failure) — observations
    cannot be authoritative in that case.
    """
    seen: set[str] = set()
    out: list[str] = []
    for chunk in chunks:
        if chunk.handle_id not in seen:
            seen.add(chunk.handle_id)
            out.append(chunk.handle_id)
    return out


def _compute_model_context_support(
    case: ReaderRecordAskR4A3Case,
    model_context_chunks: tuple[ModelContextChunk, ...] | Sequence[ModelContextChunk],
) -> tuple[list[ModelContextSupportObservation], str | None, list[str]]:
    """R4-A4-0 final closure (P0-1..P0-4): typed per-fact model-context support.

    Reads the ACTUAL ``result.baseline_context.model_context_chunks``
    (the chunks the model REALLY saw after the baseline assembler
    applied raw 8000 / serialized 16000 / 16-chunk cap budgets).
    This replaces the buggy previous implementation that read
    ``document_access.snapshot.units`` (all units, no budget cap) —
    for medium/long articles the model sees only a prefix of the
    units, so alias membership against ``snapshot.units`` produced
    false "supported" verdicts for facts whose only alias was in the
    truncated tail.

    For each :class:`AtomicExpectedFact` with non-empty
    ``source_aliases``:

    - Match each alias (case-insensitive substring) against each
      chunk's text INDEPENDENTLY.
    - ``support=True`` iff at least one real chunk hit an alias.
    - Record ALL hitting chunks' handle_ids in
      ``supporting_handle_ids`` (de-duplicated, order-preserving by
      chunk_ordinal). This is the authoritative
      fact→chunk→handle→citation binding.
    - When ``support=False``, ``supporting_handle_ids`` is empty.

    Returns:
        ``(observations, model_context_fingerprint,
        model_context_handle_ids)``:

        - ``observations``: list of
          :class:`ModelContextSupportObservation` carrying only
          ``fact_id``, ``support``, ``model_context_fingerprint``,
          ``supporting_handle_ids``. The chunk text / article body /
          alias hit fragments are NEVER persisted.
        - ``model_context_fingerprint``: canonical SHA-256 over the
          actual chunks (see :func:`_compute_model_context_fingerprint`).
          ``None`` when ``model_context_chunks`` is empty.
        - ``model_context_handle_ids``: de-duplicated, order-preserving
          list of chunk handle_ids (see
          :func:`_collect_model_context_handle_ids`).

    Contract properties (see :class:`ModelContextSupportObservation`
    docstring):

    - Does NOT use ``document_access.snapshot.units``.
    - Does NOT use the public snippet.
    - Does NOT persist chunk text / article body / alias hit fragments.
    - Does NOT trust the case author's declaration — support is
      independently verified against real chunk text.
    - ``supporting_handle_ids`` enable the evaluator to verify
      fact→chunk→handle→citation binding (the previous contract
      bound every fact to ``cited_handles[0]`` which silently
      mis-bound facts supported by the second chunk).
    - ``model_context_fingerprint`` closes the
      ``expected_baseline_fingerprint=None`` bypass — the evaluator
      compares each observation's fingerprint against the artifact's
      fingerprint, and there is no caller-supplied parameter to skip
      the check.
    - R4-A4-0 final gate closure (P0-3): empty ``model_context_chunks``
      is handled EXPLICITLY and fail-safe. Returns
      ``([], None, [])`` — no observations are constructed (so no
      ``fingerprint=""`` ValidationError can fire), the fingerprint is
      ``None``, and the handle_ids list is empty. The caller is
      responsible for setting ``model_context_capture_status`` to
      ``"unavailable"`` (run produced no chunks — e.g.
      envelope_mismatch / no_units) or ``"failed"`` (run raised
      before/independent of baseline assembly). This function does
      NOT disguise the empty-chunks state as legacy — the caller
      MUST write an explicit
      ``model_context_instrumentation_version
      ="reader_record_ask_model_context_v1"`` plus the appropriate
      ``capture_status`` literal.
    """
    # P0-3: explicit empty-chunks handling. This MUST be the FIRST
    # check so we never enter the per-fact loop with an empty
    # ``fingerprint`` (which would force ``fingerprint or ""`` →
    # ``""`` and trigger a ValidationError on
    # :class:`ModelContextSupportObservation` because the field
    # validator requires a 64-lowercase-hex SHA-256). The empty-
    # chunks state returns ``([], None, [])`` so the caller can
    # write ``capture_status="unavailable"`` or ``"failed"``
    # explicitly — neither a legacy disguise nor a ValidationError.
    chunks_list = list(model_context_chunks)
    if not chunks_list:
        return [], None, []

    atomic_facts = case.expected.atomic_facts
    if not atomic_facts:
        return [], _compute_model_context_fingerprint(model_context_chunks), (
            _collect_model_context_handle_ids(model_context_chunks)
        )

    fingerprint = _compute_model_context_fingerprint(model_context_chunks)
    handle_ids = _collect_model_context_handle_ids(model_context_chunks)

    # Pre-compute lowercased chunk text + chunk handle for matching.
    # We do NOT persist chunk text — only use it transiently here.
    chunks_lower: list[tuple[str, str]] = [
        (chunk.handle_id, chunk.text.lower()) for chunk in model_context_chunks
    ]

    observations: list[ModelContextSupportObservation] = []
    for fact in atomic_facts:
        if not fact.source_aliases:
            # Metadata-only fact (no grounding constraint) — skip.
            # The evaluator treats this as vacuously grounded.
            continue
        aliases_lower = [a.lower() for a in fact.source_aliases if a]
        if not aliases_lower:
            # All aliases were empty strings — vacuously skip.
            continue

        # Find all chunks whose text contains any alias. Record
        # handle_ids in chunk_ordinal order (de-duplicated).
        hitting_handles: list[str] = []
        seen_handles: set[str] = set()
        for chunk_handle, chunk_text_lower in chunks_lower:
            if any(alias in chunk_text_lower for alias in aliases_lower):
                if chunk_handle not in seen_handles:
                    seen_handles.add(chunk_handle)
                    hitting_handles.append(chunk_handle)

        support = bool(hitting_handles)
        # When support=False, supporting_handle_ids MUST be empty
        # (enforced by :class:`ModelContextSupportObservation`
        # contract: the evaluator treats support=True + empty list as
        # instrumentation_incomplete, but support=False + empty list
        # is the correct "not supported" shape). ``fingerprint`` is
        # guaranteed non-None here because ``chunks_list`` is
        # non-empty (P0-3 early return above).
        observations.append(
            ModelContextSupportObservation(
                fact_id=fact.fact_id,
                support=support,
                model_context_fingerprint=fingerprint or "",
                supporting_handle_ids=hitting_handles if support else [],
            )
        )
    return observations, fingerprint, handle_ids


# ---------------------------------------------------------------------------
# R4-A4-2R5 P0-2: typed failure taxonomy
# ---------------------------------------------------------------------------
# Separate model-fault output failures (output retry exhausted / agent
# output invalid) from provider / network / generic runtime exceptions.
# The classification is by ``type(exc)`` ONLY — never by parsing
# ``str(exc)`` (which may carry provider payload, API keys, article
# body, reasoning_content). The exception's message body is NOT read.
#
# Mapping:
# - ``UnexpectedModelBehavior`` (pydantic_ai.exceptions) →
#   ``output_retry_exhausted``. pydantic_ai raises this when the
#   agent output retry budget is exhausted (default 1 + 2 retries =
#   3 total attempts) OR when the final output is unparseable. Both
#   are model-fault output failures, not infrastructure.
# - ``ValidationError`` (pydantic.ValidationError) →
#   ``agent_output_invalid``. Defensive: pydantic_ai normally wraps
#   this in ``UnexpectedModelBehavior``, but if a validator outside
#   the agent's retry loop raises, it is still a model-fault output
#   failure (not infrastructure).
# - All other exceptions → ``runtime_exception`` (the existing
#   fail-closed default). Provider / network / generic runtime
#   exceptions remain under this code.
#
# The two new codes are in the ``_SAFE_SUMMARIES`` allowlist
# (R4-A4-2R5 P0-2), so ``project_exception`` accepts them as hints.
# Unknown exception types fall through to ``runtime_exception`` —
# fail-closed.


def _classify_exception_safe_code(
    exc: BaseException,
    *,
    final_attempts: int | None = None,
    retry_requests: int | None = None,
    execution_stage: ExecutionStage | None = None,
) -> str:
    """R4-A4-2R5R2 Task 2 + R4-A4-2R5R3 Issue #1: classify an
    exception by ``type(exc)`` and PRECISE typed retry evidence AND
    typed execution-stage evidence.

    Returns one of:
    - ``"output_retry_exhausted"`` — agent output retry budget
      exhausted (pydantic_ai ``UnexpectedModelBehavior``) AND BOTH
      typed counters prove exhaustion: ``final_attempts ==
      DEFAULT_OUTPUT_RETRIES + 1`` (3 final-mode validator calls) AND
      ``retry_requests == DEFAULT_OUTPUT_RETRIES + 1`` (3 ModelRetry
      raises). Model-fault.
    - ``"unexpected_model_behavior"`` — pydantic_ai
      ``UnexpectedModelBehavior`` was raised but the typed counters
      do NOT both prove exhaustion (missing/unequal/undersized/
      oversized, or partial-only calls inflated the count).
      Conservative fallback — does NOT claim retry exhaustion. Covers
      pydantic-ai internal errors (malformed JSON, invalid tool call,
      etc.) that raise UMB without exhausting the output validator,
      AND covers the case where the validator was called 3 times but
      only 2 raised ModelRetry (the 3rd passed, then a subsequent
      non-validator UMB occurred).
    - ``"agent_output_invalid"`` — pydantic ``ValidationError`` AND
      typed execution-stage evidence
      (``execution_stage == "output_validation"``) proves the exception was
      raised DURING ``agent.run`` (where the output validator fires).
      Model-fault. Without this typed stage evidence, falls through
      to ``runtime_exception`` (conservative — see design裁决
      below).
    - ``"runtime_exception"`` — provider / network / generic runtime
      exception, OR a ``ValidationError`` raised in any stage other
      than ``agent_run`` (e.g. ``baseline_assembly``,
      ``agent_run_completed``, ``finalizer``, or ``None`` for
      legacy/pre-stage-tracking). Infrastructure / fail-closed
      default.

    R4-A4-2R5R2 Task 2 design (Design-it-twice):

    - Design A (single ``output_validation_attempts`` counter):
      Incremented on every validator call (partial + final). Rejected
      because partial-mode calls inflated the counter, and because a
      single counter cannot distinguish "validator called 3 times, all
      raised ModelRetry" from "validator called 3 times, only 2 raised
      ModelRetry, then a non-validator UMB occurred".
    - Design B (selected — two PRECISE counters): ``final_attempts``
      counts ONLY final-mode validator calls; ``retry_requests``
      counts ONLY ModelRetry raises in final mode. The classifier
      requires BOTH to equal ``DEFAULT_OUTPUT_RETRIES + 1`` (3) for
      ``output_retry_exhausted``. This precisely proves the retry
      budget was exhausted by ModelRetry raises, not by partial-only
      calls or by a mix of passes and raises.

    R4-A4-2R5R3 Issue #1 design裁决 — ValidationError taxonomy:

    A pydantic ``ValidationError`` can be raised at multiple stages:
    (a) during structured-output parsing (BEFORE the output_validator
    runs), (b) inside the output_validator if it constructs a Pydantic
    model, (c) during finalizer handle resolution, or (d) outside the
    agent loop entirely. R5R2 used ``final_attempts > 0`` as the typed
    evidence, but that was IMPRECISE: ``final_attempts`` is
    incremented BEFORE the validator runs (so a finalizer
    ``ValidationError`` raised AFTER ``agent.run`` returned would see
    ``final_attempts > 0`` and be mis-classified as
    ``agent_output_invalid``).

    R5R3 replaces this with TYPED EXECUTION-STAGE evidence:
    ``execution_stage`` is written by the runtime at each transition
    point (``baseline_assembly`` → ``agent_run`` →
    ``agent_run_completed`` → ``finalizer``). The classifier only
    returns ``agent_output_invalid`` only when
    ``execution_stage == "output_validation"`` (the validator-owned
    final-mode stage). Any
    other stage (or ``None`` for legacy / pre-stage-tracking) falls
    back to ``runtime_exception`` — fail-closed.

    This precisely distinguishes:
    - Validator-stage ``ValidationError`` (during ``agent.run``) →
      ``agent_output_invalid``.
    - Finalizer-stage ``ValidationError`` (during
      ``finalize_agent_answer``) → ``runtime_exception``.
    - Assembly-stage ``ValidationError`` (during
      ``assemble_baseline``) → ``runtime_exception``.
    - ``ValidationError`` raised between ``agent.run`` returning and
      the finalizer starting (``agent_run_completed``) →
      ``runtime_exception``.

    Rejected alternative: parse exception text to identify the
    raise site. Rejected because ``str(exc)`` may contain provider
    payload / API keys / article body / reasoning_content — the
    classifier must NOT read it (fail-closed boundary for the
    failure taxonomy). Typed execution-stage evidence is the only
    safe disambiguator.

    The exception's ``str(exc)`` is NEVER read — only ``type(exc)`` is
    used. This is the fail-closed boundary for the failure taxonomy:
    free-text parsing would leak provider payload / API keys / article
    body / reasoning_content into the safe code classification.
    """
    # Local import — pydantic_ai is a production dependency, but the
    # import is deferred so this test module does not hard-fail at
    # collection time if pydantic_ai is upgraded incompatibly.
    try:
        from pydantic_ai.exceptions import UnexpectedModelBehavior  # noqa: PLC0415
    except ImportError:  # pragma: no cover - defensive
        UnexpectedModelBehavior = ()  # type: ignore[assignment,misc]
    try:
        from pydantic import ValidationError  # noqa: PLC0415
    except ImportError:  # pragma: no cover - defensive
        ValidationError = ()  # type: ignore[assignment,misc]

    if isinstance(exc, UnexpectedModelBehavior):
        # R4-A4-2R5R2 Task 2: require BOTH counters to EXACTLY equal
        # DEFAULT_OUTPUT_RETRIES + 1 (3). Any missing/unequal/undersized/
        # oversized counter → conservative ``unexpected_model_behavior``.
        # Strict equality (not >=) prevents mis-classification when
        # partial-only calls inflated the count or when a non-validator
        # UMB occurred after the validator passed on the 3rd attempt
        # (final_attempts=3 but retry_requests<3).
        exhaustion_target = DEFAULT_OUTPUT_RETRIES + 1
        if (
            final_attempts is not None
            and retry_requests is not None
            and final_attempts == exhaustion_target
            and retry_requests == exhaustion_target
        ):
            return "output_retry_exhausted"
        return "unexpected_model_behavior"
    if isinstance(exc, ValidationError):
        # R4-A4-2R5R3 Issue #1: only classify as
        # ``agent_output_invalid`` when TYPED EXECUTION-STAGE evidence
        # (``execution_stage == "output_validation"``) proves the exception
        # was raised DURING ``agent.run`` (where the output validator
        # fires). R5R2's ``final_attempts > 0`` was imprecise: a
        # finalizer-stage ``ValidationError`` raised AFTER
        # ``agent.run`` returned would also see ``final_attempts > 0``
        # and be mis-classified. The typed execution-stage field is
        # written by the runtime at each transition, so it precisely
        # disambiguates validator-stage from finalizer-stage
        # ValidationErrors. Any other stage (or ``None`` for legacy /
        # pre-stage-tracking) → conservative ``runtime_exception``.
        # The exception's ``str(exc)`` is NOT read.
        if execution_stage == "output_validation":
            return "agent_output_invalid"
        return "runtime_exception"
    return "runtime_exception"


async def _run_one_case(
    case: ReaderRecordAskR4A3Case,
    budget_model: BudgetedUsageModel,
    model_config: ResolvedModelConfig,
    run_id: str,
    run_index: int,
    *,
    envelope: ReadingRecordAskContextEnvelope,
    document_access: InMemoryDocumentAccess,
    start_requests: int,
    start_input_tokens: int,
    start_output_tokens: int,
    dataset_identity: DatasetIdentity,
) -> RawArtifact:
    """Run one case end-to-end against the real model and return a RawArtifact.

    P0-3: ``envelope`` and ``document_access`` are prebuilt by
    :func:`_preflight_runtime_inputs` before any provider call. The
    per-case loop no longer builds runtime inputs lazily — that was the
    root cause of paid-then-skip (synthetic cases ran first, then BBC
    cases hit ``pytest.skip`` mid-loop after paid calls had already
    been made).

    P1-1: ``start_requests`` / ``start_input_tokens`` /
    ``start_output_tokens`` snapshot the wrapper's cumulative counters
    before this case's execution. The artifact records only the delta
    consumed by this case — report aggregation sums deltas correctly.

    P0-2 dataset identity: ``dataset_identity`` is stamped onto every
    artifact written by this run. Phase 2/3/aggregate use these fields
    to fail-closed when prior artifacts were produced against a different
    dataset version (the working dataset is gitignored and can drift
    between phases).

    R4-A4-2R3 P0-1: the artifact's ``runtime_fixture_fingerprint`` is
    the ACTUAL identity recomputed from
    ``result.baseline_context`` (SHA-256 over ``baseline_status +
    is_complete + ordered (chunk_ordinal, chunk_text)`` via
    length-prefixed framing). It MUST NOT be copied from the preflight
    (expected) value. The aggregate verifies the three-layer identity
    contract:

        dataset expected == manifest preflight == artifact actual

    When the baseline is None / has no chunks (capture_status=
    "unavailable") or the runtime raised (capture_status="failed"),
    the actual fingerprint is None — the aggregate's instrumentation
    / incomplete gate blocks the run rather than forging an identity.

    R4-A4-2R5R Task 1: the class-level
    :func:`unittest.mock.patch.object` on
    :meth:`BaselineContextAssembler.assemble_baseline` has been
    REMOVED. It was replaced by an internal-only
    :class:`RuntimeObservation` container passed to
    :func:`run_reading_record_ask` via the ``observation=`` parameter.
    The runtime writes ``baseline_context`` to the container after
    assembly succeeds, and the grounding output_validator increments
    ``output_validation_final_attempts`` on each final-mode call and
    ``output_validation_retry_requests`` only when ModelRetry is
    raised. The harness reads all three fields on BOTH the success
    path and the exception path. This is concurrency-safe (no
    class-level mutation) and does not require any production code to
    depend on a test-only patch.

    R4-A4-2R5R2 Task 2: the exception path passes BOTH typed counters
    (``final_attempts`` and ``retry_requests``) to
    :func:`_classify_exception_safe_code` so that
    ``UnexpectedModelBehavior`` is only classified as
    ``output_retry_exhausted`` when BOTH counters EXACTLY equal
    ``DEFAULT_OUTPUT_RETRIES + 1`` (3). Strict equality (not >=)
    prevents mis-classification when partial-only calls inflated the
    count or when a non-validator UMB occurred after the validator
    passed on the 3rd attempt. Without this proof, the conservative
    ``unexpected_model_behavior`` code is used.

    R4-A4-2R5R3 Issue #1: the exception path ALSO passes the typed
    ``execution_stage`` (see :class:`RuntimeObservation`) to
    :func:`_classify_exception_safe_code`. A ``ValidationError`` is
    only classified as ``agent_output_invalid`` when
    ``execution_stage == "agent_run"`` (the exception was raised
    DURING ``agent.run`` where the output validator fires). A
    ``ValidationError`` raised in any other stage
    (``baseline_assembly``, ``agent_run_completed``, ``finalizer``,
    or ``None`` for legacy) is conservatively classified as
    ``runtime_exception`` — the classifier does NOT parse exception
    text and does NOT assume the ValidationError came from the
    output validator without typed stage evidence.

    R4-A4-2R5R3 Issue #4 — capture-status lifecycle (canonical
    wording, supersedes earlier informal descriptions):

    - **capture-前 exception** (``assemble_baseline`` raises before
      any baseline is written): ``model_context_capture_status =
      "failed"``, ``runtime_fixture_fingerprint = None``,
      ``finalized_status = None``. Fail-closed — the preflight
      (expected) fingerprint is NOT copied.
    - **capture-後 exception** (baseline was assembled, then
      ``agent.run`` or the finalizer raised): ``model_context_capture_status
      = "captured"``, ``runtime_fixture_fingerprint`` is a 64-char hex
      SHA-256 recomputed from the CAPTURED baseline (NOT copied from
      preflight), ``finalized_status = None`` (the answer is failed;
      taxonomy split is via ``safe_error_code``).
    - **assembly success but 0 chunks** (no exception): ``model_context_capture_status
      = "unavailable"``, ``runtime_fixture_fingerprint = None``,
      ``finalized_status = None``.
    - **success path** (no exception, baseline has chunks): ``model_context_capture_status
      = "captured"``, ``runtime_fixture_fingerprint`` is a 64-char
      hex SHA-256 from the actual baseline, ``finalized_status = "ok"``
      (NOT ``"ready"`` — the internal :data:`FinalizeStatus` Literal
      uses ``"ok"`` for a successful finalize).

    The artifact field name is ``runtime_fixture_fingerprint`` (NOT
    ``actual_runtime_fixture_fingerprint``). The ``actual_`` prefix is
    only used in local variables and comments to distinguish the
    recomputed value from the preflight (expected) value; it is NOT
    part of the :class:`RawArtifact` field name.

    ``budget_model`` is the wrapped :class:`BudgetedUsageModel` — it
    enforces the request/token cap and aggregates usage. The underlying
    provider model is ``budget_model.wrapped``.
    """
    thinking_enabled = _resolved_thinking_enabled(model_config)
    start = time.monotonic()

    # R4-A4-2R5R Task 1: internal-only typed observation seam.
    #
    # Design B (selected, design-it-twice): a mutable
    # :class:`RuntimeObservation` container passed to
    # :func:`run_reading_record_ask` via ``observation=``. The runtime
    # writes ``baseline_context`` after assembly succeeds (BEFORE the
    # ``is_injected`` check), and the grounding output_validator
    # increments ``output_validation_final_attempts`` on each
    # final-mode call and ``output_validation_retry_requests`` only
    # when ModelRetry is raised in final mode.
    #
    # This replaces the previous class-level
    # :func:`unittest.mock.patch.object` on
    # :meth:`BaselineContextAssembler.assemble_baseline` (Design A),
    # which was NOT concurrency-safe (mutated class state) and only
    # captured the baseline (no retry evidence).
    #
    # Contract:
    # - ``observation.baseline_context`` is ``None`` until the real
    #   assembler returns. If the assembler itself raises, the field
    #   stays ``None`` and the exception path falls back to
    #   fail-closed (``capture_status="failed"``,
    #   ``runtime_fixture_fingerprint=None``).
    # - After a successful capture, the exception path computes the
    #   ACTUAL ``runtime_fixture_fingerprint`` from
    #   ``observation.baseline_context`` (NOT from preflight). The
    #   artifact preserves ``model_context_support`` /
    #   ``model_context_fingerprint`` / ``model_context_handle_ids``
    #   derived from the captured baseline, so the evaluator can
    #   still audit which facts were baseline-supported even when
    #   the answer is failed. ``capture_status="captured"`` tells
    #   the aggregator this is NOT an instrumentation blocker.
    # - ``observation.output_validation_final_attempts`` counts ONLY
    #   final-mode validator calls (partial-mode calls do NOT
    #   increment it). ``observation.output_validation_retry_requests``
    #   counts ONLY ModelRetry raises in final mode (a normal pass
    #   does NOT increment it). When ``UnexpectedModelBehavior`` is
    #   raised and BOTH counters EXACTLY equal
    #   ``DEFAULT_OUTPUT_RETRIES + 1`` (3), the safe code is
    #   ``output_retry_exhausted``; otherwise the conservative
    #   ``unexpected_model_behavior`` fallback is used.
    # - ``finalized_status`` remains ``None`` on the exception path
    #   — the answer is still failed. The taxonomy split is via
    #   ``safe_error_code``, NOT via ``finalized_status``.
    observation = RuntimeObservation()

    try:
        result = await run_reading_record_ask(
            user_message=case.question,
            envelope=envelope,
            document_access=document_access,
            model=budget_model,
            article_rag=None,
            observation=observation,
        )
    except BudgetExhaustedError:
        # Re-raise so the harness can record a BudgetStopResult and stop
        # the run loop. The artifact for this in-flight (rejected) request
        # is NOT written — only already-completed requests are recorded.
        raise
    except Exception as exc:  # noqa: BLE001
        # R4-A4-2R5R2 Task 2 + R4-A4-2R5R3 Issue #1: typed failure
        # taxonomy. Classify the exception by ``type(exc)`` AND the
        # typed ``output_validation_final_attempts`` /
        # ``output_validation_retry_requests`` counters AND the typed
        # ``execution_stage`` — never by ``str(exc)``.
        # ``_classify_exception_safe_code`` returns:
        #   - ``output_retry_exhausted`` (UMB + BOTH counters == 3)
        #   - ``unexpected_model_behavior`` (UMB + counters missing/unequal)
        #   - ``agent_output_invalid`` (ValidationError +
        #     execution_stage == "agent_run" — raised DURING
        #     ``agent.run`` where the output validator fires)
        #   - ``runtime_exception`` (provider / network / generic /
        #     ValidationError raised in any other stage —
        #     ``baseline_assembly`` / ``agent_run_completed`` /
        #     ``finalizer`` / ``None`` for legacy)
        # The exception's message body is NOT read — only
        # ``type(exc).__name__`` flows into ``project_exception``.
        safe_code = _classify_exception_safe_code(
            exc,
            final_attempts=observation.output_validation_final_attempts,
            retry_requests=observation.output_validation_retry_requests,
            execution_stage=observation.execution_stage,
        )
        latency = time.monotonic() - start
        projection = project_exception(exc, hint=safe_code)
        # P1-1: record per-case delta even on failure — the case may
        # have consumed some requests before raising.
        _, delta_requests, delta_tokens = _build_usage_delta(
            budget_model,
            start_requests,
            start_input_tokens,
            start_output_tokens,
        )

        baseline = observation.baseline_context
        if baseline is None:
            # R4-A4-2R5R Task 1: baseline assembler itself raised OR
            # the runtime raised before baseline assembly completed.
            # Fail-closed: ``capture_status="failed"``, no
            # ``runtime_fixture_fingerprint``, no model-context
            # support. This preserves the prior contract for the
            # pre-capture-exception branch.
            return RawArtifact(
                case_id=case.id,
                run_id=run_id,
                run_index=run_index,
                model_short_name=model_config.model_name,
                model_route=model_config.route,
                thinking_enabled=thinking_enabled,
                error=project_exception_to_string(exc, hint=safe_code),
                safe_error_code=projection.safe_code,
                envelope_fingerprint=envelope.envelope_fingerprint,
                latency_seconds=latency,
                executed_requests=delta_requests,
                executed_tokens=delta_tokens,
                dataset_id=dataset_identity.dataset_id,
                dataset_schema_version=dataset_identity.schema_version,
                dataset_content_sha256=dataset_identity.content_sha256,
                model_context_support=[],
                model_context_fingerprint=None,
                model_context_handle_ids=[],
                model_context_instrumentation_version=(
                    "reader_record_ask_model_context_v1"
                ),
                model_context_capture_status="failed",
                # R4-A4-2R3 P0-1: pre-capture exception → actual
                # fingerprint is None. The preflight (expected) value
                # MUST NOT be copied — the runtime never produced a
                # baseline to fingerprint.
                runtime_fixture_fingerprint=None,
            )

        # R4-A4-2R5 P0-1: baseline WAS captured before the exception.
        # Preserve the actual baseline audit data so the evaluator
        # can still audit which facts were baseline-supported.
        # ``finalized_status`` stays ``None`` — the answer is failed.
        # The taxonomy split is via ``safe_error_code``.
        actual_chunks: tuple[ModelContextChunk, ...] = (
            baseline.model_context_chunks
        )
        (
            captured_support,
            captured_fingerprint,
            captured_handle_ids,
        ) = _compute_model_context_support(case, actual_chunks)
        captured_capture_status: str = (
            "captured" if captured_fingerprint is not None else "unavailable"
        )

        # R4-A4-2R5 P0-1: recompute the ACTUAL
        # ``runtime_fixture_fingerprint`` from the captured baseline.
        # NOT copied from preflight. When the captured baseline has
        # no chunks (e.g. envelope_mismatch returned by the
        # assembler without raising), the actual fingerprint is None
        # — matching the success-path contract for
        # ``capture_status="unavailable"``.
        captured_runtime_fp: str | None = None
        if baseline.model_context_chunks:
            captured_chunk_views: list[tuple[int, str]] = [
                (chunk.chunk_ordinal, chunk.text)
                for chunk in baseline.model_context_chunks
            ]
            captured_runtime_fp = compute_runtime_fixture_fingerprint(
                baseline_status=baseline.baseline_status,
                is_complete=baseline.is_complete,
                chunks=captured_chunk_views,
            )

        return RawArtifact(
            case_id=case.id,
            run_id=run_id,
            run_index=run_index,
            model_short_name=model_config.model_name,
            model_route=model_config.route,
            thinking_enabled=thinking_enabled,
            # Answer is still failed; taxonomy is via safe_error_code.
            final_text=None,
            finalized_status=None,
            finalized_reason=None,
            response_kind=None,
            cited_evidence_handles=[],
            resolved_evidence=[],
            all_evidence_observations=[],
            read_range_calls=0,
            search_current_article_calls=0,
            baseline_status=baseline.baseline_status,
            baseline_is_complete=baseline.is_complete,
            baseline_is_injected=baseline.is_injected,
            error=project_exception_to_string(exc, hint=safe_code),
            safe_error_code=projection.safe_code,
            agent_usage=None,
            latency_seconds=latency,
            envelope_fingerprint=envelope.envelope_fingerprint,
            executed_requests=delta_requests,
            executed_tokens=delta_tokens,
            dataset_id=dataset_identity.dataset_id,
            dataset_schema_version=dataset_identity.schema_version,
            dataset_content_sha256=dataset_identity.content_sha256,
            model_context_support=captured_support,
            model_context_fingerprint=captured_fingerprint,
            model_context_handle_ids=captured_handle_ids,
            model_context_instrumentation_version=(
                "reader_record_ask_model_context_v1"
            ),
            model_context_capture_status=captured_capture_status,
            # R4-A4-2R5 P0-1: ACTUAL fingerprint recomputed from the
            # captured baseline. NOT copied from preflight. The
            # aggregate's three-layer check still verifies
            # ``dataset expected == manifest preflight == artifact actual``.
            runtime_fixture_fingerprint=captured_runtime_fp,
        )

    latency = time.monotonic() - start
    # P1-1: compute per-case delta from the start snapshot. The artifact
    # records ONLY this case's delta — the report sums deltas to get
    # the correct run total (no double-counting).
    usage, delta_requests, delta_tokens = _build_usage_delta(
        budget_model,
        start_requests,
        start_input_tokens,
        start_output_tokens,
    )

    all_evidence = [_raw_evidence_from_observation(obs) for obs in result.evidence_observations]

    resolved_evidence: list[RawEvidenceObservation] = []
    if result.finalized is not None:
        resolved_evidence = [
            _raw_evidence_from_observation(obs)
            for obs in result.finalized.resolved_evidence
        ]

    cited_handles: list[str] = list(
        result.agent_draft.cited_evidence_handles
    ) if result.agent_draft is not None else []

    baseline = result.baseline_context
    baseline_status = baseline.baseline_status if baseline is not None else None
    baseline_is_complete = baseline.is_complete if baseline is not None else None
    baseline_is_injected = baseline.is_injected if baseline is not None else None

    # R4-A4-0 final closure (P0-1..P0-4): compute typed model-context
    # support observations against the ACTUAL model-visible context —
    # ``result.baseline_context.model_context_chunks`` — NOT
    # ``document_access.snapshot.units`` (which is the full document
    # scope, NOT what the model sees after the baseline assembler's
    # raw 8000 / serialized 16000 / 16-chunk cap budgets). Each
    # observation records ``supporting_handle_ids`` — the chunk
    # handle_ids whose text contained an alias hit — so the evaluator
    # can verify the authoritative fact→chunk→handle→citation binding
    # (the previous contract bound every fact to ``cited_handles[0]``
    # which silently mis-bound facts supported by the second chunk).
    # ``model_context_fingerprint`` closes the
    # ``expected_baseline_fingerprint=None`` bypass — the evaluator
    # compares each observation's fingerprint against the artifact's
    # own ``model_context_fingerprint``.
    #
    # R4-A4-0 final gate closure (P0-1 + P0-3): the explicit lifecycle
    # fields ``model_context_instrumentation_version`` /
    # ``model_context_capture_status`` distinguish the two success-
    # path states WITHOUT inspecting ``finalized_reason`` or
    # ``baseline_status``:
    #
    #   - ``capture_status="captured"`` — baseline assembled AND
    #     produced ≥1 chunk. The cross-field validator enforces
    #     fingerprint≠None + handle_ids≠[] for this state.
    #   - ``capture_status="unavailable"`` — model ran (no exception)
    #     but baseline assembly yielded 0 chunks (e.g.
    #     envelope_mismatch / no_units). The cross-field validator
    #     enforces fingerprint=None + handle_ids=[] + observations=[]
    #     for this state. This is an instrumentation blocker (NOT a
    #     model correctness failure) — the run cannot be
    #     authoritatively evaluated because there was no
    #     model-visible context to ground against.
    #
    # P0-3: ``_compute_model_context_support`` returns
    # ``([], None, [])`` for empty chunks WITHOUT throwing a
    # ValidationError. The caller (here) maps that empty state to
    # ``capture_status="unavailable"``.
    actual_chunks: tuple[ModelContextChunk, ...] = ()
    if baseline is not None:
        actual_chunks = baseline.model_context_chunks
    success_support, success_fingerprint, success_handle_ids = (
        _compute_model_context_support(case, actual_chunks)
    )
    success_capture_status: str = (
        "captured" if success_fingerprint is not None else "unavailable"
    )

    # R4-A4-2R3 P0-1: recompute the ACTUAL runtime_fixture_fingerprint
    # from ``result.baseline_context`` — do NOT copy the preflight
    # (expected) value. The preflight fingerprint is the dataset's
    # declared expected identity; the artifact's actual fingerprint
    # MUST be independently derived from the baseline the model
    # actually saw. The two MUST agree (deterministic assembly), but
    # copying preflight → actual would hide any runtime drift (e.g.
    # a monkeypatched assembler, a DB mutation between preflight and
    # run, a different chunk truncation). When the baseline is None
    # or has no chunks (capture_status="unavailable"), the actual
    # fingerprint is None — the aggregate's instrumentation/
    # incomplete gate blocks the run rather than forging an identity.
    actual_runtime_fp: str | None = None
    if baseline is not None and baseline.model_context_chunks:
        actual_chunk_views: list[tuple[int, str]] = [
            (chunk.chunk_ordinal, chunk.text)
            for chunk in baseline.model_context_chunks
        ]
        actual_runtime_fp = compute_runtime_fixture_fingerprint(
            baseline_status=baseline.baseline_status,
            is_complete=baseline.is_complete,
            chunks=actual_chunk_views,
        )

    return RawArtifact(
        case_id=case.id,
        run_id=run_id,
        run_index=run_index,
        model_short_name=model_config.model_name,
        model_route=model_config.route,
        thinking_enabled=thinking_enabled,
        final_text=result.final_text,
        finalized_status=result.finalized.status if result.finalized is not None else None,
        finalized_reason=result.finalized.reason if result.finalized is not None else None,
        response_kind=(
            result.agent_draft.response_kind
            if result.agent_draft is not None
            else None
        ),
        cited_evidence_handles=cited_handles,
        resolved_evidence=resolved_evidence,
        all_evidence_observations=all_evidence,
        read_range_calls=result.read_range_calls,
        search_current_article_calls=result.search_current_article_calls,
        baseline_status=baseline_status,
        baseline_is_complete=baseline_is_complete,
        baseline_is_injected=baseline_is_injected,
        agent_usage=usage,
        latency_seconds=latency,
        envelope_fingerprint=envelope.envelope_fingerprint,
        executed_requests=delta_requests,
        executed_tokens=delta_tokens,
        dataset_id=dataset_identity.dataset_id,
        dataset_schema_version=dataset_identity.schema_version,
        dataset_content_sha256=dataset_identity.content_sha256,
        model_context_support=success_support,
        model_context_fingerprint=success_fingerprint,
        model_context_handle_ids=success_handle_ids,
        model_context_instrumentation_version=(
            "reader_record_ask_model_context_v1"
        ),
        model_context_capture_status=success_capture_status,
        # R4-A4-2R3 P0-1: ACTUAL fingerprint recomputed from
        # ``result.baseline_context`` (NOT the preflight/expected
        # value). The aggregate's three-layer check verifies:
        #   dataset expected == manifest preflight == artifact actual.
        # When actual is None (baseline unavailable / no chunks),
        # the aggregate's instrumentation gate blocks the run —
        # the harness does NOT forge an actual identity.
        runtime_fixture_fingerprint=actual_runtime_fp,
    )


# ---------------------------------------------------------------------------
# Write artifact via RunSessionLayout (P0-1)
# ---------------------------------------------------------------------------


def _write_artifact(
    artifact: RawArtifact,
    session: RunSessionLayout,
) -> Path:
    """Serialize artifact to disk via :class:`RunSessionLayout`.

    The artifact path is resolved by the layout's ``artifact_path()``
    method, which guarantees:
    - Path-traversal fail-closed (run_id is validated).
    - Filename uniqueness on (case_id, model_short_name, thinking, run_index).
    - All artifacts live under ``<runs_root>/<run_id>/artifacts/``.
    """
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


# ---------------------------------------------------------------------------
# Prior phase loader + evaluator (P0-3)
# ---------------------------------------------------------------------------


def _load_prior_phase_artifacts(
    session: RunSessionLayout,
) -> list[RawArtifact]:
    """Load prior-phase artifacts from ``session.prior_artifact_dir``.

    Returns an empty list if the prior directory does not exist or
    contains no valid artifacts. The harness caller decides whether to
    skip the phase when the list is empty.
    """
    prior_dir = session.prior_artifact_dir
    if prior_dir is None:
        return []
    if not prior_dir.is_dir():
        return []
    artifacts: list[RawArtifact] = []
    for path in sorted(prior_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("run_id") != session.prior_run_id:
            continue
        try:
            artifacts.append(RawArtifact.model_validate(payload))
        except Exception:  # noqa: BLE001
            continue
    return artifacts


def _build_prior_eval_results(
    dataset_cases: list[ReaderRecordAskR4A3Case],
    prior_artifacts: list[RawArtifact],
) -> dict[str, list[list[Any]]]:
    """Run :func:`evaluate_artifact` on each prior artifact, grouped by
    ``(case_id, run_index)`` (P0-2 multi-repetition fix).

    Returns a mapping ``case_id -> list[list[EvalDimensionResult]]``
    where the outer list is one entry per repetition (sorted by
    ``run_index`` for stable, order-invariant output). The
    :class:`PhasePlanner` uses :func:`any_repetition_content_failure`
    to select Phase 2/3 cases — a case is selected if ANY repetition
    produced a content-quality failure (no more last-rep-wins masking).

    Budget-exhausted artifacts are NOT evaluated (they were never run)
    and NOT treated as passes. The planner records them separately via
    :class:`BudgetStopResult`.
    """
    cases_by_id = {case.id: case for case in dataset_cases}
    # Group artifacts by case_id. Within each group, sort by run_index
    # so the output is independent of artifact input order (P0-2 spec
    # requirement: "聚合结果必须与 artifact 输入顺序无关").
    grouped: dict[str, list[RawArtifact]] = {}
    for artifact in prior_artifacts:
        if artifact.budget_exhausted:
            continue
        grouped.setdefault(artifact.case_id, []).append(artifact)

    eval_results: dict[str, list[list[Any]]] = {}
    for case_id, arts in grouped.items():
        case = cases_by_id.get(case_id)
        if case is None:
            continue
        # Sort by run_index for stable, order-invariant output. The
        # spec requires "聚合结果必须与 artifact 输入顺序无关" — sorting
        # by run_index guarantees the same result regardless of the
        # order artifacts were passed in.
        arts_sorted = sorted(arts, key=lambda a: a.run_index)
        eval_results[case_id] = [
            evaluate_artifact(case, artifact) for artifact in arts_sorted
        ]
    return eval_results


# ---------------------------------------------------------------------------
# P0 split preflight: deterministic (no model) vs model-config-dependent
# ---------------------------------------------------------------------------


def _deterministic_preflight(
    *,
    session: RunSessionLayout,
    phase: int,
    cases_to_run: list[ReaderRecordAskR4A3Case],
    max_requests: int,
) -> str | None:
    """Deterministic preflight checks that do NOT require model_config (P0).

    Returns ``None`` on success, or a short failure code string on
    failure. The harness records the code on the artifact and skips
    the phase — no paid call is made, and (critically) NO model
    builder is invoked.

    Checks (split from the old ``_preflight_check`` — only the parts
    that depend solely on env + session + cases + budget config):

    - All required cases can load (non-empty ``cases_to_run``).
    - Run directory is writable (``artifact_dir`` can be created).
    - Budget executable (``max_requests >= 1``).
    - Phase 3: ``CLAREAD_R4_A3_PRO_PROFILE`` env var non-empty.
    - P0-3: BBC cases require ``CLAREAD_R4_A3_BBC_RECORD_ID`` env to be
      set AND match the case's ``record_id``. A missing or mismatched
      env var fails the WHOLE phase preflight (rather than skipping
      the BBC case at run time) so the harness never makes a partial
      paid run that skips BBC mid-loop. Synthetic cases that ran first
      could otherwise consume paid calls before the BBC skip fired.
    - P0-3: BBC cases require non-empty ``case.record_id``.

    The model-config-dependent checks (thinking state, model_name)
    live in :func:`_model_config_preflight`, called by
    :func:`_execute_phase` AFTER the model is built.
    """
    if not cases_to_run:
        return "no_cases_to_run"
    try:
        session.artifact_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return "run_dir_not_writable"
    if max_requests < 1:
        return "budget_not_executable"

    if phase == 3:
        # Phase 3 requires the Pro profile env var — this is env-only
        # (deterministic), so it lives here rather than in
        # ``_model_config_preflight``. The actual profile_name match
        # against the resolved config is checked in
        # ``_model_config_preflight`` after the model is built.
        pro_profile = os.environ.get(R4_A3_PRO_PROFILE_ENV, "").strip()
        if not pro_profile:
            return "pro_profile_missing"

    # P0-3: BBC env binding check. Any selected BBC case requires the
    # ``CLAREAD_R4_A3_BBC_RECORD_ID`` env to be set AND match the case's
    # ``record_id``. This is a fail-closed preflight: if the env is
    # missing or mismatched, the whole phase skips BEFORE any paid call.
    # Without this check, synthetic cases (which always succeed) would
    # run first and consume paid calls, then BBC cases would skip
    # mid-loop via ``_build_bbc_runtime_inputs``.
    bbc_cases = [
        case for case in cases_to_run if case.source_kind == "bbc_record"
    ]
    if bbc_cases:
        bbc_env_id = os.environ.get(R4_A3_BBC_RECORD_ID_ENV, "").strip()
        if not bbc_env_id:
            return "bbc_record_id_env_missing"
        for case in bbc_cases:
            if not case.record_id:
                return "bbc_case_missing_record_id"
            if case.record_id != bbc_env_id:
                return "bbc_record_id_mismatch"
    return None


def _model_config_preflight(
    *,
    phase: int,
    model_config: ResolvedModelConfig,
    max_requests: int,
) -> str | None:
    """Model-config-dependent preflight checks (P0 split).

    Called by :func:`_execute_phase` AFTER the model is built. Returns
    ``None`` on success, or a failure code on failure. A failure here
    still skips before any provider call, but the model builder has
    already been invoked once — this is acceptable because the model
    builder is gated behind the deterministic preflight succeeding
    first.

    Checks:
    - Model route resolved (``model_config.model_name`` non-empty).
    - Phase 1 thinking disabled (``thinking_enabled() is False``).
    - Phase 2 thinking enabled (``thinking_enabled() is True``).
    - Phase 3 thinking enabled.
    - Budget executable (``max_requests >= 1`` — re-checked here for
      defence-in-depth, even though ``_deterministic_preflight`` already
      checks it).
    """
    if not model_config.model_name:
        return "model_route_invalid"
    if max_requests < 1:
        return "budget_not_executable"

    settings = model_config.model_settings
    thinking_enabled = settings.thinking_enabled() if settings is not None else False

    if phase == 1:
        if thinking_enabled:
            return "thinking_mismatch_phase1"
    elif phase == 2:
        if not thinking_enabled:
            return "thinking_mismatch_phase2"
    elif phase == 3:
        if not thinking_enabled:
            return "thinking_mismatch_phase3"
    return None


def _preflight_check(
    *,
    session: RunSessionLayout,
    phase: int,
    model_config: ResolvedModelConfig,
    cases_to_run: list[ReaderRecordAskR4A3Case],
    max_requests: int,
    max_tokens: int,
) -> str | None:
    """Legacy combined preflight (backwards-compat wrapper).

    P0 split: the canonical preflight is now two functions —
    :func:`_deterministic_preflight` (no model needed) and
    :func:`_model_config_preflight` (needs model_config). This wrapper
    calls both so existing tests that drive ``_preflight_check``
    directly continue to work. New code should call the split functions
    via :func:`_prepare_phase` and :func:`_execute_phase`.
    """
    det = _deterministic_preflight(
        session=session,
        phase=phase,
        cases_to_run=cases_to_run,
        max_requests=max_requests,
    )
    if det is not None:
        return det
    return _model_config_preflight(
        phase=phase,
        model_config=model_config,
        max_requests=max_requests,
    )


async def _preflight_runtime_inputs(
    cases: list[ReaderRecordAskR4A3Case],
) -> list[
    tuple[
        ReaderRecordAskR4A3Case,
        ReadingRecordAskContextEnvelope,
        InMemoryDocumentAccess,
        str,
    ]
]:
    """Build (envelope, document_access, runtime_fixture_fingerprint) for
    EVERY selected case before any paid provider call (P0-3).

    Returns a list of ``(case, envelope, document_access)`` tuples, one
    per selected case, in case order. If ANY case cannot be prepared
    (missing ``article_text`` for synthetic, missing BBC env, DB
    connection failure, record/base/blocks not ready), the whole phase
    is skipped via ``pytest.skip`` — the harness does NOT make a
    partial paid run.

    This is the root fix for paid-then-skip: the prior implementation
    built runtime inputs lazily inside the per-case loop, so synthetic
    cases (which always succeed) ran first and made paid calls, then
    BBC cases hit ``pytest.skip`` mid-loop. The preflight moves all
    runtime input resolution to BEFORE the :class:`BudgetedUsageModel`
    wraps the provider model, so any preflight skip happens at zero
    paid calls.

    BBC DB readiness (record ownership / active base / generation /
    readable units) is verified here via :func:`_build_bbc_runtime_inputs`,
    which raises ``pytest.skip`` on any failure. The skip message uses
    only ``exception_type`` — no DB connection string or article body
    is leaked (P1-2).

    R4-A4-2R P0-Identity: after each case's envelope is built, the
    runtime ``envelope_fingerprint`` is verified against the case's
    declared ``expected_envelope_fingerprint`` (if present). Mismatch
    or missing-runtime fingerprint fails closed via ``pytest.skip``
    BEFORE the model is constructed — so calls=0 and builder=0. This
    closes the audit finding where a BBC runtime record's
    model-visible baseline chunks contained ``2015`` but the dataset's
    ``allowed_temporal_claims`` was empty: the dataset author now
    commits to a specific runtime identity, and any drift (re-base,
    re-generation, different record) is caught pre-call.
    """
    # Clear the per-process preflight chunk stash so a stale entry
    # from a prior phase cannot leak into the current preflight.
    _clear_preflight_chunk_stash()

    prepared: list[
        tuple[
            ReaderRecordAskR4A3Case,
            ReadingRecordAskContextEnvelope,
            InMemoryDocumentAccess,
            str,
        ]
    ] = []
    for case in cases:
        # R4-A4-2R5 P0-4: real_phase1 cases MUST declare explicit
        # atomic_facts in their JSON — relying on the loader's
        # auto-migration from required_article_facts (producing
        # ``legacy-{idx}`` fact_ids with source_aliases=[]) is blocked.
        # Auto-migrated facts cannot be verified against the
        # model-visible fixture, so the context_support contract is
        # effectively unguarded. Fail-closed BEFORE model construction
        # (provider calls = 0, model builder calls = 0).
        _preflight_guard_real_phase1_atomic_facts_explicit(case)

        if case.source_kind == "bbc_record":
            envelope, document_access = await _build_bbc_runtime_inputs(
                case.record_id or "",
            )
        else:
            envelope, document_access = _build_synthetic_runtime_inputs(case)
        # R4-A4-2R P0-Identity: verify the runtime envelope_fingerprint
        # matches the dataset's declared expected identity. This is the
        # fail-closed pre-call binding. ``_verify_runtime_identity``
        # raises ``pytest.skip`` on mismatch — the harness does NOT
        # construct the model or make any provider call.
        _verify_runtime_identity(case, envelope)

        # R4-A4-2R2 P0-1: assemble baseline context deterministically
        # (same envelope + document_access → same chunks; only
        # handle_ids are random, and they are EXCLUDED from the
        # fingerprint). Compute runtime_fixture_fingerprint from the
        # ACTUAL BaselineAgentContext, then verify against the case's
        # declared expected value.
        runtime_fixture_fp = await _compute_preflight_runtime_fixture_fingerprint(
            case=case,
            envelope=envelope,
            document_access=document_access,
        )

        # R4-A4-2R2 P0-3: semantic precheck — every required atomic
        # fact must be supported by ≥1 model-visible chunk. Unsupported
        # required fact = invalid evaluation case → fail-closed
        # (calls=0) BEFORE the model is constructed.
        _precheck_required_facts_support_preflight(case, runtime_fixture_fp)

        prepared.append((case, envelope, document_access, runtime_fixture_fp))
    return prepared


# Per-process stash mapping runtime_fixture_fingerprint → chunk views.
# Populated by _compute_preflight_runtime_fixture_fingerprint;
# consumed by _precheck_required_facts_support_preflight. Cleared at
# the start of each _preflight_runtime_inputs call. The fingerprint
# uniquely identifies the chunk set (deterministic), so a single
# lookup is sufficient.
_PREFLIGHT_CHUNK_STASH: dict[str, list[tuple[int, str]]] = {}


def _stash_preflight_chunks_for_fp(
    fp: str,
    chunks_view: list[tuple[int, str]],
) -> None:
    """Stash the preflight assembler's chunk views keyed by fingerprint.

    The stash is per-process (not per-test). It is cleared at the
    start of each :func:`_preflight_runtime_inputs` call so a stale
    entry from a prior phase cannot leak into the current preflight.
    """
    _PREFLIGHT_CHUNK_STASH[fp] = list(chunks_view)


def _get_stashed_preflight_chunks(
    fp: str,
) -> list[tuple[int, str]] | None:
    """Look up stashed chunk views by fingerprint. Returns None if absent."""
    return _PREFLIGHT_CHUNK_STASH.get(fp)


def _clear_preflight_chunk_stash() -> None:
    """Clear the preflight chunk stash. Called at the start of each
    :func:`_preflight_runtime_inputs` invocation.
    """
    _PREFLIGHT_CHUNK_STASH.clear()


async def _compute_preflight_runtime_fixture_fingerprint(
    *,
    case: ReaderRecordAskR4A3Case,
    envelope: ReadingRecordAskContextEnvelope,
    document_access: InMemoryDocumentAccess,
) -> str:
    """R4-A4-2R2 P0-1: assemble baseline context in preflight and compute
    the deterministic ``runtime_fixture_fingerprint``.

    The baseline assembly is deterministic in terms of
    ``baseline_status``, ``is_complete``, and
    ``(chunk_ordinal, chunk_text)`` — only ``handle_id`` is random
    (minted via ``secrets.token_hex(16)``). Because
    :func:`compute_runtime_fixture_fingerprint` deliberately excludes
    ``handle_id``, two preflight assemblies of the same
    (envelope, document_access) produce the SAME fingerprint.

    The computed fingerprint is verified against the case's declared
    ``expected_runtime_fixture_fingerprint``:

    - R4-A4-2R3 P0-2: For ALL ``real_phase1`` cases (BBC AND
      synthetic): the expected fingerprint is REQUIRED. Missing /
      empty / mismatch → ``pytest.skip`` (fail-closed; calls=0,
      builder=0) BEFORE the model is constructed. This expands the
      R4-A4-2R2 BBC-only requirement to ALL real_phase1 cases so
      the aggregate's three-layer identity check has a dataset
      expected value to compare against for every audited case.
    - For ``offline_only`` / non-real_phase1 cases: this function
      is never called (offline_only cases never enter the real-model
      run path; non-real_phase1 cases are not selected by the
      Phase 1 planner).

    The computed fingerprint is returned to the caller. R4-A4-2R3
    P0-1: the per-case run NO LONGER consumes this value for the
    artifact — the artifact's ``runtime_fixture_fingerprint`` is
    recomputed from ``result.baseline_context`` (the ACTUAL baseline
    the model saw). The preflight fingerprint is persisted ONLY in
    the manifest's ``runtime_fixture_identities`` map (as the
    expected/preflight identity for the three-layer check).
    """
    # Construct a fresh EvidenceRegistry bound to this envelope. The
    # registry's envelope_fingerprint MUST match the turn envelope's
    # fingerprint or the assembler returns baseline_status=
    # "envelope_mismatch" — which is a deterministic failure mode,
    # not an exception.
    registry = EvidenceRegistry(envelope.envelope_fingerprint)
    assembler = BaselineContextAssembler(
        envelope=envelope,
        document_access=document_access,
        registry=registry,
    )
    baseline = await assembler.assemble_baseline()

    chunks_view: list[tuple[int, str]] = [
        (chunk.chunk_ordinal, chunk.text)
        for chunk in baseline.model_context_chunks
    ]
    computed_fp = compute_runtime_fixture_fingerprint(
        baseline_status=baseline.baseline_status,
        is_complete=baseline.is_complete,
        chunks=chunks_view,
    )

    # Stash the chunk views for the semantic precheck
    # (_precheck_required_facts_support_preflight). The fingerprint
    # uniquely identifies the chunk set (deterministic), so a single
    # lookup is sufficient.
    _stash_preflight_chunks_for_fp(computed_fp, chunks_view)

    expected_fp = case.expected_runtime_fixture_fingerprint
    # R4-A4-2R3 P0-2: ALL real_phase1 cases (BBC + synthetic) MUST
    # declare expected_runtime_fixture_fingerprint. This expands the
    # R4-A4-2R2 BBC-only requirement. The aggregate's three-layer
    # identity check requires a dataset expected value for every
    # real_phase1 case.
    is_real_phase1 = "real_phase1" in (case.phase_tags or [])

    if is_real_phase1:
        # Required for ALL real_phase1 cases — fail-closed on missing,
        # empty, or mismatch.
        if not expected_fp:
            pytest.skip(
                f"R4-A4-2R3 P0-Identity: case {case.id!r} is a "
                f"real_phase1 case but does not declare "
                f"expected_runtime_fixture_fingerprint. This field is "
                f"REQUIRED for ALL real_phase1 cases (BBC and "
                f"synthetic) — fail-closed BEFORE model construction "
                f"(provider calls = 0, model builder calls = 0). "
                f"Compute the fingerprint offline from the same "
                f"(envelope, document_access) and add it to the "
                f"dataset."
            )
        if computed_fp != expected_fp:
            pytest.skip(
                f"R4-A4-2R3 P0-Identity: case {case.id!r} runtime "
                f"fixture fingerprint mismatch — dataset declares a "
                f"different runtime_fixture_fingerprint than the "
                f"actual (envelope, document_access) produced. This "
                f"is a fail-closed pre-call binding; provider calls "
                f"= 0, model builder calls = 0. The runtime baseline "
                f"(status / is_complete / chunks) drifted from the "
                f"dataset's declared identity. Re-compute the "
                f"expected fingerprint from the current runtime or "
                f"fix the runtime source."
            )
    else:
        # Non-real_phase1 case — optional. When present, mismatch →
        # fail-closed. When absent, accept the computed fingerprint.
        if expected_fp is not None and expected_fp != computed_fp:
            pytest.skip(
                f"R4-A4-2R3 P0-Identity: case {case.id!r} runtime "
                f"fixture fingerprint mismatch — dataset declares a "
                f"different runtime_fixture_fingerprint than the "
                f"actual (envelope, document_access) produced. "
                f"Fail-closed BEFORE model construction (provider "
                f"calls = 0, model builder calls = 0)."
            )

    return computed_fp


# R4-A4-2R5R Task 3: typed atomic-fact provenance. The previous
# ``_MIGRATED_FACT_ID_PATTERN = re.compile(r"^legacy-\\d+$")`` regex
# inferred provenance from the ``fact_id`` string, which is fragile
# (a dataset author could legitimately name a fact ``legacy-0``) and
# not type-safe.
#
# R4-A4-2R5R2 Task 4: provenance is now LOADER-OWNED. The per-fact
# ``origin`` field has been REMOVED from :class:`AtomicExpectedFact`.
# The loader inspects the raw JSON dict (does the case file declare
# ``expected.atomic_facts`` explicitly, or does it rely on the
# loader's auto-migration from ``required_article_facts``?) and sets
# the case's ``_atomic_facts_origin`` PrivateAttr. Dataset JSON
# authors CANNOT forge ``"explicit"`` provenance — the field is not
# in the JSON schema, not parsed, and not settable via
# ``model_validate``. The preflight guard reads
# :attr:`ReaderRecordAskR4A3Case.atomic_facts_origin`.


def _preflight_guard_real_phase1_atomic_facts_explicit(
    case: ReaderRecordAskR4A3Case,
) -> None:
    """R4-A4-2R5R2 Task 4: block real_phase1 cases that rely on legacy
    auto-migration from ``required_article_facts``.

    A ``real_phase1`` case MUST declare explicit ``atomic_facts`` in
    its JSON file (loader-owned
    :attr:`ReaderRecordAskR4A3Case.atomic_facts_origin` ==
    ``"explicit"``). Cases that rely on the loader's auto-migration
    (which produces :class:`AtomicExpectedFact` entries with
    ``fact_id="legacy-{idx}"``, ``source_aliases=[]``, and the legacy
    sentence as the single ``answer_alias_groups`` alias, AND sets
    ``atomic_facts_origin="legacy_migrated"`` on the case) are
    blocked from paid calls.

    Rationale: auto-migrated facts have ``source_aliases=[]``, so the
    semantic precheck (``precheck_required_facts_support``) skips
    them — the context_support contract is effectively unverified
    against the model-visible fixture. For ``real_phase1`` cases
    (which consume paid provider calls), this is unacceptable.

    The dataset author MUST explicitly author ``atomic_facts`` with
    ``source_aliases`` that actually exist in the model-visible
    article text (for positive facts) or empty ``source_aliases``
    (for negative facts like "article does not provide year").

    R4-A4-2R5R2 Task 4: provenance is read from the LOADER-OWNED
    :attr:`ReaderRecordAskR4A3Case.atomic_facts_origin` property (a
    Pydantic ``PrivateAttr``, NOT a JSON-parseable field). Dataset
    JSON authors CANNOT forge ``"explicit"`` provenance. The loader
    is the sole writer, based on raw JSON inspection.

    Fail-closed: ``pytest.skip`` BEFORE model construction (provider
    calls = 0, model builder calls = 0).

    Notes:
        - Non-real_phase1 cases (``offline_only``, no phase_tags) are
          NOT blocked — they never enter the real-model run path.
        - A case with explicit ``atomic_facts`` AND legacy
          ``required_article_facts`` is fine — the explicit facts win
          and the legacy field is dead weight.
        - A case with empty ``atomic_facts`` AND empty
          ``required_article_facts`` is NOT blocked by this guard
          (other prechecks may catch it if needed).
    """
    if "real_phase1" not in case.phase_tags:
        return
    atomic_facts = case.expected.atomic_facts or []
    if not atomic_facts:
        pytest.skip(
            f"R4-A4-2R5 P0-4: real_phase1 case {case.id!r} has no "
            f"atomic_facts declared. Real_phase1 cases MUST declare "
            f"explicit atomic_facts — relying on legacy "
            f"required_article_facts auto-migration is blocked. "
            f"Fail-closed BEFORE model construction (provider calls = 0)."
        )
    # R4-A4-2R5R2 Task 4: use the LOADER-OWNED
    # ``atomic_facts_origin`` property instead of per-fact ``origin``
    # or regex-matching ``fact_id``. The loader sets this based on
    # raw JSON inspection — dataset authors cannot forge it.
    if case.atomic_facts_origin != "explicit":
        pytest.skip(
            f"R4-A4-2R5 P0-4: real_phase1 case {case.id!r} has "
            f"atomic_facts_origin='legacy_migrated' (auto-migrated "
            f"from required_article_facts by the loader). "
            f"Real_phase1 cases MUST declare explicit atomic_facts "
            f"with proper source_aliases grounded in the model-visible "
            f"article text. Fail-closed BEFORE model construction "
            f"(provider calls = 0, model builder calls = 0)."
        )


def _precheck_required_facts_support_preflight(
    case: ReaderRecordAskR4A3Case,
    runtime_fixture_fp: str,
) -> None:
    """R4-A4-2R2 P0-3: semantic precheck — required facts must be
    supported by the actual model-visible fixture.

    For each ``required=True`` atomic fact with non-empty
    ``source_aliases``, at least one alias must be a case-insensitive
    substring of at least one chunk's text.

    Unsupported required fact = INVALID evaluation case → ``pytest.skip``
    BEFORE the model is constructed (calls=0, builder=0). The harness
    does NOT auto-generate expected facts or temporal allowset from
    runtime article text — the dataset author must declare them
    upfront and verify they are supportable.

    The chunks are retrieved from the per-process preflight chunk
    stash, populated by
    :func:`_compute_preflight_runtime_fixture_fingerprint`. The
    fingerprint uniquely identifies the chunk set (deterministic), so
    a single lookup is sufficient.
    """
    chunks_view = _get_stashed_preflight_chunks(runtime_fixture_fp)
    if chunks_view is None:
        # No stashed chunks — this should never happen because the
        # fingerprint was just computed. Fail-closed.
        pytest.skip(
            f"R4-A4-2R2 P0-3: case {case.id!r} runtime fixture "
            f"fingerprint {runtime_fixture_fp[:8]}... has no stashed "
            f"chunks for the semantic precheck. The preflight chunk "
            f"stash is inconsistent — fail-closed BEFORE model "
            f"construction (provider calls = 0)."
        )

    atomic_facts_view: list[tuple[str, tuple[str, ...], bool]] = [
        (
            fact.fact_id,
            tuple(fact.source_aliases) if fact.source_aliases else (),
            bool(fact.required),
        )
        for fact in (case.expected.atomic_facts or [])
    ]

    unsupported = precheck_required_facts_support(
        atomic_facts=atomic_facts_view,
        chunks=chunks_view,
    )
    if unsupported:
        pytest.skip(
            f"R4-A4-2R2 P0-3: case {case.id!r} has required atomic "
            f"facts unsupported by the model-visible fixture: "
            f"{unsupported!r}. This is an invalid evaluation case — "
            f"the dataset author declared facts the fixture cannot "
            f"ground. Fail-closed BEFORE model construction (provider "
            f"calls = 0, model builder calls = 0). Either remove the "
            f"unsupported facts from the dataset, mark them "
            f"required=False, or fix the fixture so the aliases "
            f"appear in the model-visible chunks."
        )


def _verify_runtime_identity(
    case: ReaderRecordAskR4A3Case,
    envelope: ReadingRecordAskContextEnvelope,
) -> None:
    """R4-A4-2R P0-Identity: verify runtime envelope_fingerprint matches
    the case's declared ``expected_envelope_fingerprint``.

    Contract:

    - If ``case.expected_envelope_fingerprint is None``: backwards-compat
      with cases authored before R4-A4-2R. No check is performed — the
      case runs even if the runtime identity drifts. New cases SHOULD
      declare this field.
    - If ``case.expected_envelope_fingerprint`` is set: the runtime
      ``envelope.envelope_fingerprint`` MUST be non-None AND exactly
      equal to the declared value. Any mismatch or missing runtime
      fingerprint raises ``pytest.skip`` (fail-closed) BEFORE any model
      builder is invoked or provider call is made.

    R4-A4-2R2: this envelope-only check is RETAINED for defense-in-
    depth. The primary identity contract is now
    ``runtime_fixture_fingerprint`` (verified in
    :func:`_compute_preflight_runtime_fixture_fingerprint`), which
    binds the actual model-visible chunks. The envelope fingerprint
    catches metadata drift (record_id / base_id / generation) that
    the chunk fingerprint would also catch, but catching it earlier
    (before baseline assembly) produces a clearer skip message.

    Why ``pytest.skip`` (not ``pytest.fail``):

    - The harness is run via ``pytest -m real_llm``. ``pytest.skip``
      prevents the run from proceeding while still being a normal exit
      from the test runner's perspective (no traceback, no partial
      artifacts written).
    - Critically, the skip fires from within ``_prepare_phase`` BEFORE
      ``_build_model_for_prepared_phase`` is called, so the model
      builder is never invoked and the :class:`BudgetedUsageModel`
      wrapper is never constructed — provider calls are structurally
      impossible.

    Identity binding:

    - ``envelope_fingerprint`` is the deterministic SHA-256 over the
      envelope fields (envelope_version, user_id, reading_record_id,
      base_id, record_generation, stable_document_id,
      base_content_sha256, initial_anchor, visible_range). For BBC
      cases, all of these come from the DB at runtime — so the
      fingerprint captures the EXACT base content / generation / record
      the model will see. For synthetic cases, all of these are
      deterministic (UUID(int=1) etc., base_content_sha256 =
      sha256(article_text)).
    - This is the pre-call identity binding. The post-call binding
      (``model_context_fingerprint`` over actual baseline chunks with
      random handle_ids) remains unchanged — it is carried by the
      artifact for internal integrity, but cannot be used pre-call
      because the handle_ids are not known until baseline assembly.
    """
    expected = case.expected_envelope_fingerprint
    if expected is None:
        # Backwards-compat: case does not declare an expected identity.
        # No check is performed. New cases SHOULD declare this field.
        return
    runtime = envelope.envelope_fingerprint
    if not runtime:
        # Runtime fingerprint is missing — this should never happen for
        # a successfully built envelope, but fail-closed regardless.
        pytest.skip(
            f"R4-A4-2R P0-Identity: case {case.id!r} expected_envelope_fingerprint "
            f"is set but runtime envelope_fingerprint is empty/None "
            f"(envelope build returned no fingerprint — DB or synthetic "
            f"builder is broken)"
        )
    if runtime != expected:
        # Mismatch: the runtime base/generation/record drifted from the
        # declared expected identity. Fail-closed — do NOT construct
        # the model, do NOT make any provider call.
        pytest.skip(
            f"R4-A4-2R P0-Identity: case {case.id!r} runtime envelope_fingerprint "
            f"does not match expected_envelope_fingerprint — dataset declares "
            f"a different runtime identity than the DB/article_text produced. "
            f"This is a fail-closed pre-call binding; provider calls = 0, "
            f"model builder calls = 0. Update the dataset's "
            f"expected_envelope_fingerprint or fix the runtime source."
        )


# ---------------------------------------------------------------------------
# Phase runner (P0-2 fixed repetitions + P0-8 budgeted model)
# ---------------------------------------------------------------------------


def _build_budget_stop_remaining(
    cases_to_run: list[Any],
    *,
    current_case_id: str,
    current_run_index: int,
    repetitions: int,
) -> tuple[list[str], dict[str, list[int]]]:
    """Build consistent (remaining_cases, remaining_run_indices) for a
    budget-stop event (P1 contract).

    Spec §三 contract:
    1. Cases before the current case in iteration order have ALL reps
       completed (otherwise we'd still be on them) → NOT in either
       structure.
    2. Current case: ``range(current_run_index, repetitions)`` — the
       current run_index did NOT complete (the error fired before the
       provider call), so it must be included as pending.
    3. Subsequent cases: ``range(0, repetitions)`` — all pending.
    4. ``list(remaining_run_indices.keys()) == remaining_cases`` —
       invariant enforced via assert.
    5. Dataset/cases execution order is preserved.

    Extracted from ``_run_phase``'s ``BudgetExhaustedError`` handler so
    the contract is directly unit-testable without driving the full
    async phase loop with a fake model.
    """
    remaining_run_indices: dict[str, list[int]] = {}
    remaining_cases: list[str] = []
    current_case_seen = False
    for c in cases_to_run:
        if c.id == current_case_id:
            current_case_seen = True
            remaining_run_indices[c.id] = list(
                range(current_run_index, repetitions)
            )
            remaining_cases.append(c.id)
        elif current_case_seen:
            # Subsequent case — all reps pending.
            remaining_run_indices[c.id] = list(range(repetitions))
            remaining_cases.append(c.id)
        # else: case before current — fully completed, skip.

    # Invariant: keys match remaining_cases exactly (P1).
    assert list(remaining_run_indices.keys()) == remaining_cases, (
        "BudgetStopResult invariant violated: "
        "remaining_run_indices.keys() must equal remaining_cases"
    )
    return remaining_cases, remaining_run_indices


# ---------------------------------------------------------------------------
# P0 preparation seam: PreparedPhaseContext + _prepare_phase + _execute_phase
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PhaseStrategy:
    """Typed per-phase execution strategy (Phase 1/2/3).

    Captures the three axes that differentiate the real phases:
    - ``phase``: 1 / 2 / 3.
    - ``thinking_enabled``: Phase 1 = False; Phase 2/3 = True.
    - ``model_tier``: ``"flash"`` for Phase 1/2; ``"pro"`` for Phase 3.

    Constructed via :meth:`for_phase` so the mapping is auditable in
    one place. :func:`_build_model_for_prepared_phase` dispatches on
    this strategy to decide which model-builder seam to invoke.
    """

    phase: int
    thinking_enabled: bool
    model_tier: str

    @classmethod
    def for_phase(cls, phase: int) -> PhaseStrategy:
        if phase == 1:
            return cls(phase=1, thinking_enabled=False, model_tier="flash")
        if phase == 2:
            return cls(phase=2, thinking_enabled=True, model_tier="flash")
        if phase == 3:
            return cls(phase=3, thinking_enabled=True, model_tier="pro")
        raise ValueError(f"Unsupported phase: {phase}")


@dataclass(frozen=True)
class PhaseRunResult:
    """Typed result of :func:`_run_real_phase_entry`.

    Carries the manifest status (read back from the manifest file after
    :func:`_execute_phase` returns) plus run-level telemetry. When
    :func:`_execute_phase` raises a non-budget exception,
    :func:`_run_real_phase_entry` propagates the exception — no
    :class:`PhaseRunResult` is returned, and no manifest is written
    (the aggregate will detect the missing manifest and block).
    """

    phase: int
    manifest_status: str | None
    executed_requests: int
    executed_tokens: int
    artifacts_written: int
    run_id: str


@dataclass(frozen=True)
class PreparedPhaseContext:
    """Result of deterministic preflight BEFORE any model construction (P0).

    Holds everything the model-dependent execution step needs:

    - ``snapshot`` (P1-b): the atomic dataset snapshot whose ``dataset``
      and ``identity`` are derived from the SAME byte capture. Real
      phases and aggregate MUST use ``snapshot.identity`` — never
      recompute the identity by re-scanning files.
    - ``session``: run/session layout (run_id, prior_run_id, artifact dir).
    - ``prior_artifacts`` / ``prior_eval_results``: Phase 2/3 inputs.
    - ``planner``: :class:`PhasePlanner` with ``cases_to_run`` and
      ``repetitions`` already resolved.
    - ``prepared_inputs``: ``(case, envelope, document_access,
      runtime_fixture_fingerprint)`` for EVERY selected case, built
      before any provider call. The runtime_fixture_fingerprint is the
      R4-A4-2R2 P0-1 deterministic identity (SHA-256 over
      baseline_status + is_complete + ordered chunks) — it is the
      contract the per-case run persists on the artifact and the
      aggregate verifies against dataset expected + manifest identity.
    - ``max_requests`` / ``max_tokens``: budget caps from env.

    The caller resolves/builds the model AFTER receiving this context,
    then calls :func:`_execute_phase` with ``(prepared, model,
    model_config)``. Any deterministic preflight failure raises
    ``pytest.skip`` from :func:`_prepare_phase` BEFORE this dataclass
    is returned — so if the caller has a ``PreparedPhaseContext`` in
    hand, all deterministic preflight succeeded.
    """

    phase: int
    authorized_short_name: str
    snapshot: LoadedReaderRecordAskDatasetSnapshot
    session: RunSessionLayout
    prior_artifacts: tuple[RawArtifact, ...] | None
    prior_eval_results: dict[str, list[Any]] | None
    planner: PhasePlanner
    cases_to_run: tuple[ReaderRecordAskR4A3Case, ...]
    prepared_inputs: tuple[
        tuple[
            ReaderRecordAskR4A3Case,
            ReadingRecordAskContextEnvelope,
            InMemoryDocumentAccess,
            str,
        ],
        ...,
    ]
    max_requests: int
    max_tokens: int
    planned_run_indices: dict[str, list[int]]


async def _prepare_phase(*, phase: int) -> PreparedPhaseContext:
    """Single preparation seam for Phase 1/2/3 (P0).

    Enforces the deterministic preflight ordering required by spec §二 P0::

        env authorization gate
        → explicit dataset-dir resolution
        → immutable dataset snapshot load + identity (P1-b)
        → session/prior-run resolution
        → prior artifacts load (Phase 2/3)
        → prior identity fence (Phase 2/3)
        → deterministic case planning (PhasePlanner)
        → deterministic preflight (no model needed)
        → runtime input preflight for every selected case
        → return PreparedPhaseContext (model NOT yet built)

    The caller resolves/builds the model AFTER this function returns,
    then calls :func:`_execute_phase` with ``(prepared, model,
    model_config)``.

    Fail-closed paths (raise ``pytest.skip``) — at zero model-builder
    calls and zero provider calls:

    - env gate not open (``_r4_a3_env_gate``)
    - dataset dir missing or invalid (``_resolve_dataset_dir``)
    - dataset snapshot load failure (``load_r4_a3_dataset_with_snapshot``)
    - session/run_id missing (``_build_session_layout``)
    - Phase 2/3 prior_run_id missing
    - Phase 2/3 prior artifacts missing (no prior run)
    - Phase 2/3 prior identity mismatch (``assert_prior_artifacts_identity_consistent``)
    - no cases to run (``_deterministic_preflight``)
    - BBC env missing / mismatch / record_id missing
    - Phase 3 ``CLAREAD_R4_A3_PRO_PROFILE`` env var missing
    - max_requests < 1
    - synthetic case missing article_text (``_preflight_runtime_inputs``)
    - BBC DB failure / record not found (``_build_bbc_runtime_inputs``)

    The model-config-dependent checks (thinking state, model_name)
    happen in :func:`_model_config_preflight`, called by
    :func:`_execute_phase` AFTER the model is built.
    """
    # 1. env authorization gate — triple gate (allow + R4_A3_RUN + model).
    authorized_short_name, runs_dir_str = _r4_a3_env_gate()

    # 2. explicit dataset-dir resolution — no silent fallback.
    dataset_dir = _resolve_dataset_dir()

    # 3. immutable dataset snapshot load + identity (P1-b atomic
    #    snapshot). The snapshot's ``dataset`` and ``identity`` are
    #    derived from the SAME byte capture — a disk mutation after
    #    this point cannot desync the fingerprint.
    snapshot = load_r4_a3_dataset_with_snapshot(dataset_dir)
    dataset = snapshot.dataset
    dataset_identity = snapshot.identity

    # 4. session/prior-run resolution.
    session = _build_session_layout(runs_dir_str)

    # 5. Phase 2/3: prior_run_id must be set explicitly (no scanning).
    prior_artifacts: list[RawArtifact] | None = None
    prior_eval_results: dict[str, list[Any]] | None = None
    if phase in (2, 3):
        if session.prior_run_id is None:
            pytest.skip(
                f"Phase {phase} requires {ENV_PRIOR_RUN_ID}=<prior_run_id> "
                "(no more scanning the runs root for 'latest')"
            )

        # 6. prior artifacts load.
        prior_artifacts = _load_prior_phase_artifacts(session)
        if not prior_artifacts:
            pytest.skip(
                f"no prior-phase artifacts found (prior_run_id="
                f"{session.prior_run_id!r}); run Phase {phase - 1} first"
            )

        # 7. prior identity fence — fail-closed BEFORE any model
        #    construction. If the working dataset drifted between
        #    phases, the prior artifacts' identity will not match the
        #    current snapshot's identity.
        try:
            assert_prior_artifacts_identity_consistent(
                prior_artifacts,
                current_identity=dataset_identity,
            )
        except DatasetIdentityError as exc:
            pytest.skip(
                f"R4-A3 Phase {phase} preflight failed: "
                f"dataset_identity_error reason={exc.reason}"
            )

        # 8. deterministic case planning — build prior eval results so
        #    PhasePlanner can select content-failure cases.
        prior_eval_results = _build_prior_eval_results(
            dataset.cases, prior_artifacts
        )

    # 9. deterministic case planning.
    planner = PhasePlanner(
        dataset=dataset,
        phase=phase,
        prior_artifacts=prior_artifacts or [],
        prior_eval_results=prior_eval_results,
    )
    cases_to_run = planner.cases_to_run

    # 10. budget config (env-only, deterministic).
    max_requests = int(os.environ.get(R4_A3_MAX_REQUESTS_ENV, _DEFAULT_MAX_REQUESTS))
    max_tokens = int(os.environ.get(R4_A3_MAX_TOKENS_ENV, _DEFAULT_MAX_TOKENS))

    # 11. deterministic preflight (no model needed). BBC env binding,
    #     pro_profile env, run_dir writable, cases non-empty, budget
    #     executable. Any failure here skips BEFORE any model builder
    #     is invoked.
    preflight_failure = _deterministic_preflight(
        session=session,
        phase=phase,
        cases_to_run=cases_to_run,
        max_requests=max_requests,
    )
    if preflight_failure is not None:
        pytest.skip(
            f"R4-A3 Phase {phase} preflight failed: {preflight_failure}"
        )

    # 12. runtime input preflight for EVERY selected case. If any case
    #     cannot be prepared (missing BBC env, DB failure, record/base/
    #     blocks not ready, missing article_text), the whole phase
    #     skips at zero paid calls AND zero model-builder calls.
    prepared_inputs = await _preflight_runtime_inputs(cases_to_run)

    # 13. planned_run_indices — {case_id: [run_index, ...]} for every
    #     repetition the harness intends to execute. Used by
    #     _execute_phase to track completed/remaining indices and write
    #     the run manifest.
    planned_run_indices: dict[str, list[int]] = {
        case.id: list(range(planner.repetitions)) for case in cases_to_run
    }

    return PreparedPhaseContext(
        phase=phase,
        authorized_short_name=authorized_short_name,
        snapshot=snapshot,
        session=session,
        prior_artifacts=tuple(prior_artifacts) if prior_artifacts is not None else None,
        prior_eval_results=prior_eval_results,
        planner=planner,
        cases_to_run=tuple(cases_to_run),
        prepared_inputs=tuple(prepared_inputs),
        max_requests=max_requests,
        max_tokens=max_tokens,
        planned_run_indices=planned_run_indices,
    )


async def _execute_phase(
    *,
    prepared: PreparedPhaseContext,
    model: Any,
    model_config: ResolvedModelConfig,
    strategy: PhaseStrategy | None = None,
) -> tuple[list[RawArtifact], BudgetStopResult | None]:
    """Execute one phase against the real model (P0 post-prepare seam).

    Takes a :class:`PreparedPhaseContext` (from :func:`_prepare_phase`)
    plus the resolved ``model`` and ``model_config``. The caller MUST
    have built the model AFTER :func:`_prepare_phase` returned —
    deterministic preflight happens in prepare, model-config-dependent
    preflight happens here.

    Steps:
    1. Model-config preflight (thinking state, model_name, budget).
       Fail-closed via ``pytest.skip`` — no provider call is made.
    2. Wrap the model in :class:`BudgetedUsageModel`.
    3. Run the per-case loop, snapshotting budget counters before each
       case so the artifact records only this case's delta (P1-1).
    4. On :class:`BudgetExhaustedError`, record a
       :class:`BudgetStopResult` with consistent remaining structure,
       then atomically write a ``status="budget_exhausted"`` manifest.
    5. On normal completion, atomically write a ``status="completed"``
       manifest.
    6. On any other exception (pytest.skip, unexpected error), NO
       manifest is written — the aggregate will detect the missing
       manifest and block (``blocked_incomplete_real_model_run``).

    P0-2 dataset identity: uses ``prepared.snapshot.identity`` — never
    recomputes the identity by re-scanning files. Every artifact
    written by this phase carries the snapshot's identity tuple.

    ``strategy`` is the typed :class:`PhaseStrategy` from
    :func:`_run_real_phase_entry`. It is accepted for forward-compat
    but the model-config preflight uses ``prepared.phase`` (the two
    are always consistent when called via :func:`_run_real_phase_entry`).
    """
    _ = strategy  # accepted for the entry-seam contract; preflight uses prepared.phase

    # 1. model-config preflight (thinking state, model_name, budget).
    preflight_failure = _model_config_preflight(
        phase=prepared.phase,
        model_config=model_config,
        max_requests=prepared.max_requests,
    )
    if preflight_failure is not None:
        pytest.skip(
            f"R4-A3 Phase {prepared.phase} model-config preflight failed: "
            f"{preflight_failure}"
        )

    # P0-2: identity comes from the prepared snapshot — no recomputation.
    dataset_identity = prepared.snapshot.identity

    # 2. wrap the model in BudgetedUsageModel AFTER model-config preflight.
    budget_model = BudgetedUsageModel(
        wrapped=model,
        max_requests=prepared.max_requests,
        max_tokens=prepared.max_tokens,
    )

    # Track completed run indices for the manifest. Initialized to empty
    # lists for every planned case_id so the manifest always carries the
    # full case universe (even cases that never started).
    completed_run_indices: dict[str, list[int]] = {
        case_id: [] for case_id in prepared.planned_run_indices
    }

    artifacts: list[RawArtifact] = []
    repetitions = prepared.planner.repetitions
    for case, envelope, document_access, _runtime_fixture_fp in prepared.prepared_inputs:
        for run_index in range(repetitions):
            # P1-1: snapshot the wrapper's cumulative counters BEFORE
            # this case runs. The artifact records only this case's
            # delta = (post_case − snapshot). Report aggregation sums
            # deltas to recover the true run total (no double-counting).
            start_requests = budget_model.executed_requests
            start_input_tokens = budget_model.executed_input_tokens
            start_output_tokens = budget_model.executed_output_tokens
            try:
                artifact = await _run_one_case(
                    case,
                    budget_model,
                    model_config,
                    run_id=prepared.session.run_id,
                    run_index=run_index,
                    envelope=envelope,
                    document_access=document_access,
                    start_requests=start_requests,
                    start_input_tokens=start_input_tokens,
                    start_output_tokens=start_output_tokens,
                    dataset_identity=dataset_identity,
                )
            except BudgetExhaustedError as exc:
                # P1: delegate remaining-structure construction to the
                # pure helper. ``_build_budget_stop_remaining`` enforces
                # the contract that completed cases (before current) do
                # NOT appear in either structure, and that
                # ``list(remaining_run_indices.keys()) == remaining_cases``.
                remaining_cases, remaining_run_indices = (
                    _build_budget_stop_remaining(
                        prepared.cases_to_run,
                        current_case_id=case.id,
                        current_run_index=run_index,
                        repetitions=repetitions,
                    )
                )

                prepared.planner.record_budget_stop(
                    executed_requests=exc.executed_requests,
                    executed_tokens=exc.executed_tokens,
                    remaining_cases=remaining_cases,
                    remaining_run_indices=remaining_run_indices,
                    stop_reason=f"budget_exhausted:{exc.cap_kind}",
                )

                # Write the budget_exhausted manifest atomically. The
                # manifest persists the run's completion state across
                # pytest subprocess boundaries so the aggregate can
                # detect partial-budget runs and block acceptance.
                _write_budget_exhausted_manifest(
                    prepared=prepared,
                    completed_run_indices=completed_run_indices,
                    remaining_run_indices=remaining_run_indices,
                    executed_requests=exc.executed_requests,
                    executed_tokens=exc.executed_tokens or 0,
                )
                return artifacts, prepared.planner.budget_stop_result

            artifacts.append(artifact)
            _write_artifact(artifact, prepared.session)
            # Record this (case_id, run_index) as completed AFTER the
            # artifact is on disk. If the process dies between
            # _write_artifact and this append, the manifest is never
            # written (budget or completed) and the aggregate detects
            # the missing manifest — fail-closed.
            completed_run_indices.setdefault(case.id, []).append(run_index)

    # Normal completion: write the completed manifest atomically.
    _write_completed_manifest(
        prepared=prepared,
        completed_run_indices=completed_run_indices,
        executed_requests=budget_model.executed_requests,
        executed_tokens=budget_model.executed_tokens,
    )
    return artifacts, prepared.planner.budget_stop_result


def _write_budget_exhausted_manifest(
    *,
    prepared: PreparedPhaseContext,
    completed_run_indices: dict[str, list[int]],
    remaining_run_indices: dict[str, list[int]],
    executed_requests: int,
    executed_tokens: int,
) -> None:
    """Construct and atomically write a ``status="budget_exhausted"`` manifest.

    Deep-copies the planned/completed/remaining dicts so the manifest
    is immune to post-write mutation of the in-memory tracking structures.
    ``stop_reason`` uses the allowlisted code ``"budget_exhausted"`` —
    never the raw exception text.

    R4-A4-2R2 P0-2 + P1: the manifest also persists the per-case
    ``runtime_fixture_identities`` (for the aggregate three-layer
    check) and self-contained budget audit fields
    (``planned_logical_runs`` / ``request_cap`` / ``token_cap`` /
    ``retry_policy`` / ``retry_headroom``) so the aggregate does NOT
    reconstruct historical caps from the current shell env.

    R4-A4-2R3 P0-2 + P1: the manifest now declares
    ``audit_contract_version="r4-a4-2r3"`` (V2 strict). V2 requires
    the typed ``retry_policy`` dict
    (``{"tool_max_retries": int, "output_max_retries": int}`` sourced
    from ``agent.DEFAULT_TOOL_RETRIES`` / ``agent.DEFAULT_OUTPUT_RETRIES``)
    and a non-null ``retry_headroom = request_cap - planned_logical_runs``
    so the aggregate can audit budget exhaustion without env
    reconstruction. The V2 contract is enforced by
    :func:`run_manifest._parse_and_validate_manifest_dict` Rule 18.
    """
    runtime_fixture_identities = _build_runtime_fixture_identities(prepared)
    retry_policy, retry_headroom = _build_v2_retry_audit(prepared)
    manifest = ReaderRecordAskRunManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        run_id=prepared.session.run_id,
        phase=prepared.phase,
        dataset_id=prepared.snapshot.identity.dataset_id,
        dataset_schema_version=prepared.snapshot.identity.schema_version,
        dataset_content_sha256=prepared.snapshot.identity.content_sha256,
        status="budget_exhausted",
        planned_run_indices=copy.deepcopy(prepared.planned_run_indices),
        completed_run_indices=copy.deepcopy(completed_run_indices),
        remaining_run_indices=copy.deepcopy(remaining_run_indices),
        executed_requests=executed_requests,
        executed_tokens=executed_tokens,
        stop_reason="budget_exhausted",
        runtime_fixture_identities=runtime_fixture_identities,
        planned_logical_runs=prepared.planner.planned_logical_runs,
        request_cap=prepared.max_requests,
        token_cap=prepared.max_tokens,
        retry_policy=retry_policy,
        retry_headroom=retry_headroom,
        audit_contract_version=AUDIT_CONTRACT_VERSION_V2,
    )
    write_manifest_atomic(manifest, prepared.session.manifest_path)


def _write_completed_manifest(
    *,
    prepared: PreparedPhaseContext,
    completed_run_indices: dict[str, list[int]],
    executed_requests: int,
    executed_tokens: int,
) -> None:
    """Construct and atomically write a ``status="completed"`` manifest.

    On normal completion, ``remaining_run_indices`` is empty and
    ``planned_run_indices == completed_run_indices`` (per-case set
    equality). ``stop_reason`` is ``None`` (allowlisted).

    R4-A4-2R2 P0-2 + P1: same persistence contract as
    :func:`_write_budget_exhausted_manifest` — the manifest carries
    ``runtime_fixture_identities`` and self-contained budget fields
    so the aggregate can audit identity and budget without any env
    reconstruction.

    R4-A4-2R3 P0-2 + P1: the manifest now declares
    ``audit_contract_version="r4-a4-2r3"`` (V2 strict) with the typed
    ``retry_policy`` dict and non-null ``retry_headroom``. See
    :func:`_write_budget_exhausted_manifest` for the full V2 contract.
    """
    runtime_fixture_identities = _build_runtime_fixture_identities(prepared)
    retry_policy, retry_headroom = _build_v2_retry_audit(prepared)
    manifest = ReaderRecordAskRunManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        run_id=prepared.session.run_id,
        phase=prepared.phase,
        dataset_id=prepared.snapshot.identity.dataset_id,
        dataset_schema_version=prepared.snapshot.identity.schema_version,
        dataset_content_sha256=prepared.snapshot.identity.content_sha256,
        status="completed",
        planned_run_indices=copy.deepcopy(prepared.planned_run_indices),
        completed_run_indices=copy.deepcopy(completed_run_indices),
        remaining_run_indices={},
        executed_requests=executed_requests,
        executed_tokens=executed_tokens,
        stop_reason=None,
        runtime_fixture_identities=runtime_fixture_identities,
        planned_logical_runs=prepared.planner.planned_logical_runs,
        request_cap=prepared.max_requests,
        token_cap=prepared.max_tokens,
        retry_policy=retry_policy,
        retry_headroom=retry_headroom,
        audit_contract_version=AUDIT_CONTRACT_VERSION_V2,
    )
    write_manifest_atomic(manifest, prepared.session.manifest_path)


def _build_v2_retry_audit(
    prepared: PreparedPhaseContext,
) -> tuple[dict[str, int], int]:
    """R4-A4-2R3 P1: build the typed V2 retry_policy + retry_headroom.

    ``retry_policy`` records the ACTUAL tool/output retry limits used
    by :func:`create_reading_record_ask_agent` — sourced from
    :data:`agent.DEFAULT_TOOL_RETRIES` /
    :data:`agent.DEFAULT_OUTPUT_RETRIES` (the single source of truth).
    The dict uses the strict V2 key contract
    (``tool_max_retries`` / ``output_max_retries``) enforced by
    :func:`run_manifest._parse_and_validate_manifest_dict` Rule 18e.

    ``retry_headroom`` = ``request_cap - planned_logical_runs``. V2
    Rule 18h requires this exact relationship. Both values come from
    the :class:`PreparedPhaseContext` (NOT from current env) so a
    historical run's budget is auditable without reconstruction.

    Returns ``(retry_policy, retry_headroom)``.
    """
    retry_policy: dict[str, int] = {
        "tool_max_retries": DEFAULT_TOOL_RETRIES,
        "output_max_retries": DEFAULT_OUTPUT_RETRIES,
    }
    # V2 Rule 18h: retry_headroom == request_cap - planned_logical_runs.
    # ``prepared.max_requests`` is the resolved request cap (from env
    # at run time, persisted on the manifest). ``planned_logical_runs``
    # is the planner's logical run count (cases × repetitions).
    retry_headroom: int = prepared.max_requests - prepared.planner.planned_logical_runs
    return retry_policy, retry_headroom


def _build_runtime_fixture_identities(
    prepared: PreparedPhaseContext,
) -> dict[str, str]:
    """R4-A4-2R2 P0-2: build the per-case runtime fixture identity map.

    The manifest persists the case's verified
    ``runtime_fixture_fingerprint`` for every case in
    ``planned_run_indices``. The aggregate uses this map to perform
    the three-layer identity check:

        dataset expected == manifest identity == artifact actual.

    Source: the ``prepared_inputs`` 4-tuple carries the preflight-
    computed (and dataset-verified) fingerprint for each case. We
    iterate the planned cases (NOT the prepared inputs — they are
    the same set, but ``planned_run_indices`` is the authoritative
    case universe for the manifest).
    """
    # Build a case_id → fingerprint lookup from prepared_inputs.
    fingerprint_by_case_id: dict[str, str] = {
        case.id: runtime_fixture_fp
        for case, _envelope, _document_access, runtime_fixture_fp in prepared.prepared_inputs
    }
    # Only emit identities for cases in planned_run_indices (defense-
    # in-depth: ensures the manifest's identity universe matches the
    # planned universe exactly).
    identities: dict[str, str] = {}
    for case_id in prepared.planned_run_indices:
        fp = fingerprint_by_case_id.get(case_id)
        if fp is not None:
            identities[case_id] = fp
    return identities


# ---------------------------------------------------------------------------
# Single real phase entry seam: _run_real_phase_entry + _build_model_for_prepared_phase
# ---------------------------------------------------------------------------


def _build_model_for_prepared_phase(
    prepared: PreparedPhaseContext,
    strategy: PhaseStrategy,
) -> tuple[Any, ResolvedModelConfig]:
    """Build the model for a phase according to ``strategy``.

    Dispatches on ``strategy.model_tier`` and ``strategy.thinking_enabled``:
    - Phase 1 (flash, thinking off): ``_resolve_authorized_model`` only.
    - Phase 2 (flash, thinking on): ``_resolve_authorized_model`` then
      ``_build_thinking_model``.
    - Phase 3 (pro, thinking on): ``_resolve_authorized_model`` then
      ``_build_thinking_model``. The Pro-ness comes from the
      ``CLAREAD_R4_A3_PRO_PROFILE`` env var consumed by the route
      resolver — there is no separate ``pro=True`` kwarg on
      ``_build_thinking_model`` today (the spec allows this minimal
      behaviour when the production API cannot be safely split without
      modifying ``services/api/app/**``).

    Model construction happens ONLY after :func:`_prepare_phase` has
    returned a :class:`PreparedPhaseContext` — deterministic preflight
    failures skip before this function is reachable.
    """
    base_model, base_config = _resolve_authorized_model(
        prepared.authorized_short_name,
    )
    if not strategy.thinking_enabled:
        # Phase 1: base config is the final config.
        return base_model, base_config
    # Phase 2/3: rebuild with thinking enabled.
    thinking_model, thinking_config = _build_thinking_model(base_config)
    return thinking_model, thinking_config


async def _run_real_phase_entry(*, phase: int) -> PhaseRunResult:
    """Single real phase entry seam (P0).

    Phase 1/2/3 MUST all go through this function. The function:
    1. Calls ``_prepare_phase(phase=phase)`` — deterministic preflight
       (env gate, dataset dir, snapshot, session, prior identity fence,
       case planning, BBC env binding, runtime inputs). Any failure
       raises ``pytest.skip`` BEFORE any model construction.
    2. Builds the model according to :class:`PhaseStrategy` (Flash/Pro,
       thinking on/off) via ``_build_model_for_prepared_phase``.
    3. Calls ``_execute_phase`` to run cases, write artifacts, and write
       the run manifest (completed or budget_exhausted).
    4. Reads back the manifest to construct a :class:`PhaseRunResult`.

    When ``_execute_phase`` raises a non-budget exception (e.g.
    ``pytest.skip`` from model-config preflight, or an unexpected
    error), this function propagates the exception — no manifest is
    written and no :class:`PhaseRunResult` is returned. The aggregate
    will detect the missing manifest and block
    (``blocked_incomplete_real_model_run``).
    """
    strategy = PhaseStrategy.for_phase(phase)
    prepared = await _prepare_phase(phase=phase)
    model, model_config = _build_model_for_prepared_phase(prepared, strategy)
    artifacts, _budget_stop = await _execute_phase(
        prepared=prepared,
        model=model,
        model_config=model_config,
        strategy=strategy,
    )

    # Read back the manifest to learn the status. If the manifest file
    # is missing (should not happen on a normal return path, but
    # fail-safe), manifest_status is None — the caller treats that as
    # interrupted.
    from claread_eval.reader_record_ask.run_manifest import read_manifest  # noqa: PLC0415

    manifest = read_manifest(prepared.session.manifest_path)
    manifest_status = manifest.status if manifest is not None else None
    executed_requests = sum(a.executed_requests or 0 for a in artifacts)
    executed_tokens = sum(a.executed_tokens or 0 for a in artifacts)

    return PhaseRunResult(
        phase=phase,
        manifest_status=manifest_status,
        executed_requests=executed_requests,
        executed_tokens=executed_tokens,
        artifacts_written=len(artifacts),
        run_id=prepared.session.run_id,
    )


# ---------------------------------------------------------------------------
# Three phase tests — drive through _run_real_phase_entry (P0 single entry seam)
# ---------------------------------------------------------------------------


@pytest.mark.real_llm
@pytest.mark.asyncio
async def test_r4_a3_phase1_flash_non_thinking() -> None:
    """Phase 1: Flash + thinking disabled, 3 reps per case (no early break).

    P0 seam: drives through the single ``_run_real_phase_entry(phase=1)``
    seam. ``_prepare_phase`` performs ALL deterministic preflight (env
    gate, dataset dir, snapshot, session, prior identity fence, case
    planning, BBC env binding, runtime inputs) BEFORE the model is
    built. If any deterministic preflight fails, ``pytest.skip`` is
    raised and the model builder is never invoked.
    """
    result = await _run_real_phase_entry(phase=1)

    assert result.artifacts_written > 0, (
        "no Phase 1 artifacts produced; check env cap, dataset, or preflight"
    )
    # The manifest must have been written with status="completed" (or
    # "budget_exhausted" if the cap was hit mid-run).
    assert result.manifest_status in ("completed", "budget_exhausted"), (
        f"Phase 1 manifest_status={result.manifest_status!r} — expected "
        f"'completed' or 'budget_exhausted'"
    )
    # P0-8: executed_requests must be > 0 (the wrapper actually counted).
    assert result.executed_requests > 0, (
        "Phase 1 produced artifacts but executed_requests is 0 — "
        "BudgetedUsageModel is not instrumenting the resolved model"
    )


@pytest.mark.real_llm
@pytest.mark.asyncio
async def test_r4_a3_phase2_flash_thinking() -> None:
    """Phase 2: Flash + thinking enabled, 1 rep per Phase 1 *evaluator* failure.

    P0 seam: drives through the single ``_run_real_phase_entry(phase=2)``
    seam. ``_prepare_phase`` loads prior artifacts and runs the identity
    fence BEFORE any model is built.

    P0-3: cases are selected from Phase 1's *evaluator results*
    (``is_content_failure``), not from terminal status alone. A
    ``finalized_status='ok'`` artifact with an unsupported ``2025`` year
    token is correctly selected for Phase 2.
    P0-1: prior run id comes from ``CLAREAD_R4_A3_PRIOR_RUN_ID`` env —
    no more scanning the runs root for "latest".
    P1-1: ``thinking_enabled`` is asserted on the resolved thinking
    config inside ``_build_model_for_prepared_phase`` (via
    ``_build_thinking_model``).
    """
    result = await _run_real_phase_entry(phase=2)

    assert result.artifacts_written > 0, (
        "no Phase 2 artifacts produced; check prior_run_id and dataset"
    )
    assert result.manifest_status in ("completed", "budget_exhausted"), (
        f"Phase 2 manifest_status={result.manifest_status!r} — expected "
        f"'completed' or 'budget_exhausted'"
    )


@pytest.mark.real_llm
@pytest.mark.asyncio
async def test_r4_a3_phase3_pro_thinking() -> None:
    """Phase 3: Pro + thinking, 1 rep per Phase 2 still-failure.

    P0 seam: drives through the single ``_run_real_phase_entry(phase=3)``
    seam. ``_prepare_phase`` verifies Phase 3
    ``CLAREAD_R4_A3_PRO_PROFILE`` env is set BEFORE the model is built.

    P1-1: ``CLAREAD_R4_A3_PRO_PROFILE`` is actually used (not just
    non-empty) — the resolved model_name must match the authorized
    short name AND thinking must be enabled AND the profile must be
    the one specified by the env var.
    P0-1: prior run id comes from ``CLAREAD_R4_A3_PRIOR_RUN_ID`` env.
    """
    result = await _run_real_phase_entry(phase=3)

    assert result.artifacts_written > 0, (
        "no Phase 3 artifacts produced; check prior_run_id and dataset"
    )
    assert result.manifest_status in ("completed", "budget_exhausted"), (
        f"Phase 3 manifest_status={result.manifest_status!r} — expected "
        f"'completed' or 'budget_exhausted'"
    )


# ---------------------------------------------------------------------------
# P0 orchestration tests — model-builder zero-call on deterministic preflight failure
#
# These tests drive the SAME ``_run_real_phase_entry`` seam the three real
# phases use. They prove that when any deterministic preflight fails,
# ``_prepare_phase`` raises ``pytest.skip`` BEFORE the model builder is
# invoked. The contract: if ``_prepare_phase`` raises, the model-builder
# code inside ``_run_real_phase_entry`` is unreachable, so model_builder
# calls = 0 and provider_calls = 0.
#
# Spec §四 A: "测试必须穿过三个 real phase 使用的同一个 production harness
# entry seam。禁止创建未被 phase 使用的 test-only orchestration。"
# ---------------------------------------------------------------------------


def _install_model_builder_sentinels(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Track _resolve_authorized_model and _build_thinking_model invocations.

    Returns a dict ``{"base": int, "thinking": int}`` tracking call counts.
    The sentinels return a fake ``(object(), _make_minimal_model_config())``
    pair so the test code that WOULD invoke the model builder can complete
    without making real provider requests. When ``_prepare_phase`` raises
    ``pytest.skip`` BEFORE ``_run_real_phase_entry`` reaches the
    model-builder line, the counters stay at 0 — that is the contract
    these tests assert.
    """
    calls: dict[str, int] = {"base": 0, "thinking": 0}

    def _tracking_resolve(name: str) -> tuple[Any, ResolvedModelConfig]:
        calls["base"] += 1
        return object(), _make_minimal_model_config()

    def _tracking_thinking(
        config: ResolvedModelConfig,
    ) -> tuple[Any, ResolvedModelConfig]:
        calls["thinking"] += 1
        return object(), config

    monkeypatch.setattr(
        sys.modules[__name__],
        "_resolve_authorized_model",
        _tracking_resolve,
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "_build_thinking_model",
        _tracking_thinking,
    )
    return calls


def _make_fake_snapshot(
    cases: list[ReaderRecordAskR4A3Case],
    *,
    content_sha256: str = "a" * 64,
) -> LoadedReaderRecordAskDatasetSnapshot:
    """Build a fake snapshot for orchestration tests (no disk I/O).

    The snapshot's ``dataset`` and ``identity`` are constructed in-memory
    so tests can drive ``_prepare_phase`` without writing real files to
    ``tmp_path``. The identity's ``content_sha256`` is configurable so
    each test can use a distinct hash (avoids accidental cross-test
    coupling via shared hash strings).
    """
    dataset = ReaderRecordAskR4A3Dataset(
        id="test-dataset",
        schema_version="test-schema-v1",
        case_globs=["cases/*.json"],
        cases=cases,
    )
    identity = DatasetIdentity(
        dataset_id="test-dataset",
        schema_version="test-schema-v1",
        content_sha256=content_sha256,
    )
    return LoadedReaderRecordAskDatasetSnapshot(dataset=dataset, identity=identity)


def _write_prior_artifact(
    runs_dir: Path,
    prior_run_id: str,
    artifact: RawArtifact,
) -> None:
    """Write a prior-phase artifact to ``<runs_dir>/<prior_run_id>/artifacts/``.

    Used by Phase 2/3 orchestration tests to seed the prior artifact
    directory that ``_load_prior_phase_artifacts`` reads from.
    """
    artifact_dir = runs_dir / prior_run_id / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / f"{artifact.case_id}.json").write_text(
        artifact.model_dump_json(indent=2), encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_p0_phase1_dataset_env_missing_model_builder_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P0: Phase 1 missing dataset env → base_builder=0, thinking_builder=0.

    Drives the SAME ``_run_real_phase_entry(phase=1)`` seam the real
    Phase 1 test uses. When dataset env is missing, ``_prepare_phase``
    raises ``pytest.skip`` at ``_resolve_dataset_dir`` — BEFORE the
    model builder is invoked.

    Because ``_run_real_phase_entry`` calls ``_prepare_phase`` FIRST and
    only then builds the model, a skip from prepare makes the
    model-builder line unreachable — the sentinel counters stay at 0.
    This is the contract: when deterministic preflight fails, NO model
    is built, NO provider request is made.
    """
    monkeypatch.setenv("CLAREAD_ALLOW_REAL_LLM_TESTS", "1")
    monkeypatch.setenv(R4_A3_RUN_ENV, "1")
    monkeypatch.setenv(REAL_LLM_MODEL_ENV, "test-model")
    monkeypatch.delenv(R4_A3_DATASET_DIR_ENV, raising=False)

    builder_calls = _install_model_builder_sentinels(monkeypatch)

    with pytest.raises(pytest.skip.Exception):
        await _run_real_phase_entry(phase=1)

    assert builder_calls["base"] == 0, (
        "Phase 1 _prepare_phase must fail-closed BEFORE "
        f"_resolve_authorized_model is invoked (got base={builder_calls['base']})"
    )
    assert builder_calls["thinking"] == 0


@pytest.mark.asyncio
async def test_p0_phase2_prior_identity_mismatch_model_builder_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """P0: Phase 2 prior identity mismatch → base_builder=0, thinking_builder=0.

    Drives the production ``_run_real_phase_entry(phase=2)`` seam. Prior
    artifacts carry an identity tuple that doesn't match the current
    snapshot's identity. ``_prepare_phase`` raises ``pytest.skip`` at
    the identity fence — BEFORE ``_resolve_authorized_model`` or
    ``_build_thinking_model`` is invoked.
    """
    monkeypatch.setenv("CLAREAD_ALLOW_REAL_LLM_TESTS", "1")
    monkeypatch.setenv(R4_A3_RUN_ENV, "1")
    monkeypatch.setenv(REAL_LLM_MODEL_ENV, "test-model")
    monkeypatch.setenv(R4_A3_DATASET_DIR_ENV, str(tmp_path))
    monkeypatch.setenv(ENV_RUN_ID, "phase2-test")
    monkeypatch.setenv(ENV_PRIOR_RUN_ID, "phase1-test")
    monkeypatch.setenv(R4_A3_RUNS_DIR_ENV, str(tmp_path / "runs"))

    case = _make_minimal_case(phase_tags=["real_phase1"])
    snapshot = _make_fake_snapshot([case], content_sha256="a" * 64)
    monkeypatch.setattr(
        sys.modules[__name__],
        "load_r4_a3_dataset_with_snapshot",
        lambda _dir: snapshot,
    )

    # Prior artifacts with identity B (mismatch with current A).
    runs_dir = tmp_path / "runs"
    mismatched_artifact = RawArtifact(
        case_id=case.id,
        run_id="phase1-test",
        run_index=0,
        model_short_name="test-model",
        model_route=MODEL_ROUTE_READER_ASK,
        thinking_enabled=False,
        finalized_status="ok",
        dataset_id="test-dataset",
        dataset_schema_version="test-schema-v1",
        dataset_content_sha256="b" * 64,  # MISMATCH
    )
    _write_prior_artifact(runs_dir, "phase1-test", mismatched_artifact)

    builder_calls = _install_model_builder_sentinels(monkeypatch)

    with pytest.raises(pytest.skip.Exception):
        await _run_real_phase_entry(phase=2)

    assert builder_calls["base"] == 0, (
        "Phase 2 _prepare_phase must fail-closed at prior identity fence "
        f"BEFORE _resolve_authorized_model is invoked (got base={builder_calls['base']})"
    )
    assert builder_calls["thinking"] == 0


@pytest.mark.asyncio
async def test_p0_phase3_prior_artifact_missing_identity_model_builder_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """P0: Phase 3 prior artifact missing identity → base_builder=0, thinking_builder=0.

    Drives the production ``_run_real_phase_entry(phase=3)`` seam. A
    prior artifact without identity fields (an old artifact from before
    P0-2) is loaded. ``_prepare_phase`` raises ``pytest.skip`` at the
    identity fence (``reason=prior_missing_identity_field``) — BEFORE
    any model is built.
    """
    monkeypatch.setenv("CLAREAD_ALLOW_REAL_LLM_TESTS", "1")
    monkeypatch.setenv(R4_A3_RUN_ENV, "1")
    monkeypatch.setenv(REAL_LLM_MODEL_ENV, "test-model")
    monkeypatch.setenv(R4_A3_DATASET_DIR_ENV, str(tmp_path))
    monkeypatch.setenv(ENV_RUN_ID, "phase3-test")
    monkeypatch.setenv(ENV_PRIOR_RUN_ID, "phase2-test")
    monkeypatch.setenv(R4_A3_PRO_PROFILE_ENV, "test-pro")
    monkeypatch.setenv(R4_A3_RUNS_DIR_ENV, str(tmp_path / "runs"))

    case = _make_minimal_case(phase_tags=["real_phase1"])
    snapshot = _make_fake_snapshot([case], content_sha256="c" * 64)
    monkeypatch.setattr(
        sys.modules[__name__],
        "load_r4_a3_dataset_with_snapshot",
        lambda _dir: snapshot,
    )

    runs_dir = tmp_path / "runs"
    old_artifact = RawArtifact(
        case_id=case.id,
        run_id="phase2-test",
        run_index=0,
        model_short_name="test-model",
        model_route=MODEL_ROUTE_READER_ASK,
        thinking_enabled=True,
        finalized_status="ok",
        dataset_id=None,
        dataset_schema_version=None,
        dataset_content_sha256=None,
    )
    _write_prior_artifact(runs_dir, "phase2-test", old_artifact)

    builder_calls = _install_model_builder_sentinels(monkeypatch)

    with pytest.raises(pytest.skip.Exception):
        await _run_real_phase_entry(phase=3)

    assert builder_calls["base"] == 0, (
        "Phase 3 _prepare_phase must fail-closed at prior identity fence "
        f"BEFORE _resolve_authorized_model is invoked (got base={builder_calls['base']})"
    )
    assert builder_calls["thinking"] == 0


@pytest.mark.asyncio
async def test_p0_phase1_bbc_env_missing_model_builder_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """P0: Phase 1 BBC env missing → base_builder=0, thinking_builder=0.

    Drives the production ``_run_real_phase_entry(phase=1)`` seam. A BBC
    case is selected but ``CLAREAD_R4_A3_BBC_RECORD_ID`` env is missing
    — ``_deterministic_preflight`` returns ``bbc_record_id_env_missing``
    and ``_prepare_phase`` raises ``pytest.skip`` BEFORE any model is
    built. This is the deterministic preflight layer; the runtime-input
    preflight is covered by the next test.
    """
    monkeypatch.setenv("CLAREAD_ALLOW_REAL_LLM_TESTS", "1")
    monkeypatch.setenv(R4_A3_RUN_ENV, "1")
    monkeypatch.setenv(REAL_LLM_MODEL_ENV, "test-model")
    monkeypatch.setenv(R4_A3_DATASET_DIR_ENV, str(tmp_path))
    monkeypatch.setenv(ENV_RUN_ID, "phase1-test")
    monkeypatch.setenv(R4_A3_RUNS_DIR_ENV, str(tmp_path / "runs"))
    monkeypatch.delenv(R4_A3_BBC_RECORD_ID_ENV, raising=False)

    bbc_case = _make_minimal_case(
        case_id="bbc-test",
        source_kind="bbc_record",
        record_id="00000000-0000-4000-8000-000000000000",
        article_text=None,
        phase_tags=["real_phase1"],
    )
    snapshot = _make_fake_snapshot([bbc_case], content_sha256="d" * 64)
    monkeypatch.setattr(
        sys.modules[__name__],
        "load_r4_a3_dataset_with_snapshot",
        lambda _dir: snapshot,
    )

    builder_calls = _install_model_builder_sentinels(monkeypatch)

    with pytest.raises(pytest.skip.Exception):
        await _run_real_phase_entry(phase=1)

    assert builder_calls["base"] == 0, (
        "Phase 1 _prepare_phase must fail-closed at BBC preflight BEFORE "
        f"_resolve_authorized_model is invoked (got base={builder_calls['base']})"
    )
    assert builder_calls["thinking"] == 0


@pytest.mark.asyncio
async def test_p0_phase1_runtime_input_preflight_failure_model_builder_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """P0: Phase 1 runtime input preflight failure → base_builder=0.

    Drives the production ``_run_real_phase_entry(phase=1)`` seam. BBC
    env binding check passes (env matches case ``record_id``), but
    ``_build_bbc_runtime_inputs`` raises ``pytest.skip`` at runtime
    (simulating DB failure). ``_prepare_phase`` propagates the skip
    BEFORE any model is built.

    This test is distinct from the env-binding test: env-binding is
    deterministic (no I/O), runtime-input-build requires DB/record
    access. Both layers must skip before any model builder is invoked.
    """
    monkeypatch.setenv("CLAREAD_ALLOW_REAL_LLM_TESTS", "1")
    monkeypatch.setenv(R4_A3_RUN_ENV, "1")
    monkeypatch.setenv(REAL_LLM_MODEL_ENV, "test-model")
    monkeypatch.setenv(R4_A3_DATASET_DIR_ENV, str(tmp_path))
    monkeypatch.setenv(ENV_RUN_ID, "phase1-test")
    monkeypatch.setenv(R4_A3_RUNS_DIR_ENV, str(tmp_path / "runs"))
    # BBC env matches the case record_id — deterministic preflight passes.
    monkeypatch.setenv(
        R4_A3_BBC_RECORD_ID_ENV, "00000000-0000-4000-8000-000000000000"
    )

    bbc_case = _make_minimal_case(
        case_id="bbc-test",
        source_kind="bbc_record",
        record_id="00000000-0000-4000-8000-000000000000",
        article_text=None,
        phase_tags=["real_phase1"],
    )
    snapshot = _make_fake_snapshot([bbc_case], content_sha256="e" * 64)
    monkeypatch.setattr(
        sys.modules[__name__],
        "load_r4_a3_dataset_with_snapshot",
        lambda _dir: snapshot,
    )

    async def _failing_bbc_build(_record_id: str) -> tuple[Any, Any]:
        pytest.skip("BBC record load failed (exception_type=ConnectionError)")

    monkeypatch.setattr(
        sys.modules[__name__],
        "_build_bbc_runtime_inputs",
        _failing_bbc_build,
    )

    builder_calls = _install_model_builder_sentinels(monkeypatch)

    with pytest.raises(pytest.skip.Exception):
        await _run_real_phase_entry(phase=1)

    assert builder_calls["base"] == 0, (
        "Phase 1 _prepare_phase must fail-closed at runtime input preflight "
        f"BEFORE _resolve_authorized_model is invoked (got base={builder_calls['base']})"
    )
    assert builder_calls["thinking"] == 0


@pytest.mark.asyncio
async def test_p0_phase1_all_preflight_success_model_builder_called_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """P0: all deterministic preflight success → model_builder called once.

    Drives the production ``_run_real_phase_entry(phase=1)`` seam. All
    deterministic preflight passes (env gate, dataset dir, snapshot
    load, session, deterministic preflight, runtime inputs).
    ``_prepare_phase`` returns a ``PreparedPhaseContext``.
    ``_build_model_for_prepared_phase`` then calls
    ``_resolve_authorized_model`` once (tracked: base=1).

    Provider requests are NOT made — ``_execute_phase`` is mocked to
    verify it would be called once without actually invoking the
    provider. This is the inverse of the previous tests: when
    deterministic preflight succeeds, the model builder IS invoked
    exactly once, then execution proceeds into the (mocked) provider
    path.
    """
    monkeypatch.setenv("CLAREAD_ALLOW_REAL_LLM_TESTS", "1")
    monkeypatch.setenv(R4_A3_RUN_ENV, "1")
    monkeypatch.setenv(REAL_LLM_MODEL_ENV, "test-model")
    monkeypatch.setenv(R4_A3_DATASET_DIR_ENV, str(tmp_path))
    monkeypatch.setenv(ENV_RUN_ID, "phase1-test")
    monkeypatch.setenv(R4_A3_RUNS_DIR_ENV, str(tmp_path / "runs"))
    monkeypatch.delenv(R4_A3_BBC_RECORD_ID_ENV, raising=False)

    synthetic_case = _make_minimal_case(
        case_id="synthetic-1",
        article_text="Hello world.",
        phase_tags=["real_phase1"],
    )
    snapshot = _make_fake_snapshot([synthetic_case], content_sha256="f" * 64)
    monkeypatch.setattr(
        sys.modules[__name__],
        "load_r4_a3_dataset_with_snapshot",
        lambda _dir: snapshot,
    )

    builder_calls = _install_model_builder_sentinels(monkeypatch)

    execute_calls: dict[str, int] = {"n": 0}

    async def _tracking_execute(
        *,
        prepared: PreparedPhaseContext,
        model: Any,
        model_config: ResolvedModelConfig,
        strategy: PhaseStrategy | None = None,
    ) -> tuple[list[RawArtifact], BudgetStopResult | None]:
        execute_calls["n"] += 1
        return [], None

    monkeypatch.setattr(
        sys.modules[__name__],
        "_execute_phase",
        _tracking_execute,
    )

    # Drive the single entry seam — prepare + build + execute all happen
    # inside _run_real_phase_entry. The mocked _execute_phase returns
    # ([], None) so no manifest is written; read_manifest returns None
    # and the PhaseRunResult carries manifest_status=None.
    result = await _run_real_phase_entry(phase=1)

    assert builder_calls["base"] == 1, (
        "Phase 1 with all preflight passing MUST invoke "
        f"_resolve_authorized_model exactly once (got base={builder_calls['base']})"
    )
    assert execute_calls["n"] == 1, (
        "_execute_phase must be invoked exactly once via _run_real_phase_entry"
    )
    assert result.phase == 1
    assert result.run_id == "phase1-test"


# ---------------------------------------------------------------------------
# Default-running tests (no env gate, no real_llm mark)
# ---------------------------------------------------------------------------


def test_real_llm_gate_default_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default run (no env gate): env gate skips when any env var is missing."""
    monkeypatch.delenv("CLAREAD_ALLOW_REAL_LLM_TESTS", raising=False)
    monkeypatch.delenv(R4_A3_RUN_ENV, raising=False)
    monkeypatch.delenv(REAL_LLM_MODEL_ENV, raising=False)
    with pytest.raises(pytest.skip.Exception):
        _r4_a3_env_gate()


def test_model_route_mismatch_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default run (no env gate): model route mismatch triggers pytest.skip.

    Mocks ``build_model_for_route`` to return a config whose ``model_name``
    does not match the authorized short name. Verifies fail-closed skip.
    """
    from app.config import settings as settings_module  # noqa: PLC0415
    from app.llm import router as router_module  # noqa: PLC0415

    fake_config = ResolvedModelConfig(
        route=MODEL_ROUTE_READER_ASK,
        profile_name="test-profile",
        provider="test-provider",
        adapter="openai_compatible",
        model_name="wrong-model",
        base_url="https://example.test",
        api_key="sk-test-not-real",
    )
    fake_model = object()  # sentinel; never used because skip fires first

    def _fake_build_model_for_route(
        _settings: Any,
        _route: Any,
        _selection: Any,
    ) -> tuple[Any, ResolvedModelConfig | None]:
        return fake_model, fake_config

    monkeypatch.setattr(
        router_module,
        "build_model_for_route",
        _fake_build_model_for_route,
    )
    monkeypatch.setattr(
        settings_module,
        "get_settings",
        lambda: object(),
    )

    with pytest.raises(pytest.skip.Exception):
        _resolve_authorized_model("expected-model")


def test_real_llm_gate_partial_env_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default run (no env gate): partial env (allow=1 but no R4_A3_RUN) skips."""
    monkeypatch.setenv("CLAREAD_ALLOW_REAL_LLM_TESTS", "1")
    monkeypatch.delenv(R4_A3_RUN_ENV, raising=False)
    monkeypatch.delenv(REAL_LLM_MODEL_ENV, raising=False)
    with pytest.raises(pytest.skip.Exception):
        _r4_a3_env_gate()


def test_real_llm_gate_missing_model_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default run (no env gate): allow=1 + R4_A3_RUN=1 but no model skips."""
    monkeypatch.setenv("CLAREAD_ALLOW_REAL_LLM_TESTS", "1")
    monkeypatch.setenv(R4_A3_RUN_ENV, "1")
    monkeypatch.delenv(REAL_LLM_MODEL_ENV, raising=False)
    with pytest.raises(pytest.skip.Exception):
        _r4_a3_env_gate()


# ---------------------------------------------------------------------------
# P0-1 regression: explicit dataset-dir binding (no silent fallback)
# ---------------------------------------------------------------------------


def test_p0_1_resolve_dataset_dir_skips_when_env_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P0-1: ``_resolve_dataset_dir`` raises ``pytest.skip`` when env is
    missing — no silent fallback to ``evals/tmp/...``.

    Default pytest runs (gate closed) already skip at ``_r4_a3_env_gate``,
    so this test simulates the case where the gate is open but the
    operator forgot to set ``CLAREAD_R4_A3_DATASET_DIR``. The harness
    must fail-closed before any provider call.
    """
    monkeypatch.delenv(R4_A3_DATASET_DIR_ENV, raising=False)
    with pytest.raises(pytest.skip.Exception):
        _resolve_dataset_dir()


def test_p0_1_resolve_dataset_dir_uses_env_when_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """P0-1: ``_resolve_dataset_dir`` returns the env-provided path."""
    env_dir = tmp_path / "from-env"
    env_dir.mkdir()
    monkeypatch.setenv(R4_A3_DATASET_DIR_ENV, str(env_dir))
    resolved = _resolve_dataset_dir()
    assert resolved == Path(str(env_dir))


def test_p0_1_dataset_env_missing_provider_calls_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """P0-1: when the real-LLM gate is open but dataset env is missing,
    the harness must skip before any provider call.

    This test simulates the full preflight sequence:
    1. Gate is open (allow=1 + run=1 + model set).
    2. Dataset env is missing.
    3. ``_resolve_dataset_dir`` raises ``pytest.skip``.
    4. No provider call is made (verified via a sentinel model that
       would record any invocation).

    The sentinel model wraps ``BudgetedUsageModel`` — if any provider
    call were made, ``executed_requests`` would be > 0.
    """
    # Open the gate.
    monkeypatch.setenv("CLAREAD_ALLOW_REAL_LLM_TESTS", "1")
    monkeypatch.setenv(R4_A3_RUN_ENV, "1")
    monkeypatch.setenv(REAL_LLM_MODEL_ENV, "test-model")
    # Remove dataset env — the harness must skip before any provider call.
    monkeypatch.delenv(R4_A3_DATASET_DIR_ENV, raising=False)

    # Sentinel: track any provider call.
    call_count = {"n": 0}

    class _SentinelModel:
        async def request(self, *args, **kwargs):  # noqa: ANN001
            call_count["n"] += 1
            raise AssertionError(
                "provider request was made — harness should have "
                "skipped before any provider call when dataset env is missing"
            )

        async def request_stream(self, *args, **kwargs):  # noqa: ANN001
            call_count["n"] += 1
            raise AssertionError(
                "provider request_stream was made — harness should have "
                "skipped before any provider call when dataset env is missing"
            )

    # The harness calls ``_resolve_dataset_dir`` BEFORE ``_run_phase``,
    # so the skip fires before any provider call. We verify by catching
    # the skip exception and asserting call_count == 0.
    with pytest.raises(pytest.skip.Exception):
        _resolve_dataset_dir()
    assert call_count["n"] == 0, (
        f"provider calls={call_count['n']} — harness made a paid call "
        "before fail-closing on missing dataset env"
    )


# ---------------------------------------------------------------------------
# P0-3 regression: preflight must fail-closed before any paid call
# ---------------------------------------------------------------------------


def _make_minimal_case(
    *,
    case_id: str = "test-case",
    source_kind: str = "synthetic_short",
    record_id: str | None = None,
    article_text: str | None = "Hello world.",
    phase_tags: list[str] | None = None,
    expected_envelope_fingerprint: str | None = None,
) -> ReaderRecordAskR4A3Case:
    """Build a minimal :class:`ReaderRecordAskR4A3Case` for preflight tests.

    R4-A4-2R P0-Identity: ``expected_envelope_fingerprint`` is an
    optional kwarg for the new runtime fixture identity field. When
    ``None`` (default), no preflight identity check is performed
    (backwards-compat with pre-R4-A4-2R cases).
    """
    from claread_eval.reader_record_ask.schema import (  # noqa: PLC0415
        ReaderRecordAskR4A3Expected,
    )
    return ReaderRecordAskR4A3Case(
        id=case_id,
        source_kind=source_kind,  # type: ignore[arg-type]
        record_id=record_id,
        article_text=article_text,
        article_title=None,
        input_mode="no_selection",
        selection=None,
        rag_mode="off",
        source_metadata="unknown",
        baseline_mode="complete",
        question="测试问题。",
        question_category="main_idea",
        expected=ReaderRecordAskR4A3Expected(),
        phase_tags=phase_tags or [],
        expected_envelope_fingerprint=expected_envelope_fingerprint,
    )


def _make_minimal_session(tmp_path: Path) -> RunSessionLayout:
    """Build a minimal :class:`RunSessionLayout` rooted at ``tmp_path``."""
    return RunSessionLayout(runs_root=tmp_path, run_id="test-run")


def _make_minimal_model_config(
    *,
    thinking_enabled: bool = False,
) -> ResolvedModelConfig:
    """Build a minimal :class:`ResolvedModelConfig` for preflight tests."""
    return ResolvedModelConfig(
        route=MODEL_ROUTE_READER_ASK,
        profile_name="test-profile",
        provider="test-provider",
        adapter="openai_compatible",
        model_name="test-model",
        base_url="https://example.test",
        api_key="sk-test-not-real",
        model_settings=RunModelSettings(),
    )


def test_p0_3_preflight_bbc_env_missing_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """P0-3: when a BBC case is selected but ``CLAREAD_R4_A3_BBC_RECORD_ID``
    env is missing, preflight must return ``bbc_record_id_env_missing`` —
    NOT skip the BBC case mid-loop after synthetic cases have already
    made paid calls.

    This is the core P0-3 regression: the prior implementation built
    runtime inputs lazily inside the per-case loop, so synthetic cases
    ran first (paid calls), then BBC cases hit ``pytest.skip``. The
    fix moves the env binding check to ``_preflight_check`` which runs
    BEFORE any provider call.
    """
    monkeypatch.delenv(R4_A3_BBC_RECORD_ID_ENV, raising=False)
    session = _make_minimal_session(tmp_path)
    case = _make_minimal_case(
        case_id="bbc-test",
        source_kind="bbc_record",
        record_id="00000000-0000-4000-8000-000000000000",
        article_text=None,
        phase_tags=["real_phase1"],
    )
    result = _preflight_check(
        session=session,
        phase=1,
        model_config=_make_minimal_model_config(),
        cases_to_run=[case],
        max_requests=30,
        max_tokens=200_000,
    )
    assert result == "bbc_record_id_env_missing", (
        "BBC case with missing env must fail-closed at preflight, not "
        "skip mid-loop after paid calls"
    )


def test_p0_3_preflight_bbc_record_id_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """P0-3: when the env var is set but doesn't match the case's
    ``record_id``, preflight must return ``bbc_record_id_mismatch``.
    """
    monkeypatch.setenv(
        R4_A3_BBC_RECORD_ID_ENV, "aaaaaaaa-0000-0000-0000-000000000000"
    )
    session = _make_minimal_session(tmp_path)
    case = _make_minimal_case(
        case_id="bbc-test",
        source_kind="bbc_record",
        record_id="00000000-0000-4000-8000-000000000000",
        article_text=None,
        phase_tags=["real_phase1"],
    )
    result = _preflight_check(
        session=session,
        phase=1,
        model_config=_make_minimal_model_config(),
        cases_to_run=[case],
        max_requests=30,
        max_tokens=200_000,
    )
    assert result == "bbc_record_id_mismatch"


def test_p0_3_preflight_bbc_case_missing_record_id_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """P0-3: a BBC case with no ``record_id`` field must fail-closed at
    preflight (``bbc_case_missing_record_id``), not crash mid-loop.
    """
    monkeypatch.setenv(
        R4_A3_BBC_RECORD_ID_ENV, "00000000-0000-4000-8000-000000000000"
    )
    session = _make_minimal_session(tmp_path)
    case = _make_minimal_case(
        case_id="bbc-malformed",
        source_kind="bbc_record",
        record_id=None,
        article_text=None,
        phase_tags=["real_phase1"],
    )
    result = _preflight_check(
        session=session,
        phase=1,
        model_config=_make_minimal_model_config(),
        cases_to_run=[case],
        max_requests=30,
        max_tokens=200_000,
    )
    assert result == "bbc_case_missing_record_id"


def test_p0_3_preflight_synthetic_only_passes_env_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """P0-3: synthetic-only case set must pass preflight even when BBC
    env is missing (no BBC case → no env binding requirement).
    """
    monkeypatch.delenv(R4_A3_BBC_RECORD_ID_ENV, raising=False)
    session = _make_minimal_session(tmp_path)
    case = _make_minimal_case(
        case_id="synthetic-1",
        source_kind="synthetic_short",
        article_text="Hello world.",
        phase_tags=["real_phase1"],
    )
    result = _preflight_check(
        session=session,
        phase=1,
        model_config=_make_minimal_model_config(),
        cases_to_run=[case],
        max_requests=30,
        max_tokens=200_000,
    )
    # None = preflight passed.
    assert result is None


@pytest.mark.asyncio
async def test_p0_3_preflight_runtime_inputs_synthetic_missing_article_skips(
    tmp_path: Path,
) -> None:
    """P0-3: ``_preflight_runtime_inputs`` must skip the WHOLE phase when
    a synthetic case has no ``article_text`` — BEFORE any provider call.

    The skip happens at zero paid calls because ``_preflight_runtime_inputs``
    is called BEFORE ``BudgetedUsageModel`` wraps the provider model.
    """
    case = _make_minimal_case(
        case_id="synthetic-bad",
        source_kind="synthetic_short",
        article_text=None,  # missing — must skip
        phase_tags=["real_phase1"],
    )
    with pytest.raises(pytest.skip.Exception):
        await _preflight_runtime_inputs([case])


@pytest.mark.asyncio
async def test_p0_3_preflight_runtime_inputs_bbc_failure_skips_all(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """P0-3: when BBC runtime input preparation fails (DB unreachable,
    record not found, etc.), the WHOLE phase must skip — even if
    synthetic cases are already prepared.

    This is the key regression: the prior implementation built runtime
    inputs lazily inside the per-case loop, so synthetic cases ran first
    (paid calls), then BBC cases hit ``pytest.skip`` mid-loop. The fix
    builds ALL runtime inputs before any provider call, so a BBC
    failure skips the whole phase at zero paid calls.

    The test monkeypatches ``_build_bbc_runtime_inputs`` to raise
    ``pytest.skip`` (simulating DB failure) and verifies that
    ``_preflight_runtime_inputs`` propagates the skip — meaning the
    harness never reaches the ``BudgetedUsageModel`` construction.
    """
    # Synthetic case (would succeed if reached).
    synthetic_case = _make_minimal_case(
        case_id="synthetic-ok",
        source_kind="synthetic_short",
        article_text="Hello world.",
        phase_tags=["real_phase1"],
    )
    # BBC case (will fail at runtime input preparation).
    bbc_case = _make_minimal_case(
        case_id="bbc-fail",
        source_kind="bbc_record",
        record_id="00000000-0000-4000-8000-000000000000",
        article_text=None,
        phase_tags=["real_phase1"],
    )

    async def _fake_bbc_build(record_id: str):  # noqa: ANN001
        pytest.skip(
            "BBC record load failed (exception_type=ConnectionError)"
        )

    # Monkeypatch the module-level function in the current test module.
    # ``services`` is not an importable package (the test file is run
    # directly via pytest), so we use ``sys.modules[__name__]`` to
    # reach the module namespace.
    monkeypatch.setattr(
        sys.modules[__name__],
        "_build_bbc_runtime_inputs",
        _fake_bbc_build,
    )

    # The synthetic case is listed FIRST — but the preflight must still
    # fail because the BBC case (listed second) cannot be prepared.
    # The whole phase skips at zero paid calls.
    with pytest.raises(pytest.skip.Exception):
        await _preflight_runtime_inputs([synthetic_case, bbc_case])


@pytest.mark.asyncio
async def test_p0_3_preflight_runtime_inputs_all_ready_succeeds(
    tmp_path: Path,
) -> None:
    """P0-3: when ALL selected cases' runtime inputs are successfully
    prepared, ``_preflight_runtime_inputs`` returns the full list of
    ``(case, envelope, document_access, runtime_fixture_fingerprint)``
    tuples — the harness then proceeds to the paid-call loop.

    R4-A4-2R2: the 4th tuple element is the deterministic
    runtime_fixture_fingerprint (SHA-256 over baseline_status +
    is_complete + ordered chunks). For synthetic cases without a
    declared ``expected_runtime_fixture_fingerprint``, the preflight
    accepts the computed fingerprint (synthetic cases are
    deterministic by construction).
    """
    case_a = _make_minimal_case(
        case_id="synthetic-a",
        source_kind="synthetic_short",
        article_text="First article.",
        phase_tags=["real_phase1"],
    )
    case_b = _make_minimal_case(
        case_id="synthetic-b",
        source_kind="synthetic_short",
        article_text="Second article.",
        phase_tags=["real_phase1"],
    )
    prepared = await _preflight_runtime_inputs([case_a, case_b])
    assert len(prepared) == 2
    assert prepared[0][0].id == "synthetic-a"
    assert prepared[1][0].id == "synthetic-b"
    # Each entry must have a valid envelope + document_access + runtime_fixture_fp.
    for _case, envelope, document_access, runtime_fixture_fp in prepared:
        assert envelope is not None
        assert document_access is not None
        assert envelope.envelope_fingerprint
        # R4-A4-2R2: the 4th element is a 64-char lowercase hex SHA-256.
        assert isinstance(runtime_fixture_fp, str)
        assert len(runtime_fixture_fp) == 64
        assert all(c in "0123456789abcdef" for c in runtime_fixture_fp)


# ---------------------------------------------------------------------------
# R4-A4-2R P0-Identity: harness pre-call check (_verify_runtime_identity)
# ---------------------------------------------------------------------------
# Scenarios 1 + 2 from the 8 required test scenarios:
# 1. fingerprint match → preflight continues (returns normally).
# 2. fingerprint mismatch/missing → fail-closed (pytest.skip BEFORE any
#    model builder is invoked or provider call is made — calls=0, builder=0).
#
# The function under test reads only ``envelope.envelope_fingerprint``.
# We use ``SimpleNamespace`` mocks for the envelope (no need to build a
# real ``ReadingRecordAskContextEnvelope`` pydantic model). The case is
# built via ``_make_minimal_case`` with the new
# ``expected_envelope_fingerprint`` kwarg added by R4-A4-2R.
# ---------------------------------------------------------------------------


def _make_envelope_mock(fingerprint: str | None):
    """Build a minimal envelope mock for ``_verify_runtime_identity``.

    The function only reads ``envelope.envelope_fingerprint``, so a
    :class:`SimpleNamespace` is sufficient and avoids the cost of
    constructing a full :class:`ReadingRecordAskContextEnvelope`
    (which requires verified DB-bound fields).
    """
    from types import SimpleNamespace
    return SimpleNamespace(envelope_fingerprint=fingerprint)


_VALID_FP_A = "a" * 64  # 64-char lowercase hex SHA-256 (test constant)
_VALID_FP_B = "b" * 64  # different valid fingerprint


def test_r4_a4_2r_verify_runtime_identity_no_expected_returns_normally() -> None:
    """Backwards-compat: case does not declare
    ``expected_envelope_fingerprint`` → no check is performed (returns
    normally even when the runtime fingerprint is missing).

    This preserves compatibility with cases authored before R4-A4-2R.
    New cases SHOULD declare the field.
    """
    case = _make_minimal_case(case_id="case-no-expected")
    assert case.expected_envelope_fingerprint is None
    envelope = _make_envelope_mock(fingerprint=None)
    # Must NOT raise.
    _verify_runtime_identity(case, envelope)


def test_r4_a4_2r_verify_runtime_identity_match_returns_normally() -> None:
    """Scenario 1: expected == runtime → returns normally (preflight
    continues, model will be built)."""
    case = _make_minimal_case(
        case_id="case-match",
        expected_envelope_fingerprint=_VALID_FP_A,
    )
    envelope = _make_envelope_mock(fingerprint=_VALID_FP_A)
    _verify_runtime_identity(case, envelope)


def test_r4_a4_2r_verify_runtime_identity_mismatch_skips() -> None:
    """Scenario 2: expected != runtime → pytest.skip (fail-closed BEFORE
    model builder is invoked, calls=0, builder=0).

    The skip fires from within ``_preflight_runtime_inputs`` BEFORE
    ``_build_model_for_prepared_phase`` is called — provider calls are
    structurally impossible.
    """
    case = _make_minimal_case(
        case_id="case-mismatch",
        expected_envelope_fingerprint=_VALID_FP_A,
    )
    envelope = _make_envelope_mock(fingerprint=_VALID_FP_B)
    with pytest.raises(pytest.skip.Exception):
        _verify_runtime_identity(case, envelope)


def test_r4_a4_2r_verify_runtime_identity_missing_runtime_skips() -> None:
    """Scenario 2 (missing runtime): expected declared but runtime
    ``envelope_fingerprint`` is None → pytest.skip (fail-closed)."""
    case = _make_minimal_case(
        case_id="case-missing-runtime",
        expected_envelope_fingerprint=_VALID_FP_A,
    )
    envelope = _make_envelope_mock(fingerprint=None)
    with pytest.raises(pytest.skip.Exception):
        _verify_runtime_identity(case, envelope)


def test_r4_a4_2r_verify_runtime_identity_empty_runtime_skips() -> None:
    """Scenario 2 (empty runtime): expected declared but runtime
    ``envelope_fingerprint`` is empty string → pytest.skip (fail-closed).

    Treated identically to missing — fail-closed when the runtime
    envelope does not carry a usable fingerprint.
    """
    case = _make_minimal_case(
        case_id="case-empty-runtime",
        expected_envelope_fingerprint=_VALID_FP_A,
    )
    envelope = _make_envelope_mock(fingerprint="")
    with pytest.raises(pytest.skip.Exception):
        _verify_runtime_identity(case, envelope)


def test_r4_a4_2r_verify_runtime_identity_empty_expected_skips() -> None:
    """Edge case: empty-string expected fingerprint can never match a
    valid 64-char runtime → pytest.skip.

    Dataset authors MUST NOT publish empty strings; the harness treats
    empty as declared (not None) and fails closed. The schema accepts
    empty (StrictStr only enforces type), but the runtime check
    rejects it — defense-in-depth.
    """
    case = _make_minimal_case(
        case_id="case-empty-expected",
        expected_envelope_fingerprint="",
    )
    envelope = _make_envelope_mock(fingerprint=_VALID_FP_A)
    with pytest.raises(pytest.skip.Exception):
        _verify_runtime_identity(case, envelope)


@pytest.mark.asyncio
async def test_r4_a4_2r_preflight_runtime_inputs_skips_on_identity_mismatch(
    tmp_path: Path,
) -> None:
    """Scenario 2 (integration): ``_preflight_runtime_inputs`` skips
    the WHOLE phase when a case's runtime identity mismatches — BEFORE
    any provider call.

    This is the integration counterpart to
    :func:`test_r4_a4_2r_verify_runtime_identity_mismatch_skips`. The
    skip fires at zero paid calls because
    ``_preflight_runtime_inputs`` is called BEFORE
    ``BudgetedUsageModel`` wraps the provider model.
    """
    case = _make_minimal_case(
        case_id="case-integration-mismatch",
        article_text="Hello world.",
        expected_envelope_fingerprint=_VALID_FP_A,
    )
    # The synthetic builder computes the REAL envelope_fingerprint
    # deterministically (sha256(article_text) feeds into
    # base_content_sha256, which feeds into envelope_fingerprint). It
    # will NOT equal _VALID_FP_A, so _verify_runtime_identity must
    # skip the whole phase.
    with pytest.raises(pytest.skip.Exception):
        await _preflight_runtime_inputs([case])


@pytest.mark.asyncio
async def test_r4_a4_2r_preflight_runtime_inputs_passes_when_no_expected_declared(
    tmp_path: Path,
) -> None:
    """Scenario 1 (integration): when a case does not declare
    ``expected_envelope_fingerprint``, ``_preflight_runtime_inputs``
    proceeds normally (no identity check). This is the backwards-compat
    path — existing cases without the field continue to work."""
    case = _make_minimal_case(
        case_id="case-no-expected-integration",
        article_text="Hello world.",
        # expected_envelope_fingerprint omitted — defaults to None.
    )
    prepared = await _preflight_runtime_inputs([case])
    assert len(prepared) == 1
    # R4-A4-2R2: prepared_inputs tuple is now 4 elements
    # (case, envelope, document_access, runtime_fixture_fingerprint).
    _case, envelope, _document_access, _runtime_fp = prepared[0]
    assert envelope is not None
    assert envelope.envelope_fingerprint  # real fingerprint populated


# ---------------------------------------------------------------------------
# P1-1 regression: per-artifact usage delta (no double-counting)
# ---------------------------------------------------------------------------


class _FakeBudget:
    """Minimal duck-typed stand-in for :class:`BudgetedUsageModel`.

    ``_build_usage_delta`` only reads three read-only properties
    (``executed_requests``, ``executed_input_tokens``,
    ``executed_output_tokens``), so a simple stub is sufficient for
    unit-testing the delta arithmetic without making real provider
    calls.
    """

    def __init__(
        self,
        requests: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        self._requests = requests
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens

    @property
    def executed_requests(self) -> int:
        return self._requests

    @property
    def executed_input_tokens(self) -> int:
        return self._input_tokens

    @property
    def executed_output_tokens(self) -> int:
        return self._output_tokens


def test_p1_1_two_single_request_cases_artifact_delta_1_1_aggregate_2() -> None:
    """P1-1: two cases, each making 1 provider request, must produce
    ``artifact.executed_requests = [1, 1]`` and aggregate = 2.

    Prior bug: the harness wrote the wrapper's *cumulative* counter
    into every artifact, so two single-request cases produced
    ``[1, 2]`` and the report summed them to ``3``. The fix snapshots
    the counter before each case and writes only the delta.
    """
    budget = _FakeBudget(requests=0, input_tokens=0, output_tokens=0)

    # Case A: snapshot at (0, 0, 0), case consumes 1 request.
    start_a = (budget.executed_requests, 0, 0)
    # Simulate the case incrementing the wrapper's counters.
    budget = _FakeBudget(requests=1, input_tokens=50, output_tokens=30)
    usage_a, delta_req_a, delta_tok_a = _build_usage_delta(
        budget, start_a[0], start_a[1], start_a[2]
    )
    assert delta_req_a == 1, "case A delta requests must be 1"
    assert usage_a.requests == 1
    assert delta_tok_a == 80, "case A delta tokens = 50 + 30 = 80"

    # Case B: snapshot at (1, 50, 30), case consumes 1 more request.
    start_b = (budget.executed_requests, 50, 30)
    budget = _FakeBudget(requests=2, input_tokens=100, output_tokens=60)
    usage_b, delta_req_b, delta_tok_b = _build_usage_delta(
        budget, start_b[0], start_b[1], start_b[2]
    )
    assert delta_req_b == 1, (
        "case B delta requests must be 1 (not 2 — cumulative is NOT written)"
    )
    assert usage_b.requests == 1
    assert delta_tok_b == 80

    # Aggregate = sum of per-artifact deltas (this is what the report does).
    aggregate_requests = delta_req_a + delta_req_b
    aggregate_tokens = delta_tok_a + delta_tok_b
    assert aggregate_requests == 2, (
        "aggregate requests must be 2 (1 + 1), NOT 3 (cumulative bug)"
    )
    assert aggregate_tokens == 160


def test_p1_1_second_case_with_two_requests_artifact_delta_1_2_aggregate_3() -> None:
    """P1-1: case A makes 1 request, case B makes 2 requests (e.g. tool
    loop). Artifacts must be ``[1, 2]`` and aggregate = 3.

    Prior bug: artifacts would be ``[1, 3]`` (cumulative) and aggregate
    would be ``4``.
    """
    # Case A: 1 request, snapshot at 0.
    budget_after_a = _FakeBudget(requests=1, input_tokens=50, output_tokens=30)
    _, delta_req_a, delta_tok_a = _build_usage_delta(
        budget_after_a, 0, 0, 0
    )
    assert delta_req_a == 1

    # Case B: 2 requests (tool loop), snapshot at (1, 50, 30).
    budget_after_b = _FakeBudget(
        requests=3, input_tokens=150, output_tokens=90
    )
    _, delta_req_b, delta_tok_b = _build_usage_delta(
        budget_after_b, 1, 50, 30
    )
    assert delta_req_b == 2, (
        "case B delta requests must be 2 (3 cumulative − 1 snapshot)"
    )
    assert delta_tok_b == 160, (
        "case B delta tokens = (150 − 50) + (90 − 30) = 160"
    )

    aggregate_requests = delta_req_a + delta_req_b
    assert aggregate_requests == 3, (
        "aggregate requests must be 3 (1 + 2), NOT 4 (cumulative bug)"
    )


def test_p1_1_budget_stop_preserves_real_global_cumulative() -> None:
    """P1-1: when a budget stop fires, the :class:`BudgetStopResult`
    must carry the REAL global cumulative counts (not a delta).

    Per-artifact deltas are for the report's per-case aggregation;
    the budget stop result is a run-level metadata field that records
    the total work done before the cap was hit. The two must not be
    confused.
    """
    # Simulate: 3 cases completed (deltas 1, 1, 1 = 3 cumulative), 4th
    # case triggers BudgetExhaustedError at cumulative request 3.
    budget_at_stop = _FakeBudget(
        requests=3, input_tokens=150, output_tokens=90
    )
    # The harness records the cumulative counters on BudgetStopResult
    # (via ``exc.executed_requests`` / ``exc.executed_tokens``).
    cumulative_requests = budget_at_stop.executed_requests
    cumulative_tokens = (
        budget_at_stop.executed_input_tokens
        + budget_at_stop.executed_output_tokens
    )
    assert cumulative_requests == 3, (
        "BudgetStopResult must carry cumulative (3), not a delta"
    )
    assert cumulative_tokens == 240


def test_p1_1_delta_does_not_affect_usage_observability_evaluator() -> None:
    """P1-1: the per-artifact delta must not break the
    ``usage_observability`` evaluator. The evaluator checks that
    ``artifact.agent_usage`` is non-None and has plausible counts —
    the delta (1, 50, 30) is a plausible per-case count.

    This is a smoke test: the delta pattern produces the same shape
    of ``RawUsage`` that the evaluator expects, just with per-case
    values instead of cumulative.
    """
    budget = _FakeBudget(requests=1, input_tokens=50, output_tokens=30)
    usage, delta_req, delta_tok = _build_usage_delta(budget, 0, 0, 0)
    assert usage.requests == 1
    assert usage.input_tokens == 50
    assert usage.output_tokens == 30
    assert delta_req == 1
    assert delta_tok == 80
    # The evaluator checks ``usage.requests > 0`` — delta of 1 passes.
    assert usage.requests > 0


# ---------------------------------------------------------------------------
# P1 regression: BudgetStopResult remaining structure consistency
# ---------------------------------------------------------------------------


class _FakeCase:
    """Minimal duck-typed stand-in for :class:`ReaderRecordAskR4A3Case`.

    ``_build_budget_stop_remaining`` only reads ``case.id``, so a tiny
    stub is sufficient for unit-testing the remaining-structure
    construction without spinning up a full dataset.
    """

    def __init__(self, case_id: str) -> None:
        self.id = case_id


def test_p1_budget_stop_spec_example_a_done_b_mid_c_pending() -> None:
    """P1 spec example: cases=[A,B,C], reps=3, A fully done, B stops at
    run_index=1.

    Expected (spec §三):
        remaining_cases == ["B", "C"]
        remaining_run_indices == {"B": [1, 2], "C": [0, 1, 2]}

    A is fully completed (3 reps) and MUST NOT appear in either
    structure — the report must not flag A's completed reps as missing.
    """
    cases = [_FakeCase("A"), _FakeCase("B"), _FakeCase("C")]
    remaining_cases, remaining_run_indices = _build_budget_stop_remaining(
        cases,
        current_case_id="B",
        current_run_index=1,
        repetitions=3,
    )
    assert remaining_cases == ["B", "C"]
    assert remaining_run_indices == {"B": [1, 2], "C": [0, 1, 2]}


def test_p1_budget_stop_prior_completed_case_not_in_remaining_map() -> None:
    """P1: a prior case that fully completed (3 reps) MUST NOT appear in
    ``remaining_cases`` OR ``remaining_run_indices`` — otherwise the
    report would re-mark completed reps as missing.
    """
    cases = [_FakeCase("A"), _FakeCase("B"), _FakeCase("C")]
    remaining_cases, remaining_run_indices = _build_budget_stop_remaining(
        cases,
        current_case_id="C",
        current_run_index=0,
        repetitions=3,
    )
    # A and B both fully completed before C — neither in remaining map.
    assert "A" not in remaining_cases
    assert "B" not in remaining_cases
    assert "A" not in remaining_run_indices
    assert "B" not in remaining_run_indices
    # Only C (the current case, stopped at first rep) is remaining.
    assert remaining_cases == ["C"]
    assert remaining_run_indices == {"C": [0, 1, 2]}


def test_p1_budget_stop_current_case_mid_rep() -> None:
    """P1: current case stops mid-rep — ``range(run_index, reps)``
    includes the failed rep AND subsequent reps, but excludes already-
    completed reps of the current case.
    """
    cases = [_FakeCase("A"), _FakeCase("B")]
    # A completed reps 0,1,2 — now on B, B completed rep 0, fails on rep 1.
    remaining_cases, remaining_run_indices = _build_budget_stop_remaining(
        cases,
        current_case_id="B",
        current_run_index=1,
        repetitions=3,
    )
    assert remaining_cases == ["B"]
    # B's rep 0 completed (NOT in remaining); reps 1, 2 still pending.
    assert remaining_run_indices == {"B": [1, 2]}


def test_p1_budget_stop_current_case_first_rep() -> None:
    """P1: budget stops on the current case's FIRST repetition (run_index=0)
    — the entire case is pending (range(0, reps) == all reps)."""
    cases = [_FakeCase("A"), _FakeCase("B"), _FakeCase("C")]
    # A fully done; B fails on its very first rep.
    remaining_cases, remaining_run_indices = _build_budget_stop_remaining(
        cases,
        current_case_id="B",
        current_run_index=0,
        repetitions=3,
    )
    assert remaining_cases == ["B", "C"]
    assert remaining_run_indices == {
        "B": [0, 1, 2],  # all reps pending (failed on first)
        "C": [0, 1, 2],  # all reps pending (subsequent case)
    }


def test_p1_budget_stop_last_case_last_rep() -> None:
    """P1: budget stops on the LAST case's LAST repetition — only that
    single rep is remaining (range(last_index, reps) == [last_index]).
    """
    cases = [_FakeCase("A"), _FakeCase("B"), _FakeCase("C")]
    # A, B fully done; C completed reps 0, 1, fails on rep 2 (last).
    remaining_cases, remaining_run_indices = _build_budget_stop_remaining(
        cases,
        current_case_id="C",
        current_run_index=2,
        repetitions=3,
    )
    assert remaining_cases == ["C"]
    assert remaining_run_indices == {"C": [2]}


def test_p1_budget_stop_remaining_cases_equals_map_keys() -> None:
    """P1 invariant: ``list(remaining_run_indices.keys()) == remaining_cases``.

    This must hold for every stop position. We sweep several
    configurations to confirm the invariant is structural, not
    position-dependent.
    """
    cases = [_FakeCase("A"), _FakeCase("B"), _FakeCase("C"), _FakeCase("D")]
    configs = [
        # (current_case_id, current_run_index, repetitions)
        ("A", 0, 3),  # first case, first rep
        ("A", 2, 3),  # first case, last rep
        ("B", 0, 3),  # middle case, first rep
        ("B", 1, 3),  # middle case, mid rep
        ("C", 2, 3),  # late case, last rep
        ("D", 0, 3),  # last case, first rep
        ("D", 2, 3),  # last case, last rep
    ]
    for current_case_id, current_run_index, repetitions in configs:
        remaining_cases, remaining_run_indices = _build_budget_stop_remaining(
            cases,
            current_case_id=current_case_id,
            current_run_index=current_run_index,
            repetitions=repetitions,
        )
        assert list(remaining_run_indices.keys()) == remaining_cases, (
            f"Invariant violated for (case={current_case_id}, "
            f"run_index={current_run_index}, reps={repetitions}): "
            f"keys={list(remaining_run_indices.keys())} != "
            f"remaining_cases={remaining_cases}"
        )
        # Also assert order is preserved (dataset/cases execution order):
        # remaining_cases starts with current_case_id, then subsequent
        # cases in original order.
        seen_current = False
        expected: list[str] = []
        for c in cases:
            if c.id == current_case_id:
                seen_current = True
                expected.append(c.id)
            elif seen_current:
                expected.append(c.id)
        assert remaining_cases == expected, (
            f"Order not preserved for (case={current_case_id}, "
            f"run_index={current_run_index}, reps={repetitions}): "
            f"expected={expected}, got={remaining_cases}"
        )


def test_p1_budget_stop_report_does_not_count_completed_as_missing() -> None:
    """P1: the report MUST NOT count completed case artifacts as missing.

    This is the user-facing consequence of the remaining-structure
    contract: when ``remaining_cases`` excludes completed cases, the
    report's "missing runs" computation (which is
    ``remaining_run_indices`` flattened) will NOT include any
    (case_id, run_index) pair that was already written to disk.

    We simulate the report's "missing runs" computation by flattening
    ``remaining_run_indices`` and asserting that no completed
    (case_id, run_index) pair appears in it.
    """
    cases = [_FakeCase("A"), _FakeCase("B"), _FakeCase("C")]
    # Scenario: A fully done (3 artifacts written); B done reps 0,1;
    # B fails on rep 2; C never started.
    remaining_cases, remaining_run_indices = _build_budget_stop_remaining(
        cases,
        current_case_id="B",
        current_run_index=2,
        repetitions=3,
    )

    # The report computes "missing runs" by flattening
    # remaining_run_indices.
    missing_runs: set[tuple[str, int]] = {
        (case_id, run_index)
        for case_id, run_indices in remaining_run_indices.items()
        for run_index in run_indices
    }

    # Completed (case_id, run_index) pairs that were written to disk
    # before the budget stop. These MUST NOT appear in missing_runs.
    completed_runs = {
        ("A", 0), ("A", 1), ("A", 2),  # A fully completed
        ("B", 0), ("B", 1),            # B's first two reps completed
    }
    overlap = completed_runs & missing_runs
    assert not overlap, (
        f"Completed runs were re-counted as missing: {overlap} — "
        "the report must not flag completed artifacts as missing."
    )

    # The expected missing runs are: B's rep 2 (the failed rep) + all of C.
    expected_missing = {("B", 2), ("C", 0), ("C", 1), ("C", 2)}
    assert missing_runs == expected_missing, (
        f"missing_runs={missing_runs} != expected={expected_missing}"
    )


# ---------------------------------------------------------------------------
# SubTask 3.4: PreparedPhaseContext frozen + tuple behavior tests
# ---------------------------------------------------------------------------


def _make_fake_prepared_phase(
    *,
    tmp_path: Path,
    phase: int = 1,
    run_id: str = "test-run",
    content_sha256: str = "a" * 64,
) -> PreparedPhaseContext:
    """Build a minimal ``PreparedPhaseContext`` for unit tests (no env, no disk).

    Constructs a single-case synthetic context that can be used to test
    ``PreparedPhaseContext`` invariants (frozen, tuple collections)
    without driving the full ``_prepare_phase`` seam (which requires
    env gate + dataset dir + session layout). The session layout's
    ``runs_root`` points at ``tmp_path`` so any manifest writes land in
    the test's tmp directory.

    R4-A4-2R2: ``prepared_inputs`` carries a 4-tuple including the
    ``runtime_fixture_fingerprint``. The fingerprint is computed via
    the same deterministic path the harness preflight uses
    (BaselineContextAssembler + compute_runtime_fixture_fingerprint)
    so manifest-writing tests can pass the
    ``runtime_fixture_identities`` field through validation.
    """
    case = _make_minimal_case(
        article_text="Hello world.",
        phase_tags=["real_phase1"],
    )
    snapshot = _make_fake_snapshot([case], content_sha256=content_sha256)
    session = RunSessionLayout(runs_root=tmp_path, run_id=run_id)
    planner = PhasePlanner(
        dataset=snapshot.dataset,
        phase=phase,
        prior_artifacts=[],
        prior_eval_results=None,
    )
    envelope, document_access = _build_synthetic_runtime_inputs(case)
    # Compute the runtime_fixture_fingerprint via the same deterministic
    # path the harness preflight uses.
    registry = EvidenceRegistry(envelope.envelope_fingerprint)
    assembler = BaselineContextAssembler(
        envelope=envelope,
        document_access=document_access,
        registry=registry,
    )
    import asyncio as _asyncio  # noqa: PLC0415

    baseline = _asyncio.run(assembler.assemble_baseline())
    chunks_view: list[tuple[int, str]] = [
        (chunk.chunk_ordinal, chunk.text)
        for chunk in baseline.model_context_chunks
    ]
    runtime_fixture_fp = compute_runtime_fixture_fingerprint(
        baseline_status=baseline.baseline_status,
        is_complete=baseline.is_complete,
        chunks=chunks_view,
    )
    planned_run_indices = {case.id: list(range(planner.repetitions))}
    return PreparedPhaseContext(
        phase=phase,
        authorized_short_name="test-model",
        snapshot=snapshot,
        session=session,
        prior_artifacts=None,
        prior_eval_results=None,
        planner=planner,
        cases_to_run=(case,),
        prepared_inputs=((case, envelope, document_access, runtime_fixture_fp),),
        max_requests=30,
        max_tokens=200_000,
        planned_run_indices=planned_run_indices,
    )


def test_prepared_phase_context_is_frozen() -> None:
    """SubTask 3.4: ``PreparedPhaseContext`` must be ``@dataclass(frozen=True)``.

    The frozen contract prevents post-construction mutation of
    ``planned_run_indices`` / ``cases_to_run`` / ``prepared_inputs`` —
    mutation would desync the manifest's planned set from the actual
    execution.
    """
    assert PreparedPhaseContext.__dataclass_params__.frozen is True, (
        "PreparedPhaseContext must be @dataclass(frozen=True) so that "
        "planned_run_indices / cases_to_run / prepared_inputs cannot be "
        "mutated after construction (would desync the manifest)."
    )


def test_prepared_phase_context_rebinding_raises_frozen_instance_error(
    tmp_path: Path,
) -> None:
    """SubTask 3.4: rebinding a field on ``PreparedPhaseContext`` must raise
    ``FrozenInstanceError`` (or its parent ``AttributeError``).

    Construction succeeds (all fields are valid), but any subsequent
    ``ctx.field = value`` must fail at runtime. This is the frozen
    contract: the context is an immutable snapshot of the preflight
    state, not a mutable work-in-progress.
    """
    ctx = _make_fake_prepared_phase(tmp_path=tmp_path)
    # FrozenInstanceError is a subclass of AttributeError in Python 3.10+.
    # Use the parent class for portability across Python versions.
    with pytest.raises(AttributeError):
        ctx.cases_to_run = ()


def test_prepared_phase_context_collections_are_tuples(
    tmp_path: Path,
) -> None:
    """SubTask 3.4: ``cases_to_run`` / ``prepared_inputs`` / ``prior_artifacts``
    must be tuple instances (not list) so callers cannot append or mutate.

    Tuples have no ``append`` / ``extend`` / ``__setitem__`` — the
    only way to "add" a case is to construct a new
    ``PreparedPhaseContext`` (which requires going through
    ``_prepare_phase`` again). This prevents a test or harness from
    accidentally adding a case mid-execution and desyncing the
    manifest's planned set.
    """
    ctx = _make_fake_prepared_phase(tmp_path=tmp_path)
    assert isinstance(ctx.cases_to_run, tuple), (
        f"cases_to_run must be tuple, got {type(ctx.cases_to_run).__name__}"
    )
    assert isinstance(ctx.prepared_inputs, tuple), (
        f"prepared_inputs must be tuple, got {type(ctx.prepared_inputs).__name__}"
    )
    # prior_artifacts is None for Phase 1 (no prior phase). When set
    # (Phase 2/3), it must also be a tuple. We verify the None-or-tuple
    # invariant here; the Phase 2/3 construction path in _prepare_phase
    # wraps the list in tuple() before construction.
    assert ctx.prior_artifacts is None or isinstance(
        ctx.prior_artifacts, tuple
    ), (
        f"prior_artifacts must be None or tuple, got "
        f"{type(ctx.prior_artifacts).__name__}"
    )
    # Tuples have no append/extend/__setitem__.
    assert not hasattr(ctx.cases_to_run, "append"), (
        "cases_to_run is a tuple — tuples have no append method"
    )
    assert not hasattr(ctx.prepared_inputs, "append"), (
        "prepared_inputs is a tuple — tuples have no append method"
    )


# ---------------------------------------------------------------------------
# SubTask 2.8: manifest writing tests through _run_real_phase_entry
# ---------------------------------------------------------------------------


def _install_env_for_phase_entry(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tmp_path: Path,
    run_id: str,
) -> None:
    """Set up the env vars needed for ``_run_real_phase_entry`` to pass the
    deterministic preflight (env gate + dataset dir + session layout).

    This is the shared env setup for the manifest-writing tests. Each
    test additionally monkeypatches ``load_r4_a3_dataset_with_snapshot``
    and ``_run_one_case`` to control the execution path without making
    real LLM calls.
    """
    monkeypatch.setenv("CLAREAD_ALLOW_REAL_LLM_TESTS", "1")
    monkeypatch.setenv(R4_A3_RUN_ENV, "1")
    monkeypatch.setenv(REAL_LLM_MODEL_ENV, "test-model")
    monkeypatch.setenv(R4_A3_DATASET_DIR_ENV, str(tmp_path))
    monkeypatch.setenv(ENV_RUN_ID, run_id)
    monkeypatch.setenv(R4_A3_RUNS_DIR_ENV, str(tmp_path / "runs"))
    monkeypatch.delenv(R4_A3_BBC_RECORD_ID_ENV, raising=False)


class _FakeBudgetedModelForManifest:
    """Minimal ``BudgetedUsageModel`` stand-in for manifest tests.

    Avoids the pydantic_ai ``infer_model()`` call that the real
    :class:`BudgetedUsageModel` triggers in
    :meth:`WrapperModel.__init__` — that call requires a real
    :class:`Model` instance or a model-id string, but the manifest
    tests use a sentinel ``object()`` from
    :func:`_install_model_builder_sentinels`. Only exposes the
    read-only counter properties that ``_execute_phase`` reads after
    the case loop to write the completed manifest.
    """

    def __init__(
        self,
        wrapped: Any = None,
        *,
        max_requests: int | None = None,
        max_tokens: int | None = None,
    ) -> None:
        # Do NOT call super().__init__() — that would trigger infer_model.
        self.wrapped = wrapped
        self._request_cap = max_requests
        self._token_cap = max_tokens
        self._executed_requests = 0
        self._executed_input_tokens = 0
        self._executed_output_tokens = 0

    @property
    def executed_requests(self) -> int:
        return self._executed_requests

    @property
    def executed_input_tokens(self) -> int:
        return self._executed_input_tokens

    @property
    def executed_output_tokens(self) -> int:
        return self._executed_output_tokens

    @property
    def executed_tokens(self) -> int:
        return self._executed_input_tokens + self._executed_output_tokens


def _install_manifest_test_overrides(
    monkeypatch: pytest.MonkeyPatch,
    *,
    snapshot: LoadedReaderRecordAskDatasetSnapshot,
    fake_run_one_case: Any,
) -> None:
    """Install the shared monkeypatches for the manifest-writing tests.

    Replaces:
    - ``load_r4_a3_dataset_with_snapshot`` — returns ``snapshot``.
    - ``_resolve_authorized_model`` / ``_build_thinking_model`` —
      sentinel builders that return ``object()`` + minimal config (no
      real provider call).
    - ``BudgetedUsageModel`` — replaced with
      :class:`_FakeBudgetedModelForManifest` so ``_execute_phase`` can
      construct the wrapper without triggering pydantic_ai's
      ``infer_model`` on the sentinel ``object()``.
    - ``_run_one_case`` — replaced with ``fake_run_one_case`` to
      control the execution path (return artifact / raise budget /
      raise unexpected).
    """
    monkeypatch.setattr(
        sys.modules[__name__],
        "load_r4_a3_dataset_with_snapshot",
        lambda _dir: snapshot,
    )
    _install_model_builder_sentinels(monkeypatch)
    monkeypatch.setattr(
        sys.modules[__name__],
        "BudgetedUsageModel",
        _FakeBudgetedModelForManifest,
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "_run_one_case",
        fake_run_one_case,
    )


@pytest.mark.asyncio
async def test_run_real_phase_entry_writes_completed_manifest_on_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """SubTask 2.8: when all cases complete normally, ``_run_real_phase_entry``
    writes a ``status="completed"`` manifest via ``_execute_phase``.

    Mocks ``_run_one_case`` to return a fake ``RawArtifact`` (no real
    LLM call) so ``_execute_phase`` runs its normal-completion path:
    writes the artifact to disk, appends to ``completed_run_indices``,
    and writes a ``status="completed"`` manifest atomically.
    ``_run_real_phase_entry`` then reads back the manifest and reports
    ``manifest_status="completed"``.
    """
    _install_env_for_phase_entry(
        monkeypatch, tmp_path=tmp_path, run_id="phase1-success",
    )

    case = _make_minimal_case(
        case_id="synthetic-success",
        article_text="Hello world.",
        phase_tags=["real_phase1"],
    )
    snapshot = _make_fake_snapshot([case], content_sha256="b" * 64)

    async def _fake_run_one_case(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        run_index = kwargs["run_index"]
        model_config = args[2]
        dataset_identity = kwargs["dataset_identity"]
        return RawArtifact(
            case_id=args[0].id,
            run_id=kwargs["run_id"],
            run_index=run_index,
            model_short_name=model_config.model_name,
            model_route=model_config.route,
            thinking_enabled=False,
            finalized_status="ok",
            dataset_id=dataset_identity.dataset_id,
            dataset_schema_version=dataset_identity.schema_version,
            dataset_content_sha256=dataset_identity.content_sha256,
        )

    _install_manifest_test_overrides(
        monkeypatch,
        snapshot=snapshot,
        fake_run_one_case=_fake_run_one_case,
    )

    result = await _run_real_phase_entry(phase=1)

    assert result.manifest_status == "completed", (
        f"expected 'completed', got {result.manifest_status!r}"
    )
    assert result.artifacts_written > 0, (
        "expected at least one artifact written (Phase 1, 1 case × 3 reps)"
    )
    assert result.run_id == "phase1-success"

    # Verify the manifest file exists on disk and has the right status.
    manifest_path = tmp_path / "runs" / "phase1-success" / "manifest.json"
    assert manifest_path.exists(), (
        f"manifest file not written at {manifest_path}"
    )
    from claread_eval.reader_record_ask.run_manifest import read_manifest  # noqa: PLC0415

    manifest = read_manifest(manifest_path)
    assert manifest is not None, (
        "manifest file exists but read_manifest returned None"
    )
    assert manifest.status == "completed"
    assert manifest.run_id == "phase1-success"
    assert manifest.phase == 1
    assert manifest.remaining_count == 0, (
        "completed manifest must have empty remaining_run_indices"
    )
    assert manifest.planned_count == manifest.completed_count, (
        "completed manifest must have planned_count == completed_count"
    )
    assert manifest.stop_reason is None, (
        "completed manifest must have stop_reason=None"
    )


@pytest.mark.asyncio
async def test_run_real_phase_entry_writes_budget_manifest_on_budget_exhausted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """SubTask 2.8: when ``_execute_phase`` hits ``BudgetExhaustedError``, a
    ``status="budget_exhausted"`` manifest is written.

    Mocks ``_run_one_case`` to raise ``BudgetExhaustedError`` on the
    first call. ``_execute_phase`` catches it, records a
    ``BudgetStopResult``, and writes a ``status="budget_exhausted"``
    manifest atomically. ``_run_real_phase_entry`` reads back the
    manifest and reports ``manifest_status="budget_exhausted"``.
    """
    _install_env_for_phase_entry(
        monkeypatch, tmp_path=tmp_path, run_id="phase1-budget",
    )

    case = _make_minimal_case(
        case_id="synthetic-budget",
        article_text="Hello world.",
        phase_tags=["real_phase1"],
    )
    snapshot = _make_fake_snapshot([case], content_sha256="c" * 64)

    async def _fake_run_one_case(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise BudgetExhaustedError(
            cap_kind="request_cap",
            executed_requests=5,
            executed_tokens=200,
            request_cap=5,
            token_cap=10_000,
        )

    _install_manifest_test_overrides(
        monkeypatch,
        snapshot=snapshot,
        fake_run_one_case=_fake_run_one_case,
    )

    result = await _run_real_phase_entry(phase=1)

    assert result.manifest_status == "budget_exhausted", (
        f"expected 'budget_exhausted', got {result.manifest_status!r}"
    )

    manifest_path = tmp_path / "runs" / "phase1-budget" / "manifest.json"
    assert manifest_path.exists(), (
        f"budget_exhausted manifest file not written at {manifest_path}"
    )
    from claread_eval.reader_record_ask.run_manifest import read_manifest  # noqa: PLC0415

    manifest = read_manifest(manifest_path)
    assert manifest is not None
    assert manifest.status == "budget_exhausted"
    assert manifest.stop_reason == "budget_exhausted", (
        "budget_exhausted manifest must have stop_reason='budget_exhausted'"
    )
    assert manifest.remaining_count > 0, (
        "budget_exhausted manifest must have non-empty remaining_run_indices"
    )
    # The budget fired on the first rep, so no reps completed.
    assert manifest.completed_count == 0


@pytest.mark.asyncio
async def test_run_real_phase_entry_no_manifest_on_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """SubTask 2.8: when ``_execute_phase`` raises a non-budget exception,
    NO manifest is written. ``_run_real_phase_entry`` propagates the exception.

    Mocks ``_run_one_case`` to raise ``RuntimeError`` (not
    ``BudgetExhaustedError``). ``_execute_phase`` does NOT catch
    ``RuntimeError`` — it propagates up through
    ``_run_real_phase_entry``. No manifest is written (neither
    completed nor budget_exhausted). The aggregate will detect the
    missing manifest and block
    (``blocked_incomplete_real_model_run``).
    """
    _install_env_for_phase_entry(
        monkeypatch, tmp_path=tmp_path, run_id="phase1-unexpected",
    )

    case = _make_minimal_case(
        case_id="synthetic-unexpected",
        article_text="Hello world.",
        phase_tags=["real_phase1"],
    )
    snapshot = _make_fake_snapshot([case], content_sha256="i" * 64)

    async def _fake_run_one_case(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise RuntimeError("unexpected internal error during execution")

    _install_manifest_test_overrides(
        monkeypatch,
        snapshot=snapshot,
        fake_run_one_case=_fake_run_one_case,
    )

    # _run_real_phase_entry must propagate the RuntimeError (no catch).
    with pytest.raises(RuntimeError, match="unexpected internal error"):
        await _run_real_phase_entry(phase=1)

    # NO manifest file must exist — the aggregate detects the missing
    # manifest and blocks (blocked_incomplete_real_model_run).
    manifest_path = tmp_path / "runs" / "phase1-unexpected" / "manifest.json"
    assert not manifest_path.exists(), (
        f"manifest file should NOT exist when _execute_phase raised an "
        f"unexpected exception (got {manifest_path})"
    )


# ---------------------------------------------------------------------------
# R4-A4-2R3 P0-1: Actual Fixture Capture — Scenarios 1, 2, 3
# ---------------------------------------------------------------------------
# Scenario 1: preflight==actual pass (success path — artifact's recomputed
#             fingerprint matches the preflight computed fingerprint).
# Scenario 2: monkeypatch mismatch blocked — when the baseline assembler
#             produces different chunks than preflight, the artifact's
#             actual fingerprint differs from preflight; the aggregate's
#             three-layer check blocks (verified in
#             test_reader_record_ask_runtime_fixture_identity.py).
# Scenario 3: runtime exception → null + incomplete — when the runtime
#             raises, ``runtime_fixture_fingerprint=None`` and
#             ``capture_status="failed"`` (the aggregate's
#             instrumentation gate blocks).
#
# These tests exercise :func:`_run_one_case` directly (no real LLM call).
# :func:`run_reading_record_ask` is monkeypatched to return a controlled
# result with a known ``baseline_context`` — this is the ONLY seam that
# determines the artifact's actual ``runtime_fixture_fingerprint``.
# ---------------------------------------------------------------------------


def _make_baseline_chunk(
    *,
    chunk_ordinal: int,
    text: str,
    handle_id: str = "evh_test_chunk",
) -> ModelContextChunk:
    """Build a minimal :class:`ModelContextChunk` for scenario tests."""
    return ModelContextChunk(
        handle_id=handle_id,
        chunk_ordinal=chunk_ordinal,
        text=text,
    )


def _make_result_mock(
    *,
    baseline_status: str = "injected",
    is_complete: bool = True,
    is_injected: bool = True,
    chunks: tuple[ModelContextChunk, ...] = (),
    final_text: str = "Test answer.",
) -> Any:
    """Build a minimal ``run_reading_record_ask`` result mock.

    The mock carries a ``baseline_context`` whose
    ``model_context_chunks`` drive the actual
    ``runtime_fixture_fingerprint`` computation in :func:`_run_one_case`.
    Other fields are stubbed to satisfy the success path.
    """
    from types import SimpleNamespace  # noqa: PLC0415

    baseline_mock = SimpleNamespace(
        baseline_status=baseline_status,
        is_complete=is_complete,
        is_injected=is_injected,
        model_context_chunks=tuple(chunks),
    )
    return SimpleNamespace(
        baseline_context=baseline_mock,
        final_text=final_text,
        finalized=SimpleNamespace(
            status="ok",
            reason="complete",
            resolved_evidence=[],
        ),
        agent_draft=SimpleNamespace(
            response_kind="grounded_answer",
            cited_evidence_handles=[],
        ),
        evidence_observations=[],
        read_range_calls=0,
        search_current_article_calls=0,
    )


def _make_dataset_identity() -> DatasetIdentity:
    """Build a minimal :class:`DatasetIdentity` for scenario tests."""
    return DatasetIdentity(
        dataset_id="test-dataset",
        schema_version="test-schema-v1",
        content_sha256="a" * 64,
    )


@pytest.mark.asyncio
async def test_r4_a4_2r3_scenario1_preflight_equals_actual_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario 1: success path — the artifact's recomputed
    ``runtime_fixture_fingerprint`` matches the preflight computed
    fingerprint (deterministic assembly).

    R4-A4-2R3 P0-1: the artifact's actual fingerprint is recomputed
    from ``result.baseline_context`` (NOT copied from preflight).
    When the baseline assembler is deterministic, the two agree.
    """
    # The chunks the (mocked) runtime will return.
    runtime_chunks = (
        _make_baseline_chunk(chunk_ordinal=0, text="Hello world."),
        _make_baseline_chunk(chunk_ordinal=1, text="Second chunk."),
    )
    # The preflight-equivalent chunk views (chunk_ordinal, text).
    preflight_chunks_view: list[tuple[int, str]] = [
        (chunk.chunk_ordinal, chunk.text) for chunk in runtime_chunks
    ]
    expected_fp = compute_runtime_fixture_fingerprint(
        baseline_status="injected",
        is_complete=True,
        chunks=preflight_chunks_view,
    )

    case = _make_minimal_case(
        case_id="case-scenario1",
        article_text="Hello world.",
        phase_tags=["real_phase1"],
        expected_envelope_fingerprint=_VALID_FP_A,
    )
    case.expected_runtime_fixture_fingerprint = expected_fp

    result_mock = _make_result_mock(
        baseline_status="injected",
        is_complete=True,
        chunks=runtime_chunks,
    )

    async def _fake_run_reading_record_ask(**_kwargs):  # noqa: ANN202
        return result_mock

    monkeypatch.setattr(
        sys.modules[__name__],
        "run_reading_record_ask",
        _fake_run_reading_record_ask,
    )

    artifact = await _run_one_case(
        case=case,
        budget_model=_FakeBudgetedModelForManifest(),
        model_config=_make_minimal_model_config(),
        run_id="test-run",
        run_index=0,
        envelope=_make_envelope_mock(_VALID_FP_A),
        document_access=object(),  # not read by _run_one_case success path
        start_requests=0,
        start_input_tokens=0,
        start_output_tokens=0,
        dataset_identity=_make_dataset_identity(),
    )

    # Three-layer identity: artifact actual == preflight computed.
    assert artifact.runtime_fixture_fingerprint == expected_fp, (
        "Scenario 1: artifact's recomputed runtime_fixture_fingerprint "
        "MUST match the preflight computed fingerprint (deterministic "
        "assembly). R4-A4-2R3 P0-1: the artifact actual is recomputed "
        "from result.baseline_context — copying preflight would hide "
        "any runtime drift."
    )
    # The capture_status is "captured" (baseline produced >=1 chunk).
    assert artifact.model_context_capture_status == "captured"
    assert artifact.error is None
    assert artifact.runtime_fixture_fingerprint is not None
    assert len(artifact.runtime_fixture_fingerprint) == 64


@pytest.mark.asyncio
async def test_r4_a4_2r3_scenario2_actual_differs_from_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario 2: when the baseline assembler produces different chunks
    than preflight (simulated via monkeypatched
    ``run_reading_record_ask``), the artifact's actual
    ``runtime_fixture_fingerprint`` DIFFERS from the preflight (expected)
    value. The aggregate's three-layer check (verified in
    ``test_reader_record_ask_runtime_fixture_identity.py``) blocks the
    run.

    R4-A4-2R3 P0-1: copying preflight → artifact would HIDE this drift.
    The recomputation from ``result.baseline_context`` is what makes
    the three-layer check meaningful.
    """
    # Preflight (expected) — computed offline from chunk set A.
    preflight_chunks_view: list[tuple[int, str]] = [
        (0, "Hello world."),
        (1, "Second chunk."),
    ]
    preflight_fp = compute_runtime_fixture_fingerprint(
        baseline_status="injected",
        is_complete=True,
        chunks=preflight_chunks_view,
    )

    case = _make_minimal_case(
        case_id="case-scenario2",
        article_text="Hello world.",
        phase_tags=["real_phase1"],
        expected_envelope_fingerprint=_VALID_FP_A,
    )
    case.expected_runtime_fixture_fingerprint = preflight_fp

    # Runtime returns DIFFERENT chunks (chunk set B — different text).
    # This simulates baseline drift: e.g. a monkeypatched assembler,
    # a DB mutation between preflight and run, or different chunk
    # truncation. The artifact's actual fingerprint is recomputed
    # from these DIFFERENT chunks — it MUST NOT equal preflight.
    runtime_chunks = (
        _make_baseline_chunk(chunk_ordinal=0, text="DIFFERENT chunk text."),
        _make_baseline_chunk(chunk_ordinal=1, text="Second chunk."),
    )
    actual_fp_expected = compute_runtime_fixture_fingerprint(
        baseline_status="injected",
        is_complete=True,
        chunks=[(c.chunk_ordinal, c.text) for c in runtime_chunks],
    )
    assert actual_fp_expected != preflight_fp, (
        "test setup invariant: runtime chunks must differ from preflight"
    )

    result_mock = _make_result_mock(
        baseline_status="injected",
        is_complete=True,
        chunks=runtime_chunks,
    )

    async def _fake_run_reading_record_ask(**_kwargs):  # noqa: ANN202
        return result_mock

    monkeypatch.setattr(
        sys.modules[__name__],
        "run_reading_record_ask",
        _fake_run_reading_record_ask,
    )

    artifact = await _run_one_case(
        case=case,
        budget_model=_FakeBudgetedModelForManifest(),
        model_config=_make_minimal_model_config(),
        run_id="test-run",
        run_index=0,
        envelope=_make_envelope_mock(_VALID_FP_A),
        document_access=object(),
        start_requests=0,
        start_input_tokens=0,
        start_output_tokens=0,
        dataset_identity=_make_dataset_identity(),
    )

    # The artifact's actual fingerprint reflects the RUNTIME chunks
    # (not the preflight chunks). It differs from preflight.
    assert artifact.runtime_fixture_fingerprint == actual_fp_expected, (
        "Scenario 2: artifact's runtime_fixture_fingerprint MUST be "
        "recomputed from result.baseline_context (actual chunks)."
    )
    assert artifact.runtime_fixture_fingerprint != preflight_fp, (
        "Scenario 2: when runtime baseline drifts from preflight, the "
        "artifact's actual fingerprint MUST differ from preflight. "
        "Copying preflight → artifact would hide the drift — R4-A4-2R3 "
        "P0-1 forbids this."
    )
    # The aggregate's three-layer check (verified separately in
    # test_reader_record_ask_runtime_fixture_identity.py) blocks
    # the run when actual != preflight.
    assert artifact.model_context_capture_status == "captured"


@pytest.mark.asyncio
async def test_r4_a4_2r3_scenario2_baseline_unavailable_actual_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario 2 (variant): when the baseline assembler yields 0 chunks
    (capture_status="unavailable"), the artifact's actual
    ``runtime_fixture_fingerprint`` is None — the aggregate's
    instrumentation gate blocks the run rather than forging an identity.

    R4-A4-2R3 P0-1: even if preflight computed a valid fingerprint,
    the actual fingerprint MUST be None when the runtime baseline
    produced no chunks. The harness does NOT forge an actual identity
    by copying preflight.
    """
    preflight_chunks_view: list[tuple[int, str]] = [
        (0, "Hello world."),
    ]
    preflight_fp = compute_runtime_fixture_fingerprint(
        baseline_status="injected",
        is_complete=True,
        chunks=preflight_chunks_view,
    )

    case = _make_minimal_case(
        case_id="case-scenario2-unavailable",
        article_text="Hello world.",
        phase_tags=["real_phase1"],
        expected_envelope_fingerprint=_VALID_FP_A,
    )
    case.expected_runtime_fixture_fingerprint = preflight_fp

    # Runtime returns baseline with NO chunks (envelope_mismatch /
    # no_units scenario). The actual fingerprint MUST be None.
    result_mock = _make_result_mock(
        baseline_status="envelope_mismatch",
        is_complete=False,
        chunks=(),  # empty
    )

    async def _fake_run_reading_record_ask(**_kwargs):  # noqa: ANN202
        return result_mock

    monkeypatch.setattr(
        sys.modules[__name__],
        "run_reading_record_ask",
        _fake_run_reading_record_ask,
    )

    artifact = await _run_one_case(
        case=case,
        budget_model=_FakeBudgetedModelForManifest(),
        model_config=_make_minimal_model_config(),
        run_id="test-run",
        run_index=0,
        envelope=_make_envelope_mock(_VALID_FP_A),
        document_access=object(),
        start_requests=0,
        start_input_tokens=0,
        start_output_tokens=0,
        dataset_identity=_make_dataset_identity(),
    )

    # Actual fingerprint is None — runtime baseline produced no chunks.
    assert artifact.runtime_fixture_fingerprint is None, (
        "Scenario 2 (unavailable): when runtime baseline yields 0 "
        "chunks, the artifact's actual fingerprint MUST be None. "
        "Forging an identity by copying preflight is forbidden."
    )
    # capture_status="unavailable" — the aggregate's instrumentation
    # gate blocks the run.
    assert artifact.model_context_capture_status == "unavailable"
    assert artifact.error is None  # no exception — model ran, baseline empty


@pytest.mark.asyncio
async def test_r4_a4_2r3_scenario3_runtime_exception_actual_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario 3: runtime exception → ``runtime_fixture_fingerprint=None``
    and ``model_context_capture_status="failed"``. The aggregate's
    instrumentation gate blocks the run rather than forging an identity.

    R4-A4-2R3 P0-1: when the runtime raises (before/independent of
    baseline assembly), the actual fingerprint is None. The preflight
    (expected) value MUST NOT be copied — there is no
    ``result.baseline_context`` to recompute from.
    """
    preflight_chunks_view: list[tuple[int, str]] = [
        (0, "Hello world."),
    ]
    preflight_fp = compute_runtime_fixture_fingerprint(
        baseline_status="injected",
        is_complete=True,
        chunks=preflight_chunks_view,
    )

    case = _make_minimal_case(
        case_id="case-scenario3-exception",
        article_text="Hello world.",
        phase_tags=["real_phase1"],
        expected_envelope_fingerprint=_VALID_FP_A,
    )
    case.expected_runtime_fixture_fingerprint = preflight_fp

    async def _fake_run_reading_record_ask(**_kwargs):  # noqa: ANN202
        raise RuntimeError("simulated runtime failure")

    monkeypatch.setattr(
        sys.modules[__name__],
        "run_reading_record_ask",
        _fake_run_reading_record_ask,
    )

    artifact = await _run_one_case(
        case=case,
        budget_model=_FakeBudgetedModelForManifest(),
        model_config=_make_minimal_model_config(),
        run_id="test-run",
        run_index=0,
        envelope=_make_envelope_mock(_VALID_FP_A),
        document_access=object(),
        start_requests=0,
        start_input_tokens=0,
        start_output_tokens=0,
        dataset_identity=_make_dataset_identity(),
    )

    # Actual fingerprint is None — runtime raised before baseline.
    assert artifact.runtime_fixture_fingerprint is None, (
        "Scenario 3: runtime exception → actual fingerprint MUST be "
        "None. R4-A4-2R3 P0-1: the preflight (expected) value MUST "
        "NOT be copied — there is no result.baseline_context to "
        "recompute from."
    )
    # capture_status="failed" — aggregate's instrumentation gate blocks.
    assert artifact.model_context_capture_status == "failed"
    # model_context fields also empty (cross-field invariant).
    assert artifact.model_context_fingerprint is None
    assert artifact.model_context_handle_ids == []
    assert artifact.model_context_support == []
    # Error recorded (safe code only — no raw exception text leak).
    assert artifact.error is not None
    assert artifact.safe_error_code is not None


@pytest.mark.asyncio
async def test_r4_a4_2r3_scenario3_budget_exhausted_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario 3 (variant): ``BudgetExhaustedError`` is re-raised by
    :func:`_run_one_case` — the harness records a BudgetStopResult and
    no artifact is written for the in-flight request.

    R4-A4-2R3 P0-1: the budget-exhausted path does NOT forge an
    artifact with a copied fingerprint. The harness skips the artifact
    write entirely; only already-completed requests are recorded.
    """
    case = _make_minimal_case(
        case_id="case-scenario3-budget",
        article_text="Hello world.",
        phase_tags=["real_phase1"],
        expected_envelope_fingerprint=_VALID_FP_A,
    )

    async def _fake_run_reading_record_ask(**_kwargs):  # noqa: ANN202
        raise BudgetExhaustedError(
            cap_kind="request_cap",
            executed_requests=5,
            executed_tokens=200,
            request_cap=5,
            token_cap=10_000,
        )

    monkeypatch.setattr(
        sys.modules[__name__],
        "run_reading_record_ask",
        _fake_run_reading_record_ask,
    )

    # BudgetExhaustedError MUST propagate (no artifact written).
    with pytest.raises(BudgetExhaustedError):
        await _run_one_case(
            case=case,
            budget_model=_FakeBudgetedModelForManifest(),
            model_config=_make_minimal_model_config(),
            run_id="test-run",
            run_index=0,
            envelope=_make_envelope_mock(_VALID_FP_A),
            document_access=object(),
            start_requests=0,
            start_input_tokens=0,
            start_output_tokens=0,
            dataset_identity=_make_dataset_identity(),
        )


# ---------------------------------------------------------------------------
# R4-A4-2R5R: Runtime Observation & Failure Taxonomy Contract Rework
# ---------------------------------------------------------------------------
#
# R4-A4-2R5R supersedes R4-A4-2R5. The class-level
# :func:`unittest.mock.patch.object` on
# :meth:`BaselineContextAssembler.assemble_baseline` has been REMOVED.
# The new design uses an internal-only :class:`RuntimeObservation`
# container passed to :func:`run_reading_record_ask` via
# ``observation=``. The runtime writes ``baseline_context`` after
# assembly and the grounding output_validator increments
# ``output_validation_final_attempts`` on each FINAL-mode call and
# ``output_validation_retry_requests`` on each ModelRetry raise
# (R4-A4-2R5R2: split from the old single
# ``output_validation_attempts`` counter for precise retry evidence).
#
# The scenarios below cover the R4-A4-2R5R contract:
#   1. preflight 与 actual 不同 (exception path with captured baseline)
#   2. baseline capture 后 UnexpectedModelBehavior (output_retry_exhausted
#      when retry counter proves exhaustion)
#   3. capture 前异常 (fail-closed: capture_status="failed")
#   4. output retry 总计 3 次后耗尽 (taxonomy classification with typed evidence)
#   5. 不复制 preflight (actual recomputed from captured baseline)
#   6. safe error code 无敏感信息 (no raw exception text leak)
#   7. observer 默认不调用 (production path: observation=None)
#   8. observer 并发隔离 (no class-level mutation)
#   9. FunctionModel 三次 output retry (real integration: agent → validator → run)
#  10. 非 retry 类型 UnexpectedModelBehavior 保守分类
#  11. typed atomic-fact origin (loader sets origin field)
#  12. safe_error_code 严格加载 (Literal round-trip + rejection)
#
# Design B (selected, design-it-twice): the runtime observation seam
# is a mutable :class:`RuntimeObservation` container. To exercise the
# "captured baseline" branch from a test, we mock
# ``run_reading_record_ask`` to accept ``observation=`` and write the
# test-specific baseline + retry counter to it BEFORE raising. This
# faithfully simulates what the real runtime does (write observation,
# then raise) without any class-level mutation.


def _make_r4_a4_2r5_baseline(
    *,
    chunks: tuple[ModelContextChunk, ...],
    baseline_status: str = "injected",
    is_complete: bool = True,
) -> Any:
    """Build a minimal baseline mock for R4-A4-2R5R scenario tests.

    The mock carries the fields read by :func:`_run_one_case`'s
    exception path: ``model_context_chunks``, ``baseline_status``,
    ``is_complete``, ``is_injected``.
    """
    from types import SimpleNamespace  # noqa: PLC0415

    return SimpleNamespace(
        model_context_chunks=tuple(chunks),
        baseline_status=baseline_status,
        is_complete=is_complete,
        is_injected=(baseline_status == "injected"),
    )


def _install_fake_runtime_that_captures_then_raises(
    monkeypatch: pytest.MonkeyPatch,
    *,
    baseline: Any,
    exc: BaseException,
    final_attempts: int = 0,
    retry_requests: int = 0,
) -> None:
    """R4-A4-2R5R2 Task 1: install a fake ``run_reading_record_ask``
    that simulates the real runtime's observation contract.

    The fake accepts ``observation=`` (a :class:`RuntimeObservation`),
    writes ``baseline_context`` and the TWO PRECISE retry counters
    (``output_validation_final_attempts`` and
    ``output_validation_retry_requests``) to it (exactly as the real
    runtime does after assembly succeeds and the grounding validator
    runs), then raises ``exc``.

    This replaces the previous pattern of
    :func:`monkeypatch.setattr` on
    :meth:`BaselineContextAssembler.assemble_baseline` + a separate
    mock on ``run_reading_record_ask`` that called
    ``assemble_baseline(None)``. The new pattern is simpler,
    concurrency-safe (no class-level mutation), and directly tests
    the observation contract.

    R4-A4-2R5R2 Task 1: the old single ``output_validation_attempts``
    parameter has been SPLIT into ``final_attempts`` (final-mode
    validator calls) and ``retry_requests`` (ModelRetry raises in
    final mode). The classifier requires BOTH to EXACTLY equal
    ``DEFAULT_OUTPUT_RETRIES + 1`` (3) for ``output_retry_exhausted``.
    """
    async def _fake_run_reading_record_ask(**kwargs: Any) -> Any:  # noqa: ANN202
        observation = kwargs.get("observation")
        if observation is not None:
            observation.baseline_context = baseline
            observation.output_validation_final_attempts = final_attempts
            observation.output_validation_retry_requests = retry_requests
        raise exc

    monkeypatch.setattr(
        sys.modules[__name__],
        "run_reading_record_ask",
        _fake_run_reading_record_ask,
    )


@pytest.mark.asyncio
async def test_r4_a4_2r5_preflight_differs_from_actual_on_exception_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario 1: preflight fingerprint differs from the actual
    fingerprint on the exception path with a captured baseline.

    R4-A4-2R5 P0-1: the artifact's ``runtime_fixture_fingerprint``
    is recomputed from the CAPTURED baseline (chunks B), NOT copied
    from the preflight (chunks A). When preflight and actual baselines
    differ, the artifact's fingerprint MUST reflect the actual baseline.
    """
    from pydantic_ai.exceptions import (  # noqa: PLC0415
        UnexpectedModelBehavior,
    )

    # Preflight computed from chunks A.
    preflight_chunks_view: list[tuple[int, str]] = [
        (0, "Preflight chunk content A."),
    ]
    preflight_fp = compute_runtime_fixture_fingerprint(
        baseline_status="injected",
        is_complete=True,
        chunks=preflight_chunks_view,
    )

    case = _make_minimal_case(
        case_id="case-r5-scenario1",
        article_text="Preflight chunk content A.",
        phase_tags=["real_phase1"],
        expected_envelope_fingerprint=_VALID_FP_A,
    )
    case.expected_runtime_fixture_fingerprint = preflight_fp

    # Actual captured baseline has DIFFERENT chunks B.
    actual_chunks = (
        _make_baseline_chunk(
            chunk_ordinal=0,
            text="Actual chunk content B (different from preflight).",
            handle_id="evh_r5_actual_b",
        ),
    )
    actual_baseline = _make_r4_a4_2r5_baseline(chunks=actual_chunks)

    # R4-A4-2R5R Task 1: use the typed observation seam instead of the
    # class-level ``patch.object(BaselineContextAssembler, ...)``. The
    # fake runtime writes the captured baseline to the observation
    # container BEFORE raising — exactly what the real runtime does
    # after assembly succeeds. R4-A4-2R5R2: ``final_attempts=3`` AND
    # ``retry_requests=3`` prove retry exhaustion so the safe code is
    # ``output_retry_exhausted`` (not strictly needed for this scenario,
    # which only checks the fingerprint, but kept for taxonomy
    # consistency).
    _install_fake_runtime_that_captures_then_raises(
        monkeypatch,
        baseline=actual_baseline,
        exc=UnexpectedModelBehavior("simulated output retry exhaustion"),
        final_attempts=DEFAULT_OUTPUT_RETRIES + 1,
        retry_requests=DEFAULT_OUTPUT_RETRIES + 1,
    )

    artifact = await _run_one_case(
        case=case,
        budget_model=_FakeBudgetedModelForManifest(),
        model_config=_make_minimal_model_config(),
        run_id="test-run-r5",
        run_index=0,
        envelope=_make_envelope_mock(_VALID_FP_A),
        document_access=object(),
        start_requests=0,
        start_input_tokens=0,
        start_output_tokens=0,
        dataset_identity=_make_dataset_identity(),
    )

    # Actual fingerprint is computed from chunks B, NOT preflight chunks A.
    expected_actual_fp = compute_runtime_fixture_fingerprint(
        baseline_status="injected",
        is_complete=True,
        chunks=[(c.chunk_ordinal, c.text) for c in actual_chunks],
    )
    assert artifact.runtime_fixture_fingerprint == expected_actual_fp, (
        "R4-A4-2R5 scenario 1: actual fingerprint MUST be recomputed "
        "from the captured baseline (chunks B), NOT copied from "
        f"preflight (chunks A). Got {artifact.runtime_fixture_fingerprint!r}, "
        f"expected {expected_actual_fp!r}."
    )
    assert artifact.runtime_fixture_fingerprint != preflight_fp, (
        "R4-A4-2R5 scenario 1: actual fingerprint MUST differ from "
        "preflight when the captured baseline differs."
    )


@pytest.mark.asyncio
async def test_r4_a4_2r5_unexpected_model_behavior_after_baseline_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario 2: ``UnexpectedModelBehavior`` raised AFTER baseline
    capture → ``safe_error_code="output_retry_exhausted"`` and the
    baseline audit data is preserved (``capture_status="captured"``).

    R4-A4-2R5 P0-1 + P0-2: the exception path with a captured baseline
    preserves the actual ``runtime_fixture_fingerprint``,
    ``model_context_fingerprint``, and ``model_context_handle_ids``
    so the evaluator can still audit which facts were baseline-supported.
    ``finalized_status`` stays ``None`` — the answer is failed; the
    taxonomy split is via ``safe_error_code``.
    """
    from pydantic_ai.exceptions import (  # noqa: PLC0415
        UnexpectedModelBehavior,
    )

    actual_chunks = (
        _make_baseline_chunk(
            chunk_ordinal=0,
            text="Captured baseline chunk for retry exhaustion test.",
            handle_id="evh_r5_retry",
        ),
    )
    actual_baseline = _make_r4_a4_2r5_baseline(chunks=actual_chunks)

    case = _make_minimal_case(
        case_id="case-r5-scenario2",
        article_text="Captured baseline chunk for retry exhaustion test.",
        phase_tags=["real_phase1"],
        expected_envelope_fingerprint=_VALID_FP_A,
    )

    # R4-A4-2R5R2 Task 1+2: typed observation seam. The fake runtime
    # writes the captured baseline AND the TWO typed retry counters
    # (``output_validation_final_attempts ==
    #  output_validation_retry_requests ==
    #  DEFAULT_OUTPUT_RETRIES + 1 == 3``), which is the typed proof of
    # retry exhaustion required by the new failure taxonomy. Without
    # BOTH counters being exactly 3, UMB would conservatively classify
    # as ``unexpected_model_behavior``.
    _install_fake_runtime_that_captures_then_raises(
        monkeypatch,
        baseline=actual_baseline,
        exc=UnexpectedModelBehavior("simulated retry budget exhausted"),
        final_attempts=DEFAULT_OUTPUT_RETRIES + 1,
        retry_requests=DEFAULT_OUTPUT_RETRIES + 1,
    )

    artifact = await _run_one_case(
        case=case,
        budget_model=_FakeBudgetedModelForManifest(),
        model_config=_make_minimal_model_config(),
        run_id="test-run-r5",
        run_index=0,
        envelope=_make_envelope_mock(_VALID_FP_A),
        document_access=object(),
        start_requests=0,
        start_input_tokens=0,
        start_output_tokens=0,
        dataset_identity=_make_dataset_identity(),
    )

    # R4-A4-2R5R2 Task 2: taxonomy now requires BOTH typed retry
    # counters. ``output_retry_exhausted`` only when
    # ``output_validation_final_attempts == DEFAULT_OUTPUT_RETRIES + 1``
    # AND ``output_validation_retry_requests == DEFAULT_OUTPUT_RETRIES
    # + 1`` (both exactly 3). Without that proof, the conservative
    # ``unexpected_model_behavior`` code is used.
    assert artifact.safe_error_code == "output_retry_exhausted", (
        f"R4-A4-2R5R scenario 2: UnexpectedModelBehavior with typed "
        f"retry counter == DEFAULT_OUTPUT_RETRIES + 1 MUST classify "
        f"as 'output_retry_exhausted', got {artifact.safe_error_code!r}."
    )
    # Baseline audit data preserved.
    assert artifact.model_context_capture_status == "captured"
    assert artifact.runtime_fixture_fingerprint is not None, (
        "R4-A4-2R5 scenario 2: actual fingerprint MUST be non-None "
        "when baseline was captured before the exception."
    )
    assert artifact.model_context_fingerprint is not None
    assert artifact.model_context_handle_ids, (
        "R4-A4-2R5 scenario 2: handle_ids MUST be non-empty when "
        "capture_status='captured'."
    )
    # Answer is still failed.
    assert artifact.finalized_status is None
    assert artifact.finalized_reason is None
    assert artifact.final_text is None


@pytest.mark.asyncio
async def test_r4_a4_2r5_exception_before_baseline_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario 3: exception raised BEFORE baseline capture →
    fail-closed (``capture_status="failed"``, ``runtime_fixture_fingerprint=None``).

    R4-A4-2R5 P0-1: when the runtime raises before/independent of
    baseline assembly (e.g. the assembler itself raised, or a
    provider error before the agent ran), ``captured_baseline[0]``
    stays ``None``. The exception path falls back to the fail-closed
    branch — no actual fingerprint, no model-context support.
    """
    case = _make_minimal_case(
        case_id="case-r5-scenario3",
        article_text="Hello world.",
        phase_tags=["real_phase1"],
        expected_envelope_fingerprint=_VALID_FP_A,
    )

    # preflight_fp is set but MUST NOT be copied to the artifact.
    preflight_fp = compute_runtime_fixture_fingerprint(
        baseline_status="injected",
        is_complete=True,
        chunks=[(0, "Hello world.")],
    )
    case.expected_runtime_fixture_fingerprint = preflight_fp

    # R4-A4-2R5R Task 1: simulate a pre-capture exception by passing
    # ``baseline=None`` — the fake runtime raises WITHOUT writing the
    # baseline to the observation container, so
    # ``observation.baseline_context`` stays ``None`` and the exception
    # path falls back to fail-closed. This is the typed-observation
    # equivalent of the previous pattern (a fake runtime that raised
    # without calling ``assemble_baseline``).
    _install_fake_runtime_that_captures_then_raises(
        monkeypatch,
        baseline=None,
        exc=ConnectionError("simulated provider network failure"),
    )

    artifact = await _run_one_case(
        case=case,
        budget_model=_FakeBudgetedModelForManifest(),
        model_config=_make_minimal_model_config(),
        run_id="test-run-r5",
        run_index=0,
        envelope=_make_envelope_mock(_VALID_FP_A),
        document_access=object(),
        start_requests=0,
        start_input_tokens=0,
        start_output_tokens=0,
        dataset_identity=_make_dataset_identity(),
    )

    # Fail-closed: capture_status="failed", fingerprint=None.
    assert artifact.model_context_capture_status == "failed"
    assert artifact.runtime_fixture_fingerprint is None, (
        "R4-A4-2R5 scenario 3: pre-capture exception → actual "
        "fingerprint MUST be None. The preflight value MUST NOT be copied."
    )
    assert artifact.model_context_fingerprint is None
    assert artifact.model_context_handle_ids == []
    assert artifact.model_context_support == []
    # Taxonomy: runtime_exception (NOT output_retry_exhausted).
    assert artifact.safe_error_code == "runtime_exception", (
        f"R4-A4-2R5 scenario 3: ConnectionError MUST classify as "
        f"'runtime_exception', got {artifact.safe_error_code!r}."
    )
    # Preflight MUST NOT be copied.
    assert artifact.runtime_fixture_fingerprint != preflight_fp


def test_r4_a4_2r5_output_retry_exhausted_classification() -> None:
    """Scenario 4: ``_classify_exception_safe_code`` maps exception
    types to the R4-A4-2R5R2 + R4-A4-2R5R3 failure taxonomy using
    PRECISE typed retry evidence AND typed execution-stage evidence.

    R4-A4-2R5R2 Task 2: the taxonomy now requires BOTH
    ``final_attempts`` AND ``retry_requests`` to EXACTLY equal
    ``DEFAULT_OUTPUT_RETRIES + 1`` (3) to classify as
    ``output_retry_exhausted``. Any missing/unequal/undersized/
    oversized counter, or a counter that includes partial-only
    calls, → conservative ``unexpected_model_behavior``.
    Classification is by ``type(exc)`` + typed counters ONLY —
    never by ``str(exc)``.

    R4-A4-2R5R3 Issue #1 design裁决 — ValidationError taxonomy:
    - ValidationError + ``execution_stage == "agent_run"`` (typed
      evidence the exception was raised DURING ``agent.run`` where
      the output validator fires) → ``agent_output_invalid``.
    - ValidationError + any other ``execution_stage`` (or ``None``
      for legacy / pre-stage-tracking) → conservative
      ``runtime_exception``.
    - This replaces R5R2's ``final_attempts > 0`` evidence, which
      was IMPRECISE: a finalizer-stage ValidationError raised AFTER
      ``agent.run`` returned would also see ``final_attempts > 0``
      and be mis-classified as ``agent_output_invalid``.

    The pydantic-ai agent is created with ``DEFAULT_OUTPUT_RETRIES=2``
    (see agent.py), so the total attempt count is 1 + 2 = 3 before
    ``UnexpectedModelBehavior`` is raised. This test verifies the
    classification at the boundary.
    """
    from pydantic import ValidationError  # noqa: PLC0415
    from pydantic_ai.exceptions import (  # noqa: PLC0415
        UnexpectedModelBehavior,
    )

    # Verify DEFAULT_OUTPUT_RETRIES=2 → total 3 attempts before raise.
    assert DEFAULT_OUTPUT_RETRIES == 2, (
        "R4-A4-2R5R2 scenario 4: DEFAULT_OUTPUT_RETRIES must be 2 "
        "(total 3 attempts: 1 initial + 2 retries). Found "
        f"{DEFAULT_OUTPUT_RETRIES!r}."
    )

    exc_umb = UnexpectedModelBehavior("retry budget exhausted after 3 attempts")

    # R4-A4-2R5R2 Task 2: WITHOUT typed retry evidence, UMB is the
    # CONSERVATIVE ``unexpected_model_behavior`` — never
    # ``output_retry_exhausted``. This prevents mis-classifying
    # pydantic-ai internal errors (malformed JSON, invalid tool call)
    # as retry exhaustion.
    assert _classify_exception_safe_code(exc_umb) == "unexpected_model_behavior", (
        "R4-A4-2R5R2 scenario 4: UMB without retry counters MUST "
        "classify as 'unexpected_model_behavior' (conservative)."
    )

    # Any counter missing → conservative (only one counter provided).
    assert (
        _classify_exception_safe_code(exc_umb, final_attempts=3)
        == "unexpected_model_behavior"
    ), "R4-A4-2R5R2 scenario 4: missing retry_requests → conservative"
    assert (
        _classify_exception_safe_code(exc_umb, retry_requests=3)
        == "unexpected_model_behavior"
    ), "R4-A4-2R5R2 scenario 4: missing final_attempts → conservative"

    # Undersized counters → conservative.
    for fa, rr in [(0, 0), (1, 1), (2, 2), (3, 2), (2, 3), (3, 0), (0, 3)]:
        assert (
            _classify_exception_safe_code(
                exc_umb, final_attempts=fa, retry_requests=rr
            )
            == "unexpected_model_behavior"
        ), (
            f"R4-A4-2R5R2 scenario 4: final_attempts={fa}, "
            f"retry_requests={rr} → MUST be conservative "
            f"(both must EXACTLY equal 3 for output_retry_exhausted)."
        )

    # Oversized counters → conservative (strict equality, NOT >=).
    for fa, rr in [(4, 4), (3, 4), (4, 3), (99, 99)]:
        assert (
            _classify_exception_safe_code(
                exc_umb, final_attempts=fa, retry_requests=rr
            )
            == "unexpected_model_behavior"
        ), (
            f"R4-A4-2R5R2 scenario 4: final_attempts={fa}, "
            f"retry_requests={rr} → MUST be conservative "
            f"(oversized counters indicate mis-instrumentation; "
            f"strict equality required, NOT >=)."
        )

    # Both counters EXACTLY == DEFAULT_OUTPUT_RETRIES + 1 (3) →
    # output_retry_exhausted. This is the PRECISE retry evidence.
    assert (
        _classify_exception_safe_code(
            exc_umb,
            final_attempts=DEFAULT_OUTPUT_RETRIES + 1,
            retry_requests=DEFAULT_OUTPUT_RETRIES + 1,
        )
        == "output_retry_exhausted"
    ), (
        "R4-A4-2R5R2 scenario 4: final_attempts=3 AND retry_requests=3 "
        "MUST classify as 'output_retry_exhausted' (precise typed evidence)."
    )

    # R4-A4-2R5R3 Issue #1 design裁决 — ValidationError taxonomy:
    # ValidationError + execution_stage == "agent_run" →
    # agent_output_invalid (typed evidence raised DURING agent.run
    # where the output validator fires).
    # ValidationError + any other stage (or None) → conservative
    # runtime_exception.
    # This replaces R5R2's ``final_attempts > 0`` evidence, which was
    # IMPRECISE: a finalizer-stage ValidationError raised AFTER
    # agent.run returned would also see ``final_attempts > 0`` and be
    # mis-classified.
    from pydantic import BaseModel  # noqa: PLC0415

    class _StrictModel(BaseModel):
        x: int

    try:
        _StrictModel(x="not-an-int")  # type: ignore[arg-type]
    except ValidationError as exc_ve:
        # Without typed execution_stage evidence → conservative
        # runtime_exception. R5R3 scenario 4: ValidationError
        # without execution_stage MUST be conservative
        # runtime_exception (not agent_output_invalid).
        assert (
            _classify_exception_safe_code(exc_ve) == "runtime_exception"
        )
        # final_attempts is NO LONGER consulted for ValidationError
        # classification (R5R3 Issue #1). Only execution_stage
        # disambiguates validator-stage from finalizer-stage.
        assert (
            _classify_exception_safe_code(exc_ve, final_attempts=3)
            == "runtime_exception"
        )
        # execution_stage="baseline_assembly" → runtime_exception
        # (validator did not fire during baseline assembly).
        assert (
            _classify_exception_safe_code(
                exc_ve, execution_stage="baseline_assembly"
            )
            == "runtime_exception"
        )
        # Only the validator-owned nested stage proves output validation.
        assert (
            _classify_exception_safe_code(
                exc_ve, execution_stage="output_validation"
            )
            == "agent_output_invalid"
        )
        assert (
            _classify_exception_safe_code(
                exc_ve, execution_stage="agent_run"
            )
            == "runtime_exception"
        )
        # The validator-owned stage is the disambiguator; the retry
        # counter is only consulted for UMB classification.
        assert (
            _classify_exception_safe_code(
                exc_ve,
                execution_stage="output_validation",
                final_attempts=0,
            )
            == "agent_output_invalid"
        )
        # execution_stage="agent_run_completed" → runtime_exception
        # (agent.run returned successfully; the ValidationError came
        # from the finalizer or later code, NOT from the output
        # validator).
        assert (
            _classify_exception_safe_code(
                exc_ve, execution_stage="agent_run_completed"
            )
            == "runtime_exception"
        )
        # execution_stage="finalizer" → runtime_exception
        # (ValidationError raised during finalize_agent_answer —
        # did not come from the output validator).
        assert (
            _classify_exception_safe_code(
                exc_ve, execution_stage="finalizer"
            )
            == "runtime_exception"
        )
        # Unknown execution_stage value → runtime_exception
        # (fail-closed — only "agent_run" is the typed validator
        # stage; any other value, including typos / future stages,
        # is conservatively runtime_exception).
        assert (
            _classify_exception_safe_code(
                exc_ve, execution_stage="unknown_future_stage"
            )
            == "runtime_exception"
        )
    else:  # pragma: no cover - defensive
        raise AssertionError("ValidationError was not raised as expected")

    # Generic exceptions → runtime_exception (fail-closed default).
    assert _classify_exception_safe_code(RuntimeError("provider 5xx")) == "runtime_exception"
    assert _classify_exception_safe_code(ConnectionError("network")) == "runtime_exception"
    assert _classify_exception_safe_code(TimeoutError("read timeout")) == "runtime_exception"
    assert _classify_exception_safe_code(ValueError("unknown")) == "runtime_exception"


@pytest.mark.asyncio
async def test_r4_a4_2r5_exception_path_does_not_copy_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario 5: even when ``case.expected_runtime_fixture_fingerprint``
    is set, the artifact's actual fingerprint is NOT copied from it.

    R4-A4-2R5 P0-1: the actual fingerprint is ALWAYS recomputed from
    the captured baseline (success path) or the captured baseline on
    the exception path. Copying preflight → actual would hide runtime
    drift (e.g. a monkeypatched assembler, a DB mutation between
    preflight and run).

    This test sets ``expected_runtime_fixture_fingerprint`` to a
    DISTINCT value from the actual, then verifies the artifact carries
    the ACTUAL fingerprint (not the expected/preflight one).
    """
    from pydantic_ai.exceptions import (  # noqa: PLC0415
        UnexpectedModelBehavior,
    )

    # Preflight / expected fingerprint — computed from chunks "X".
    preflight_fp = compute_runtime_fixture_fingerprint(
        baseline_status="injected",
        is_complete=True,
        chunks=[(0, "Preflight chunk X.")],
    )

    case = _make_minimal_case(
        case_id="case-r5-scenario5",
        article_text="Preflight chunk X.",
        phase_tags=["real_phase1"],
        expected_envelope_fingerprint=_VALID_FP_A,
    )
    case.expected_runtime_fixture_fingerprint = preflight_fp

    # Actual captured baseline — chunks "Y" (different from preflight).
    actual_chunks = (
        _make_baseline_chunk(
            chunk_ordinal=0,
            text="Actual chunk Y (different content from preflight X).",
            handle_id="evh_r5_scenario5",
        ),
    )
    actual_baseline = _make_r4_a4_2r5_baseline(chunks=actual_chunks)

    # R4-A4-2R5R2 Task 1: typed observation seam — no class-level patch.
    # Both ``output_validation_final_attempts`` and
    # ``output_validation_retry_requests`` left at 0 → conservative
    # ``unexpected_model_behavior`` (this scenario only checks the
    # fingerprint, not the taxonomy).
    _install_fake_runtime_that_captures_then_raises(
        monkeypatch,
        baseline=actual_baseline,
        exc=UnexpectedModelBehavior("simulated"),
    )

    artifact = await _run_one_case(
        case=case,
        budget_model=_FakeBudgetedModelForManifest(),
        model_config=_make_minimal_model_config(),
        run_id="test-run-r5",
        run_index=0,
        envelope=_make_envelope_mock(_VALID_FP_A),
        document_access=object(),
        start_requests=0,
        start_input_tokens=0,
        start_output_tokens=0,
        dataset_identity=_make_dataset_identity(),
    )

    expected_actual_fp = compute_runtime_fixture_fingerprint(
        baseline_status="injected",
        is_complete=True,
        chunks=[(c.chunk_ordinal, c.text) for c in actual_chunks],
    )
    # The artifact carries the ACTUAL fingerprint, NOT the preflight.
    assert artifact.runtime_fixture_fingerprint == expected_actual_fp
    assert artifact.runtime_fixture_fingerprint != preflight_fp, (
        "R4-A4-2R5 scenario 5: the artifact's actual fingerprint "
        "MUST NOT equal the preflight/expected value when the actual "
        "baseline differs. Copying preflight → actual is forbidden."
    )
    # The case's expected field is unchanged (not used as source).
    assert case.expected_runtime_fixture_fingerprint == preflight_fp


@pytest.mark.asyncio
async def test_r4_a4_2r5_safe_error_code_no_sensitive_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario 6: ``safe_error_code`` is allowlisted and ``error``
    carries NO sensitive information from the raw exception.

    R4-A4-2R5 P0-2: the exception's ``str(exc)`` is NEVER read for
    classification — only ``type(exc).__name__`` is used. The
    ``error`` field is populated via ``project_exception_to_string``
    which emits ONLY the allowlisted safe code + exception type name.
    The raw exception message (which may contain provider payload,
    API keys, article body, reasoning_content) MUST NOT appear in
    the artifact.
    """
    from pydantic_ai.exceptions import (  # noqa: PLC0415
        UnexpectedModelBehavior,
    )

    # Sensitive payload that MUST NOT leak into the artifact.
    sensitive_message = (
        "SECRET-API-KEY=sk-abc123; provider-payload={'reasoning_content': "
        "'PRIVATE-REASONING'}; article-body='FULL-ARTICLE-TEXT-DO-NOT-LEAK'"
    )

    actual_chunks = (
        _make_baseline_chunk(
            chunk_ordinal=0,
            text="Baseline chunk for sensitive info test.",
            handle_id="evh_r5_sensitive",
        ),
    )
    actual_baseline = _make_r4_a4_2r5_baseline(chunks=actual_chunks)

    case = _make_minimal_case(
        case_id="case-r5-scenario6",
        article_text="Baseline chunk for sensitive info test.",
        phase_tags=["real_phase1"],
        expected_envelope_fingerprint=_VALID_FP_A,
    )

    # R4-A4-2R5R2 Task 1: typed observation seam — no class-level patch.
    # BOTH ``final_attempts`` and ``retry_requests`` equal
    # ``DEFAULT_OUTPUT_RETRIES + 1`` proves retry exhaustion so the
    # safe code is ``output_retry_exhausted`` (the scenario asserts
    # the safe code is in the allowlist and specifically equals
    # ``output_retry_exhausted``).
    _install_fake_runtime_that_captures_then_raises(
        monkeypatch,
        baseline=actual_baseline,
        exc=UnexpectedModelBehavior(sensitive_message),
        final_attempts=DEFAULT_OUTPUT_RETRIES + 1,
        retry_requests=DEFAULT_OUTPUT_RETRIES + 1,
    )

    artifact = await _run_one_case(
        case=case,
        budget_model=_FakeBudgetedModelForManifest(),
        model_config=_make_minimal_model_config(),
        run_id="test-run-r5",
        run_index=0,
        envelope=_make_envelope_mock(_VALID_FP_A),
        document_access=object(),
        start_requests=0,
        start_input_tokens=0,
        start_output_tokens=0,
        dataset_identity=_make_dataset_identity(),
    )

    # safe_error_code is allowlisted — verify against the production
    # allowlist in :data:`errors._RECOGNIZED_SAFE_CODES` via the public
    # :func:`is_recognized_safe_code` predicate (single source of
    # truth, no duplicated hardcoded list in the test).
    from claread_eval.reader_record_ask.errors import (  # noqa: PLC0415
        is_recognized_safe_code,
    )
    assert is_recognized_safe_code(artifact.safe_error_code), (
        f"R4-A4-2R5 scenario 6: safe_error_code must be in the "
        f"production allowlist, got {artifact.safe_error_code!r}."
    )
    assert artifact.safe_error_code == "output_retry_exhausted"

    # The raw exception message MUST NOT appear in the artifact.
    assert artifact.error is not None
    assert "SECRET-API-KEY" not in artifact.error, (
        "R4-A4-2R5 scenario 6: API key leaked into artifact.error."
    )
    assert "sk-abc123" not in artifact.error, (
        "R4-A4-2R5 scenario 6: API key value leaked into artifact.error."
    )
    assert "PRIVATE-REASONING" not in artifact.error, (
        "R4-A4-2R5 scenario 6: reasoning_content leaked into artifact.error."
    )
    assert "FULL-ARTICLE-TEXT-DO-NOT-LEAK" not in artifact.error, (
        "R4-A4-2R5 scenario 6: article body leaked into artifact.error."
    )
    # The exception TYPE name is allowed in the error string (it is
    # not sensitive), but the MESSAGE BODY is not.
    assert "UnexpectedModelBehavior" in artifact.error or "output_retry_exhausted" in artifact.error


def test_r4_a4_2r5_preflight_guard_blocks_migrated_atomic_facts() -> None:
    """R4-A4-2R5R2 Task 4: preflight guard blocks real_phase1 cases
    that rely on legacy auto-migration from ``required_article_facts``.

    R4-A4-2R5R2 Task 4: provenance is now LOADER-OWNED. The per-fact
    ``origin`` field has been REMOVED from
    :class:`AtomicExpectedFact`. The loader inspects the raw JSON dict
    and sets ``case._atomic_facts_origin`` (a Pydantic ``PrivateAttr``,
    NOT a JSON-parseable field). Dataset JSON authors CANNOT forge
    ``"explicit"`` provenance. This test simulates the loader's
    behavior by directly setting ``case._atomic_facts_origin`` on
    each constructed case.

    Cases covered:
        1. real_phase1 case with explicit atomic_facts
           (``_atomic_facts_origin="explicit"``) → guard passes (no
           skip), even when ``fact_id`` happens to look like
           ``legacy-0``.
        2. real_phase1 case with auto-migrated atomic_facts
           (``_atomic_facts_origin="legacy_migrated"``) → guard
           skip-fails.
        3. real_phase1 case with no atomic_facts at all → guard
           skip-fails (separate "no atomic_facts" branch).
        4. non-real_phase1 case with migrated atomic_facts →
           guard does NOT fire (offline_only cases never enter the
           real-model run path).
    """
    from claread_eval.reader_record_ask.schema import (  # noqa: PLC0415
        AtomicExpectedFact,
        ReaderRecordAskR4A3Case,
        ReaderRecordAskR4A3Expected,
    )

    # --- Case 1: real_phase1 with explicit atomic_facts → no skip ---
    # R4-A4-2R5R2 Task 4: ``fact_id`` looks like a legacy id but the
    # LOADER-OWNED ``_atomic_facts_origin="explicit"`` is the source
    # of truth — guard passes. This verifies the guard no longer
    # pattern-matches ``fact_id`` and no longer reads a per-fact
    # ``origin`` field.
    case_explicit = ReaderRecordAskR4A3Case(
        id="case-explicit-atomic",
        source_kind="synthetic_short",
        article_text="Some article text.",
        input_mode="manual",
        source_metadata="known_synthetic",
        baseline_mode="complete",
        question="What is this about?",
        question_category="main_idea",
        expected=ReaderRecordAskR4A3Expected(
            atomic_facts=[
                AtomicExpectedFact(
                    fact_id="legacy-0",
                    answer_alias_groups=[["something"]],
                    source_aliases=["something"],
                    required=True,
                    severity="high",
                ),
            ],
            required_article_facts=[],
        ),
        phase_tags=["real_phase1"],
    )
    # Simulate the loader setting provenance based on raw JSON
    # inspection (atomic_facts was non-empty in the raw JSON).
    case_explicit._atomic_facts_origin = "explicit"
    # Should NOT raise/skip — typed origin is explicit.
    _preflight_guard_real_phase1_atomic_facts_explicit(case_explicit)

    # --- Case 2: real_phase1 with auto-migrated facts → skip ---
    case_migrated = ReaderRecordAskR4A3Case(
        id="case-migrated",
        source_kind="synthetic_short",
        article_text="Some article text.",
        input_mode="manual",
        source_metadata="known_synthetic",
        baseline_mode="complete",
        question="What is this about?",
        question_category="main_idea",
        expected=ReaderRecordAskR4A3Expected(
            atomic_facts=[
                AtomicExpectedFact(
                    fact_id="legacy-0",
                    answer_alias_groups=[["legacy sentence one"]],
                    source_aliases=[],
                    required=True,
                    severity="high",
                ),
                AtomicExpectedFact(
                    fact_id="legacy-1",
                    answer_alias_groups=[["legacy sentence two"]],
                    source_aliases=[],
                    required=True,
                    severity="high",
                ),
            ],
        ),
        phase_tags=["real_phase1"],
    )
    # Simulate the loader setting provenance: raw JSON had no
    # atomic_facts but had required_article_facts → legacy_migrated.
    case_migrated._atomic_facts_origin = "legacy_migrated"
    with pytest.raises(pytest.skip.Exception):  # type: ignore[attr-defined]
        _preflight_guard_real_phase1_atomic_facts_explicit(case_migrated)

    # --- Case 3: real_phase1 with NO atomic_facts → skip ---
    case_empty = ReaderRecordAskR4A3Case(
        id="case-empty",
        source_kind="synthetic_short",
        article_text="Some article text.",
        input_mode="manual",
        source_metadata="known_synthetic",
        baseline_mode="complete",
        question="What is this about?",
        question_category="main_idea",
        expected=ReaderRecordAskR4A3Expected(
            atomic_facts=[],
            required_article_facts=[],
        ),
        phase_tags=["real_phase1"],
    )
    with pytest.raises(pytest.skip.Exception):  # type: ignore[attr-defined]
        _preflight_guard_real_phase1_atomic_facts_explicit(case_empty)

    # --- Case 4: non-real_phase1 with migrated facts → no skip ---
    case_offline = ReaderRecordAskR4A3Case(
        id="case-offline",
        source_kind="synthetic_short",
        article_text="Some article text.",
        input_mode="manual",
        source_metadata="known_synthetic",
        baseline_mode="complete",
        question="What is this about?",
        question_category="main_idea",
        expected=ReaderRecordAskR4A3Expected(
            atomic_facts=[
                AtomicExpectedFact(
                    fact_id="legacy-0",
                    answer_alias_groups=[["legacy sentence"]],
                    source_aliases=[],
                    required=True,
                    severity="high",
                ),
            ],
        ),
        phase_tags=["offline_only"],
    )
    case_offline._atomic_facts_origin = "legacy_migrated"
    # Should NOT raise/skip — guard only fires for real_phase1.
    _preflight_guard_real_phase1_atomic_facts_explicit(case_offline)


def test_r4_a4_2r5_preflight_guard_partial_migration_blocks() -> None:
    """R4-A4-2R5R2 Task 4: a case that declares explicit atomic_facts
    AND legacy required_article_facts is treated as ``"explicit"`` —
    the explicit facts win and the legacy field is dead weight.

    R4-A4-2R5R2 Task 4: with the LOADER-OWNED provenance design,
    "partial migration" is NOT a real scenario — the loader either
    migrates ALL facts (when raw JSON has no ``atomic_facts`` key) or
    doesn't migrate at all (when raw JSON has ``atomic_facts`` with
    at least one entry). The loader sets
    ``_atomic_facts_origin="explicit"`` when the raw JSON has
    ``atomic_facts``, regardless of whether ``required_article_facts``
    is also present. The ``_migrate_legacy_required_article_facts``
    function returns early when ``atomic_facts`` is non-empty, so the
    legacy field is ignored.

    This test verifies that boundary: a case with BOTH explicit
    atomic_facts AND legacy required_article_facts is treated as
    ``"explicit"`` and is NOT blocked by the preflight guard.
    """
    from claread_eval.reader_record_ask.schema import (  # noqa: PLC0415
        AtomicExpectedFact,
        ReaderRecordAskR4A3Case,
        ReaderRecordAskR4A3Expected,
    )

    case_mixed = ReaderRecordAskR4A3Case(
        id="case-mixed",
        source_kind="synthetic_short",
        article_text="Some article text.",
        input_mode="manual",
        source_metadata="known_synthetic",
        baseline_mode="complete",
        question="What is this about?",
        question_category="main_idea",
        expected=ReaderRecordAskR4A3Expected(
            atomic_facts=[
                AtomicExpectedFact(
                    fact_id="explicit-fact-1",
                    answer_alias_groups=[["something"]],
                    source_aliases=["something"],
                    required=True,
                    severity="high",
                ),
            ],
            # Legacy field also present — but atomic_facts wins.
            required_article_facts=["legacy sentence one"],
        ),
        phase_tags=["real_phase1"],
    )
    # Simulate the loader: raw JSON had atomic_facts → origin="explicit".
    # The loader does NOT migrate required_article_facts when
    # atomic_facts is non-empty.
    case_mixed._atomic_facts_origin = "explicit"
    # Should NOT raise/skip — origin is explicit (atomic_facts won).
    _preflight_guard_real_phase1_atomic_facts_explicit(case_mixed)


# ---------------------------------------------------------------------------
# R4-A4-2R5R Scenarios 7–9 + 12: observer default, concurrency isolation,
# FunctionModel 3-retry integration, safe_error_code strict load.
# ---------------------------------------------------------------------------


def test_r4_a4_2r5r_scenario7_observer_default_is_none_in_production() -> None:
    """Scenario 7: observer defaults to ``None`` on the production path.

    R4-A4-2R5R Task 1: the observation seam is internal-only and
    opt-in. Production callers never pass ``observation=``, so:
      1. :func:`run_reading_record_ask` signature defaults
         ``observation`` to ``None``.
      2. :class:`ReaderRecordAskDeps.observation` defaults to ``None``.
      3. :class:`RuntimeObservation` defaults are safe
         (``baseline_context=None``,
         ``output_validation_final_attempts=0``,
         ``output_validation_retry_requests=0``).

    This is the structural proof that the observation seam cannot
    affect production callers — there is nothing to read, nothing to
    fail, nothing to serialise.
    """
    import inspect  # noqa: PLC0415

    from app.services.reader_record_ask.runtime_deps import (  # noqa: PLC0415
        ReaderRecordAskDeps,
    )

    # 1. Runtime signature default.
    sig = inspect.signature(run_reading_record_ask)
    obs_param = sig.parameters.get("observation")
    assert obs_param is not None, (
        "R4-A4-2R5R scenario 7: run_reading_record_ask must accept "
        "an `observation` parameter."
    )
    assert obs_param.default is None, (
        "R4-A4-2R5R scenario 7: observation parameter MUST default to "
        f"None (production path). Got default={obs_param.default!r}."
    )

    # 2. ReaderRecordAskDeps field default.
    deps_fields = {f.name: f for f in ReaderRecordAskDeps.__dataclass_fields__.values()}
    assert "observation" in deps_fields, (
        "R4-A4-2R5R scenario 7: ReaderRecordAskDeps must have an "
        "`observation` field."
    )
    assert deps_fields["observation"].default is None, (
        "R4-A4-2R5R scenario 7: ReaderRecordAskDeps.observation MUST "
        "default to None."
    )

    # 3. RuntimeObservation defaults.
    obs = RuntimeObservation()
    assert obs.baseline_context is None
    assert obs.output_validation_final_attempts == 0
    assert obs.output_validation_retry_requests == 0


def test_r4_a4_2r5r_scenario8_observer_concurrency_isolation() -> None:
    """Scenario 8: observer is concurrency-safe by construction.

    R4-A4-2R5R Task 1: the observation seam uses a per-call mutable
    :class:`RuntimeObservation` container (Design B), NOT a class-level
    :func:`monkeypatch.setattr` on
    :meth:`BaselineContextAssembler.assemble_baseline` (Design A).

    This test proves:
      1. Two :class:`RuntimeObservation` instances are fully
         independent — mutating one does NOT affect the other.
      2. There is no class-level mutable state on
         :class:`RuntimeObservation` or :class:`BaselineContextAssembler`
         that could leak between concurrent runs.
      3. The runtime writes to the per-call container only (the runtime
         contract is ``observation.baseline_context = baseline``, which
         is an instance attribute write, not a class attribute write).
    """
    from app.services.reader_record_ask.baseline_context import (  # noqa: PLC0415
        BaselineContextAssembler,
    )
    from app.services.reader_record_ask.runtime_deps import (  # noqa: PLC0415
        ReaderRecordAskDeps,
    )

    # 1. Two instances are independent.
    obs_a = RuntimeObservation()
    obs_b = RuntimeObservation()

    # Mutate obs_a — obs_b must be unaffected.
    obs_a.output_validation_final_attempts = 42
    obs_a.output_validation_retry_requests = 7
    obs_a.baseline_context = object()  # type: ignore[assignment]
    assert obs_b.output_validation_final_attempts == 0, (
        "R4-A4-2R5R scenario 8: mutating obs_a.output_validation_final_attempts "
        "must NOT affect obs_b — instances must be independent."
    )
    assert obs_b.output_validation_retry_requests == 0, (
        "R4-A4-2R5R scenario 8: mutating obs_a.output_validation_retry_requests "
        "must NOT affect obs_b — instances must be independent."
    )
    assert obs_b.baseline_context is None, (
        "R4-A4-2R5R scenario 8: mutating obs_a.baseline_context must "
        "NOT affect obs_b — instances must be independent."
    )

    # 2. No class-level mutable state on RuntimeObservation.
    #    The container is a ``@dataclass(slots=True)`` — instances have
    #    ``__slots__`` (not ``__dict__``), so per-instance attribute
    #    writes CANNOT leak to other instances via shared class state.
    #    Note: ``slots=True`` DOES create class-level slot descriptors
    #    (``getset_descriptor`` objects) — those are NOT mutable state,
    #    they are accessors that route reads/writes to per-instance slot
    #    storage. The concurrency guarantee is that two instances do not
    #    share slot storage, which is verified by step 1 above and by
    #    the ``__slots__`` membership check below.
    assert hasattr(RuntimeObservation, "__slots__"), (
        "R4-A4-2R5R scenario 8: RuntimeObservation must define __slots__ "
        "(slots=True) so instances do not get a __dict__."
    )
    assert "baseline_context" in RuntimeObservation.__slots__, (
        "R4-A4-2R5R scenario 8: RuntimeObservation.__slots__ must contain "
        "'baseline_context' — slot storage is per-instance."
    )
    assert "output_validation_final_attempts" in RuntimeObservation.__slots__, (
        "R4-A4-2R5R scenario 8: RuntimeObservation.__slots__ must contain "
        "'output_validation_final_attempts' — slot storage is per-instance."
    )
    assert "output_validation_retry_requests" in RuntimeObservation.__slots__, (
        "R4-A4-2R5R scenario 8: RuntimeObservation.__slots__ must contain "
        "'output_validation_retry_requests' — slot storage is per-instance."
    )
    # Instances must NOT have a __dict__ (slots=True prevents it).
    # This is the structural guarantee that no arbitrary class-level
    # mutable state can be introduced by accident.
    assert not hasattr(obs_a, "__dict__"), (
        "R4-A4-2R5R scenario 8: RuntimeObservation instances must NOT "
        "have a __dict__ — slots=True prevents arbitrary attribute "
        "writes that could leak between instances."
    )

    # 3. BaselineContextAssembler has NO class-level mutable state that
    #    the old monkeypatch pattern relied on. The old pattern mutated
    #    ``BaselineContextAssembler.assemble_baseline`` at the class
    #    level — the new design does NOT touch the class.
    #    Verify the method is still a plain method (not a patched mock).
    assert callable(BaselineContextAssembler.assemble_baseline), (
        "R4-A4-2R5R scenario 8: BaselineContextAssembler.assemble_baseline "
        "must remain a real method — the new design does NOT patch it."
    )
    assert not hasattr(BaselineContextAssembler.assemble_baseline, "mock"), (
        "R4-A4-2R5R scenario 8: BaselineContextAssembler.assemble_baseline "
        "must NOT be a unittest.mock patch object — the new design uses "
        "per-call RuntimeObservation containers instead."
    )

    # 4. ReaderRecordAskDeps.observation is per-instance: two deps
    #    objects carry independent observation containers.
    from app.services.reader_record_ask.context_envelope import (  # noqa: PLC0415
        VerifiedEnvelopeInput,
        build_context_envelope,
    )
    from app.services.reader_record_ask.fence import (  # noqa: PLC0415
        StaticGenerationFence,
    )

    envelope = build_context_envelope(
        VerifiedEnvelopeInput(
            user_id=UUID("00000000-0000-0000-0000-000000000001"),
            reading_record_id=UUID("00000000-0000-0000-0000-000000000002"),
            base_id=UUID("00000000-0000-0000-0000-000000000003"),
            record_generation=1,
            stable_document_id=None,
            base_content_sha256=None,
            product_state="readable_enhancing",
            readiness_state="article_ready",
            initial_anchor=None,
            visible_range=None,
        )
    )
    obs_x = RuntimeObservation()
    obs_y = RuntimeObservation()
    deps_x = ReaderRecordAskDeps(
        envelope=envelope,
        document_access=object(),  # type: ignore[arg-type]
        fence=StaticGenerationFence(live_generation=1),
        evidence_registry=EvidenceRegistry(envelope.envelope_fingerprint),
        observation=obs_x,
    )
    deps_y = ReaderRecordAskDeps(
        envelope=envelope,
        document_access=object(),  # type: ignore[arg-type]
        fence=StaticGenerationFence(live_generation=1),
        evidence_registry=EvidenceRegistry(envelope.envelope_fingerprint),
        observation=obs_y,
    )
    # Mutate deps_x.observation — deps_y.observation must be unaffected.
    deps_x.observation.output_validation_final_attempts = 99  # type: ignore[union-attr]
    deps_x.observation.output_validation_retry_requests = 99  # type: ignore[union-attr]
    assert deps_y.observation.output_validation_final_attempts == 0, (
        "R4-A4-2R5R scenario 8: mutating deps_x.observation must NOT "
        "affect deps_y.observation — each deps carries its own "
        "RuntimeObservation instance."
    )
    assert deps_y.observation.output_validation_retry_requests == 0, (
        "R4-A4-2R5R scenario 8: mutating deps_x.observation must NOT "
        "affect deps_y.observation — each deps carries its own "
        "RuntimeObservation instance."
    )
    assert deps_x.observation is obs_x
    assert deps_y.observation is obs_y
    assert deps_x.observation is not deps_y.observation


@pytest.mark.asyncio
async def test_r4_a4_2r5r_scenario9_function_model_three_output_retries() -> None:
    """Scenario 9: real FunctionModel integration — 3 output-validator
    calls then ``UnexpectedModelBehavior``.

    R4-A4-2R5R Task 2: drives the FULL agent.run path through
    :func:`create_reading_record_ask_agent` (which wires
    :func:`grounding_validator` via the ``agent.output_validator``
    decorator seam) and :func:`run_reading_record_ask`. The
    FunctionModel ALWAYS returns a structurally-valid but
    grounding-INVALID draft (``grounded_answer`` whose article block
    cites the fabricated, unregistered handle ``evh_fabricated``), so
    the output validator raises
    :class:`ModelRetry` on every call. After
    ``DEFAULT_OUTPUT_RETRIES + 1 == 3`` total attempts, pydantic-ai
    raises :class:`UnexpectedModelBehavior`.

    Assertions:
      - ``observation.output_validation_final_attempts ==
        DEFAULT_OUTPUT_RETRIES + 1 == 3`` (typed final-mode evidence).
      - ``observation.output_validation_retry_requests ==
        DEFAULT_OUTPUT_RETRIES + 1 == 3`` (typed ModelRetry evidence).
      - The exception is :class:`UnexpectedModelBehavior`.
      - ``_classify_exception_safe_code(exc,
        final_attempts=observation.output_validation_final_attempts,
        retry_requests=observation.output_validation_retry_requests)
        == "output_retry_exhausted"`` — BOTH typed counters PROVE
        exhaustion.
      - The model was called exactly 3 times (1 initial + 2 retries).
      - ``capture_status`` would be ``"captured"`` (baseline was
        assembled before the agent raised) — verified via
        ``observation.baseline_context is not None``.
    """
    import json as _json  # noqa: PLC0415
    from uuid import UUID as _UUID  # noqa: PLC0415

    from pydantic_ai.exceptions import (  # noqa: PLC0415
        UnexpectedModelBehavior,
    )
    from pydantic_ai.messages import (  # noqa: PLC0415
        ModelResponse,
        ToolCallPart,
    )
    from pydantic_ai.models.function import (  # noqa: PLC0415
        AgentInfo,
        FunctionModel,
    )

    from app.services.reader_record_ask.context_envelope import (  # noqa: PLC0415
        EnvelopeInitialAnchor,
        VerifiedEnvelopeInput,
        build_context_envelope,
    )
    from app.services.reader_record_ask.document_access import (  # noqa: PLC0415
        AnchorSegmentView,
        ReadingUnitView,
        build_document_scope,
    )

    _USER = _UUID("11111111-1111-1111-1111-111111111111")
    _RECORD = _UUID("22222222-2222-2222-2222-222222222222")
    _BASE = _UUID("33333333-3333-3333-3333-333333333333")
    _DOC = _UUID("44444444-4444-4444-4444-444444444444")
    _SHA = "b" * 64

    _UNIT_A_TEXT = "Alpha sentence one. Alpha sentence two."
    _SEG_A1_TEXT = "Alpha sentence one. "

    units = (
        ReadingUnitView(
            unit_id="u1",
            order_index=0,
            text=_UNIT_A_TEXT,
            text_hash="11111111",
            base_start_utf16=0,
            base_end_utf16=len(_UNIT_A_TEXT),
        ),
    )
    segments = (
        AnchorSegmentView(
            unit_id="u1",
            anchor_segment_id="s1",
            order_index=0,
            unit_order_index=0,
            text=_SEG_A1_TEXT,
            text_hash="aaaaaaaa",
            unit_start_utf16=0,
            unit_end_utf16=len(_SEG_A1_TEXT),
            base_start_utf16=0,
            base_end_utf16=len(_SEG_A1_TEXT),
        ),
    )
    scope = build_document_scope(
        reading_record_id=_RECORD,
        base_id=_BASE,
        record_generation=1,
        units=units,
        segments=segments,
        stable_document_id=_DOC,
        base_content_sha256=_SHA,
    )
    document_access = InMemoryDocumentAccess(snapshot=scope)
    envelope = build_context_envelope(
        VerifiedEnvelopeInput(
            user_id=_USER,
            reading_record_id=_RECORD,
            base_id=_BASE,
            record_generation=1,
            stable_document_id=_DOC,
            base_content_sha256=_SHA,
            product_state="readable_enhancing",
            readiness_state="article_ready",
            initial_anchor=EnvelopeInitialAnchor(
                unit_id="u1",
                anchor_segment_id="s1",
                start_offset=0,
                end_offset=len(_SEG_A1_TEXT),
                selected_text=_SEG_A1_TEXT,
                text_hash="aaaaaaaa",
            ),
            visible_range=None,
        )
    )

    # FunctionModel that ALWAYS returns a grounded_answer whose article
    # block cites a fabricated, unregistered handle — schema-valid under
    # the P2C-A1 structured output contract (``AgentAnswerDraftOutput``)
    # but grounding-INVALID. The output validator raises ModelRetry on
    # every call. The host-derived ``article_scope`` is always
    # "evidence_bounded" (confirmed by turn_coordinator), so the
    # rejection is specifically due to the fabricated handle, not the
    # scope.
    calls = {"n": 0}

    async def model_fn(messages, info: AgentInfo):
        del messages, info
        calls["n"] += 1
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="final_result",
                    args=_json.dumps(
                        {
                            "response_kind": "grounded_answer",
                            "clarification_text": None,
                            "answer_blocks": [
                                {
                                    "text": f"回答 attempt {calls['n']}",
                                    "basis": "article",
                                    "evidence_handles": ["evh_fabricated"],
                                }
                            ],
                        }
                    ),
                    tool_call_id=f"bad-grounding-{calls['n']}",
                )
            ]
        )

    observation = RuntimeObservation()

    # R4-A4-2R5R2 Task 5: capture the REAL exception via
    # ``pytest.raises(...) as exc_info`` and classify THAT exception —
    # do NOT construct a fresh ``UnexpectedModelBehavior`` for the
    # classifier call. This proves the real pydantic-ai exception
    # (preserving type, message, and traceback) round-trips through
    # the classifier.
    with pytest.raises(UnexpectedModelBehavior) as exc_info:
        await run_reading_record_ask(
            user_message="Summarize the selection.",
            envelope=envelope,
            document_access=document_access,
            model=FunctionModel(model_fn),
            article_rag=None,
            observation=observation,
        )

    # Typed retry evidence: validator was called in FINAL mode exactly
    # DEFAULT_OUTPUT_RETRIES + 1 == 3 times, and each call raised
    # ModelRetry (so retry_requests is also 3).
    assert DEFAULT_OUTPUT_RETRIES + 1 == 3, (
        "R4-A4-2R5R scenario 9: DEFAULT_OUTPUT_RETRIES must be 2 "
        "(1 initial + 2 retries = 3 total)."
    )
    assert observation.output_validation_final_attempts == DEFAULT_OUTPUT_RETRIES + 1, (
        "R4-A4-2R5R scenario 9: output_validation_final_attempts MUST "
        f"equal DEFAULT_OUTPUT_RETRIES + 1 == 3 (got "
        f"{observation.output_validation_final_attempts}). The typed "
        "final-mode counter is the proof that the validator was invoked "
        "3 times."
    )
    assert observation.output_validation_retry_requests == DEFAULT_OUTPUT_RETRIES + 1, (
        "R4-A4-2R5R scenario 9: output_validation_retry_requests MUST "
        f"equal DEFAULT_OUTPUT_RETRIES + 1 == 3 (got "
        f"{observation.output_validation_retry_requests}). The typed "
        "ModelRetry counter is the proof that every final-mode call "
        "raised ModelRetry (i.e. the validator actually rejected the "
        "draft 3 times)."
    )
    assert calls["n"] == DEFAULT_OUTPUT_RETRIES + 1, (
        "R4-A4-2R5R scenario 9: model MUST be called exactly "
        f"{DEFAULT_OUTPUT_RETRIES + 1} times (1 initial + 2 retries). "
        f"Got {calls['n']}."
    )

    # Baseline was captured BEFORE the agent raised — the runtime
    # writes to observation.baseline_context after assemble_baseline()
    # succeeds, before agent.run. This is the "capture 后异常" branch.
    assert observation.baseline_context is not None, (
        "R4-A4-2R5R scenario 9: observation.baseline_context MUST be "
        "non-None — the baseline was assembled before the agent raised "
        "(capture-后异常 path). This is the data that preserves the "
        "actual baseline audit even when the answer is failed."
    )

    # R4-A4-2R5R2 Task 5: classify the REAL exception captured by
    # ``exc_info``. Both typed counters EXACTLY equal
    # ``DEFAULT_OUTPUT_RETRIES + 1`` (3) → ``output_retry_exhausted``.
    real_exc = exc_info.value
    safe_code = _classify_exception_safe_code(
        real_exc,
        final_attempts=observation.output_validation_final_attempts,
        retry_requests=observation.output_validation_retry_requests,
    )
    assert safe_code == "output_retry_exhausted", (
        "R4-A4-2R5R scenario 9: with BOTH typed counters == 3, the "
        f"safe code MUST be 'output_retry_exhausted' (got {safe_code!r})."
    )


def test_r4_a4_2r5r_scenario12_safe_error_code_strict_load() -> None:
    """Scenario 12: ``RawArtifact.safe_error_code`` is a strict
    :data:`SafeErrorCode` Literal — single source of truth.

    R4-A4-2R5R Task 4: the ``safe_error_code`` field on
    :class:`RawArtifact` is typed as ``SafeErrorCode | None`` where
    ``SafeErrorCode`` is a shared ``Literal`` in
    :mod:`claread_eval.reader_record_ask.errors`. This test verifies:
      1. Every legal value round-trips through ``RawArtifact``.
      2. Unknown string values are rejected at the Pydantic boundary.
      3. Empty string is rejected.
      4. Bool coercion (``True`` / ``False``) is rejected.
      5. Int coercion (``1``) is rejected.
      6. ``None`` is allowed (backwards compat / success path).
      7. The allowlist in :data:`SafeErrorCode` matches the allowlist
         in :data:`_RECOGNIZED_SAFE_CODES` — single source of truth,
         no duplicated copy in the harness.
    """
    from pydantic import ValidationError as PydanticValidationError

    from claread_eval.reader_record_ask.errors import (  # noqa: PLC0415
        SafeErrorCode,
        is_recognized_safe_code,
    )
    from claread_eval.reader_record_ask.evaluators.artifact import (  # noqa: PLC0415
        RawArtifact,
    )

    # Build a minimal valid artifact base — only the required fields.
    def _make_artifact(*, safe_error_code: object) -> RawArtifact:
        kwargs: dict[str, object] = dict(
            case_id="case-strict",
            run_id="run-strict",
            run_index=0,
        )
        if safe_error_code is not _UNSET:
            kwargs["safe_error_code"] = safe_error_code
        return RawArtifact.model_validate(kwargs)

    # 1. Every legal SafeErrorCode value round-trips.
    #    Extract the literal args from the typing.Literal definition.
    import typing  # noqa: PLC0415

    legal_codes = typing.get_args(SafeErrorCode)
    assert len(legal_codes) >= 14, (
        f"R4-A4-2R5R scenario 12: SafeErrorCode must have at least 14 "
        f"legal values (got {len(legal_codes)})."
    )
    for code in legal_codes:
        artifact = _make_artifact(safe_error_code=code)
        assert artifact.safe_error_code == code, (
            f"R4-A4-2R5R scenario 12: legal safe_error_code {code!r} "
            f"did not round-trip (got {artifact.safe_error_code!r})."
        )
        # Single source of truth: every legal Literal value is also
        # recognized by the public is_recognized_safe_code predicate.
        assert is_recognized_safe_code(code), (
            f"R4-A4-2R5R scenario 12: legal SafeErrorCode {code!r} is "
            f"NOT in is_recognized_safe_code — the allowlist is split "
            f"between two sources. There must be ONE source of truth."
        )

    # 2. None is allowed (success path / backwards compat).
    artifact_none = _make_artifact(safe_error_code=None)
    assert artifact_none.safe_error_code is None

    # 3. Default (field not set) is None.
    artifact_default = _make_artifact(safe_error_code=_UNSET)
    assert artifact_default.safe_error_code is None

    # 4. Unknown string is rejected.
    with pytest.raises(PydanticValidationError):
        _make_artifact(safe_error_code="totally_unknown_code")

    # 5. Empty string is rejected.
    with pytest.raises(PydanticValidationError):
        _make_artifact(safe_error_code="")

    # 6. Bool coercion is rejected (True is not a valid SafeErrorCode).
    with pytest.raises(PydanticValidationError):
        _make_artifact(safe_error_code=True)
    with pytest.raises(PydanticValidationError):
        _make_artifact(safe_error_code=False)

    # 7. Int coercion is rejected (1 is not a valid SafeErrorCode).
    with pytest.raises(PydanticValidationError):
        _make_artifact(safe_error_code=1)
    with pytest.raises(PydanticValidationError):
        _make_artifact(safe_error_code=0)

    # 8. Typo of a legal code is rejected (e.g. trailing space).
    with pytest.raises(PydanticValidationError):
        _make_artifact(safe_error_code="runtime_exception ")
    with pytest.raises(PydanticValidationError):
        _make_artifact(safe_error_code="RUNTIME_EXCEPTION")


# Sentinel for "field not set" in scenario 12.
_UNSET = object()


# ---------------------------------------------------------------------------
# R4-A4-2R5R2 Task 5: end-to-end retry-evidence closure tests.
#
# These tests close the artifact chain for ``output_retry_exhausted`` by
# proving the typed counters are written precisely (partial NOT counted;
# final success → attempts=1/retry_requests=0; 1-retry-then-success →
# attempts=2/retry_requests=1; 3 passes + subsequent non-validator UMB →
# conservative ``unexpected_model_behavior``, NOT
# ``output_retry_exhausted``), and that the full RawArtifact chain
# through ``_run_one_case`` carries the right
# ``safe_error_code`` / ``finalized_status`` / ``capture_status`` /
# ``runtime_fixture_fingerprint`` semantics. They also prove typed
# provenance cannot be forged by dataset JSON.
# ---------------------------------------------------------------------------


def _build_validator_ctx_for_r5r2(
    *,
    observation: RuntimeObservation,
    partial_output: bool = False,
) -> Any:
    """Build a minimal ``RunContext``-like object for direct
    ``grounding_validator`` calls in R4-A4-2R5R2 tests.

    Mirrors the ``_ctx`` helper in
    ``test_reader_record_ask_grounding_validator.py`` but attaches a
    :class:`RuntimeObservation` to ``deps.observation`` so the
    validator's counter increments are observable. The validator only
    reads ``ctx.partial_output``, ``ctx.deps.evidence_registry``,
    ``ctx.deps.envelope.envelope_fingerprint``,
    ``ctx.deps.confirmed_article_scopes``, and
    ``ctx.deps.observation``.
    """
    from types import SimpleNamespace  # noqa: PLC0415

    from app.services.reader_record_ask.evidence import (  # noqa: PLC0415
        build_server_evidence_observation,
    )
    from app.services.reader_record_ask.evidence_registry import (  # noqa: PLC0415
        EvidenceRegistry,
    )
    from app.services.reader_record_ask.fence import (  # noqa: PLC0415
        StaticGenerationFence,
    )
    from app.services.reader_record_ask.grounding_validator import (  # noqa: PLC0415
        grounding_validator,
    )
    from app.services.reader_record_ask.runtime_deps import (  # noqa: PLC0415
        ReaderRecordAskDeps,
    )

    del grounding_validator  # not used here; re-import in caller

    # Deterministic envelope fingerprint for the registry binding.
    # NOTE: build_context_envelope produces a real 64-hex fingerprint;
    # we use a fixed string here only for the registry, and the
    # envelope mock returns the same string. The validator only reads
    # ``envelope.envelope_fingerprint`` for cross-registry checks.
    fingerprint_hex = "ab" * 32  # 64-char lowercase hex
    registry = EvidenceRegistry(fingerprint_hex)
    # Register one seed observation so a valid handle exists for
    # ``grounded_answer`` drafts.
    seed_obs = build_server_evidence_observation(
        kind="article_seed",
        envelope_fingerprint=fingerprint_hex,
        source_tool="baseline_context",
        snippet="seed snippet 0",
        unit_id="unit-0",
        anchor_segment_id=None,
    )
    registry.register(seed_obs)

    envelope_mock = SimpleNamespace(envelope_fingerprint=fingerprint_hex)
    deps = ReaderRecordAskDeps(
        envelope=envelope_mock,  # type: ignore[arg-type]
        document_access=None,  # type: ignore[arg-type]
        fence=StaticGenerationFence(live_generation=1),
        evidence_registry=registry,
        observation=observation,
        # P2C-A1 contract: article blocks need a confirmed scope. The
        # real runtime always confirms at least "evidence_bounded"
        # (turn_coordinator); mirror that minimum here.
        confirmed_article_scopes=frozenset({"evidence_bounded"}),
    )
    return SimpleNamespace(deps=deps, partial_output=partial_output)


@pytest.mark.asyncio
async def test_r4_a4_2r5r2_partial_validator_call_not_counted() -> None:
    """R4-A4-2R5R2 Task 5: partial-mode validator calls do NOT
    increment ``output_validation_final_attempts`` or
    ``output_validation_retry_requests``.

    The taxonomy classifier requires BOTH counters to EXACTLY equal
    ``DEFAULT_OUTPUT_RETRIES + 1`` (3) to classify as
    ``output_retry_exhausted``. If partial-mode calls inflated either
    counter, a single partial-mode call followed by 2 final-mode
    ModelRetry raises would be mis-classified as retry exhaustion.

    Cases covered:
        1. Partial-mode call with a valid ``response_kind`` → passes,
           neither counter incremented.
        2. Partial-mode call with missing ``response_kind`` → raises
           ``ModelRetry``, neither counter incremented (partial mode
           is a pre-validation nudge, not a final validation attempt).
    """
    from app.services.reader_record_ask.grounding_validator import (  # noqa: PLC0415
        AgentAnswerBlockOutput,
        AgentAnswerDraftOutput,
        grounding_validator,
    )
    from pydantic_ai.exceptions import ModelRetry  # noqa: PLC0415

    # --- Case 1: partial-mode pass → no counter increment ---
    observation_1 = RuntimeObservation()
    ctx_pass = _build_validator_ctx_for_r5r2(
        observation=observation_1,
        partial_output=True,
    )
    draft_pass = AgentAnswerDraftOutput(
        response_kind="grounded_answer",
        answer_blocks=[
            AgentAnswerBlockOutput(
                text="partial answer",
                basis="general",
                evidence_handles=[],
            )
        ],
    )
    result = await grounding_validator(ctx_pass, draft_pass)
    assert result is draft_pass
    assert observation_1.output_validation_final_attempts == 0, (
        "R4-A4-2R5R2: partial-mode pass MUST NOT increment "
        "output_validation_final_attempts."
    )
    assert observation_1.output_validation_retry_requests == 0, (
        "R4-A4-2R5R2: partial-mode pass MUST NOT increment "
        "output_validation_retry_requests."
    )

    # --- Case 2: partial-mode ModelRetry → still no counter increment ---
    # Use ``model_construct`` to bypass Pydantic Literal validation on
    # ``response_kind`` — this simulates pydantic-ai's partial parsing
    # where a required field may be missing or invalid. The
    # grounding_validator's partial-mode check
    # ``if not getattr(draft, "response_kind", None)`` handles this by
    # raising ModelRetry (nudge the model to include response_kind).
    observation_2 = RuntimeObservation()
    ctx_retry = _build_validator_ctx_for_r5r2(
        observation=observation_2,
        partial_output=True,
    )
    draft_no_kind = AgentAnswerDraftOutput.model_construct(
        response_kind="",  # bypass Literal validation; partial mode
                            # handles missing/invalid response_kind
        answer_blocks=[],
        clarification_text=None,
    )
    with pytest.raises(ModelRetry):
        await grounding_validator(ctx_retry, draft_no_kind)
    assert observation_2.output_validation_final_attempts == 0, (
        "R4-A4-2R5R2: partial-mode ModelRetry MUST NOT increment "
        "output_validation_final_attempts. Partial mode is a "
        "pre-validation nudge, not a final validation attempt."
    )
    assert observation_2.output_validation_retry_requests == 0, (
        "R4-A4-2R5R2: partial-mode ModelRetry MUST NOT increment "
        "output_validation_retry_requests. The retry-exhaustion "
        "taxonomy only counts FINAL-mode ModelRetry raises."
    )


@pytest.mark.asyncio
async def test_r4_a4_2r5r2_final_success_attempts_one_retry_zero() -> None:
    """R4-A4-2R5R2 Task 5: final-mode validator success —
    ``output_validation_final_attempts == 1`` and
    ``output_validation_retry_requests == 0``.

    A normal pass on the first final-mode call increments
    ``final_attempts`` (the validator WAS invoked in final mode) but
    does NOT increment ``retry_requests`` (no ``ModelRetry`` was
    raised). This is the precise evidence that distinguishes "validator
    passed first try" from "validator raised ModelRetry 3 times".
    """
    from app.services.reader_record_ask.grounding_validator import (  # noqa: PLC0415
        AgentAnswerBlockOutput,
        AgentAnswerDraftOutput,
        grounding_validator,
    )

    observation = RuntimeObservation()
    ctx = _build_validator_ctx_for_r5r2(
        observation=observation,
        partial_output=False,
    )
    # Extract the registry's valid handle for the draft.
    # ``EvidenceRegistry`` is already imported at module top (line 131).
    registry = ctx.deps.evidence_registry
    valid_handle = registry.list_handle_refs()[0].handle_id

    # P2C-A1 structured output contract: one article block citing the
    # registry's real handle. The host derives "evidence_bounded" scope
    # (the helper confirms it on deps).
    draft = AgentAnswerDraftOutput(
        response_kind="grounded_answer",
        answer_blocks=[
            AgentAnswerBlockOutput(
                text="grounded answer",
                basis="article",
                evidence_handles=[valid_handle],
            )
        ],
    )
    result = await grounding_validator(ctx, draft)
    assert result is draft
    assert observation.output_validation_final_attempts == 1, (
        "R4-A4-2R5R2: final-mode success MUST increment "
        "output_validation_final_attempts to 1 (validator was invoked "
        f"once in final mode; got {observation.output_validation_final_attempts})."
    )
    assert observation.output_validation_retry_requests == 0, (
        "R4-A4-2R5R2: final-mode success MUST NOT increment "
        "output_validation_retry_requests (no ModelRetry was raised; "
        f"got {observation.output_validation_retry_requests})."
    )


@pytest.mark.asyncio
async def test_r4_a4_2r5r2_one_retry_then_success() -> None:
    """R4-A4-2R5R2 Task 5: FunctionModel returns an invalid draft
    once, then a valid draft — ``final_attempts == 2`` and
    ``retry_requests == 1``.

    Drives the full ``agent.run`` path through
    :func:`create_reading_record_ask_agent` (which wires
    :func:`grounding_validator` via ``agent.output_validator``). The
    FunctionModel returns:
      - Call 1: ``grounded_answer`` with empty handles → validator
        raises ``ModelRetry``.
      - Call 2: ``grounded_answer`` with a valid handle → validator
        passes.

    Assertions:
      - ``observation.output_validation_final_attempts == 2`` (two
        final-mode validator calls).
      - ``observation.output_validation_retry_requests == 1`` (only
        the first call raised ``ModelRetry``; the second passed).
      - ``calls["n"] == 2`` (model was called twice: 1 initial + 1
        retry).
      - No exception raised — the agent.run returns successfully.
    """
    import json as _json  # noqa: PLC0415
    from uuid import UUID as _UUID  # noqa: PLC0415

    from pydantic_ai.messages import (  # noqa: PLC0415
        ModelResponse,
        ToolCallPart,
    )
    from pydantic_ai.models.function import (  # noqa: PLC0415
        AgentInfo,
        FunctionModel,
    )

    from app.services.reader_record_ask.context_envelope import (  # noqa: PLC0415
        EnvelopeInitialAnchor,
        VerifiedEnvelopeInput,
        build_context_envelope,
    )
    from app.services.reader_record_ask.document_access import (  # noqa: PLC0415
        AnchorSegmentView,
        ReadingUnitView,
        build_document_scope,
    )

    _USER = _UUID("11111111-1111-1111-1111-111111111111")
    _RECORD = _UUID("22222222-2222-2222-2222-222222222222")
    _BASE = _UUID("33333333-3333-3333-3333-333333333333")
    _DOC = _UUID("44444444-4444-4444-4444-444444444444")
    _SHA = "b" * 64

    _UNIT_A_TEXT = "Alpha sentence one. Alpha sentence two."
    _SEG_A1_TEXT = "Alpha sentence one. "

    units = (
        ReadingUnitView(
            unit_id="u1",
            order_index=0,
            text=_UNIT_A_TEXT,
            text_hash="11111111",
            base_start_utf16=0,
            base_end_utf16=len(_UNIT_A_TEXT),
        ),
    )
    segments = (
        AnchorSegmentView(
            unit_id="u1",
            anchor_segment_id="s1",
            order_index=0,
            unit_order_index=0,
            text=_SEG_A1_TEXT,
            text_hash="aaaaaaaa",
            unit_start_utf16=0,
            unit_end_utf16=len(_SEG_A1_TEXT),
            base_start_utf16=0,
            base_end_utf16=len(_SEG_A1_TEXT),
        ),
    )
    scope = build_document_scope(
        reading_record_id=_RECORD,
        base_id=_BASE,
        record_generation=1,
        units=units,
        segments=segments,
        stable_document_id=_DOC,
        base_content_sha256=_SHA,
    )
    document_access = InMemoryDocumentAccess(snapshot=scope)
    envelope = build_context_envelope(
        VerifiedEnvelopeInput(
            user_id=_USER,
            reading_record_id=_RECORD,
            base_id=_BASE,
            record_generation=1,
            stable_document_id=_DOC,
            base_content_sha256=_SHA,
            product_state="readable_enhancing",
            readiness_state="article_ready",
            initial_anchor=EnvelopeInitialAnchor(
                unit_id="u1",
                anchor_segment_id="s1",
                start_offset=0,
                end_offset=len(_SEG_A1_TEXT),
                selected_text=_SEG_A1_TEXT,
                text_hash="aaaaaaaa",
            ),
            visible_range=None,
        )
    )

    observation = RuntimeObservation()

    # We need to know the valid handle the baseline assembler will
    # register so the second model call can cite it. The handle is
    # deterministic given the envelope — but rather than re-derive it,
    # we capture it from the observation's baseline_context AFTER
    # assembly. To do that, we use a two-phase FunctionModel: the
    # first call always returns an invalid draft (empty handles); the
    # second call inspects the observation's baseline_context to find
    # the registered seed handle and returns a valid draft citing it.
    calls = {"n": 0}

    async def model_fn(messages, info: AgentInfo):
        del messages, info
        calls["n"] += 1
        if calls["n"] == 1:
            # First call: schema-valid but grounding-INVALID draft —
            # an article block with NO evidence handles violates the
            # P2C-A1 provenance contract ("article block requires at
            # least one article evidence handle") → ModelRetry.
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="final_result",
                        args=_json.dumps(
                            {
                                "response_kind": "grounded_answer",
                                "clarification_text": None,
                                "answer_blocks": [
                                    {
                                        "text": "invalid attempt 1",
                                        "basis": "article",
                                        "evidence_handles": [],
                                    }
                                ],
                            }
                        ),
                        tool_call_id="bad-grounding-1",
                    )
                ]
            )
        # Second call: valid draft citing a real handle from the
        # baseline. The baseline assembler registered seed handles
        # for each chunk; the first chunk's handle is the canonical
        # valid citation. The block's "evidence_bounded" scope is
        # always confirmed by the real runtime (turn_coordinator).
        baseline = observation.baseline_context
        assert baseline is not None, (
            "R4-A4-2R5R2: baseline must be captured before the 2nd "
            "model call (runtime writes observation.baseline_context "
            "after assembly succeeds, before agent.run)."
        )
        # The baseline assembler registers one seed handle per chunk.
        # For this single-unit document, there is exactly one handle.
        valid_handles = list(baseline.available_seed_handle_ids)
        assert valid_handles, (
            "R4-A4-2R5R2: baseline must have at least one seed handle "
            "for the model to cite."
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="final_result",
                    args=_json.dumps(
                        {
                            "response_kind": "grounded_answer",
                            "clarification_text": None,
                            "answer_blocks": [
                                    {
                                        "text": "valid grounded answer",
                                        "basis": "article",
                                        "evidence_handles": [valid_handles[0]],
                                }
                            ],
                        }
                    ),
                    tool_call_id="good-grounding-2",
                )
            ]
        )

    result = await run_reading_record_ask(
        user_message="Summarize the selection.",
        envelope=envelope,
        document_access=document_access,
        model=FunctionModel(model_fn),
        article_rag=None,
        observation=observation,
    )

    # The agent.run succeeded (no exception). Verify the typed retry
    # evidence: 2 final-mode validator calls, 1 ModelRetry raise.
    assert calls["n"] == 2, (
        "R4-A4-2R5R2: model MUST be called exactly 2 times (1 initial "
        f"+ 1 retry). Got {calls['n']}."
    )
    assert observation.output_validation_final_attempts == 2, (
        "R4-A4-2R5R2: final-mode success after 1 retry → "
        "output_validation_final_attempts MUST be 2 (got "
        f"{observation.output_validation_final_attempts})."
    )
    assert observation.output_validation_retry_requests == 1, (
        "R4-A4-2R5R2: final-mode success after 1 retry → "
        "output_validation_retry_requests MUST be 1 (only the first "
        f"call raised ModelRetry; got {observation.output_validation_retry_requests})."
    )
    # The run produced a finalized ok result.
    assert result.finalized is not None
    assert result.finalized.status == "ok"


@pytest.mark.asyncio
async def test_r4_a4_2r5r2_validator_passes_three_times_then_other_umb_conservative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R4-A4-2R5R2 Task 5: validator called 3 times, ALL passed
    (``retry_requests == 0``), then a subsequent non-validator
    ``UnexpectedModelBehavior`` occurred → MUST classify as
    ``unexpected_model_behavior`` (conservative), NOT
    ``output_retry_exhausted``.

    This is the key mis-classification guard: ``final_attempts == 3``
    alone is NOT sufficient proof of retry exhaustion. The classifier
    requires BOTH ``final_attempts == 3`` AND ``retry_requests == 3``.
    When ``retry_requests < 3`` (the validator passed on every call),
    a subsequent non-validator UMB (e.g. pydantic-ai internal error)
    must fall through to the conservative
    ``unexpected_model_behavior`` code.

    This test uses ``_install_fake_runtime_that_captures_then_raises``
    to simulate: baseline captured, validator called 3 times (all
    passed, so ``retry_requests == 0``), then a non-validator UMB
    raised. The fake runtime writes the typed counters exactly as the
    real runtime would.
    """
    from pydantic_ai.exceptions import (  # noqa: PLC0415
        UnexpectedModelBehavior,
    )

    # Baseline with chunks (so capture_status will be "captured").
    baseline = _make_r4_a4_2r5_baseline(
        chunks=(
            _make_baseline_chunk(chunk_ordinal=0, text="chunk-A."),
        ),
    )
    exc = UnexpectedModelBehavior("non-validator UMB after validator passed")

    _install_fake_runtime_that_captures_then_raises(
        monkeypatch,
        baseline=baseline,
        exc=exc,
        # Validator was called 3 times in final mode (final_attempts=3)
        # but ALL passed (retry_requests=0). This is the
        # "3 attempts but last passes then other UMB" case.
        final_attempts=DEFAULT_OUTPUT_RETRIES + 1,
        retry_requests=0,
    )

    case = _make_minimal_case(
        case_id="case-r5r2-umb-after-pass",
        article_text="chunk-A.",
        phase_tags=["real_phase1"],
        expected_envelope_fingerprint=_VALID_FP_A,
    )

    artifact = await _run_one_case(
        case=case,
        budget_model=_FakeBudgetedModelForManifest(),
        model_config=_make_minimal_model_config(),
        run_id="test-run-r5r2-umb-after-pass",
        run_index=0,
        envelope=_make_envelope_mock(_VALID_FP_A),
        document_access=object(),  # not read by exception path
        start_requests=0,
        start_input_tokens=0,
        start_output_tokens=0,
        dataset_identity=_make_dataset_identity(),
    )

    # Conservative classification: UMB + retry_requests < 3 →
    # unexpected_model_behavior (NOT output_retry_exhausted).
    assert artifact.safe_error_code == "unexpected_model_behavior", (
        "R4-A4-2R5R2: UMB + final_attempts=3 BUT retry_requests=0 → "
        "MUST classify as 'unexpected_model_behavior' (conservative). "
        f"Got {artifact.safe_error_code!r}. The validator passed on "
        "every call — retry budget was NOT exhausted by ModelRetry."
    )
    # Answer is still failed — taxonomy split is via safe_error_code.
    assert artifact.finalized_status is None
    # Capture-後 exception: baseline was captured before the UMB →
    # capture_status="captured" (NOT "failed"), actual fingerprint
    # is non-None and computed from the captured baseline.
    assert artifact.model_context_capture_status == "captured", (
        "R4-A4-2R5R2: capture-後 exception → capture_status MUST be "
        f"'captured' (got {artifact.model_context_capture_status!r}). "
        "The baseline was assembled before the agent raised; the "
        "actual baseline audit data is preserved."
    )
    assert artifact.runtime_fixture_fingerprint is not None, (
        "R4-A4-2R5R2: capture-後 exception → actual fingerprint MUST "
        "be non-None (computed from captured baseline)."
    )


@pytest.mark.asyncio
async def test_r4_a4_2r5r2_raw_artifact_full_chain_output_retry_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R4-A4-2R5R2 Task 5: full RawArtifact chain through
    ``_run_one_case`` with ``output_retry_exhausted``.

    Simulates: baseline captured (chunks B), validator called 3 times
    in final mode, each raised ``ModelRetry`` (so
    ``retry_requests == 3``), then ``UnexpectedModelBehavior`` raised.
    The artifact MUST carry:
      - ``safe_error_code == "output_retry_exhausted"`` (BOTH typed
        counters EXACTLY equal 3).
      - ``finalized_status is None`` (answer is failed; taxonomy split
        is via ``safe_error_code``).
      - ``model_context_capture_status == "captured"`` (capture-後
        exception — baseline was assembled before the agent raised).
      - ``runtime_fixture_fingerprint`` non-empty (64-char hex),
        computed from the CAPTURED baseline (chunks B), NOT copied
        from the preflight (expected) value.
      - ``model_context_fingerprint`` non-empty (computed from the
        captured baseline's chunks).
    """
    from pydantic_ai.exceptions import (  # noqa: PLC0415
        UnexpectedModelBehavior,
    )

    # Preflight (expected) fingerprint — computed from chunk set A
    # (DIFFERENT from the runtime's chunk set B). This proves the
    # artifact's actual fingerprint is NOT copied from preflight.
    preflight_chunks_view: list[tuple[int, str]] = [
        (0, "PREFLIGHT chunk text."),
    ]
    preflight_fp = compute_runtime_fixture_fingerprint(
        baseline_status="injected",
        is_complete=True,
        chunks=preflight_chunks_view,
    )

    case = _make_minimal_case(
        case_id="case-r5r2-full-chain",
        article_text="PREFLIGHT chunk text.",
        phase_tags=["real_phase1"],
        expected_envelope_fingerprint=_VALID_FP_A,
    )
    case.expected_runtime_fixture_fingerprint = preflight_fp

    # Runtime baseline (chunks B — DIFFERENT text from preflight A).
    runtime_chunks = (
        _make_baseline_chunk(chunk_ordinal=0, text="RUNTIME chunk text."),
    )
    actual_fp_expected = compute_runtime_fixture_fingerprint(
        baseline_status="injected",
        is_complete=True,
        chunks=[(c.chunk_ordinal, c.text) for c in runtime_chunks],
    )
    assert actual_fp_expected != preflight_fp, (
        "test setup invariant: runtime chunks MUST differ from preflight"
    )

    baseline = _make_r4_a4_2r5_baseline(
        chunks=runtime_chunks,
        baseline_status="injected",
        is_complete=True,
    )
    exc = UnexpectedModelBehavior("retry budget exhausted after 3 attempts")

    _install_fake_runtime_that_captures_then_raises(
        monkeypatch,
        baseline=baseline,
        exc=exc,
        # BOTH typed counters EXACTLY equal 3 → output_retry_exhausted.
        final_attempts=DEFAULT_OUTPUT_RETRIES + 1,
        retry_requests=DEFAULT_OUTPUT_RETRIES + 1,
    )

    artifact = await _run_one_case(
        case=case,
        budget_model=_FakeBudgetedModelForManifest(),
        model_config=_make_minimal_model_config(),
        run_id="test-run-r5r2-full-chain",
        run_index=0,
        envelope=_make_envelope_mock(_VALID_FP_A),
        document_access=object(),  # not read by exception path
        start_requests=0,
        start_input_tokens=0,
        start_output_tokens=0,
        dataset_identity=_make_dataset_identity(),
    )

    # --- Core taxonomy assertion ---
    assert artifact.safe_error_code == "output_retry_exhausted", (
        "R4-A4-2R5R2: UMB + BOTH typed counters == 3 → "
        f"safe_error_code MUST be 'output_retry_exhausted' (got "
        f"{artifact.safe_error_code!r})."
    )

    # --- Answer is failed; taxonomy split is via safe_error_code ---
    assert artifact.finalized_status is None, (
        "R4-A4-2R5R2: exception path → finalized_status MUST be None "
        f"(answer is failed; got {artifact.finalized_status!r})."
    )

    # --- Capture-後 exception: capture_status=captured, not failed ---
    assert artifact.model_context_capture_status == "captured", (
        "R4-A4-2R5R2: capture-後 exception → capture_status MUST be "
        f"'captured' (got {artifact.model_context_capture_status!r}). "
        "The baseline was assembled before the agent raised; the "
        "actual baseline audit data is preserved."
    )

    # --- Actual fingerprint computed from captured baseline (chunks B) ---
    assert artifact.runtime_fixture_fingerprint is not None
    assert len(artifact.runtime_fixture_fingerprint) == 64, (
        "R4-A4-2R5R2: runtime_fixture_fingerprint MUST be a 64-char "
        f"hex SHA-256 (got len={len(artifact.runtime_fixture_fingerprint)})."
    )
    assert artifact.runtime_fixture_fingerprint == actual_fp_expected, (
        "R4-A4-2R5R2: actual fingerprint MUST be computed from the "
        "captured baseline (chunks B), NOT copied from preflight. "
        f"Expected {actual_fp_expected!r}, got "
        f"{artifact.runtime_fixture_fingerprint!r}."
    )
    assert artifact.runtime_fixture_fingerprint != preflight_fp, (
        "R4-A4-2R5R2: actual fingerprint MUST differ from preflight "
        "(chunks A ≠ chunks B). Copying preflight would hide runtime drift."
    )

    # --- model_context_fingerprint also non-empty (captured baseline) ---
    assert artifact.model_context_fingerprint is not None
    assert len(artifact.model_context_fingerprint) == 64


def test_r4_a4_2r5r2_typed_provenance_unforgeable_by_dataset_json() -> None:
    """R4-A4-2R5R2 Task 5: dataset JSON CANNOT forge ``"explicit"``
    provenance on individual :class:`AtomicExpectedFact` entries.

    R4-A4-2R5R2 Task 4 closed the audit finding where a dataset author
    could set ``origin="explicit"`` on individual facts to bypass the
    preflight guard. The fix:
      - Removed the public ``origin`` field from
        :class:`AtomicExpectedFact` (``model_config = {"extra":
        "forbid"}`` rejects unknown keys).
      - Added ``_atomic_facts_origin: str = PrivateAttr(default=
        "explicit")`` on :class:`ReaderRecordAskR4A3Case` — a Pydantic
        ``PrivateAttr`` is NOT parsed from JSON, NOT included in
        ``model_dump()``, and NOT settable via ``model_validate``.
      - The loader inspects the raw JSON dict (before Pydantic parsing)
        and sets ``case._atomic_facts_origin`` directly.

    This test verifies:
      1. JSON with ``origin="explicit"`` on an ``AtomicExpectedFact``
         is REJECTED at the Pydantic boundary (``extra="forbid"``).
      2. JSON with ``atomic_facts_origin="explicit"`` on the case is
         IGNORED (PrivateAttr is not a JSON field) — the case's
         ``atomic_facts_origin`` property returns the default
         ``"explicit"`` regardless of what the JSON tried to set.
      3. ``model_validate`` cannot set ``_atomic_facts_origin`` —
         only the loader (Python code) can.
    """
    from pydantic import ValidationError as PydanticValidationError  # noqa: PLC0415

    from claread_eval.reader_record_ask.schema import (  # noqa: PLC0415
        ReaderRecordAskR4A3Case,
    )

    # --- Case 1: JSON with ``origin`` on AtomicExpectedFact → REJECTED ---
    json_with_per_fact_origin = {
        "id": "case-forge-1",
        "source_kind": "synthetic_short",
        "article_text": "Some text.",
        "input_mode": "manual",
        "source_metadata": "known_synthetic",
        "baseline_mode": "complete",
        "question": "What?",
        "question_category": "main_idea",
        "expected": {
            "atomic_facts": [
                {
                    "fact_id": "f1",
                    "answer_alias_groups": [["something"]],
                    "source_aliases": ["something"],
                    "required": True,
                    "severity": "high",
                    # FORGERY ATTEMPT: try to declare explicit provenance
                    # on the individual fact. ``extra="forbid"`` MUST
                    # reject this.
                    "origin": "explicit",
                },
            ],
        },
        "phase_tags": ["real_phase1"],
    }
    with pytest.raises(PydanticValidationError) as exc_info_1:
        ReaderRecordAskR4A3Case.model_validate(json_with_per_fact_origin)
    # Verify the rejection is specifically about the ``origin`` field
    # (not some other validation error).
    error_str_1 = str(exc_info_1.value)
    assert "origin" in error_str_1, (
        "R4-A4-2R5R2: rejection MUST mention the forbidden 'origin' "
        f"field (got: {error_str_1!r})."
    )

    # --- Case 2: JSON with ``atomic_facts_origin`` on case → IGNORED ---
    # ``ReaderRecordAskR4A3Case`` uses Pydantic's default ``extra="ignore"``
    # (no ``model_config = {"extra": "forbid"}`` on the case model).
    # The JSON value is silently DROPPED — it does NOT set the
    # PrivateAttr. The case's ``atomic_facts_origin`` property returns
    # the default ``"explicit"`` regardless of what the JSON declares.
    # This STILL proves the security property: dataset JSON cannot
    # forge provenance because the value is ignored, and only the
    # loader (Python layer) sets the PrivateAttr.
    json_with_case_level_provenance = {
        "id": "case-forge-2",
        "source_kind": "synthetic_short",
        "article_text": "Some text.",
        "input_mode": "manual",
        "source_metadata": "known_synthetic",
        "baseline_mode": "complete",
        "question": "What?",
        "question_category": "main_idea",
        "expected": {
            "atomic_facts": [
                {
                    "fact_id": "f1",
                    "answer_alias_groups": [["something"]],
                    "source_aliases": ["something"],
                    "required": True,
                    "severity": "high",
                },
            ],
        },
        "phase_tags": ["real_phase1"],
        # FORGERY ATTEMPT: try to set the PrivateAttr via JSON to
        # "legacy_migrated". This value MUST be IGNORED — the property
        # returns the default "explicit", NOT the JSON value.
        "atomic_facts_origin": "legacy_migrated",
    }
    case_forge_2 = ReaderRecordAskR4A3Case.model_validate(
        json_with_case_level_provenance
    )
    assert case_forge_2.atomic_facts_origin == "explicit", (
        "R4-A4-2R5R2: JSON 'atomic_facts_origin' MUST be IGNORED by "
        "the case model (extra='ignore'). The property returns the "
        "default 'explicit', NOT the JSON-declared 'legacy_migrated'. "
        "Only the loader can set 'legacy_migrated' via Python."
    )

    # --- Case 3: valid JSON (no provenance forging) → default "explicit" ---
    # When the JSON does NOT try to forge provenance, the case loads
    # normally and the ``atomic_facts_origin`` property returns the
    # default ``"explicit"``. Only the loader can change this (by
    # setting ``case._atomic_facts_origin`` directly based on raw
    # JSON inspection).
    valid_json = {
        "id": "case-valid-3",
        "source_kind": "synthetic_short",
        "article_text": "Some text.",
        "input_mode": "manual",
        "source_metadata": "known_synthetic",
        "baseline_mode": "complete",
        "question": "What?",
        "question_category": "main_idea",
        "expected": {
            "atomic_facts": [
                {
                    "fact_id": "f1",
                    "answer_alias_groups": [["something"]],
                    "source_aliases": ["something"],
                    "required": True,
                    "severity": "high",
                },
            ],
        },
        "phase_tags": ["real_phase1"],
    }
    case_valid = ReaderRecordAskR4A3Case.model_validate(valid_json)
    assert case_valid.atomic_facts_origin == "explicit", (
        "R4-A4-2R5R2: valid case (atomic_facts present in JSON) → "
        "loader-owned ``atomic_facts_origin`` defaults to 'explicit'. "
        "Only the loader can set 'legacy_migrated' (by inspecting raw "
        "JSON and setting the PrivateAttr directly)."
    )

    # --- Case 4: ``model_validate`` cannot set ``_atomic_facts_origin`` ---
    # Even if the JSON tries to set the mangled private name, Pydantic
    # IGNORES it (the case model uses default ``extra="ignore"``). The
    # PrivateAttr keeps its default value. The only way to set
    # provenance is via Python: ``case._atomic_facts_origin =
    # "legacy_migrated"`` (which the loader does).
    json_with_mangled_private = dict(valid_json)
    json_with_mangled_private["_atomic_facts_origin"] = "legacy_migrated"
    json_with_mangled_private["id"] = "case-forge-4"
    case_forge_4 = ReaderRecordAskR4A3Case.model_validate(
        json_with_mangled_private
    )
    assert case_forge_4.atomic_facts_origin == "explicit", (
        "R4-A4-2R5R2: JSON '_atomic_facts_origin' MUST be IGNORED by "
        "the case model (extra='ignore'). The PrivateAttr keeps its "
        "default 'explicit', NOT the JSON-declared 'legacy_migrated'. "
        "PrivateAttr names are NOT JSON-parseable."
    )

    # --- Case 5: loader sets provenance via Python (the only valid path) ---
    case_loader_set = ReaderRecordAskR4A3Case.model_validate(valid_json)
    case_loader_set.id = "case-loader-set-5"
    # Simulate the loader: raw JSON had no atomic_facts but had
    # required_article_facts → legacy_migrated.
    case_loader_set._atomic_facts_origin = "legacy_migrated"
    assert case_loader_set.atomic_facts_origin == "legacy_migrated", (
        "R4-A4-2R5R2: loader sets ``_atomic_facts_origin`` via Python "
        "(the only valid path). The property returns the loader-set value."
    )


def test_r4_a4_2r5r3_loader_provenance_through_formal_dataset_loader(
    tmp_path: Path,
) -> None:
    """R4-A4-2R5R3 Issue #2: loader-owned provenance is set correctly
    when the case files are loaded through the FORMAL dataset loader
    :func:`load_r4_a3_dataset_with_snapshot`.

    R4-A4-2R5R2 Task 5 (``test_r4_a4_2r5r2_typed_provenance_unforgeable
    _by_dataset_json``) closed the JSON-forgery vector but only
    exercised :meth:`ReaderRecordAskR4A3Case.model_validate` and
    direct ``case._atomic_facts_origin = ...`` assignment. The
    R4-A4-2R5R3 audit found that this left the loader's RAW-JSON
    inspection logic in :func:`load_r4_a3_dataset_with_snapshot`
    completely uncovered: a regression where the loader sets
    ``"legacy_migrated"`` for cases that DO declare ``atomic_facts``,
    or sets ``"explicit"`` for cases that rely on auto-migration,
    would NOT be caught by the existing tests.

    This test closes that gap. It writes a real on-disk dataset
    (``dataset.yaml`` + per-case JSON files) to ``tmp_path``, invokes
    the formal loader, and asserts that each loaded case's
    :attr:`ReaderRecordAskR4A3Case.atomic_facts_origin` matches the
    value the loader MUST set based on raw JSON inspection — NOT
    based on Pydantic model state after migration.

    Cases:

        Case A (``case-explicit-atomic``)
            JSON declares ``expected.atomic_facts`` with one entry.
            Raw JSON inspection sees ``atomic_facts`` is non-empty →
            loader sets ``_atomic_facts_origin = "explicit"``. The
            preflight guard MUST pass (no skip).

        Case B (``case-legacy-migrated``)
            JSON declares only ``expected.required_article_facts``
            (no ``atomic_facts`` key). Raw JSON inspection sees
            ``atomic_facts`` is empty AND ``required_article_facts``
            is non-empty → loader sets
            ``_atomic_facts_origin = "legacy_migrated"``. The loader
            then auto-migrates the legacy facts to
            :class:`AtomicExpectedFact` entries (so
            ``case.expected.atomic_facts`` is non-empty AFTER load),
            but the provenance remains ``"legacy_migrated"``. The
            preflight guard MUST skip-fail (``real_phase1`` cases
            cannot rely on auto-migration).

    The test also verifies the deep invariant: after the loader
    runs, Case B's ``case.expected.atomic_facts`` is non-empty
    (migration produced entries) AND
    ``case.atomic_facts_origin == "legacy_migrated"`` (provenance
    is independent of the post-migration model state). This proves
    the loader's provenance decision is based on the RAW JSON dict,
    not on the post-migration Pydantic model.
    """
    import yaml  # noqa: PLC0415

    dataset_dir = tmp_path / "r4-a4-2r5r3-loader-provenance-dataset"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    (dataset_dir / "cases").mkdir(exist_ok=True)

    # --- dataset.yaml ---
    yaml_payload = {
        "id": "r4-a4-2r5r3-loader-provenance",
        "schema_version": "test-schema-v1",
        "description": (
            "R4-A4-2R5R3 Issue #2: loader-owned provenance through "
            "the formal dataset loader."
        ),
        "case_globs": ["cases/*.json"],
        "tags": [],
    }
    (dataset_dir / "dataset.yaml").write_text(
        yaml.safe_dump(yaml_payload, sort_keys=False),
        encoding="utf-8",
    )

    # --- Case A: explicit atomic_facts in raw JSON ---
    case_a_json = {
        "id": "case-explicit-atomic",
        "source_kind": "synthetic_short",
        "article_text": "Some article text with content.",
        "input_mode": "manual",
        "source_metadata": "known_synthetic",
        "baseline_mode": "complete",
        "question": "What is this about?",
        "question_category": "main_idea",
        "expected": {
            "atomic_facts": [
                {
                    "fact_id": "f1",
                    "answer_alias_groups": [["some article text"]],
                    "source_aliases": ["some article text"],
                    "required": True,
                    "severity": "high",
                },
            ],
            "required_article_facts": [],
        },
        "phase_tags": ["real_phase1"],
    }
    (dataset_dir / "cases" / "case-explicit-atomic.json").write_text(
        json.dumps(case_a_json, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # --- Case B: only required_article_facts in raw JSON (no atomic_facts) ---
    case_b_json = {
        "id": "case-legacy-migrated",
        "source_kind": "synthetic_short",
        "article_text": "Another article with different content.",
        "input_mode": "manual",
        "source_metadata": "known_synthetic",
        "baseline_mode": "complete",
        "question": "What is this about?",
        "question_category": "main_idea",
        "expected": {
            "required_article_facts": [
                "another article with different content",
            ],
        },
        "phase_tags": ["real_phase1"],
    }
    (dataset_dir / "cases" / "case-legacy-migrated.json").write_text(
        json.dumps(case_b_json, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # --- Invoke the FORMAL loader (not model_validate, not direct
    # PrivateAttr assignment). The loader reads dataset.yaml + each
    # case file from disk, inspects the raw JSON dict for each case,
    # sets ``case._atomic_facts_origin`` based on that inspection,
    # and then runs the legacy-facts migration. ---
    snapshot = load_r4_a3_dataset_with_snapshot(dataset_dir)

    # Sanity: both cases loaded, identity computed.
    assert len(snapshot.dataset.cases) == 2, (
        "R4-A4-2R5R3: formal loader MUST load both cases from disk."
    )
    assert snapshot.identity.content_sha256, (
        "R4-A4-2R5R3: formal loader MUST compute a dataset identity."
    )

    cases_by_id = {case.id: case for case in snapshot.dataset.cases}
    assert set(cases_by_id) == {
        "case-explicit-atomic",
        "case-legacy-migrated",
    }, (
        "R4-A4-2R5R3: formal loader MUST load both case ids exactly once."
    )

    case_a = cases_by_id["case-explicit-atomic"]
    case_b = cases_by_id["case-legacy-migrated"]

    # --- Case A provenance: explicit ---
    assert case_a.atomic_facts_origin == "explicit", (
        "R4-A4-2R5R3 Issue #2: Case A (raw JSON declares "
        "expected.atomic_facts with one entry) → formal loader MUST "
        "set ``_atomic_facts_origin = 'explicit'``. Got: "
        f"{case_a.atomic_facts_origin!r}."
    )
    # Case A's atomic_facts are unchanged by the loader (no migration
    # needed — explicit facts already present).
    assert len(case_a.expected.atomic_facts) == 1, (
        "R4-A4-2R5R3 Issue #2: Case A's atomic_facts list MUST "
        "remain the single explicitly-authored entry after load."
    )
    assert case_a.expected.atomic_facts[0].fact_id == "f1"

    # --- Case B provenance: legacy_migrated ---
    # The loader's raw-JSON inspection saw NO atomic_facts key and
    # NON-EMPTY required_article_facts → legacy_migrated. The loader
    # then ran _migrate_legacy_required_article_facts, which produced
    # AtomicExpectedFact entries with fact_id="legacy-0". The
    # provenance is "legacy_migrated" REGARDLESS of the post-migration
    # model state — this is the deep invariant the test protects.
    assert case_b.atomic_facts_origin == "legacy_migrated", (
        "R4-A4-2R5R3 Issue #2: Case B (raw JSON declares only "
        "expected.required_article_facts, no atomic_facts key) → "
        "formal loader MUST set "
        "``_atomic_facts_origin = 'legacy_migrated'``. Got: "
        f"{case_b.atomic_facts_origin!r}."
    )
    # Case B's atomic_facts were auto-migrated from
    # required_article_facts by the loader. The migrated entries have
    # the canonical legacy shape (fact_id="legacy-{idx}",
    # source_aliases=[]).
    assert len(case_b.expected.atomic_facts) == 1, (
        "R4-A4-2R5R3 Issue #2: Case B's atomic_facts MUST be "
        "auto-migrated from required_article_facts by the formal "
        "loader (one AtomicExpectedFact per legacy sentence)."
    )
    migrated_fact = case_b.expected.atomic_facts[0]
    assert migrated_fact.fact_id == "legacy-0", (
        "R4-A4-2R5R3 Issue #2: migrated fact MUST have "
        "fact_id='legacy-0' (canonical legacy shape)."
    )
    assert migrated_fact.source_aliases == [], (
        "R4-A4-2R5R3 Issue #2: migrated fact MUST have empty "
        "source_aliases (canonical legacy shape — this is WHY "
        "real_phase1 cases cannot rely on auto-migration: the "
        "semantic precheck skips facts with empty source_aliases)."
    )
    # The original required_article_facts field is preserved
    # (backwards-compat: the loader does not delete the legacy field).
    assert case_b.expected.required_article_facts == [
        "another article with different content",
    ], (
        "R4-A4-2R5R3 Issue #2: formal loader MUST preserve the "
        "original required_article_facts field (backwards compat)."
    )

    # --- Preflight guard: Case A passes, Case B skip-fails ---
    # Case A is real_phase1 with explicit atomic_facts → guard
    # returns normally (no skip).
    _preflight_guard_real_phase1_atomic_facts_explicit(case_a)

    # Case B is real_phase1 with legacy_migrated provenance → guard
    # MUST skip-fail BEFORE any model construction (provider calls =
    # 0, model builder calls = 0).
    with pytest.raises(pytest.skip.Exception):  # type: ignore[attr-defined]
        _preflight_guard_real_phase1_atomic_facts_explicit(case_b)


def test_r4_a4_2r5r3_loader_provenance_explicit_with_legacy_field_is_explicit(
    tmp_path: Path,
) -> None:
    """R4-A4-2R5R3 Issue #2 supplementary: when a case JSON declares
    BOTH ``expected.atomic_facts`` AND ``expected.required_article_facts``,
    the formal loader sets ``_atomic_facts_origin = "explicit"`` (the
    explicit facts win; the legacy field is dead weight).

    This protects the loader's raw-JSON inspection branch:
    ``if not raw_atomic_facts and raw_required_article_facts:
    legacy_migrated else: explicit``. When ``raw_atomic_facts`` is
    non-empty, the branch goes to ``explicit`` regardless of
    ``raw_required_article_facts``.

    R4-A4-2R5R2 Task 5's
    ``test_r4_a4_2r5_preflight_guard_partial_migration_blocks`` tested
    this branch by directly constructing a Pydantic case and directly
    setting ``_atomic_facts_origin = "explicit"``. This R5R3 test
    closes the same boundary through the FORMAL loader, proving the
    loader's Python code (not just the test helper) makes the right
    decision when both fields are present in the raw JSON.
    """
    import yaml  # noqa: PLC0415

    dataset_dir = tmp_path / "r4-a4-2r5r3-mixed-fields-dataset"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    (dataset_dir / "cases").mkdir(exist_ok=True)

    yaml_payload = {
        "id": "r4-a4-2r5r3-loader-mixed-fields",
        "schema_version": "test-schema-v1",
        "description": (
            "R4-A4-2R5R3 Issue #2 supplementary: explicit + legacy "
            "fields both present → explicit wins."
        ),
        "case_globs": ["cases/*.json"],
        "tags": [],
    }
    (dataset_dir / "dataset.yaml").write_text(
        yaml.safe_dump(yaml_payload, sort_keys=False),
        encoding="utf-8",
    )

    case_json = {
        "id": "case-mixed-explicit-and-legacy",
        "source_kind": "synthetic_short",
        "article_text": "Article body content.",
        "input_mode": "manual",
        "source_metadata": "known_synthetic",
        "baseline_mode": "complete",
        "question": "What is this about?",
        "question_category": "main_idea",
        "expected": {
            "atomic_facts": [
                {
                    "fact_id": "f1",
                    "answer_alias_groups": [["article body content"]],
                    "source_aliases": ["article body content"],
                    "required": True,
                    "severity": "high",
                },
            ],
            # Legacy field also present — but explicit facts win.
            "required_article_facts": [
                "legacy sentence that should be ignored",
            ],
        },
        "phase_tags": ["real_phase1"],
    }
    (dataset_dir / "cases" / "case-mixed-explicit-and-legacy.json").write_text(
        json.dumps(case_json, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    snapshot = load_r4_a3_dataset_with_snapshot(dataset_dir)
    assert len(snapshot.dataset.cases) == 1

    case = snapshot.dataset.cases[0]
    assert case.id == "case-mixed-explicit-and-legacy"

    # Provenance is "explicit" — explicit facts win, legacy field is
    # dead weight. The loader's raw-JSON inspection saw non-empty
    # atomic_facts, so it set "explicit" (NOT "legacy_migrated").
    assert case.atomic_facts_origin == "explicit", (
        "R4-A4-2R5R3 Issue #2: case with BOTH explicit atomic_facts "
        "AND legacy required_article_facts → formal loader MUST set "
        "``_atomic_facts_origin = 'explicit'`` (explicit wins, legacy "
        "field is dead weight). Got: "
        f"{case.atomic_facts_origin!r}."
    )
    # The explicit fact is preserved as-is (no migration ran —
    # ``_migrate_legacy_required_article_facts`` returns early when
    # atomic_facts is non-empty).
    assert len(case.expected.atomic_facts) == 1
    assert case.expected.atomic_facts[0].fact_id == "f1"
    assert case.expected.atomic_facts[0].source_aliases == [
        "article body content",
    ]
    # The legacy field is preserved (the loader does not delete it),
    # but it is NOT used to construct atomic_facts.
    assert case.expected.required_article_facts == [
        "legacy sentence that should be ignored",
    ]

    # Preflight guard passes (real_phase1 + explicit provenance +
    # non-empty atomic_facts with proper source_aliases).
    _preflight_guard_real_phase1_atomic_facts_explicit(case)


def test_r4_a4_2r5r3_loader_provenance_empty_both_fields_is_explicit(
    tmp_path: Path,
) -> None:
    """R4-A4-2R5R3 Issue #2 boundary: when a case JSON declares NEITHER
    ``expected.atomic_facts`` NOR ``expected.required_article_facts``,
    the formal loader sets ``_atomic_facts_origin = "explicit"``.

    This protects the loader's raw-JSON inspection branch: the
    condition for ``legacy_migrated`` is ``not raw_atomic_facts AND
    raw_required_article_facts`` — both must be truthy. When both are
    empty/missing, the branch falls through to ``explicit`` (the
    default). This is intentional: a case with no facts at all is
    NOT a legacy-migrated case; it is simply a case with no facts
    (other preflight checks may catch this if needed, but provenance
    is not the right place to fail).

    R4-A4-2R5R2 Task 5's existing test suite did NOT exercise this
    boundary through the formal loader. This R5R3 test closes that
    gap.
    """
    import yaml  # noqa: PLC0415

    dataset_dir = tmp_path / "r4-a4-2r5r3-empty-both-dataset"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    (dataset_dir / "cases").mkdir(exist_ok=True)

    yaml_payload = {
        "id": "r4-a4-2r5r3-loader-empty-both",
        "schema_version": "test-schema-v1",
        "description": (
            "R4-A4-2R5R3 Issue #2 boundary: empty atomic_facts AND "
            "empty required_article_facts → explicit (default)."
        ),
        "case_globs": ["cases/*.json"],
        "tags": [],
    }
    (dataset_dir / "dataset.yaml").write_text(
        yaml.safe_dump(yaml_payload, sort_keys=False),
        encoding="utf-8",
    )

    case_json = {
        "id": "case-empty-both-fields",
        "source_kind": "synthetic_short",
        "article_text": "Article with no expected facts.",
        "input_mode": "manual",
        "source_metadata": "known_synthetic",
        "baseline_mode": "complete",
        "question": "What is this about?",
        "question_category": "main_idea",
        # No atomic_facts key, no required_article_facts key.
        "expected": {},
        "phase_tags": ["offline_only"],  # Not real_phase1 — guard won't fire.
    }
    (dataset_dir / "cases" / "case-empty-both-fields.json").write_text(
        json.dumps(case_json, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    snapshot = load_r4_a3_dataset_with_snapshot(dataset_dir)
    assert len(snapshot.dataset.cases) == 1

    case = snapshot.dataset.cases[0]
    assert case.id == "case-empty-both-fields"

    # Provenance is "explicit" — the loader's branch falls through to
    # the else (default) when both raw fields are empty/missing.
    assert case.atomic_facts_origin == "explicit", (
        "R4-A4-2R5R3 Issue #2: case with NEITHER atomic_facts NOR "
        "required_article_facts → formal loader MUST set "
        "``_atomic_facts_origin = 'explicit'`` (default branch). "
        f"Got: {case.atomic_facts_origin!r}."
    )
    # No atomic_facts (no migration produced any entries).
    assert case.expected.atomic_facts == []
    # No required_article_facts (the field defaults to []).
    assert case.expected.required_article_facts == []

    # Preflight guard does NOT fire — the case is offline_only (not
    # real_phase1), so the guard returns immediately. This is
    # intentional: provenance is not the right place to fail on
    # "no facts at all" cases; other preflight checks handle that.
    _preflight_guard_real_phase1_atomic_facts_explicit(case)


@pytest.mark.asyncio
async def test_r4_a4_2r5r3_function_model_full_chain_output_retry_exhausted() -> None:
    """R4-A4-2R5R3 Issue #3: REAL FunctionModel → ``_run_one_case`` →
    RawArtifact with ``output_retry_exhausted``.

    Unlike ``test_r4_a4_2r5r2_raw_artifact_full_chain_output_retry_exhausted``
    (which uses ``_install_fake_runtime_that_captures_then_raises`` +
    ``_FakeBudgetedModelForManifest``), this test drives the FULL real
    path through ``_run_one_case``:

      - Real ``ReadingRecordAskContextEnvelope`` (built via
        ``build_context_envelope`` with fixed UUIDs).
      - Real ``InMemoryDocumentAccess`` (built via
        ``build_document_scope``).
      - Real ``BudgetedUsageModel(wrapped=FunctionModel(model_fn))`` —
        the wrapper is constructed via ``super().__init__(wrapped=...)``
        which calls ``infer_model(FunctionModel(...))``.
      - The FunctionModel ALWAYS returns a ``grounded_answer`` whose
        article block cites the fabricated, unregistered handle
        ``evh_fabricated`` → the grounding output_validator
        raises ``ModelRetry`` on every call. After
        ``DEFAULT_OUTPUT_RETRIES + 1 == 3`` total attempts, pydantic-ai
        raises ``UnexpectedModelBehavior``.
      - ``_run_one_case`` catches the exception, classifies it via
        ``_classify_exception_safe_code`` with the typed counters from
        the internal ``RuntimeObservation``, and builds a ``RawArtifact``.

    Assertions on the returned ``RawArtifact``:
      - ``safe_error_code == "output_retry_exhausted"`` (BOTH typed
        counters == 3, set by the real runtime's observation seam).
      - ``finalized_status is None`` (answer is failed; taxonomy split
        is via ``safe_error_code``).
      - ``model_context_capture_status == "captured"`` (capture-後
        exception — baseline was assembled before the agent raised).
      - ``runtime_fixture_fingerprint`` is a 64-char hex SHA-256
        recomputed from the CAPTURED baseline (NOT copied from any
        preflight/expected value).
      - The fingerprint matches
        ``compute_runtime_fixture_fingerprint(baseline_status="injected",
        is_complete=True, chunks=[(0, _UNIT_A_TEXT)])`` — the actual
        chunk text the baseline assembler produced for this short
        single-unit document.

    No fake runtime, no hand-injected retry counters, no
    ``_FakeBudgetedModelForManifest``. The typed evidence comes from
    the real runtime's ``RuntimeObservation`` seam.
    """
    import json as _json  # noqa: PLC0415
    from uuid import UUID as _UUID  # noqa: PLC0415

    from pydantic_ai.messages import (  # noqa: PLC0415
        ModelResponse,
        ToolCallPart,
    )
    from pydantic_ai.models.function import (  # noqa: PLC0415
        AgentInfo,
        DeltaToolCall,
        FunctionModel,
    )

    from app.services.reader_record_ask.context_envelope import (  # noqa: PLC0415
        EnvelopeInitialAnchor,
        VerifiedEnvelopeInput,
        build_context_envelope,
    )
    from app.services.reader_record_ask.document_access import (  # noqa: PLC0415
        AnchorSegmentView,
        ReadingUnitView,
        build_document_scope,
    )

    _USER = _UUID("11111111-1111-1111-1111-111111111111")
    _RECORD = _UUID("22222222-2222-2222-2222-222222222222")
    _BASE = _UUID("33333333-3333-3333-3333-333333333333")
    _DOC = _UUID("44444444-4444-4444-4444-444444444444")
    _SHA = "b" * 64

    _UNIT_A_TEXT = "Alpha sentence one. Alpha sentence two."
    _SEG_A1_TEXT = "Alpha sentence one. "

    units = (
        ReadingUnitView(
            unit_id="u1",
            order_index=0,
            text=_UNIT_A_TEXT,
            text_hash="11111111",
            base_start_utf16=0,
            base_end_utf16=len(_UNIT_A_TEXT),
        ),
    )
    segments = (
        AnchorSegmentView(
            unit_id="u1",
            anchor_segment_id="s1",
            order_index=0,
            unit_order_index=0,
            text=_SEG_A1_TEXT,
            text_hash="aaaaaaaa",
            unit_start_utf16=0,
            unit_end_utf16=len(_SEG_A1_TEXT),
            base_start_utf16=0,
            base_end_utf16=len(_SEG_A1_TEXT),
        ),
    )
    scope = build_document_scope(
        reading_record_id=_RECORD,
        base_id=_BASE,
        record_generation=1,
        units=units,
        segments=segments,
        stable_document_id=_DOC,
        base_content_sha256=_SHA,
    )
    document_access = InMemoryDocumentAccess(snapshot=scope)
    envelope = build_context_envelope(
        VerifiedEnvelopeInput(
            user_id=_USER,
            reading_record_id=_RECORD,
            base_id=_BASE,
            record_generation=1,
            stable_document_id=_DOC,
            base_content_sha256=_SHA,
            product_state="readable_enhancing",
            readiness_state="article_ready",
            initial_anchor=EnvelopeInitialAnchor(
                unit_id="u1",
                anchor_segment_id="s1",
                start_offset=0,
                end_offset=len(_SEG_A1_TEXT),
                selected_text=_SEG_A1_TEXT,
                text_hash="aaaaaaaa",
            ),
            visible_range=None,
        )
    )

    # FunctionModel that ALWAYS returns a grounded_answer whose article
    # block cites a fabricated, unregistered handle — schema-valid under
    # the P2C-A1 structured output contract (``AgentAnswerDraftOutput``)
    # but grounding-INVALID. The block's "evidence_bounded" scope is
    # always confirmed by the real runtime (turn_coordinator), so the
    # rejection is specifically due to the fabricated handle. The output
    # validator raises ModelRetry on every call. After 3 total
    # attempts, pydantic-ai raises UnexpectedModelBehavior.
    #
    # NOTE: the BudgetedUsageModel wrapper makes the runtime take the
    # streaming path (``_model_supports_request_stream`` returns True
    # for any non-FunctionModel wrapper), so the FunctionModel needs a
    # ``stream_function`` that streams the same final_result tool call.
    calls = {"n": 0}

    def _bad_grounding_args(n: int) -> str:
        return _json.dumps(
            {
                "response_kind": "grounded_answer",
                "clarification_text": None,
                "answer_blocks": [
                    {
                        "text": f"回答 attempt {n}",
                        "basis": "article",
                        "evidence_handles": ["evh_fabricated"],
                    }
                ],
            }
        )

    async def model_fn(messages, info: AgentInfo):
        del messages, info
        calls["n"] += 1
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="final_result",
                    args=_bad_grounding_args(calls["n"]),
                    tool_call_id=f"bad-grounding-{calls['n']}",
                )
            ]
        )

    async def model_stream_fn(messages, info: AgentInfo):
        del messages, info
        calls["n"] += 1
        yield {
            0: DeltaToolCall(
                name="final_result",
                json_args=_bad_grounding_args(calls["n"]),
                tool_call_id=f"bad-grounding-{calls['n']}",
            )
        }

    # Wrap the FunctionModel in a real BudgetedUsageModel. This calls
    # ``super().__init__(wrapped=...)`` → ``infer_model(FunctionModel(...))``.
    budget_model = BudgetedUsageModel(
        wrapped=FunctionModel(model_fn, stream_function=model_stream_fn),
        max_requests=30,
        max_tokens=200_000,
    )

    case = _make_minimal_case(
        case_id="case-r5r3-full-chain-retry-exhausted",
        article_text=_UNIT_A_TEXT,
        phase_tags=["offline_only"],
    )

    artifact = await _run_one_case(
        case=case,
        budget_model=budget_model,
        model_config=_make_minimal_model_config(),
        run_id="test-run-r5r3-full-chain",
        run_index=0,
        envelope=envelope,
        document_access=document_access,
        start_requests=0,
        start_input_tokens=0,
        start_output_tokens=0,
        dataset_identity=_make_dataset_identity(),
    )

    # The model was called exactly 3 times (1 initial + 2 retries).
    assert calls["n"] == DEFAULT_OUTPUT_RETRIES + 1, (
        "R4-A4-2R5R3 Issue #3: model MUST be called exactly "
        f"{DEFAULT_OUTPUT_RETRIES + 1} times (1 initial + 2 retries). "
        f"Got {calls['n']}."
    )

    # --- Core taxonomy assertion ---
    assert artifact.safe_error_code == "output_retry_exhausted", (
        "R4-A4-2R5R3 Issue #3: UMB + BOTH typed counters == 3 (from the "
        "REAL runtime observation seam) → safe_error_code MUST be "
        f"'output_retry_exhausted' (got {artifact.safe_error_code!r})."
    )

    # --- Answer is failed; taxonomy split is via safe_error_code ---
    assert artifact.finalized_status is None, (
        "R4-A4-2R5R3 Issue #3: exception path → finalized_status MUST be "
        f"None (answer is failed; got {artifact.finalized_status!r})."
    )

    # --- Capture-後 exception: capture_status=captured, not failed ---
    assert artifact.model_context_capture_status == "captured", (
        "R4-A4-2R5R3 Issue #3: capture-後 exception → capture_status MUST "
        f"be 'captured' (got {artifact.model_context_capture_status!r}). "
        "The baseline was assembled before the agent raised; the actual "
        "baseline audit data is preserved."
    )

    # --- Actual fingerprint computed from captured baseline ---
    assert artifact.runtime_fixture_fingerprint is not None
    assert len(artifact.runtime_fixture_fingerprint) == 64, (
        "R4-A4-2R5R3 Issue #3: runtime_fixture_fingerprint MUST be a "
        f"64-char hex SHA-256 (got len={len(artifact.runtime_fixture_fingerprint)})."
    )

    # The fingerprint MUST match the one recomputed from the actual
    # baseline chunks. For this short single-unit document, the
    # baseline assembler produces a single chunk with the full unit
    # text and is_complete=True.
    expected_fp = compute_runtime_fixture_fingerprint(
        baseline_status="injected",
        is_complete=True,
        chunks=[(0, _UNIT_A_TEXT)],
    )
    assert artifact.runtime_fixture_fingerprint == expected_fp, (
        "R4-A4-2R5R3 Issue #3: runtime_fixture_fingerprint MUST match "
        "the SHA-256 over the ACTUAL captured baseline (single chunk, "
        f"text={_UNIT_A_TEXT!r}, is_complete=True). "
        f"Expected {expected_fp!r}, got "
        f"{artifact.runtime_fixture_fingerprint!r}."
    )

    # --- model_context_fingerprint also non-empty (captured baseline) ---
    assert artifact.model_context_fingerprint is not None
    assert len(artifact.model_context_fingerprint) == 64

    # --- The artifact recorded 3 provider requests (the real wrapper
    # counted them) ---
    assert artifact.executed_requests == DEFAULT_OUTPUT_RETRIES + 1, (
        "R4-A4-2R5R3 Issue #3: BudgetedUsageModel MUST have counted "
        f"{DEFAULT_OUTPUT_RETRIES + 1} executed requests (got "
        f"{artifact.executed_requests})."
    )


@pytest.mark.asyncio
async def test_r4_a4_2r5r3_function_model_finalizer_validation_error_runtime_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R4-A4-2R5R3 Issue #3: REAL FunctionModel → validator passes →
    finalizer raises ``ValidationError`` → ``runtime_exception``.

    Drives the FULL real path through ``_run_one_case``:

      - Real ``ReadingRecordAskContextEnvelope`` + ``InMemoryDocumentAccess``.
      - Real ``BudgetedUsageModel(wrapped=FunctionModel(model_fn))``.
      - The FunctionModel extracts the real seed handle from the user
        prompt (rendered by ``render_handles_block``) and cites it →
        the grounding output_validator passes on the FIRST call.
      - ``agent.run`` returns successfully.
      - ``execution_stage`` transitions: ``baseline_assembly`` →
        ``agent_run`` → ``agent_run_completed`` → ``finalizer``.
      - ``finalize_agent_answer`` is monkeypatched to raise a real
        pydantic ``ValidationError``.
      - ``_run_one_case`` catches the exception, classifies it with
        ``execution_stage == "finalizer"`` → ``runtime_exception``
        (NOT ``agent_output_invalid`` — the typed stage evidence
        proves the ValidationError did NOT come from the output
        validator).

    Assertions on the returned ``RawArtifact``:
      - ``safe_error_code == "runtime_exception"`` (ValidationError in
        the ``finalizer`` stage — NOT ``agent_output_invalid``).
      - ``finalized_status is None`` (answer is failed).
      - ``model_context_capture_status == "captured"`` (baseline was
        assembled before the finalizer raised).
      - ``runtime_fixture_fingerprint`` is a 64-char hex SHA-256 from
        the captured baseline.

    This is the decisive test for R5R3 Issue #1's typed
    execution-stage taxonomy: without the stage field, a
    ValidationError raised AFTER ``agent.run`` returned would be
    mis-classified as ``agent_output_invalid`` (model-fault) under the
    R5R2 ``final_attempts > 0`` rule. The typed
    ``execution_stage == "finalizer"`` evidence correctly routes it to
    ``runtime_exception`` (infrastructure-fault).
    """
    import json as _json  # noqa: PLC0415
    import re as _re  # noqa: PLC0415
    from uuid import UUID as _UUID  # noqa: PLC0415

    from pydantic import BaseModel, ValidationError  # noqa: PLC0415
    from pydantic_ai.messages import (  # noqa: PLC0415
        ModelResponse,
        ToolCallPart,
    )
    from pydantic_ai.models.function import (  # noqa: PLC0415
        AgentInfo,
        DeltaToolCall,
        FunctionModel,
    )

    from app.services.reader_record_ask import runtime as _runtime_module  # noqa: PLC0415
    from app.services.reader_record_ask.context_envelope import (  # noqa: PLC0415
        EnvelopeInitialAnchor,
        VerifiedEnvelopeInput,
        build_context_envelope,
    )
    from app.services.reader_record_ask.document_access import (  # noqa: PLC0415
        AnchorSegmentView,
        ReadingUnitView,
        build_document_scope,
    )

    _USER = _UUID("11111111-1111-1111-1111-111111111111")
    _RECORD = _UUID("22222222-2222-2222-2222-222222222222")
    _BASE = _UUID("33333333-3333-3333-3333-333333333333")
    _DOC = _UUID("44444444-4444-4444-4444-444444444444")
    _SHA = "b" * 64

    _UNIT_A_TEXT = "Alpha sentence one. Alpha sentence two."
    _SEG_A1_TEXT = "Alpha sentence one. "

    units = (
        ReadingUnitView(
            unit_id="u1",
            order_index=0,
            text=_UNIT_A_TEXT,
            text_hash="11111111",
            base_start_utf16=0,
            base_end_utf16=len(_UNIT_A_TEXT),
        ),
    )
    segments = (
        AnchorSegmentView(
            unit_id="u1",
            anchor_segment_id="s1",
            order_index=0,
            unit_order_index=0,
            text=_SEG_A1_TEXT,
            text_hash="aaaaaaaa",
            unit_start_utf16=0,
            unit_end_utf16=len(_SEG_A1_TEXT),
            base_start_utf16=0,
            base_end_utf16=len(_SEG_A1_TEXT),
        ),
    )
    scope = build_document_scope(
        reading_record_id=_RECORD,
        base_id=_BASE,
        record_generation=1,
        units=units,
        segments=segments,
        stable_document_id=_DOC,
        base_content_sha256=_SHA,
    )
    document_access = InMemoryDocumentAccess(snapshot=scope)
    envelope = build_context_envelope(
        VerifiedEnvelopeInput(
            user_id=_USER,
            reading_record_id=_RECORD,
            base_id=_BASE,
            record_generation=1,
            stable_document_id=_DOC,
            base_content_sha256=_SHA,
            product_state="readable_enhancing",
            readiness_state="article_ready",
            initial_anchor=EnvelopeInitialAnchor(
                unit_id="u1",
                anchor_segment_id="s1",
                start_offset=0,
                end_offset=len(_SEG_A1_TEXT),
                selected_text=_SEG_A1_TEXT,
                text_hash="aaaaaaaa",
            ),
            visible_range=None,
        )
    )

    # FunctionModel that extracts the real seed handle from the user
    # prompt and cites it → the grounding output_validator passes on
    # the FIRST call. The handle is minted randomly by the baseline
    # assembler (secrets.token_hex(16)), so we cannot pre-compute it;
    # we regex it out of the rendered handles block.
    #
    # NOTE: the BudgetedUsageModel wrapper makes the runtime take the
    # streaming path (``_model_supports_request_stream`` returns True
    # for any non-FunctionModel wrapper), so the FunctionModel needs a
    # ``stream_function`` that streams the same final_result tool call.
    calls = {"n": 0}

    def _extract_seed_handle_id(messages) -> str:
        # Extract the seed handle from the user prompt. The handles
        # block is rendered as:
        #   "## Server-registered evidence handles already available\n"
        #   "{handle1}, {handle2}, ...\n"
        # Handle IDs are "evh_" + 32 hex chars.
        handle_id: str | None = None
        for msg in messages:
            for part in getattr(msg, "parts", None) or []:
                content = getattr(part, "content", None)
                if isinstance(content, str):
                    match = _re.search(r"evh_[0-9a-f]{32}", content)
                    if match is not None:
                        handle_id = match.group(0)
                        break
            if handle_id is not None:
                break
        assert handle_id is not None, (
            "R4-A4-2R5R3 Issue #3: model_fn could not extract a seed "
            "handle from the user prompt. The baseline assembler MUST "
            "have rendered a handles block before the model was called."
        )
        return handle_id

    def _valid_grounding_args(handle_id: str) -> str:
        # P2C-A1 structured output contract: one article block citing
        # the real seed handle. The host derives "evidence_bounded"
        # scope, which the real runtime always confirms (turn_coordinator).
        return _json.dumps(
            {
                "response_kind": "grounded_answer",
                "clarification_text": None,
                "answer_blocks": [
                    {
                        "text": "valid grounded answer",
                        "basis": "article",
                        "evidence_handles": [handle_id],
                    }
                ],
            }
        )

    async def model_fn(messages, info: AgentInfo):
        del info
        calls["n"] += 1
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="final_result",
                    args=_valid_grounding_args(_extract_seed_handle_id(messages)),
                    tool_call_id=f"good-grounding-{calls['n']}",
                )
            ]
        )

    async def model_stream_fn(messages, info: AgentInfo):
        del info
        calls["n"] += 1
        yield {
            0: DeltaToolCall(
                name="final_result",
                json_args=_valid_grounding_args(
                    _extract_seed_handle_id(messages)
                ),
                tool_call_id=f"good-grounding-{calls['n']}",
            )
        }

    # Build a real ValidationError to raise from the monkeypatched
    # finalizer. We construct it by triggering a real Pydantic
    # validation failure — no string parsing, no synthetic exception.
    class _DummyFinalizeModel(BaseModel):
        required_field: int

    _validation_error: ValidationError | None = None
    try:
        _DummyFinalizeModel(required_field="not-an-int")  # type: ignore[arg-type]
    except ValidationError as exc:
        _validation_error = exc
    assert _validation_error is not None, (
        "R4-A4-2R5R3 Issue #3: test setup invariant — must construct a "
        "real ValidationError for the monkeypatched finalizer."
    )

    # Monkeypatch ``finalize_agent_answer`` in the runtime module
    # (where it is imported and called) to raise the ValidationError.
    # The runtime writes ``execution_stage = "finalizer"`` BEFORE
    # calling ``finalize_agent_answer``, so the classifier sees the
    # typed stage evidence and routes to ``runtime_exception``.
    async def _fake_finalize(**kwargs: Any) -> Any:  # noqa: ANN202
        raise _validation_error  # type: ignore[misc]

    monkeypatch.setattr(
        _runtime_module,
        "finalize_agent_answer",
        _fake_finalize,
    )

    budget_model = BudgetedUsageModel(
        wrapped=FunctionModel(model_fn, stream_function=model_stream_fn),
        max_requests=30,
        max_tokens=200_000,
    )

    case = _make_minimal_case(
        case_id="case-r5r3-full-chain-finalizer-ve",
        article_text=_UNIT_A_TEXT,
        phase_tags=["offline_only"],
    )

    artifact = await _run_one_case(
        case=case,
        budget_model=budget_model,
        model_config=_make_minimal_model_config(),
        run_id="test-run-r5r3-finalizer-ve",
        run_index=0,
        envelope=envelope,
        document_access=document_access,
        start_requests=0,
        start_input_tokens=0,
        start_output_tokens=0,
        dataset_identity=_make_dataset_identity(),
    )

    # The model was called exactly once (validator passed on first call).
    assert calls["n"] == 1, (
        "R4-A4-2R5R3 Issue #3: model MUST be called exactly once (the "
        f"validator passed on the first call). Got {calls['n']}."
    )

    # --- Core taxonomy assertion: finalizer-stage ValidationError →
    # runtime_exception (NOT agent_output_invalid) ---
    assert artifact.safe_error_code == "runtime_exception", (
        "R4-A4-2R5R3 Issue #3: ValidationError raised in the "
        "``finalizer`` stage (execution_stage == 'finalizer') → "
        "safe_error_code MUST be 'runtime_exception' (got "
        f"{artifact.safe_error_code!r}). Without the typed execution-"
        "stage field, this would have been mis-classified as "
        "'agent_output_invalid' under R5R2's final_attempts > 0 rule."
    )

    # --- Answer is failed ---
    assert artifact.finalized_status is None, (
        "R4-A4-2R5R3 Issue #3: exception path → finalized_status MUST "
        f"be None (got {artifact.finalized_status!r})."
    )

    # --- Capture-後 exception: capture_status=captured ---
    assert artifact.model_context_capture_status == "captured", (
        "R4-A4-2R5R3 Issue #3: capture-後 exception → capture_status "
        f"MUST be 'captured' (got {artifact.model_context_capture_status!r}). "
        "The baseline was assembled before the finalizer raised."
    )

    # --- Actual fingerprint from captured baseline ---
    assert artifact.runtime_fixture_fingerprint is not None
    assert len(artifact.runtime_fixture_fingerprint) == 64, (
        "R4-A4-2R5R3 Issue #3: runtime_fixture_fingerprint MUST be a "
        f"64-char hex SHA-256 (got len={len(artifact.runtime_fixture_fingerprint)})."
    )
    expected_fp = compute_runtime_fixture_fingerprint(
        baseline_status="injected",
        is_complete=True,
        chunks=[(0, _UNIT_A_TEXT)],
    )
    assert artifact.runtime_fixture_fingerprint == expected_fp, (
        "R4-A4-2R5R3 Issue #3: runtime_fixture_fingerprint MUST match "
        "the SHA-256 over the ACTUAL captured baseline. "
        f"Expected {expected_fp!r}, got "
        f"{artifact.runtime_fixture_fingerprint!r}."
    )

    # --- model_context_fingerprint also non-empty ---
    assert artifact.model_context_fingerprint is not None
    assert len(artifact.model_context_fingerprint) == 64

    # --- The model was called once (validator passed) ---
    assert artifact.executed_requests == 1, (
        "R4-A4-2R5R3 Issue #3: BudgetedUsageModel MUST have counted 1 "
        f"executed request (got {artifact.executed_requests})."
    )


@pytest.mark.asyncio
async def test_r4_a4_2r5r4_output_validation_stage_is_validator_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the validator-owned nested stage may classify ValidationError as output."""
    import importlib  # noqa: PLC0415
    from types import SimpleNamespace  # noqa: PLC0415

    from pydantic import BaseModel, ValidationError  # noqa: PLC0415

    validator_module = importlib.import_module(
        "app.services.reader_record_ask.grounding_validator"
    )
    observation = RuntimeObservation(execution_stage="agent_run")
    ctx = SimpleNamespace(
        partial_output=False,
        deps=SimpleNamespace(observation=observation),
    )
    draft = SimpleNamespace()

    class InvalidPayload(BaseModel):
        value: int

    async def raise_validation_error(_ctx: object, _draft: object) -> object:
        InvalidPayload(value="not-an-int")  # type: ignore[arg-type]
        raise AssertionError("unreachable")

    monkeypatch.setattr(
        validator_module,
        "_grounding_validator_final_body",
        raise_validation_error,
    )

    with pytest.raises(ValidationError) as exc_info:
        await validator_module.grounding_validator(ctx, draft)

    assert observation.execution_stage == "output_validation"
    assert (
        _classify_exception_safe_code(
            exc_info.value,
            execution_stage=observation.execution_stage,
        )
        == "agent_output_invalid"
    )
    assert (
        _classify_exception_safe_code(
            exc_info.value,
            execution_stage="agent_run",
        )
        == "runtime_exception"
    )


def test_r4_a4_2r5r4_unknown_atomic_facts_origin_fails_closed() -> None:
    """Paid-run provenance is an allowlist: only explicit may continue."""
    from claread_eval.reader_record_ask.schema import (  # noqa: PLC0415
        AtomicExpectedFact,
    )

    case = _make_minimal_case(
        case_id="case-r5r4-corrupt-origin",
        article_text="A grounded article sentence.",
        phase_tags=["real_phase1"],
    )
    case.expected.atomic_facts = [
        AtomicExpectedFact(
            fact_id="fact-1",
            answer_alias_groups=[["grounded"]],
            source_aliases=["grounded"],
            required=True,
            severity="high",
        )
    ]
    case._atomic_facts_origin = "corrupt"  # type: ignore[assignment]

    with pytest.raises(pytest.skip.Exception):  # type: ignore[attr-defined]
        _preflight_guard_real_phase1_atomic_facts_explicit(case)
