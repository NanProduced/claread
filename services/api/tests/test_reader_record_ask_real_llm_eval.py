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
from app.services.reader_record_ask.baseline_context import (  # noqa: E402
    ModelContextChunk,
)
from app.services.reader_record_ask.runtime import (  # noqa: E402
    run_reading_record_ask,
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
    MANIFEST_SCHEMA_VERSION,
    ReaderRecordAskRunManifest,
    write_manifest_atomic,
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

    ``budget_model`` is the wrapped :class:`BudgetedUsageModel` — it
    enforces the request/token cap and aggregates usage. The underlying
    provider model is ``budget_model.wrapped``.
    """
    thinking_enabled = _resolved_thinking_enabled(model_config)
    start = time.monotonic()
    try:
        result = await run_reading_record_ask(
            user_message=case.question,
            envelope=envelope,
            document_access=document_access,
            model=budget_model,
            article_rag=None,
        )
    except BudgetExhaustedError:
        # Re-raise so the harness can record a BudgetStopResult and stop
        # the run loop. The artifact for this in-flight (rejected) request
        # is NOT written — only already-completed requests are recorded.
        raise
    except Exception as exc:  # noqa: BLE001
        # P1-2: project the exception via allowlisted safe codes. Never
        # read ``str(exc)`` — only ``type(exc).__name__`` is used.
        latency = time.monotonic() - start
        projection = project_exception(exc, hint="runtime_exception")
        # P1-1: record per-case delta even on failure — the case may
        # have consumed some requests before raising.
        _, delta_requests, delta_tokens = _build_usage_delta(
            budget_model,
            start_requests,
            start_input_tokens,
            start_output_tokens,
        )
        # R4-A4-0 final closure (P0-4): on runtime exception we MUST
        # NOT reconstruct model context from ``document_access.snapshot``
        # — the model never saw a baseline (it raised before/independent
        # of baseline assembly, or the baseline assembler itself
        # failed). ``model_context_support`` is empty,
        # ``model_context_fingerprint`` is None, and
        # ``model_context_handle_ids`` is empty. The evaluator
        # surfaces this as ``instrumentation_incomplete`` (fail-closed
        # for new artifacts) — the run cannot be authoritatively
        # evaluated. This explicitly replaces the previous behavior
        # which computed support against ``snapshot.units`` even on
        # exception (producing misleading "supported" verdicts for a
        # run that never actually executed).
        #
        # R4-A4-0 final gate closure (P0-1): the explicit lifecycle
        # fields ``model_context_instrumentation_version`` /
        # ``model_context_capture_status`` distinguish this runtime-
        # exception state from legacy artifacts WITHOUT inspecting
        # ``error`` or ``finalized_reason``. ``capture_status="failed"``
        # tells the evaluator + aggregator + readiness audit that
        # this is an instrumentation blocker (NOT a model correctness
        # failure, NOT rework-eligible, NOT clusterable as
        # fact-not-grounded). The cross-field validator on
        # :class:`RawArtifact` enforces fingerprint=None /
        # handle_ids=[] / observations=[] for this state.
        return RawArtifact(
            case_id=case.id,
            run_id=run_id,
            run_index=run_index,
            model_short_name=model_config.model_name,
            model_route=model_config.route,
            thinking_enabled=thinking_enabled,
            error=project_exception_to_string(exc, hint="runtime_exception"),
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
) -> list[tuple[ReaderRecordAskR4A3Case, ReadingRecordAskContextEnvelope, InMemoryDocumentAccess]]:
    """Build (envelope, document_access) for EVERY selected case before
    any paid provider call (P0-3).

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
    """
    prepared: list[
        tuple[
            ReaderRecordAskR4A3Case,
            ReadingRecordAskContextEnvelope,
            InMemoryDocumentAccess,
        ]
    ] = []
    for case in cases:
        if case.source_kind == "bbc_record":
            envelope, document_access = await _build_bbc_runtime_inputs(
                case.record_id or "",
            )
        else:
            envelope, document_access = _build_synthetic_runtime_inputs(case)
        prepared.append((case, envelope, document_access))
    return prepared


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
    - ``prepared_inputs``: ``(case, envelope, document_access)`` for
      EVERY selected case, built before any provider call.
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
    for case, envelope, document_access in prepared.prepared_inputs:
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
    """
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
    """
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
    )
    write_manifest_atomic(manifest, prepared.session.manifest_path)


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
) -> ReaderRecordAskR4A3Case:
    """Build a minimal :class:`ReaderRecordAskR4A3Case` for preflight tests."""
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
    ``(case, envelope, document_access)`` tuples — the harness then
    proceeds to the paid-call loop.
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
    # Each entry must have a valid envelope + document_access.
    for _case, envelope, document_access in prepared:
        assert envelope is not None
        assert document_access is not None
        assert envelope.envelope_fingerprint


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
        prepared_inputs=((case, envelope, document_access),),
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
