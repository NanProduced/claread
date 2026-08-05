# task-history: T5.6b (renamed from test_reader_section_translation_t56b.py)
"""Focused unit tests for section translation bootstrap/drain/publisher contracts (fake executor)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.reader_orchestration.execution_budget import (
    DurableBudgetLoadResult,
    ExecutionBudget,
    ExecutionBudgetSnapshot,
)
from app.services.reader_orchestration.job_bootstrap import (
    TRANSLATION_BATCH_JOB_TYPE,
    TRANSLATION_BATCH_TARGET_SCOPE,
    _LockedActiveBaseState,
)
from app.services.reader_orchestration.job_runtime import ClaimResult
from app.services.reader_orchestration.layer_publisher import (
    TranslationPublishValidationError,
    _validate_section_translation_job_shape,
)
from app.services.reader_orchestration.reading_strategy import (
    resolve_reader_variant_strategy,
)
from app.services.reader_orchestration.section_identity import (
    encode_section_target_key,
    try_build_section_identity,
    SectionUnit,
)
from app.services.reader_orchestration.section_lane import (
    SECTION_REQUEST_ORIGIN,
    TRANSLATION_SECTION_OPERATION_FINGERPRINT,
)
from app.services.reader_orchestration.section_request_planner import (
    ExplicitSectionIntent,
    PlanOutcomeKind,
    SectionRequestTrigger,
    plan_explicit_section_request,
    SectionPlannerFacts,
    REASON_SECTION_ALREADY_COVERED_OR_INFLIGHT,
)
from app.services.reader_orchestration.section_translation_bootstrap import (
    REASON_LAYER_FAMILY_NOT_TRANSLATION,
    REASON_TRANSLATION_BUDGET_EXHAUSTED,
    SectionBootstrapOutcome,
    SectionTranslationBootstrapService,
    parse_trusted_outline_payload,
)
from app.services.reader_orchestration.section_translation_drain import (
    SectionDrainOutcome,
    SectionTranslationDrainService,
)
from app.services.reader_orchestration.section_candidates import (
    OutlineNodeInput,
    TrustedOutlineInput,
)
from app.services.reader_orchestration.job_bootstrap import _fingerprint_matches_base

pytestmark = [
    pytest.mark.chain_reader_orchestration,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]

_UNITS = (
    SectionUnit("u1", 1),
    SectionUnit("u2", 2),
    SectionUnit("u3", 3),
    SectionUnit("u4", 4),
)


def _strategy():
    return resolve_reader_variant_strategy("daily_reading", "intermediate_reading")


def _state(**kw):
    base = dict(
        record_id=uuid4(),
        user_id=uuid4(),
        base_id=uuid4(),
        expected_generation=1,
        base_language="en",
        last_event_sequence=0,
        strategy=_strategy(),
        readiness_state="article_ready",
    )
    base.update(kw)
    return _LockedActiveBaseState(**base)


def _outline_ready():
    return TrustedOutlineInput(
        status="ready",
        source_base_id="base_1",
        source_generation=1,
        outline_revision="r1",
        nodes=(
            OutlineNodeInput("n1", "u1", "u2", title="A", order_index=1),
            OutlineNodeInput("n2", "u3", "u4", title="B", order_index=2),
        ),
    )


def _facts(**kw):
    base = dict(
        authorized=True,
        record_id="rec_1",
        base_id="base_1",
        generation=1,
        ordered_units=_UNITS,
        anchor_to_unit={},
        trusted_outline=_outline_ready(),
        published_units_by_family={},
        active_target_units_by_family={},
        active_section_ranges_by_family={},
    )
    base.update(kw)
    return SectionPlannerFacts(**base)


def _intent(**kw):
    base = dict(
        trigger=SectionRequestTrigger.USER_EXPLICIT,
        layer_family="translation",
        record_id="rec_1",
        base_id="base_1",
        generation=1,
        start_unit_id="u3",
        end_unit_id="u4",
    )
    base.update(kw)
    return ExplicitSectionIntent(**base)


def _exhausted_budget_result() -> DurableBudgetLoadResult:
    snap = ExecutionBudgetSnapshot(
        planned_calls=1,
        max_effective_calls=3,
        consumed_calls=3,
        remaining_calls=0,
        exhausted=True,
    )
    empty = ExecutionBudgetSnapshot(
        planned_calls=0,
        max_effective_calls=0,
        consumed_calls=0,
        remaining_calls=0,
        exhausted=False,
    )
    return DurableBudgetLoadResult(
        layer_snapshots={
            "translation": snap,
            "vocabulary": empty,
            "grammar": empty,
        },
        non_superseded_fingerprints={
            "translation": (),
            "vocabulary": (),
            "grammar": (),
        },
    )


def _fresh_budget_result() -> DurableBudgetLoadResult:
    snap = ExecutionBudgetSnapshot(
        planned_calls=1,
        max_effective_calls=6,
        consumed_calls=1,
        remaining_calls=5,
        exhausted=False,
    )
    empty = ExecutionBudgetSnapshot(
        planned_calls=0,
        max_effective_calls=0,
        consumed_calls=0,
        remaining_calls=0,
        exhausted=False,
    )
    return DurableBudgetLoadResult(
        layer_snapshots={
            "translation": snap,
            "vocabulary": empty,
            "grammar": empty,
        },
        non_superseded_fingerprints={
            "translation": ("translation_article_v1:h",),
            "vocabulary": (),
            "grammar": (),
        },
    )


# ---------------------------------------------------------------------------
# Planner overlap (OV) — pure
# ---------------------------------------------------------------------------


def test_ov01_published_overlap_noop() -> None:
    result = plan_explicit_section_request(
        _intent(),
        _facts(published_units_by_family={"translation": frozenset({"u4"})}),
    )
    assert result.kind is PlanOutcomeKind.NO_OP
    assert result.reason == REASON_SECTION_ALREADY_COVERED_OR_INFLIGHT


def test_ov02_active_window_overlap_noop() -> None:
    result = plan_explicit_section_request(
        _intent(),
        _facts(
            active_target_units_by_family={
                "translation": frozenset({"u3", "u4", "u5"})
            }
        ),
    )
    assert result.kind is PlanOutcomeKind.NO_OP


def test_cross_family_not_blocked() -> None:
    result = plan_explicit_section_request(
        _intent(layer_family="translation"),
        _facts(
            published_units_by_family={
                "vocabulary": frozenset({"u3", "u4"}),
            },
            active_target_units_by_family={
                "vocabulary": frozenset({"u1", "u2", "u3", "u4"}),
            },
        ),
    )
    assert result.kind is PlanOutcomeKind.ADMIT


# ---------------------------------------------------------------------------
# Publisher shape (PZ)
# ---------------------------------------------------------------------------


def _section_job_row(
    *,
    target_key: str,
    record_id: str = "rec_1",
    base_id: str = "base_1",
    generation: int = 1,
) -> dict:
    return {
        "target_key": target_key,
        "reading_record_id": record_id,
        "base_id": base_id,
        "expected_generation": generation,
    }


def _section_identity_payload(
    *,
    record_id: str = "rec_1",
    base_id: str = "base_1",
    generation: int = 1,
    start_unit_id: str = "u3",
    end_unit_id: str = "u4",
) -> dict:
    return {
        "record_id": record_id,
        "base_id": base_id,
        "generation": generation,
        "start_unit_id": start_unit_id,
        "end_unit_id": end_unit_id,
        "start_anchor_segment_id": None,
        "end_anchor_segment_id": None,
    }


def test_pz01_section_publisher_rejects_forged_shapes() -> None:
    identity = try_build_section_identity(
        record_id="rec_1",
        base_id="base_1",
        generation=1,
        start_unit_id="u3",
        end_unit_id="u4",
        ordered_units=_UNITS,
    )
    good_key = encode_section_target_key(identity)
    job_row = _section_job_row(target_key=good_key)
    # Missing origin with section fp
    with pytest.raises(TranslationPublishValidationError):
        _validate_section_translation_job_shape(
            job_row=job_row,
            input_json={
                "target_unit_ids": ["u3", "u4"],
                "section_identity": _section_identity_payload(),
            },
            operation_fingerprint=f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:abc",
            ordered_units=_UNITS,
        )
    # Bad target key
    with pytest.raises(TranslationPublishValidationError):
        _validate_section_translation_job_shape(
            job_row=_section_job_row(target_key="unit_range_v1|01.a|1.b|0.|0."),
            input_json={
                "request_origin": SECTION_REQUEST_ORIGIN,
                "target_unit_ids": ["u3", "u4"],
                "section_identity": _section_identity_payload(),
            },
            operation_fingerprint=f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:abc",
            ordered_units=_UNITS,
        )
    # Ordinary job — no raise
    _validate_section_translation_job_shape(
        job_row={"target_key": str(uuid4())},
        input_json={"target_unit_ids": ["u1"]},
        operation_fingerprint="translation_article_v1:hash",
        ordered_units=_UNITS,
    )


def test_pz01_section_publisher_accepts_canonical_shape() -> None:
    identity = try_build_section_identity(
        record_id="rec_1",
        base_id="base_1",
        generation=1,
        start_unit_id="u3",
        end_unit_id="u4",
        ordered_units=_UNITS,
    )
    key = encode_section_target_key(identity)
    _validate_section_translation_job_shape(
        job_row=_section_job_row(target_key=key),
        input_json={
            "request_origin": SECTION_REQUEST_ORIGIN,
            "target_unit_ids": ["u3", "u4"],
            "section_identity": _section_identity_payload(),
        },
        operation_fingerprint=f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:xyz",
        ordered_units=_UNITS,
    )


def test_pz_rejects_missing_middle_unit() -> None:
    identity = try_build_section_identity(
        record_id="rec_1",
        base_id="base_1",
        generation=1,
        start_unit_id="u1",
        end_unit_id="u4",
        ordered_units=_UNITS,
    )
    key = encode_section_target_key(identity)
    with pytest.raises(TranslationPublishValidationError) as exc:
        _validate_section_translation_job_shape(
            job_row=_section_job_row(target_key=key),
            input_json={
                "request_origin": SECTION_REQUEST_ORIGIN,
                "target_unit_ids": ["u1", "u2", "u4"],  # missing u3
                "section_identity": _section_identity_payload(
                    start_unit_id="u1", end_unit_id="u4"
                ),
            },
            operation_fingerprint=f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:h",
            ordered_units=_UNITS,
        )
    assert exc.value.failure_code == "section_target_units_not_canonical"


def test_pz_rejects_duplicate_unit() -> None:
    identity = try_build_section_identity(
        record_id="rec_1",
        base_id="base_1",
        generation=1,
        start_unit_id="u1",
        end_unit_id="u3",
        ordered_units=_UNITS,
    )
    key = encode_section_target_key(identity)
    with pytest.raises(TranslationPublishValidationError) as exc:
        _validate_section_translation_job_shape(
            job_row=_section_job_row(target_key=key),
            input_json={
                "request_origin": SECTION_REQUEST_ORIGIN,
                "target_unit_ids": ["u1", "u2", "u2", "u3"],
                "section_identity": _section_identity_payload(
                    start_unit_id="u1", end_unit_id="u3"
                ),
            },
            operation_fingerprint=f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:h",
            ordered_units=_UNITS,
        )
    assert exc.value.failure_code == "section_target_units_not_canonical"


def test_pz_rejects_extra_external_unit() -> None:
    identity = try_build_section_identity(
        record_id="rec_1",
        base_id="base_1",
        generation=1,
        start_unit_id="u1",
        end_unit_id="u2",
        ordered_units=_UNITS,
    )
    key = encode_section_target_key(identity)
    with pytest.raises(TranslationPublishValidationError) as exc:
        _validate_section_translation_job_shape(
            job_row=_section_job_row(target_key=key),
            input_json={
                "request_origin": SECTION_REQUEST_ORIGIN,
                "target_unit_ids": ["u1", "u2", "u3"],  # u3 outside range ends
                "section_identity": _section_identity_payload(
                    start_unit_id="u1", end_unit_id="u2"
                ),
            },
            operation_fingerprint=f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:h",
            ordered_units=_UNITS,
        )
    assert exc.value.failure_code == "section_target_units_not_canonical"


def test_pz_rejects_out_of_order_units() -> None:
    identity = try_build_section_identity(
        record_id="rec_1",
        base_id="base_1",
        generation=1,
        start_unit_id="u1",
        end_unit_id="u3",
        ordered_units=_UNITS,
    )
    key = encode_section_target_key(identity)
    with pytest.raises(TranslationPublishValidationError) as exc:
        _validate_section_translation_job_shape(
            job_row=_section_job_row(target_key=key),
            input_json={
                "request_origin": SECTION_REQUEST_ORIGIN,
                "target_unit_ids": ["u1", "u3", "u2"],
                "section_identity": _section_identity_payload(
                    start_unit_id="u1", end_unit_id="u3"
                ),
            },
            operation_fingerprint=f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:h",
            ordered_units=_UNITS,
        )
    assert exc.value.failure_code == "section_target_units_not_canonical"


def test_pz_rejects_identity_source_mismatch() -> None:
    identity = try_build_section_identity(
        record_id="rec_1",
        base_id="base_1",
        generation=1,
        start_unit_id="u3",
        end_unit_id="u4",
        ordered_units=_UNITS,
    )
    key = encode_section_target_key(identity)
    for bad_identity, code in (
        (
            _section_identity_payload(record_id="other"),
            "section_identity_record_mismatch",
        ),
        (
            _section_identity_payload(base_id="other"),
            "section_identity_base_mismatch",
        ),
        (
            _section_identity_payload(generation=99),
            "section_identity_generation_mismatch",
        ),
    ):
        with pytest.raises(TranslationPublishValidationError) as exc:
            _validate_section_translation_job_shape(
                job_row=_section_job_row(target_key=key),
                input_json={
                    "request_origin": SECTION_REQUEST_ORIGIN,
                    "target_unit_ids": ["u3", "u4"],
                    "section_identity": bad_identity,
                },
                operation_fingerprint=f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:h",
                ordered_units=_UNITS,
            )
        assert exc.value.failure_code == code


def test_section_fingerprint_base_match() -> None:
    assert _fingerprint_matches_base(
        f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:deadbeef",
        TRANSLATION_SECTION_OPERATION_FINGERPRINT,
    )


# ---------------------------------------------------------------------------
# Bootstrap budget (BG-02)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bg02_bootstrap_skips_insert_when_budget_exhausted() -> None:
    service = SectionTranslationBootstrapService(pool=MagicMock())
    state = _state()
    conn = AsyncMock()
    # transaction context manager
    tx = AsyncMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=tx)
    acquire_cm = AsyncMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)
    service.get_pool = MagicMock(return_value=MagicMock(acquire=MagicMock(return_value=acquire_cm)))

    with (
        patch(
            "app.services.reader_orchestration.section_translation_bootstrap._load_locked_active_base_state",
            AsyncMock(return_value=state),
        ),
        patch(
            "app.services.reader_orchestration.section_translation_bootstrap.ExecutionBudget.load_durable",
            AsyncMock(return_value=_exhausted_budget_result()),
        ),
    ):
        result = await service.request_section_translation(
            record_id=state.record_id,
            user_id=state.user_id,
            intent=_intent(
                record_id=str(state.record_id),
                base_id=str(state.base_id),
                generation=1,
            ),
        )
    assert result.outcome is SectionBootstrapOutcome.NO_OP
    assert result.reason == REASON_TRANSLATION_BUDGET_EXHAUSTED
    assert result.job_id is None


# ---------------------------------------------------------------------------
# Drain budget + claim (BG-03, DR-05, already_claimed)
# ---------------------------------------------------------------------------


def _section_row(
    *,
    job_id,
    record_id,
    base_id,
    status: str = "queued",
    run_id=None,
    generation: int = 1,
):
    return {
        "id": job_id,
        "reading_record_id": record_id,
        "base_id": base_id,
        "expected_generation": generation,
        "status": status,
        "run_id": run_id or uuid4(),
        "job_type": TRANSLATION_BATCH_JOB_TYPE,
        "target_type": TRANSLATION_BATCH_TARGET_SCOPE,
        "operation_fingerprint": f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:h",
        "input_json": {"request_origin": SECTION_REQUEST_ORIGIN},
        "rationale_code": None,
    }


def _mock_pool_conn(conn: AsyncMock) -> MagicMock:
    tx = AsyncMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=tx)
    acquire_cm = AsyncMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(acquire=MagicMock(return_value=acquire_cm))


@pytest.mark.asyncio
async def test_bg03_drain_budget_exhausted_force_fails_without_claim() -> None:
    job_id = uuid4()
    record_id = uuid4()
    base_id = uuid4()
    run_id = uuid4()
    runtime = AsyncMock()
    runtime.claim_job_by_id = AsyncMock(return_value=None)
    runtime._validate_fence = AsyncMock(return_value=None)
    runtime._mark_job_superseded = AsyncMock()
    worker = AsyncMock()
    service = SectionTranslationDrainService(
        pool=MagicMock(),
        job_runtime=runtime,
        translation_worker=worker,
    )
    section = _section_row(
        job_id=job_id, record_id=record_id, base_id=base_id, run_id=run_id
    )
    conn = AsyncMock()
    # prepare SELECT → force_fail SELECT → force_fail UPDATE RETURNING
    conn.fetchrow = AsyncMock(
        side_effect=[
            section,
            section,
            {"id": job_id, "run_id": run_id},
        ]
    )
    conn.execute = AsyncMock()
    service.get_pool = MagicMock(return_value=_mock_pool_conn(conn))

    with patch(
        "app.services.reader_orchestration.section_translation_drain.ExecutionBudget.load_durable",
        AsyncMock(return_value=_exhausted_budget_result()),
    ):
        result = await service.process_job_id(
            job_id=job_id,
            lease_owner="drain-test",
            expected_reading_record_id=record_id,
            expected_base_id=base_id,
            expected_generation=1,
        )
    assert result.outcome is SectionDrainOutcome.BUDGET_DENIED
    runtime.claim_job_by_id.assert_not_called()
    worker.process_claimed_translation_batch_job.assert_not_called()


@pytest.mark.asyncio
async def test_bg03b_exhausted_budget_ordinary_job_noop() -> None:
    """Ordinary translate_article must not be claimed or budget-failed."""
    job_id = uuid4()
    record_id = uuid4()
    base_id = uuid4()
    runtime = AsyncMock()
    runtime.claim_job_by_id = AsyncMock(return_value=None)
    worker = AsyncMock()
    service = SectionTranslationDrainService(
        pool=MagicMock(),
        job_runtime=runtime,
        translation_worker=worker,
    )
    ordinary = {
        "id": job_id,
        "reading_record_id": record_id,
        "base_id": base_id,
        "expected_generation": 1,
        "status": "queued",
        "run_id": uuid4(),
        "job_type": TRANSLATION_BATCH_JOB_TYPE,
        "target_type": TRANSLATION_BATCH_TARGET_SCOPE,
        "operation_fingerprint": "translation_article_v1:h",
        "input_json": {},  # no section origin
        "rationale_code": None,
    }
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=ordinary)
    conn.execute = AsyncMock()
    service.get_pool = MagicMock(return_value=_mock_pool_conn(conn))

    with patch(
        "app.services.reader_orchestration.section_translation_drain.ExecutionBudget.load_durable",
        AsyncMock(return_value=_exhausted_budget_result()),
    ):
        result = await service.process_job_id(
            job_id=job_id,
            lease_owner="drain-test",
            expected_reading_record_id=record_id,
            expected_base_id=base_id,
            expected_generation=1,
        )
    assert result.outcome is SectionDrainOutcome.REJECTED
    runtime.claim_job_by_id.assert_not_called()
    worker.process_claimed_translation_batch_job.assert_not_called()
    # No mutation: execute should not have been used for UPDATE.
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_bg03c_exhausted_budget_stale_section_superseded() -> None:
    job_id = uuid4()
    record_id = uuid4()
    base_id = uuid4()
    runtime = AsyncMock()
    runtime.claim_job_by_id = AsyncMock(return_value=None)
    runtime._validate_fence = AsyncMock(return_value="stale_generation")
    runtime._mark_job_superseded = AsyncMock()
    worker = AsyncMock()
    service = SectionTranslationDrainService(
        pool=MagicMock(),
        job_runtime=runtime,
        translation_worker=worker,
    )
    section = _section_row(job_id=job_id, record_id=record_id, base_id=base_id)
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=section)
    conn.execute = AsyncMock()
    service.get_pool = MagicMock(return_value=_mock_pool_conn(conn))

    with patch(
        "app.services.reader_orchestration.section_translation_drain.ExecutionBudget.load_durable",
        AsyncMock(return_value=_exhausted_budget_result()),
    ):
        result = await service.process_job_id(
            job_id=job_id,
            lease_owner="drain-test",
            expected_reading_record_id=record_id,
            expected_base_id=base_id,
            expected_generation=1,
        )
    assert result.outcome is SectionDrainOutcome.SUPERSEDED
    assert result.detail == "stale_generation"
    runtime._mark_job_superseded.assert_awaited()
    runtime.claim_job_by_id.assert_not_called()
    worker.process_claimed_translation_batch_job.assert_not_called()


@pytest.mark.asyncio
async def test_dr_already_claimed_no_double_execute() -> None:
    job_id = uuid4()
    record_id = uuid4()
    base_id = uuid4()
    runtime = AsyncMock()
    runtime.claim_job_by_id = AsyncMock(return_value=None)
    runtime._validate_fence = AsyncMock(return_value=None)
    worker = AsyncMock()
    service = SectionTranslationDrainService(
        pool=MagicMock(),
        job_runtime=runtime,
        translation_worker=worker,
    )
    section = _section_row(
        job_id=job_id, record_id=record_id, base_id=base_id, status="claimed"
    )
    conn = AsyncMock()
    # prepare SELECT (ok), then claim miss re-read
    conn.fetchrow = AsyncMock(side_effect=[section, section])
    service.get_pool = MagicMock(return_value=_mock_pool_conn(conn))
    with patch(
        "app.services.reader_orchestration.section_translation_drain.ExecutionBudget.load_durable",
        AsyncMock(return_value=_fresh_budget_result()),
    ):
        result = await service.process_job_id(
            job_id=job_id,
            lease_owner="drain-a",
            expected_reading_record_id=record_id,
            expected_base_id=base_id,
            expected_generation=1,
        )
    assert result.outcome is SectionDrainOutcome.ALREADY_CLAIMED
    worker.process_claimed_translation_batch_job.assert_not_called()


@pytest.mark.asyncio
async def test_bg04_successful_claim_then_process_once() -> None:
    job_id = uuid4()
    record_id = uuid4()
    base_id = uuid4()
    claim = ClaimResult(
        job_id=job_id,
        run_id=uuid4(),
        reading_record_id=record_id,
        user_id=uuid4(),
        base_id=base_id,
        job_type=TRANSLATION_BATCH_JOB_TYPE,
        target_type=TRANSLATION_BATCH_TARGET_SCOPE,
        target_key="unit_range_v1|2.u3|2.u4|0.|0.",
        expected_generation=1,
        operation_fingerprint=f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:h",
        attempt_count=1,
        lease_owner="drain",
        lease_token=uuid4(),
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )
    runtime = AsyncMock()
    runtime.claim_job_by_id = AsyncMock(return_value=claim)
    runtime._validate_fence = AsyncMock(return_value=None)
    worker = AsyncMock()
    worker.process_claimed_translation_batch_job = AsyncMock(
        return_value=SimpleNamespace(status="succeeded")
    )
    service = SectionTranslationDrainService(
        pool=MagicMock(),
        job_runtime=runtime,
        translation_worker=worker,
    )
    section = _section_row(job_id=job_id, record_id=record_id, base_id=base_id)
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=section)
    service.get_pool = MagicMock(return_value=_mock_pool_conn(conn))
    with patch(
        "app.services.reader_orchestration.section_translation_drain.ExecutionBudget.load_durable",
        AsyncMock(return_value=_fresh_budget_result()),
    ):
        result = await service.process_job_id(
            job_id=job_id,
            lease_owner="drain",
            expected_reading_record_id=record_id,
            expected_base_id=base_id,
            expected_generation=1,
        )
    assert result.outcome is SectionDrainOutcome.SUCCEEDED
    runtime.claim_job_by_id.assert_awaited_once()
    worker.process_claimed_translation_batch_job.assert_awaited_once()
    # Claim increments attempt_count once (BG-04 single accounting at claim).
    assert claim.attempt_count == 1


def test_bg01_bg05_budget_layer_includes_translate_article() -> None:
    from app.services.reader_orchestration.execution_budget import (
        _JOB_TYPE_TO_BUDGET_LAYER,
        WORKER_TYPE_TO_BUDGET_LAYER,
    )

    assert _JOB_TYPE_TO_BUDGET_LAYER["translate_article"] == "translation"
    assert WORKER_TYPE_TO_BUDGET_LAYER["translation_batch"] == "translation"
    # Section uses same job_type → same layer (no second budget).
    assert "section" not in _JOB_TYPE_TO_BUDGET_LAYER


# ---------------------------------------------------------------------------
# Family force (P1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_family_forged_vocabulary_rejects_without_insert() -> None:
    service = SectionTranslationBootstrapService(pool=MagicMock())
    state = _state()
    # Should reject before any pool acquire / insert.
    result = await service.request_section_translation(
        record_id=state.record_id,
        user_id=state.user_id,
        intent=_intent(layer_family="vocabulary"),
    )
    assert result.outcome is SectionBootstrapOutcome.REJECT
    assert result.reason == REASON_LAYER_FAMILY_NOT_TRANSLATION
    assert result.job_id is None


@pytest.mark.asyncio
async def test_family_forged_grammar_rejects_without_insert() -> None:
    service = SectionTranslationBootstrapService(pool=MagicMock())
    state = _state()
    result = await service.request_section_translation(
        record_id=state.record_id,
        user_id=state.user_id,
        intent=_intent(layer_family="grammar"),
    )
    assert result.outcome is SectionBootstrapOutcome.REJECT
    assert result.reason == REASON_LAYER_FAMILY_NOT_TRANSLATION


@pytest.mark.asyncio
async def test_family_none_still_plans_as_translation() -> None:
    """layer_family=None is allowed; server forces translation for planner."""
    service = SectionTranslationBootstrapService(pool=MagicMock())
    state = _state()
    conn = AsyncMock()
    service.get_pool = MagicMock(return_value=_mock_pool_conn(conn))

    captured: dict = {}

    def _plan(intent, facts):
        captured["layer_family"] = intent.layer_family
        return SimpleNamespace(
            kind=PlanOutcomeKind.REJECT,
            reason="stop_after_family_check",
            identity=None,
            target_unit_ids=(),
            audit=None,
        )

    with (
        patch(
            "app.services.reader_orchestration.section_translation_bootstrap._load_locked_active_base_state",
            AsyncMock(return_value=state),
        ),
        patch(
            "app.services.reader_orchestration.section_translation_bootstrap.ExecutionBudget.load_durable",
            AsyncMock(return_value=_fresh_budget_result()),
        ),
        patch.object(
            service,
            "_load_planner_facts",
            AsyncMock(return_value=_facts()),
        ),
        patch(
            "app.services.reader_orchestration.section_translation_bootstrap.plan_explicit_section_request",
            side_effect=_plan,
        ),
    ):
        result = await service.request_section_translation(
            record_id=state.record_id,
            user_id=state.user_id,
            intent=_intent(layer_family=None),
        )
    assert captured["layer_family"] == "translation"
    assert result.outcome is SectionBootstrapOutcome.REJECT


# ---------------------------------------------------------------------------
# Trusted outline fail-closed (P1)
# ---------------------------------------------------------------------------


def test_outline_missing_source_identity_returns_none() -> None:
    assert (
        parse_trusted_outline_payload(
            {"status": "ready", "nodes": []},
            expected_base_id="base_1",
            expected_generation=1,
        )
        is None
    )


def test_outline_source_mismatch_returns_none() -> None:
    payload = {
        "status": "ready",
        "source_identity": {"base_id": "other", "generation": 1},
        "nodes": [
            {
                "node_id": "n1",
                "start_unit_id": "u1",
                "end_unit_id": "u2",
                "order_index": 1,
            }
        ],
    }
    assert (
        parse_trusted_outline_payload(
            payload, expected_base_id="base_1", expected_generation=1
        )
        is None
    )


def test_outline_does_not_fill_missing_source_fields() -> None:
    payload = {
        "status": "ready",
        "source_identity": {"base_id": "base_1"},  # generation missing
        "nodes": [
            {
                "node_id": "n1",
                "start_unit_id": "u1",
                "end_unit_id": "u2",
                "order_index": 1,
            }
        ],
    }
    assert (
        parse_trusted_outline_payload(
            payload, expected_base_id="base_1", expected_generation=1
        )
        is None
    )


def test_outline_bad_node_fails_closed() -> None:
    good = {
        "node_id": "n1",
        "start_unit_id": "u1",
        "end_unit_id": "u2",
        "order_index": 1,
    }
    # non-object node
    assert (
        parse_trusted_outline_payload(
            {
                "status": "ready",
                "source_identity": {"base_id": "base_1", "generation": 1},
                "nodes": [good, "bad"],
            },
            expected_base_id="base_1",
            expected_generation=1,
        )
        is None
    )
    # missing start
    assert (
        parse_trusted_outline_payload(
            {
                "status": "ready",
                "source_identity": {"base_id": "base_1", "generation": 1},
                "nodes": [{**good, "start_unit_id": ""}],
            },
            expected_base_id="base_1",
            expected_generation=1,
        )
        is None
    )
    # illegal order_index
    assert (
        parse_trusted_outline_payload(
            {
                "status": "ready",
                "source_identity": {"base_id": "base_1", "generation": 1},
                "nodes": [{**good, "order_index": "1"}],
            },
            expected_base_id="base_1",
            expected_generation=1,
        )
        is None
    )
    # nodes not a list
    assert (
        parse_trusted_outline_payload(
            {
                "status": "ready",
                "source_identity": {"base_id": "base_1", "generation": 1},
                "nodes": {"n1": good},
            },
            expected_base_id="base_1",
            expected_generation=1,
        )
        is None
    )


def test_outline_valid_payload_admits() -> None:
    payload = {
        "status": "ready",
        "source_identity": {"base_id": "base_1", "generation": 1},
        "nodes": [
            {
                "node_id": "n1",
                "start_unit_id": "u1",
                "end_unit_id": "u2",
                "order_index": 1,
                "title": "A",
            }
        ],
        "publication": {"outline_revision": "r1"},
    }
    outline = parse_trusted_outline_payload(
        payload, expected_base_id="base_1", expected_generation=1
    )
    assert outline is not None
    assert outline.source_base_id == "base_1"
    assert outline.source_generation == 1
    assert len(outline.nodes) == 1
    assert outline.outline_revision == "r1"
