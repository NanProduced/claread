from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from app.schemas.reader_orchestration import (
    ReaderEnhancementProgress,
    ReaderPlateSnapshot,
    ReaderSnapshotAnchorSegment,
    ReaderSnapshotBase,
    ReaderSnapshotNavigation,
    ReaderSnapshotNavigationUnit,
    ReaderSnapshotRecord,
)
from app.services.reader_orchestration.snapshot_profiler import (
    SnapshotProfile,
    build_and_profile_reader_plate_snapshot,
    build_deterministic_profiling_fixture,
    build_minimal_build_result_for_build_profile,
    profile_reader_plate_snapshot,
)

_FIXED_TS = datetime(2026, 7, 13, 12, 0, 0, tzinfo=timezone.utc)


def _load_cli_module(module_name: str):
    """Load the CLI script as an importable module for testing."""
    cli_path = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "profile_reader_plate_snapshot.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, cli_path)
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    return cli


class _FakeSnapshotService:
    """Minimal fake service for testing the --record-id timing boundary."""

    def __init__(self, snapshot: ReaderPlateSnapshot | None = None) -> None:
        self._snapshot = snapshot
        self.load_call_count = 0

    async def load_snapshot(self, *, record_id: UUID, user_id: UUID):
        self.load_call_count += 1
        if self._snapshot is None:
            raise RuntimeError("record not found")
        return self._snapshot


def test_structure_counts_match_fixture() -> None:
    snapshot = build_deterministic_profiling_fixture()
    profile: SnapshotProfile = profile_reader_plate_snapshot(
        snapshot, collected_at=_FIXED_TS
    )

    assert profile.counts.navigation_units == 2
    assert profile.counts.anchor_segments == 2
    assert profile.counts.published_layers == 4
    assert profile.counts.user_assets == 1
    assert profile.counts.ask_supplements == 1
    assert profile.counts.parsed_decisions == 1
    assert profile.counts.base_text_length_utf16 == 120


def test_byte_buckets_non_negative_and_layer_type_sums_to_total() -> None:
    snapshot = build_deterministic_profiling_fixture()
    profile = profile_reader_plate_snapshot(snapshot, collected_at=_FIXED_TS)
    byte_buckets = profile.byte_buckets

    assert byte_buckets.full_json_utf8_bytes > 0
    assert byte_buckets.value_json_utf8_bytes > 0
    assert byte_buckets.enhancement_layers_total_utf8_bytes > 0

    expected_layer_types = {
        "translation",
        "vocabulary",
        "grammar_note",
        "sentence_analysis",
    }
    assert set(byte_buckets.enhancement_layers_by_type_utf8_bytes.keys()) == (
        expected_layer_types
    )
    for layer_type, value in byte_buckets.enhancement_layers_by_type_utf8_bytes.items():
        assert value > 0, f"layer type {layer_type} must have positive byte count"

    assert sum(byte_buckets.enhancement_layers_by_type_utf8_bytes.values()) == (
        byte_buckets.enhancement_layers_total_utf8_bytes
    )

    assert byte_buckets.full_json_utf8_bytes == len(
        snapshot.model_dump_json().encode("utf-8")
    )


