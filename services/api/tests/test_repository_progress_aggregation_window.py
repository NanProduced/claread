"""Task A3: Z+ window job_type 进入 progress 聚合.

Verifies that ``build_grammar_bundle_window`` (introduced by migration 0015
and the Z+ analysis-window design §3.2) is wired into the reader snapshot
enhancement-progress aggregation in ``repository.py``:

1. ``_JOB_CAPABILITY_BY_TYPE`` maps the new job_type to the ``grammar``
   capability (same as the legacy per-unit ``build_grammar_bundle``).
2. ``_JOB_LAYER_TYPE_BY_TYPE`` maps the new job_type to ``None`` (it is a
   job-only progress entry, just like the legacy grammar bundle job).
3. ``grammar`` is still present in ``_PROGRESS_CAPABILITIES`` (we must not
   drop it, otherwise window jobs would be silently filtered out).
4. The legacy ``build_grammar_bundle`` mappings are unchanged.

The original plan also asked for an end-to-end integration test that inserts
a ``build_grammar_bundle_window`` row into ``reader_jobs`` and asserts the
snapshot returned by ``ReaderOrchestrationRepository.load_snapshot_facts``
contains a grammar progress layer with that job_id. That path is not
exercised here because:

- ``tests/test_reader_orchestration_schema_baseline.py::BASELINE_SQL`` only
  loads migrations up to 0014; migration 0015 (which adds
  ``build_grammar_bundle_window`` to ``reader_jobs_job_type_check``) is not
  part of BASELINE_SQL, and updating that constant is outside Task A3's
  strict modify scope (only ``repository.py`` + this test file).
- The ``reader_service_env`` fixture used by sibling tests is local to those
  test modules and is not exposed via a shared ``conftest.py``.

Instead of skipping the integration entirely, the last test calls
``_build_enhancement_progress`` directly with a lightweight ``asyncpg.Record``
stand-in. This still proves that the dict changes flow through the real
progress-builder and produce a grammar ``ReaderEnhancementProgressLayer``
whose ``job_type`` and ``job_id`` match the window job row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.services.reader_orchestration.repository import (
    _JOB_CAPABILITY_BY_TYPE,
    _JOB_LAYER_TYPE_BY_TYPE,
    _PROGRESS_CAPABILITIES,
    _build_enhancement_progress,
)


def test_grammar_window_job_type_in_capability_map() -> None:
    """build_grammar_bundle_window 映射到 grammar capability."""
    assert _JOB_CAPABILITY_BY_TYPE.get("build_grammar_bundle_window") == "grammar"


def test_grammar_window_job_type_in_layer_type_map() -> None:
    """build_grammar_bundle_window 的 layer_type 是 None（与 per-unit build_grammar_bundle 一致）."""
    assert _JOB_LAYER_TYPE_BY_TYPE.get("build_grammar_bundle_window") is None


def test_grammar_capability_still_in_progress_capabilities() -> None:
    """grammar capability 仍参与 progress 聚合（不应被移除）."""
    assert "grammar" in _PROGRESS_CAPABILITIES


def test_legacy_grammar_job_type_unaffected() -> None:
    """现有 build_grammar_bundle 映射保持不变."""
    assert _JOB_CAPABILITY_BY_TYPE.get("build_grammar_bundle") == "grammar"
    assert _JOB_LAYER_TYPE_BY_TYPE.get("build_grammar_bundle") is None


class _FakeRecord:
    """Minimal asyncpg.Record stand-in keyed by column name.

    ``_build_enhancement_progress`` and its helpers (``_effective_progress_*``,
    ``_progress_job_work_key``, ``_optional_str``) only access records via
    ``row["<column>"]``. A dict-like object is sufficient.
    """

    def __init__(self, mapping: dict[str, Any]) -> None:
        self._mapping = mapping

    def __getitem__(self, key: str) -> Any:
        return self._mapping[key]

    def __repr__(self) -> str:
        return f"_FakeRecord({self._mapping!r})"


def test_build_enhancement_progress_includes_window_grammar_job() -> None:
    """_build_enhancement_progress 应将 build_grammar_bundle_window job 行投影为 grammar progress layer.

    替代被降级掉的 DB 集成测试：直接调用 progress 构造器，验证 dict 改动
    真正流经 progress 聚合逻辑（而不是只检查 dict 字段值）。
    """
    job_id = uuid4()
    now = datetime.now(UTC)
    job_row = _FakeRecord(
        {
            "id": job_id,
            "job_type": "build_grammar_bundle_window",
            "target_type": "unit_range",
            "target_key": "window_0",
            "status": "queued",
            "operation_fingerprint": "grammar_bundle_window_v1",
            "failure_code": None,
            "failure_message": None,
            "created_at": now,
            "updated_at": now,
        }
    )

    progress = _build_enhancement_progress(
        product_state="readable_enhancing",
        layer_rows=(),
        job_rows=(job_row,),
    )

    grammar_layers = [
        layer
        for layer in progress.layers
        if layer.capability == "grammar" and layer.job_type == "build_grammar_bundle_window"
    ]
    assert len(grammar_layers) == 1, (
        f"expected exactly one grammar window progress layer, got {progress.layers!r}"
    )
    grammar_layer = grammar_layers[0]
    assert grammar_layer.job_id == str(job_id)
    assert grammar_layer.layer_type is None
    assert grammar_layer.target_type == "unit_range"
    assert grammar_layer.target_key == "window_0"
    assert grammar_layer.job_status == "queued"


def test_build_enhancement_progress_emits_not_started_for_grammar_without_jobs() -> None:
    """没有 grammar job 时仍应保留 grammar not_started 占位（_PROGRESS_CAPABILITIES 兜底）."""
    progress = _build_enhancement_progress(
        product_state="readable_enhancing",
        layer_rows=(),
        job_rows=(),
    )
    not_started_grammar = [
        layer
        for layer in progress.layers
        if layer.capability == "grammar" and layer.status == "not_started"
    ]
    assert len(not_started_grammar) == 1


def test_build_enhancement_progress_handles_window_and_legacy_grammar_together() -> None:
    """window job 与 legacy build_grammar_bundle job 共存时，两者各自出现在 progress 中."""
    window_job_id = uuid4()
    legacy_job_id = uuid4()
    now = datetime.now(UTC)
    window_row = _FakeRecord(
        {
            "id": window_job_id,
            "job_type": "build_grammar_bundle_window",
            "target_type": "unit_range",
            "target_key": "window_0",
            "status": "queued",
            "operation_fingerprint": "grammar_bundle_window_v1",
            "failure_code": None,
            "failure_message": None,
            "created_at": now,
            "updated_at": now,
        }
    )
    legacy_row = _FakeRecord(
        {
            "id": legacy_job_id,
            "job_type": "build_grammar_bundle",
            "target_type": "unit",
            "target_key": "u-grammar",
            "status": "succeeded",
            "operation_fingerprint": "grammar_bundle_unit_v1",
            "failure_code": None,
            "failure_message": None,
            "created_at": now,
            "updated_at": now,
        }
    )

    progress = _build_enhancement_progress(
        product_state="readable_enhancing",
        layer_rows=(),
        job_rows=(window_row, legacy_row),
    )

    job_ids = {layer.job_id for layer in progress.layers if layer.capability == "grammar"}
    assert str(window_job_id) in job_ids
    assert str(legacy_job_id) in job_ids
