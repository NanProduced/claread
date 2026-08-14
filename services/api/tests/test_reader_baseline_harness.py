"""Focused tests for the reader baseline harness.

These tests do not require a live database or any LLM credential.
They lock down the loaders, extractors, and report shape so the
baseline numbers stay comparable across refactors.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# These tests must run from the API root so that ``app`` and
# ``verification`` import the same way the CLI does.
API_ROOT = Path(__file__).resolve().parents[2]
import os as _os
_os.chdir(API_ROOT)
import sys as _sys
_sys.path.insert(0, str(API_ROOT))


# ---------------------------------------------------------------------------
# Golden sample loader
# ---------------------------------------------------------------------------


def test_list_sample_ids_is_stable() -> None:
    from verification.reader_baseline import golden_samples

    ids = golden_samples.list_sample_ids()
    assert ids == (
        "short_news",
        "reuters_bbc_970",
        "fragmented_news",
        "long_article",
        "long_article_headings",
    ), f"golden sample set order changed: {ids!r}"


def test_every_sample_loads_and_meets_bands() -> None:
    from verification.reader_baseline import golden_samples

    for sample_id in golden_samples.list_sample_ids():
        sample = golden_samples.load_sample(sample_id)
        assert sample.sample_id == sample_id
        assert sample.plain_text.strip() != ""
        assert sample.meets_expected_bands(), (
            f"sample {sample_id} fails expected bands: "
            f"chars={sample.char_count} band={sample.expected_char_band}, "
            f"words={sample.word_count} band={sample.expected_word_band}"
        )


def test_load_unknown_sample_raises_file_not_found() -> None:
    from verification.reader_baseline import golden_samples

    with pytest.raises(FileNotFoundError):
        golden_samples.load_sample("definitely_not_a_real_sample")


# ---------------------------------------------------------------------------
# New chain extractors
# ---------------------------------------------------------------------------


def test_summarise_pipeline_summary_layer_counts_match_items() -> None:
    """Layer counts and item counts come from the same snapshot."""
    from uuid import uuid4

    from app.schemas.reader_orchestration import (
        GrammarNoteItem,
        GrammarNoteLayerOutput,
        ReaderPlateSnapshot,
        ReaderSnapshotLayer,
        ReaderTextRangeAnchor,
        SentenceAnalysisItem,
        SentenceAnalysisChunk,
        SentenceAnalysisLayerOutput,
        TranslationGroup,
        TranslationLayerOutput,
        VocabularyHighlightItem,
        VocabularyLayerOutput,
    )
    from app.services.reader_orchestration.pipeline_runner import (
        EnhancementBootstrapJobCounts,
        EnhancementOutcomeCounts,
        EnhancementWorkerTickCounts,
        ReaderPipelineRunSummary,
    )
    from verification.reader_baseline.new_chain import summarise_pipeline_summary

    record_id = uuid4()
    base_id = uuid4()
    anchor = ReaderTextRangeAnchor(
        base_id=str(base_id),
        unit_id="unit-1",
        anchor_segment_id="anchor-1",
        sentence_id=None,
        segment_type="sentence",
        start_offset=0,
        end_offset=1,
        selected_text="a",
        text_hash=__import__(
            "app.contracts.annotation", fromlist=["compute_text_range_hash"]
        ).compute_text_range_hash("a"),
    )
    layers = [
        ReaderSnapshotLayer(
            layer_id="L1",
            layer_type="translation",
            base_id=str(base_id),
            target_scope="unit",
            target_key="unit-1",
            schema_version=1,
            output=TranslationLayerOutput(
                groups=[TranslationGroup(
                    group_id="g1",
                    anchor_segment_ids=["a1", "a2"],
                    source_text_hash="00000001",
                    translated_text="[stub]",
                )]
            ).model_dump(mode="json"),
            published_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        ),
        ReaderSnapshotLayer(
            layer_id="L2",
            layer_type="vocabulary",
            base_id=str(base_id),
            target_scope="unit",
            target_key="unit-1",
            schema_version=1,
            output=VocabularyLayerOutput(
                items=[
                    VocabularyHighlightItem(
                        anchor=anchor, headword="x", brief_explanation="e", reason="r"
                    ),
                    VocabularyHighlightItem(
                        anchor=anchor, headword="y", brief_explanation="e", reason="r"
                    ),
                ]
            ).model_dump(mode="json"),
            published_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        ),
        ReaderSnapshotLayer(
            layer_id="L3",
            layer_type="grammar_note",
            base_id=str(base_id),
            target_scope="unit",
            target_key="unit-1",
            schema_version=1,
            output=GrammarNoteLayerOutput(
                items=[
                    GrammarNoteItem(
                        spans=[anchor],
                        grammar_point="point",
                        pattern="SVO",
                        note="n",
                    )
                ]
            ).model_dump(mode="json"),
            published_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        ),
        ReaderSnapshotLayer(
            layer_id="L4",
            layer_type="sentence_analysis",
            base_id=str(base_id),
            target_scope="unit",
            target_key="unit-1",
            schema_version=1,
            output=SentenceAnalysisLayerOutput(
                items=[
                    SentenceAnalysisItem(
                        anchor=anchor,
                        label="main",
                        analysis="a",
                        chunks=[SentenceAnalysisChunk(order=1, label="c", text="t")],
                    )
                ]
            ).model_dump(mode="json"),
            published_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        ),
    ]
    snapshot = ReaderPlateSnapshot.model_construct(  # type: ignore[call-arg]
        snapshot_id="s",
        snapshot_taken_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        last_event_sequence=1,
        record_id=str(record_id),
        enhancement_layers=layers,
    )
    summary = ReaderPipelineRunSummary(
        record_id=record_id,
        base_id=base_id,
        expected_generation=1,
        bootstrap=None,  # type: ignore[arg-type]
        bootstrapped_job_counts=EnhancementBootstrapJobCounts(),
        worker_tick_counts=EnhancementWorkerTickCounts(),
        outcome_counts=EnhancementOutcomeCounts(),
        total_ticks=0,
        total_jobs=0,
        last_event_sequence=1,
        snapshot_reload_recommended=False,
        stopped_reason="all_workers_no_job",
    )
    metrics = summarise_pipeline_summary(
        summary=summary,
        record_id=record_id,
        base_id=base_id,
        snapshot=snapshot,
        executor_mode="fake",
        executor_note="dev/test-only deterministic fake executors",
    )
    jsonable = metrics.to_jsonable()
    assert jsonable["layer_counts"] == {
        "translation": 1,
        "vocabulary": 1,
        "grammar_note": 1,
        "sentence_analysis": 1,
    }
    assert jsonable["layer_item_counts"]["translation_groups"] == 2
    assert jsonable["layer_item_counts"]["vocabulary_items"] == 2
    assert jsonable["layer_item_counts"]["grammar_note_items"] == 1
    assert jsonable["layer_item_counts"]["sentence_analysis_items"] == 1


def test_new_chain_metric_to_jsonable_is_serialisable() -> None:
    """to_jsonable() must round-trip through ``json.dumps``."""
    import json
    from uuid import uuid4

    from app.services.reader_orchestration.pipeline_runner import (
        EnhancementBootstrapJobCounts,
        EnhancementOutcomeCounts,
        EnhancementWorkerTickCounts,
        ReaderPipelineRunSummary,
    )
    from app.schemas.reader_orchestration import ReaderPlateSnapshot
    from verification.reader_baseline.new_chain import summarise_pipeline_summary

    record_id = uuid4()
    base_id = uuid4()
    snapshot = ReaderPlateSnapshot.model_construct(  # type: ignore[call-arg]
        snapshot_id="s",
        snapshot_taken_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        last_event_sequence=1,
        record_id=str(record_id),
        enhancement_layers=[],
    )
    summary = ReaderPipelineRunSummary(
        record_id=record_id,
        base_id=base_id,
        expected_generation=1,
        bootstrap=None,  # type: ignore[arg-type]
        bootstrapped_job_counts=EnhancementBootstrapJobCounts(),
        worker_tick_counts=EnhancementWorkerTickCounts(),
        outcome_counts=EnhancementOutcomeCounts(),
        total_ticks=0,
        total_jobs=0,
        last_event_sequence=1,
        snapshot_reload_recommended=False,
        stopped_reason="all_workers_no_job",
    )
    metrics = summarise_pipeline_summary(
        summary=summary,
        record_id=record_id,
        base_id=base_id,
        snapshot=snapshot,
        executor_mode="fake",
        executor_note="dev/test-only deterministic fake executors",
    )
    serialised = json.dumps(metrics.to_jsonable(), ensure_ascii=False, default=str)
    assert isinstance(serialised, str)
    assert "translation" in serialised
    assert "vocabulary" in serialised


# ---------------------------------------------------------------------------
# Report shape
# ---------------------------------------------------------------------------


def test_build_report_renders_new_chain_shape() -> None:
    from verification.reader_baseline import golden_samples, report

    sample = golden_samples.load_sample("short_news")
    metrics_payload = {
        "executor_mode": "fake",
        "executor_note": "dev/test-only deterministic fake executors",
        "record_id": "00000000-0000-0000-0000-000000000000",
        "base_id": "00000000-0000-0000-0000-000000000001",
        "last_event_sequence": 1,
        "total_ticks": 1,
        "total_jobs": 0,
        "worker_tick_counts": {},
        "outcome_counts": {},
        "bootstrap_job_counts": {},
        "stopped_reason": "all_workers_no_job",
        "stopped_worker_type": None,
        "stopped_outcome": None,
        "attention_code": None,
        "snapshot_reload_recommended": False,
        "layer_counts": {"translation": 0, "vocabulary": 0, "grammar_note": 0, "sentence_analysis": 0},
        "layer_item_counts": {
            "translation_groups": 0,
            "vocabulary_items": 0,
            "grammar_note_items": 0,
            "sentence_analysis_items": 0,
        },
        "no_op_windows": 0,
        "failed_windows": 0,
        "attempts": (),
        "attempt_attention_codes": (),
        "completion_status": "complete",
        "outstanding_jobs": {},
        "completion_reasons": (),
        "usage": {
            "event_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "latency_ms": 0,
            "failed_event_count": 0,
            "by_capability": {},
            "source": "skipped",
        },
        "record_reading_goal": None,
        "record_reading_variant": None,
    }

    class _StubMetrics:
        def to_jsonable(self):
            return dict(metrics_payload)

        @property
        def completion_status(self) -> str:
            return "complete"

    report_obj = report.build_report(
        sample=sample,
        new_metrics=_StubMetrics(),  # type: ignore[arg-type]
        notes="",
    )
    md = report_obj.to_markdown()
    assert "short_news" in md
    assert "## New orchestration chain" in md
    assert "executor_mode" in md
    payload = report_obj.to_jsonable()
    assert payload["completion_status"] == "complete"
    assert payload["is_complete"] is True


# ---------------------------------------------------------------------------
# Schema setup
# ---------------------------------------------------------------------------


def test_schema_setup_dependencies_have_a_baseline_sql() -> None:
    """``schema_setup`` must declare the migration files it relies on.

    The smoke harness test suite is the source of truth for which
    migrations bring a fresh schema up to the current reader
    orchestration contract. ``schema_setup`` keeps its own copy so
    it does not import a test module; this test guards against the
    two lists drifting.
    """
    from verification.reader_baseline import schema_setup

    declared = set(schema_setup.REQUIRED_MIGRATION_NAMES)
    # The single baseline migration replaces every per-step
    # migration; the isolated schema loads exactly this file.
    assert declared == {"0001_initial.sql"}
    # No accidental duplicates.
    assert len(declared) == len(schema_setup.REQUIRED_MIGRATION_NAMES)


# ---------------------------------------------------------------------------
# Schema safety
# ---------------------------------------------------------------------------


def test_schema_validate_accepts_whitelisted_name() -> None:
    from verification.reader_baseline import schema_setup

    name = schema_setup.validate_schema_name("reader_baseline_abc123")
    assert name == "reader_baseline_abc123"
    # The exact pattern is also accepted.
    schema_setup.validate_schema_name("reader_baseline_" + ("a" * 40))


def test_schema_validate_rejects_public() -> None:
    """A typo like ``--schema-name public`` must never reach DROP."""
    from verification.reader_baseline import schema_setup

    with pytest.raises(schema_setup.UnsafeSchemaNameError) as exc:
        schema_setup.validate_schema_name("public")
    assert "public" in str(exc.value)
    # Same for case-folded spellings.
    with pytest.raises(schema_setup.UnsafeSchemaNameError):
        schema_setup.validate_schema_name("PUBLIC")


def test_schema_validate_rejects_pg_prefix_and_information_schema() -> None:
    from verification.reader_baseline import schema_setup

    for bad in (
        "pg_catalog",
        "pg_toast",
        "pg_temp_1",
        "information_schema",
        "pg_anything",
    ):
        with pytest.raises(schema_setup.UnsafeSchemaNameError):
            schema_setup.validate_schema_name(bad)


def test_schema_validate_rejects_other_reader_namespaces() -> None:
    """``reader_*`` namespaces that are not ``reader_baseline_`` are off-limits."""
    from verification.reader_baseline import schema_setup

    for bad in ("reader_records", "reader_anything", "reader_"):
        with pytest.raises(schema_setup.UnsafeSchemaNameError):
            schema_setup.validate_schema_name(bad)


def test_schema_validate_rejects_injection_shaped_input() -> None:
    """Names with quotes, semicolons, dashes, or non-ASCII are rejected."""
    from verification.reader_baseline import schema_setup

    for bad in (
        "reader_baseline_abc';DROP TABLE users;--",
        "reader_baseline_abc-def",
        "reader_baseline_UpperCase",
        "reader_baseline_",
        "reader_baseline_" + ("a" * 41),  # too long
        "",
        "reader_baseline_unicode_ñ",
    ):
        with pytest.raises(schema_setup.UnsafeSchemaNameError):
            schema_setup.validate_schema_name(bad)


def test_schema_validate_rejects_non_string_input() -> None:
    from verification.reader_baseline import schema_setup

    with pytest.raises(schema_setup.UnsafeSchemaNameError):
        schema_setup.validate_schema_name(None)  # type: ignore[arg-type]
    with pytest.raises(schema_setup.UnsafeSchemaNameError):
        schema_setup.validate_schema_name(123)  # type: ignore[arg-type]


def test_isolated_schema_never_executes_drop_for_public() -> None:
    """The most important guarantee: ``isolated_schema('public')`` raises
    *before* any DB connection is opened. We assert this by patching
    :func:`asyncpg.connect` to a stub that records any call: the stub
    must never be invoked, which proves the DROP never runs.
    """
    import contextlib

    from verification.reader_baseline import schema_setup

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    @contextlib.asynccontextmanager
    async def _spy_connect(*args: object, **kwargs: object):
        calls.append((args, kwargs))
        yield None  # pragma: no cover - never reached

    import asyncio as _asyncio
    import asyncpg as _asyncpg

    async def _drive() -> None:
        original_connect = _asyncpg.connect
        _asyncpg.connect = _spy_connect  # type: ignore[assignment]
        try:
            cm = schema_setup.isolated_schema(schema_name="public")
            try:
                with pytest.raises(schema_setup.UnsafeSchemaNameError):
                    async with cm:
                        pass  # pragma: no cover - we expect the raise first
            finally:
                pass
        finally:
            _asyncpg.connect = original_connect  # type: ignore[assignment]

    _asyncio.run(_drive())
    assert calls == [], (
        "asyncpg.connect was called despite a rejected schema name; "
        "DROP SCHEMA would have been issued against an unsafe target"
    )


def test_auto_generated_schema_name_passes_validation() -> None:
    from verification.reader_baseline import schema_setup

    for _ in range(20):
        name = schema_setup.auto_generated_schema_name()
        schema_setup.validate_schema_name(name)
        assert name.startswith("reader_baseline_")


# ---------------------------------------------------------------------------
# Incomplete run detection
# ---------------------------------------------------------------------------


def test_completion_classification_complete_when_drained() -> None:
    from verification.reader_baseline import new_chain

    status, reasons = new_chain._classify_completion(
        stopped_reason="all_workers_no_job",
        outcome_counts={"succeeded": 4, "failed_terminal": 0},
        outstanding_jobs={},
    )
    assert status == "complete"
    assert reasons == ()


def test_completion_classification_incomplete_on_max_ticks() -> None:
    from verification.reader_baseline import new_chain

    status, reasons = new_chain._classify_completion(
        stopped_reason="max_ticks_reached",
        outcome_counts={"succeeded": 12, "failed_terminal": 0},
        outstanding_jobs={},
    )
    assert status == "incomplete"
    assert any("max_ticks_reached" in r for r in reasons)


def test_completion_classification_incomplete_on_outstanding_jobs() -> None:
    from verification.reader_baseline import new_chain

    status, reasons = new_chain._classify_completion(
        stopped_reason="all_workers_no_job",
        outcome_counts={"succeeded": 4, "failed_terminal": 0},
        outstanding_jobs={"queued": 2, "claimed": 1},
    )
    assert status == "incomplete"
    assert any("outstanding" in r for r in reasons)


def test_completion_classification_incomplete_on_failed_terminal_jobs() -> None:
    from verification.reader_baseline import new_chain

    status, reasons = new_chain._classify_completion(
        stopped_reason="all_workers_no_job",
        outcome_counts={"succeeded": 4, "failed_terminal": 1},
        outstanding_jobs={},
    )
    assert status == "incomplete"
    assert any("failed_terminal" in r for r in reasons)


def test_summarise_pipeline_summary_marks_max_ticks_as_incomplete() -> None:
    from uuid import uuid4

    from app.schemas.reader_orchestration import ReaderPlateSnapshot
    from app.services.reader_orchestration.pipeline_runner import (
        EnhancementBootstrapJobCounts,
        EnhancementOutcomeCounts,
        EnhancementWorkerTickCounts,
        ReaderPipelineRunSummary,
    )
    from verification.reader_baseline.new_chain import summarise_pipeline_summary

    record_id = uuid4()
    base_id = uuid4()
    snapshot = ReaderPlateSnapshot.model_construct(  # type: ignore[call-arg]
        snapshot_id="s",
        snapshot_taken_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        last_event_sequence=1,
        record_id=str(record_id),
        enhancement_layers=[],
    )
    summary = ReaderPipelineRunSummary(
        record_id=record_id,
        base_id=base_id,
        expected_generation=1,
        bootstrap=None,  # type: ignore[arg-type]
        bootstrapped_job_counts=EnhancementBootstrapJobCounts(),
        worker_tick_counts=EnhancementWorkerTickCounts(),
        outcome_counts=EnhancementOutcomeCounts(),
        total_ticks=24,
        total_jobs=19,
        last_event_sequence=1,
        snapshot_reload_recommended=False,
        stopped_reason="max_ticks_reached",
    )
    metrics = summarise_pipeline_summary(
        summary=summary,
        record_id=record_id,
        base_id=base_id,
        snapshot=snapshot,
        executor_mode="fake",
        executor_note="dev/test-only deterministic fake executors",
    )
    payload = metrics.to_jsonable()
    assert payload["completion_status"] == "incomplete"
    assert payload["completion_reasons"]  # at least one reason recorded
    assert any("max_ticks_reached" in r for r in payload["completion_reasons"])


# ---------------------------------------------------------------------------
# Usage metrics aggregation
# ---------------------------------------------------------------------------


def test_usage_metrics_round_trip() -> None:
    import json
    from verification.reader_baseline import new_chain

    usage = new_chain.UsageMetrics(
        event_count=4,
        input_tokens=100,
        output_tokens=200,
        total_tokens=300,
        latency_ms=500,
        failed_event_count=1,
        by_capability={
            "reader_translation": new_chain.UsageMetrics(
                event_count=2,
                input_tokens=60,
                output_tokens=140,
                total_tokens=200,
                latency_ms=300,
                source="ai_usage_events",
            ),
            "reader_vocabulary": new_chain.UsageMetrics(
                event_count=2,
                input_tokens=40,
                output_tokens=60,
                total_tokens=100,
                latency_ms=200,
                source="ai_usage_events",
            ),
        },
        source="ai_usage_events",
    )
    serialised = json.dumps(usage.to_jsonable(), ensure_ascii=False, default=str)
    assert "reader_translation" in serialised
    assert "reader_vocabulary" in serialised
    assert "ai_usage_events" in serialised


def test_summarise_pool_none_keeps_usage_skipped() -> None:
    """Without a pool, usage is reported as ``skipped`` and completion
    falls back to ``stopped_reason`` + outcome_counts alone.
    """
    from uuid import uuid4

    from app.schemas.reader_orchestration import ReaderPlateSnapshot
    from app.services.reader_orchestration.pipeline_runner import (
        EnhancementBootstrapJobCounts,
        EnhancementOutcomeCounts,
        EnhancementWorkerTickCounts,
        ReaderPipelineRunSummary,
    )
    from verification.reader_baseline.new_chain import summarise_pipeline_summary

    record_id = uuid4()
    base_id = uuid4()
    snapshot = ReaderPlateSnapshot.model_construct(  # type: ignore[call-arg]
        snapshot_id="s",
        snapshot_taken_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        last_event_sequence=1,
        record_id=str(record_id),
        enhancement_layers=[],
    )
    summary = ReaderPipelineRunSummary(
        record_id=record_id,
        base_id=base_id,
        expected_generation=1,
        bootstrap=None,  # type: ignore[arg-type]
        bootstrapped_job_counts=EnhancementBootstrapJobCounts(),
        worker_tick_counts=EnhancementWorkerTickCounts(),
        outcome_counts=EnhancementOutcomeCounts(),
        total_ticks=1,
        total_jobs=0,
        last_event_sequence=1,
        snapshot_reload_recommended=False,
        stopped_reason="all_workers_no_job",
    )
    metrics = summarise_pipeline_summary(
        summary=summary,
        record_id=record_id,
        base_id=base_id,
        snapshot=snapshot,
        executor_mode="fake",
        executor_note="dev/test-only deterministic fake executors",
    )
    payload = metrics.to_jsonable()
    assert payload["usage"]["source"] == "skipped"
    assert payload["usage"]["event_count"] == 0
    assert payload["usage"]["total_tokens"] == 0


# ---------------------------------------------------------------------------
# Reading metadata
# ---------------------------------------------------------------------------


def test_golden_sample_loader_propagates_reading_metadata() -> None:
    from verification.reader_baseline import golden_samples

    for sample_id in golden_samples.list_sample_ids():
        sample = golden_samples.load_sample(sample_id)
        assert sample.reading_goal in {"daily_reading", "exam", "academic"}
        assert sample.reading_variant != ""


def test_golden_sample_loader_picks_manifest_values() -> None:
    from verification.reader_baseline import golden_samples

    sample = golden_samples.load_sample("reuters_bbc_970")
    # The manifest sets exam/ielts_toefl explicitly for this sample.
    assert sample.reading_goal == "exam"
    assert sample.reading_variant == "ielts_toefl"


def test_cli_resolve_reading_metadata_cli_overrides_manifest() -> None:
    from types import SimpleNamespace

    from verification.reader_baseline import cli_helpers, golden_samples

    sample = golden_samples.load_sample("reuters_bbc_970")
    overrides = cli_helpers.ReadingMetadataOverrides(
        reading_goal="academic", reading_variant="academic_general"
    )
    goal, variant = cli_helpers.resolve_reading_metadata(
        sample=sample, overrides=overrides
    )
    assert goal == "academic"
    assert variant == "academic_general"

    # Without overrides the manifest value wins.
    overrides = cli_helpers.ReadingMetadataOverrides(
        reading_goal=None, reading_variant=None
    )
    goal, variant = cli_helpers.resolve_reading_metadata(
        sample=sample, overrides=overrides
    )
    assert goal == sample.reading_goal
    assert variant == sample.reading_variant

    # The CLI's adapter should map any object with the two fields
    # (including ``CliArgs``) into the helper's input shape.
    fake_cli_args = SimpleNamespace(
        reading_goal="daily_reading", reading_variant="intensive_reading"
    )
    goal, variant = cli_helpers.resolve_reading_metadata(
        sample=sample,
        overrides=cli_helpers.ReadingMetadataOverrides(
            reading_goal=fake_cli_args.reading_goal,
            reading_variant=fake_cli_args.reading_variant,
        ),
    )
    assert goal == "daily_reading"
    assert variant == "intensive_reading"


class _ArgsStub:
    """Tiny stand-in for ``CliArgs`` so the focused test can poke
    only the fields ``_resolve_reading_metadata`` reads.
    """

    __slots__ = ("reading_goal", "reading_variant")

    def __init__(self, reading_goal: str | None, reading_variant: str | None) -> None:
        self.reading_goal = reading_goal
        self.reading_variant = reading_variant


def test_cli_rejects_public_schema_name_at_argparse() -> None:
    """The CLI must short-circuit on ``--schema-name public`` before
    opening any DB connection.
    """
    import subprocess
    import sys

    import pytest

    cmd = [
        sys.executable,
        "services/api/scripts/run_reader_baseline.py",
        "--samples",
        "short_news",
        "--executor-mode",
        "fake",
        "--allow-fake-executors",
        "--schema-name",
        "public",
    ]
    result = subprocess.run(
        cmd,
        cwd=str(API_ROOT.parent),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0, (
        f"CLI accepted unsafe schema name; stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert "public" in result.stderr or "Unsafe" in result.stderr


# ---------------------------------------------------------------------------
# Report contains completion + usage + reading metadata
# ---------------------------------------------------------------------------


def test_report_to_jsonable_contains_completion_and_usage_and_metadata() -> None:
    from uuid import uuid4

    from verification.reader_baseline import golden_samples, report
    from verification.reader_baseline.new_chain import UsageMetrics

    sample = golden_samples.load_sample("reuters_bbc_970")

    class _StubMetrics:
        def to_jsonable(self) -> dict:
            return {
                "executor_mode": "fake",
                "record_id": "00000000-0000-0000-0000-000000000000",
                "usage": UsageMetrics(
                    event_count=4,
                    total_tokens=300,
                    source="ai_usage_events",
                ).to_jsonable(),
                "completion_status": "incomplete",
                "completion_reasons": ["stopped_reason='max_ticks_reached'"],
                "outstanding_jobs": {"claimed": 1},
                "record_reading_goal": "exam",
                "record_reading_variant": "ielts_toefl",
            }

        @property
        def completion_status(self) -> str:
            return "incomplete"

    report_obj = report.build_report(
        sample=sample,
        new_metrics=_StubMetrics(),  # type: ignore[arg-type]
    )
    payload = report_obj.to_jsonable()
    assert payload["completion_status"] == "incomplete"
    assert payload["is_complete"] is False
    assert payload["reading_goal"] == "exam"
    assert payload["reading_variant"] == "ielts_toefl"
    assert payload["new_chain"]["usage"]["total_tokens"] == 300


# ---------------------------------------------------------------------------
# Reading metadata closed loop
# ---------------------------------------------------------------------------


def test_build_report_uses_resolved_reading_metadata_over_sample_default() -> None:
    """``build_report(reading_goal=..., reading_variant=...)`` wins
    over the sample's manifest entry, so the top-level report
    metadata reflects what was actually used at run time, not the
    static default.
    """
    from verification.reader_baseline import golden_samples, report

    sample = golden_samples.load_sample("reuters_bbc_970")
    # Sanity: the manifest really does set exam/ielts_toefl so the
    # override is a *change*, not a no-op.
    assert sample.reading_goal == "exam"
    assert sample.reading_variant == "ielts_toefl"

    class _StubMetrics:
        def to_jsonable(self) -> dict:
            return {
                "usage": {
                    "event_count": 0,
                    "total_tokens": 0,
                    "source": "skipped",
                    "by_capability": {},
                },
                "completion_status": "complete",
            }

        @property
        def completion_status(self) -> str:
            return "complete"

    # Override with a different goal/variant. The top-level
    # report metadata must use the override, not the manifest.
    report_obj = report.build_report(
        sample=sample,
        new_metrics=_StubMetrics(),  # type: ignore[arg-type]
        reading_goal="academic",
        reading_variant="academic_general",
    )
    payload = report_obj.to_jsonable()
    assert payload["reading_goal"] == "academic"
    assert payload["reading_variant"] == "academic_general"

    # And the fallback to sample default still works.
    report_default = report.build_report(
        sample=sample,
        new_metrics=_StubMetrics(),  # type: ignore[arg-type]
    )
    default_payload = report_default.to_jsonable()
    assert default_payload["reading_goal"] == "exam"
    assert default_payload["reading_variant"] == "ielts_toefl"


def test_cli_resolve_reading_metadata_round_trip() -> None:
    """End-to-end: CLI override + manifest value both flow through
    the resolver, and the report's top-level metadata matches.
    """
    from types import SimpleNamespace

    from verification.reader_baseline import cli_helpers, golden_samples, report

    sample = golden_samples.load_sample("short_news")
    # ``short_news`` manifest sets daily_reading / intermediate_reading.
    # A CLI override with a different pair must replace the values
    # the report sees.
    overrides = cli_helpers.ReadingMetadataOverrides(
        reading_goal="exam", reading_variant="kaoyan"
    )
    goal, variant = cli_helpers.resolve_reading_metadata(
        sample=sample, overrides=overrides
    )
    assert goal == "exam"
    assert variant == "kaoyan"

    class _StubMetrics:
        def to_jsonable(self) -> dict:
            return {
                "usage": {
                    "event_count": 0,
                    "total_tokens": 0,
                    "source": "skipped",
                    "by_capability": {},
                },
                "completion_status": "complete",
            }

        @property
        def completion_status(self) -> str:
            return "complete"

    report_obj = report.build_report(
        sample=sample,
        new_metrics=_StubMetrics(),  # type: ignore[arg-type]
        reading_goal=goal,
        reading_variant=variant,
    )
    payload = report_obj.to_jsonable()
    assert payload["reading_goal"] == "exam"
    assert payload["reading_variant"] == "kaoyan"

    # Sanity: the resolver without overrides uses the manifest.
    bare = cli_helpers.resolve_reading_metadata(
        sample=sample,
        overrides=cli_helpers.ReadingMetadataOverrides(
            reading_goal=None, reading_variant=None
        ),
    )
    assert bare == (sample.reading_goal, sample.reading_variant)

    # ``SimpleNamespace`` works the same way ``CliArgs`` does.
    cli_args = SimpleNamespace(
        reading_goal=None, reading_variant="intensive_reading"
    )
    overrides_from_cli = cli_helpers.ReadingMetadataOverrides(
        reading_goal=getattr(cli_args, "reading_goal", None),
        reading_variant=getattr(cli_args, "reading_variant", None),
    )
    partial = cli_helpers.resolve_reading_metadata(
        sample=sample, overrides=overrides_from_cli
    )
    assert partial == ("daily_reading", "intensive_reading")


def test_reuters_bbc_970_manifest_metadata_persists_in_new_chain_record() -> None:
    """End-to-end: run ``reuters_bbc_970`` (manifest says
    exam / ielts_toefl) through the smoke harness with the
    baseline overrides forwarded. The persisted
    ``reading_records`` row must contain those values, and the
    baseline report's ``new_chain.record_reading_*`` must match.
    """
    import asyncio
    import os
    import sys
    from pathlib import Path
    from uuid import UUID, uuid4

    # The baseline harness lives under ``services/api/``; chdir
    # so the relative ``.env`` lookup and migrations resolve the
    # same way they do for the CLI.
    api_root = Path(__file__).resolve().parents[1]
    os.chdir(api_root)
    sys.path.insert(0, str(api_root))

    from app.config.settings import get_settings
    from app.database.connection import init_db, close_db
    from app.services.reader_orchestration.smoke_harness import (
        ReaderEnhancementSmokeHarness,
    )
    from verification.reader_baseline import cli_helpers, golden_samples, new_chain, report
    from verification.reader_baseline.schema_setup import isolated_schema

    settings = get_settings()
    sample = golden_samples.load_sample("reuters_bbc_970")
    assert sample.reading_goal == "exam"
    assert sample.reading_variant == "ielts_toefl"

    async def _drive() -> None:
        await init_db(
            settings.database_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout,
            max_inactive_connection_lifetime=(
                settings.database_max_inactive_connection_lifetime
            ),
        )
        try:
            async with isolated_schema() as (pool, user_id):
                from app.database import connection as db_connection
                previous_pool = db_connection.DB_POOL
                db_connection.DB_POOL = pool
                try:
                    overrides = cli_helpers.ReadingMetadataOverrides(
                        reading_goal=sample.reading_goal,
                        reading_variant=sample.reading_variant,
                    )
                    goal, variant = cli_helpers.resolve_reading_metadata(
                        sample=sample, overrides=overrides
                    )
                    harness = ReaderEnhancementSmokeHarness()
                    result = await harness.prepare_record(
                        user_id=user_id,
                        plain_text=sample.plain_text,
                        title=sample.sample_id,
                        executor_mode="fake",
                        allow_fake_executors=True,
                        reading_goal=goal,
                        reading_variant=variant,
                    )
                    metrics = await new_chain.summarise_async(
                        result=result, pool=pool
                    )
                    # The persisted record must reflect the
                    # resolved metadata, not the smoke harness's
                    # default.
                    assert metrics.record_reading_goal == "exam"
                    assert metrics.record_reading_variant == "ielts_toefl"

                    report_obj = report.build_report(
                        sample=sample,
                        new_metrics=metrics,
                        reading_goal=goal,
                        reading_variant=variant,
                    )
                    payload = report_obj.to_jsonable()
                    assert payload["reading_goal"] == "exam"
                    assert payload["reading_variant"] == "ielts_toefl"
                    assert (
                        payload["new_chain"]["record_reading_goal"] == "exam"
                    )
                    assert (
                        payload["new_chain"]["record_reading_variant"]
                        == "ielts_toefl"
                    )
                finally:
                    db_connection.DB_POOL = previous_pool
        finally:
            await close_db()

    asyncio.run(_drive())


def test_smoke_harness_defaults_unchanged_when_no_metadata_passed() -> None:
    """The new ``reading_goal`` / ``reading_variant`` kwargs default
    to ``None``, so the smoke harness keeps the production
    behaviour: the underlying
    ``PlainTextArticleReadySubmitRequest`` defaults are still
    used. We assert this by inspecting the call signature.
    """
    import inspect

    from app.services.reader_orchestration.smoke_harness import (
        ReaderEnhancementSmokeHarness,
    )

    sig = inspect.signature(ReaderEnhancementSmokeHarness.prepare_record)
    assert sig.parameters["reading_goal"].default is None
    assert sig.parameters["reading_variant"].default is None