def test_empty_layers_no_assets_no_supplements() -> None:
    snapshot = ReaderPlateSnapshot(
        snapshot_id="x",
        snapshot_taken_at=_FIXED_TS,
        last_event_sequence=0,
        record_id="r",
        record=ReaderSnapshotRecord(
            title="Minimal",
            created_at=_FIXED_TS,
            source_type="text",
            generation=1,
            product_state="readable_enhancing",
            readiness_state="article_ready",
        ),
        base=ReaderSnapshotBase(
            base_id="b",
            content_sha256="a" * 64,
            canonicalizer_version="v1",
            builder_version="v1",
            segmenter_version="v1",
            text_length_utf16=10,
        ),
        navigation=ReaderSnapshotNavigation(
            units=[
                ReaderSnapshotNavigationUnit(
                    unit_id="u1",
                    order_index=1,
                    unit_type="body",
                    base_start_utf16=0,
                    base_end_utf16=10,
                    text_hash="00000001",
                )
            ]
        ),
        anchor_segments=[
            ReaderSnapshotAnchorSegment(
                anchor_segment_id="seg_01",
                sentence_id="seg_01",
                paragraph_id="p1",
                unit_id="u1",
                order_index=1,
                unit_order_index=1,
                segment_type="sentence",
                base_start_utf16=0,
                base_end_utf16=10,
                unit_start_utf16=0,
                unit_end_utf16=10,
                text_hash="00000001",
            )
        ],
        enhancement_layers=[],
        enhancement_progress=ReaderEnhancementProgress(
            overall_status="readable_enhancing",
            layers=[],
        ),
        ask_supplements=[],
        user_assets=[],
        parsed_decisions=[],
        value=[],
    )

    profile = profile_reader_plate_snapshot(snapshot, collected_at=_FIXED_TS)

    assert profile.counts.published_layers == 0
    assert profile.counts.user_assets == 0
    assert profile.counts.ask_supplements == 0
    assert profile.counts.parsed_decisions == 0

    assert profile.byte_buckets.enhancement_layers_total_utf8_bytes == 0
    assert profile.byte_buckets.enhancement_layers_by_type_utf8_bytes == {}

    assert profile.byte_buckets.full_json_utf8_bytes > 0

    expected_value_bytes = len(
        json.dumps([], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    assert profile.byte_buckets.value_json_utf8_bytes == expected_value_bytes
    assert expected_value_bytes == 2


def test_stable_json_output_with_injected_collected_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = [0]

    def fake_perf_counter_ns() -> int:
        counter[0] += 1000
        return counter[0]

    monkeypatch.setattr("time.perf_counter_ns", fake_perf_counter_ns)

    snapshot = build_deterministic_profiling_fixture()

    p1 = profile_reader_plate_snapshot(snapshot, collected_at=_FIXED_TS)
    p2 = profile_reader_plate_snapshot(snapshot, collected_at=_FIXED_TS)

    assert p1.model_dump_json() == p2.model_dump_json()
    assert p1.model_dump_json() == p1.model_dump_json()


def test_profile_does_not_mutate_snapshot() -> None:
    snapshot = build_deterministic_profiling_fixture()
    before = snapshot.model_dump_json()

    profile_reader_plate_snapshot(snapshot, collected_at=_FIXED_TS)

    after = snapshot.model_dump_json()
    assert before == after


def test_durations_non_negative_for_build_and_profile() -> None:
    build_result = build_minimal_build_result_for_build_profile()
    snapshot, profile = build_and_profile_reader_plate_snapshot(
        build_result,
        snapshot_taken_at=_FIXED_TS,
        last_event_sequence=1,
        collected_at=_FIXED_TS,
    )

    assert profile.durations.build_duration_ns is not None
    assert profile.durations.build_duration_ns >= 0
    assert profile.durations.json_serialize_duration_ns >= 0
    assert profile.durations.duration_source == "local_monotonic"


def test_profile_metadata_contract() -> None:
    snapshot = build_deterministic_profiling_fixture()
    profile = profile_reader_plate_snapshot(snapshot, collected_at=_FIXED_TS)

    assert profile.schema_kind == "reader_plate_snapshot_profile"
    assert profile.schema_version == 1
    assert profile.measurement_scope == "logical_serialized_bytes"
    assert profile.record_id == snapshot.record_id
    assert profile.base_id == snapshot.base.base_id
    assert profile.generation == snapshot.record.generation
    assert profile.snapshot_id == snapshot.snapshot_id
    assert profile.last_event_sequence == snapshot.last_event_sequence
    assert profile.collected_at == _FIXED_TS

    notes_joined = "\n".join(profile.notes)
    assert "MUST NOT be reused as an HTTP ETag" in notes_joined
    assert "Content-Length / Content-Encoding not validated" in notes_joined
    assert "browser transfer / parse / render not collected" in notes_joined
    assert "logical_serialized_bytes" in notes_joined
    assert "record_snapshot_load_duration_ns" in notes_joined
    assert "pool init/close" in notes_joined
    assert "NOT HTTP route" in notes_joined


def test_from_json_roundtrip_via_model_validate() -> None:
    snapshot = build_deterministic_profiling_fixture()
    json_text = snapshot.model_dump_json()
    restored = ReaderPlateSnapshot.model_validate_json(json_text)
    profile = profile_reader_plate_snapshot(restored, collected_at=_FIXED_TS)

    assert profile.counts.published_layers == 4


def test_record_snapshot_load_duration_none_in_non_record_modes() -> None:
    """In fixture / from-json modes the load duration field must be None."""
    snapshot = build_deterministic_profiling_fixture()
    profile = profile_reader_plate_snapshot(snapshot, collected_at=_FIXED_TS)
    assert profile.durations.record_snapshot_load_duration_ns is None


# ---------------------------------------------------------------------------
# CLI timing boundary tests (LP-R3.1)
# ---------------------------------------------------------------------------


def test_record_id_timing_boundary_excludes_pool_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The --record-id path wraps ONLY service.load_snapshot() with
    perf_counter_ns; _init_snapshot_service and cleanup are outside the
    timing boundary."""
    cli = _load_cli_module("cli_timing_boundary")

    fixture_snapshot = build_deterministic_profiling_fixture()
    fake_service = _FakeSnapshotService(fixture_snapshot)

    cleanup_called = [False]

    async def fake_cleanup() -> None:
        cleanup_called[0] = True

    init_call_count = [0]

    async def fake_init():
        init_call_count[0] += 1
        return fake_service, fake_cleanup

    monkeypatch.setattr(cli, "_init_snapshot_service", fake_init)

    output_file = tmp_path / "profile_timing.json"
    args = argparse.Namespace(
        fixture=False,
        from_json=None,
        record_id=UUID("00000000-0000-0000-0000-000000000001"),
        user_id=UUID("00000000-0000-0000-0000-000000000002"),
        output=str(output_file),
        collected_at="2026-07-13T12:00:00+00:00",
        repeat=1,
    )
    exit_code = asyncio.run(cli.async_main(args))
    assert exit_code == 0

    # _init_snapshot_service called exactly once (not per-iteration)
    assert init_call_count[0] == 1
    # load_snapshot called exactly once
    assert fake_service.load_call_count == 1
    # cleanup called exactly once
    assert cleanup_called[0] is True

    profile_data = json.loads(output_file.read_text(encoding="utf-8"))
    load_ns = profile_data["durations"]["record_snapshot_load_duration_ns"]
    assert load_ns is not None
    assert load_ns >= 0


def test_record_id_failure_pool_init_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """If _init_snapshot_service returns (None, None), exit code is 1."""
    cli = _load_cli_module("cli_fail_pool_init")

    async def fake_init():
        return None, None

    monkeypatch.setattr(cli, "_init_snapshot_service", fake_init)

    output_file = tmp_path / "profile_fail_pool.json"
    args = argparse.Namespace(
        fixture=False,
        from_json=None,
        record_id=UUID("00000000-0000-0000-0000-000000000001"),
        user_id=UUID("00000000-0000-0000-0000-000000000002"),
        output=str(output_file),
        collected_at="2026-07-13T12:00:00+00:00",
        repeat=1,
    )
    exit_code = asyncio.run(cli.async_main(args))
    assert exit_code == 1
    assert not output_file.exists()


def test_record_id_failure_load_snapshot_raises_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """If service.load_snapshot() raises, exit code is 1 and cleanup runs."""
    cli = _load_cli_module("cli_fail_load")

    # _FakeSnapshotService with snapshot=None raises in load_snapshot
    fake_service = _FakeSnapshotService(snapshot=None)

    cleanup_called = [False]

    async def fake_cleanup() -> None:
        cleanup_called[0] = True

    async def fake_init():
        return fake_service, fake_cleanup

    monkeypatch.setattr(cli, "_init_snapshot_service", fake_init)

    output_file = tmp_path / "profile_fail_load.json"
    args = argparse.Namespace(
        fixture=False,
        from_json=None,
        record_id=UUID("00000000-0000-0000-0000-000000000001"),
        user_id=UUID("00000000-0000-0000-0000-000000000002"),
        output=str(output_file),
        collected_at="2026-07-13T12:00:00+00:00",
        repeat=1,
    )
    exit_code = asyncio.run(cli.async_main(args))
    assert exit_code == 1
    # cleanup must still run even on failure
    assert cleanup_called[0] is True
    assert not output_file.exists()


# ---------------------------------------------------------------------------
# CLI --repeat N tests (LP-R3.1)
# ---------------------------------------------------------------------------


def test_repeat_fixture_mode_outputs_array_without_cache_phase(
    tmp_path: Path,
) -> None:
    """--repeat N>1 in fixture mode emits a JSON array with N elements.
    No cache_phase field: repeat_index is the sole position indicator."""
    cli = _load_cli_module("cli_repeat_fixture")

    output_file = tmp_path / "profile_repeat_fixture.json"
    args = argparse.Namespace(
        fixture=True,
        from_json=None,
        record_id=None,
        user_id=None,
        output=str(output_file),
        collected_at="2026-07-13T12:00:00+00:00",
        repeat=3,
    )
    exit_code = asyncio.run(cli.async_main(args))
    assert exit_code == 0

    results = json.loads(output_file.read_text(encoding="utf-8"))
    assert isinstance(results, list)
    assert len(results) == 3

    for i, element in enumerate(results):
        assert element["repeat_index"] == i
        # cache_phase must NOT be present
        assert "cache_phase" not in element
        assert (
            element["profile"]["durations"]["record_snapshot_load_duration_ns"]
            is None
        )
        assert element["profile"]["durations"]["json_serialize_duration_ns"] >= 0
        assert element["profile"]["byte_buckets"]["full_json_utf8_bytes"] > 0


def test_repeat_record_id_mode_inits_pool_once_and_times_each_load(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """--record-id --repeat N: _init_snapshot_service called once;
    service.load_snapshot() called N times; each element has
    record_snapshot_load_duration_ns >= 0; no cache_phase field."""
    cli = _load_cli_module("cli_repeat_record")

    fixture_snapshot = build_deterministic_profiling_fixture()
    fake_service = _FakeSnapshotService(fixture_snapshot)

    cleanup_called = [False]

    async def fake_cleanup() -> None:
        cleanup_called[0] = True

    init_call_count = [0]

    async def fake_init():
        init_call_count[0] += 1
        return fake_service, fake_cleanup

    monkeypatch.setattr(cli, "_init_snapshot_service", fake_init)

    output_file = tmp_path / "profile_repeat_record.json"
    args = argparse.Namespace(
        fixture=False,
        from_json=None,
        record_id=UUID("00000000-0000-0000-0000-000000000001"),
        user_id=UUID("00000000-0000-0000-0000-000000000002"),
        output=str(output_file),
        collected_at="2026-07-13T12:00:00+00:00",
        repeat=3,
    )
    exit_code = asyncio.run(cli.async_main(args))
    assert exit_code == 0

    # Pool initialized exactly once, not per-iteration
    assert init_call_count[0] == 1
    # load_snapshot called exactly N times
    assert fake_service.load_call_count == 3
    # cleanup called exactly once
    assert cleanup_called[0] is True

    results = json.loads(output_file.read_text(encoding="utf-8"))
    assert isinstance(results, list)
    assert len(results) == 3

    for i, element in enumerate(results):
        assert element["repeat_index"] == i
        assert "cache_phase" not in element
        load_ns = element["profile"]["durations"]["record_snapshot_load_duration_ns"]
        assert load_ns is not None
        assert load_ns >= 0


def test_repeat_1_outputs_single_json_not_array(tmp_path: Path) -> None:
    """--repeat 1 emits a single profile JSON (backward compatible, not array)."""
    cli = _load_cli_module("cli_repeat_one")

    output_file = tmp_path / "profile_repeat_one.json"
    args = argparse.Namespace(
        fixture=True,
        from_json=None,
        record_id=None,
        user_id=None,
        output=str(output_file),
        collected_at="2026-07-13T12:00:00+00:00",
        repeat=1,
    )
    exit_code = asyncio.run(cli.async_main(args))
    assert exit_code == 0

    data = json.loads(output_file.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert data["schema_kind"] == "reader_plate_snapshot_profile"
    assert "repeat_index" not in data
    assert "cache_phase" not in data


def test_repeat_record_id_failure_on_second_iteration_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """--record-id --repeat N: if load_snapshot fails on iteration k>0,
    exit code is 1, cleanup runs, and no output file is written."""
    cli = _load_cli_module("cli_repeat_fail_second")

    fixture_snapshot = build_deterministic_profiling_fixture()
    fake_service = _FakeSnapshotService(fixture_snapshot)

    # Override load_snapshot to fail on the 2nd call
    original_load = fake_service.load_snapshot

    async def fail_on_second(*, record_id, user_id):
        if fake_service.load_call_count >= 1:
            raise RuntimeError("simulated failure on second iteration")
        return await original_load(record_id=record_id, user_id=user_id)

    fake_service.load_snapshot = fail_on_second

    cleanup_called = [False]

    async def fake_cleanup() -> None:
        cleanup_called[0] = True

    async def fake_init():
        return fake_service, fake_cleanup

    monkeypatch.setattr(cli, "_init_snapshot_service", fake_init)

    output_file = tmp_path / "profile_repeat_fail.json"
    args = argparse.Namespace(
        fixture=False,
        from_json=None,
        record_id=UUID("00000000-0000-0000-0000-000000000001"),
        user_id=UUID("00000000-0000-0000-0000-000000000002"),
        output=str(output_file),
        collected_at="2026-07-13T12:00:00+00:00",
        repeat=3,
    )
    exit_code = asyncio.run(cli.async_main(args))
    assert exit_code == 1
    assert cleanup_called[0] is True
    assert not output_file.exists()
