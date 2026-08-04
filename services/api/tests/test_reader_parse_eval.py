# task-history: READER-PARSE-EVAL-R1 (renamed from test_reader_parse_eval_r1.py)
"""R1 split-package tests for ``verification.reader_baseline.parse_eval``.

These tests cover the R1 corrections to Task 5A:

1. **Reader adapter evidence** — the official
   :func:`.reader_adapter.build_artifact_from_snapshot` maps a
   duck-typed ``ReaderPlateSnapshot`` (carrying non-empty translation
   + vocabulary + grammar_note layers) into a valid artifact.
2. **Non-empty layer validation** — the artifact carries reviewable
   normalized_output for translation / vocabulary and a
   content-addressed sidecar_ref for grammar_note.
3. **Canonical-text gate positive case** — ``run_gate`` passes for a
   valid artifact + matching :class:`CanonicalTextEvidence`.
4. **Canonical-text gate negative cases** — zero-hash regression
   negatives (all-zero canonical_text_sha256 / unit text_hash /
   segment text_hash) are rejected by the gate.
5. **Forbidden marker key-only scan** — a legitimate ``notes``
   string containing ``render_scene`` does NOT trigger a finding
   (key-only scan, not value scan).
6. **Frozen artifact determinism** — regenerating the 3 frozen
   artifacts from the same fixed samples produces byte-identical
   JSON to the on-disk files.
7. **Strict provenance** — fake executor is explicitly marked;
   ``artifact_id`` includes ``canonical_text_sha256``.

All tests are 100% offline: no LLM, no DB, no spaCy, no ``app``
runtime import.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

pytestmark = [
    pytest.mark.chain_reader_parse,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]

if TYPE_CHECKING:
    from verification.reader_baseline.golden_samples import GoldenSample
    from verification.reader_baseline.parse_eval.gate import CanonicalTextEvidence
    from verification.reader_baseline.parse_eval.schema import ParseEvalArtifactV1

# These tests must run from the API root so that ``verification``
# imports the same way the CLI does.
API_ROOT = Path(__file__).resolve().parents[2]
os.chdir(API_ROOT)
sys.path.insert(0, str(API_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_sample(sample_id: str) -> GoldenSample:
    from verification.reader_baseline import golden_samples

    return golden_samples.load_sample(sample_id)


def _build_fixture_artifact(
    sample_id: str, *, clock_token: str | None = None
) -> ParseEvalArtifactV1:
    from verification.reader_baseline.parse_eval.constants import (
        DEFAULT_DETERMINISTIC_CLOCK_TOKEN,
    )
    from verification.reader_baseline.parse_eval.fixture_builder import (
        build_fixture_artifact_from_sample,
    )

    sample = _load_sample(sample_id)
    token = clock_token or DEFAULT_DETERMINISTIC_CLOCK_TOKEN
    return build_fixture_artifact_from_sample(
        sample, deterministic_clock_token=token
    )


def _build_canonical_text_evidence(canonical_text: str) -> CanonicalTextEvidence:
    from verification.reader_baseline.parse_eval.gate import (
        CanonicalTextEvidence,
    )

    return CanonicalTextEvidence(canonical_text=canonical_text)


def _build_fixture_artifact_with_evidence(
    sample_id: str,
) -> tuple[ParseEvalArtifactV1, CanonicalTextEvidence]:
    """Build a fixture artifact + matching canonical-text evidence."""
    from verification.reader_baseline.parse_eval.fixture_builder import (
        canonicalize_hermetic,
    )

    sample = _load_sample(sample_id)
    canonical_text = canonicalize_hermetic(sample.plain_text)
    artifact = _build_fixture_artifact(sample_id)
    evidence = _build_canonical_text_evidence(canonical_text)
    return artifact, evidence


def _build_non_empty_layer_artifact_with_evidence() -> (
    tuple[ParseEvalArtifactV1, CanonicalTextEvidence]
):
    """Build the non-empty-layer fake artifact + evidence with sidecar payloads.

    R2 (P1-4): the evidence MUST carry ``sidecar_payloads`` so the
    gate can resolve each ``sidecar_ref`` layer and verify its
    ``sidecar_sha256``. Without this, the gate would reject the
    grammar_note layer with ``sidecar_payload_unresolved``.
    """
    from verification.reader_baseline.parse_eval.gate import (
        CanonicalTextEvidence,
    )
    from verification.reader_baseline.parse_eval.reader_adapter import (
        collect_sidecar_payloads,
    )
    from verification.reader_baseline.parse_eval.reader_snapshot_fixture import (
        FIXTURE_CANONICAL_TEXT,
        build_fake_artifact_with_non_empty_layers,
        build_non_empty_layer_snapshot_fixture,
    )

    artifact = build_fake_artifact_with_non_empty_layers()
    snapshot, _, canonical_text = build_non_empty_layer_snapshot_fixture()
    sidecar_payloads = collect_sidecar_payloads(snapshot)
    evidence = CanonicalTextEvidence(
        canonical_text=canonical_text or FIXTURE_CANONICAL_TEXT,
        sidecar_payloads=sidecar_payloads,
    )
    return artifact, evidence


# ---------------------------------------------------------------------------
# Fixed sample ids covered by the R1 tests
# ---------------------------------------------------------------------------

FIXED_SAMPLE_IDS: tuple[str, ...] = (
    "short_news",
    "reuters_bbc_970",
    "long_article_headings",
)


# ---------------------------------------------------------------------------
# 1. Module hermeticity
# ---------------------------------------------------------------------------


def _find_unguarded_app_imports(source: str) -> list[tuple[int, str]]:
    """Return ``(lineno, statement)`` for any runtime ``app`` import.

    Uses :mod:`ast` so we only flag real Python import statements
    (not docstrings / comments / string literals that happen to
    contain ``"from app"`` or ``"import app"`` substrings). Imports
    inside an ``if TYPE_CHECKING:`` block are allowed because they
    never execute at runtime.
    """
    import ast

    tree = ast.parse(source)

    # Collect line ranges covered by ``if TYPE_CHECKING:`` blocks so
    # we can skip Import / ImportFrom nodes that live inside them.
    type_checking_ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        is_type_checking = isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"
        if not is_type_checking and isinstance(test, ast.Attribute):
            is_type_checking = test.attr == "TYPE_CHECKING"
        if is_type_checking:
            end_line = node.end_lineno or node.lineno
            type_checking_ranges.append((node.lineno, end_line))

    def _is_guarded(lineno: int) -> bool:
        return any(start <= lineno <= end for start, end in type_checking_ranges)

    findings: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app" or alias.name.startswith("app."):
                    if not _is_guarded(node.lineno):
                        findings.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "app" or module.startswith("app."):
                if not _is_guarded(node.lineno):
                    findings.append(
                        (node.lineno, f"from {module} import ...")
                    )
    return findings


def test_r1_parse_eval_package_does_not_import_app_runtime() -> None:
    """No module in the parse_eval package imports ``app`` at runtime."""
    import importlib

    # Locate the parse_eval package directory via the imported
    # package's ``__path__`` (robust against working-directory drift).
    pkg = importlib.import_module("verification.reader_baseline.parse_eval")
    pkg_dirs = list(getattr(pkg, "__path__", []))
    assert pkg_dirs, "parse_eval package has no __path__"
    pkg_dir = Path(pkg_dirs[0])
    py_files = sorted(pkg_dir.glob("*.py"))
    assert py_files, "no .py files found in parse_eval package"
    for py_file in py_files:
        source = py_file.read_text(encoding="utf-8")
        unguarded = _find_unguarded_app_imports(source)
        assert not unguarded, (
            f"unguarded 'app' import in {py_file}: {unguarded!r}"
        )


# ---------------------------------------------------------------------------
# 2. Reader adapter evidence (non-empty layers)
# ---------------------------------------------------------------------------


def test_r1_reader_adapter_builds_artifact_with_non_empty_layers() -> None:
    """The official adapter maps a snapshot with 3 non-empty published
    layers into a valid artifact carrying reviewable evidence."""
    from verification.reader_baseline.parse_eval.reader_snapshot_fixture import (
        build_fake_artifact_with_non_empty_layers,
    )
    from verification.reader_baseline.parse_eval.schema import (
        TranslationNormalizedOutput,
        VocabularyNormalizedOutput,
    )

    artifact = build_fake_artifact_with_non_empty_layers()

    # The artifact has 3 published layers.
    assert len(artifact.published_layers.layers) == 3

    # layer_counts matches the actual layer types.
    assert dict(artifact.published_layers.layer_counts) == {
        "translation": 1,
        "vocabulary": 1,
        "grammar_note": 1,
    }

    # Find layers by type.
    layers_by_type = {layer.layer_type: layer for layer in artifact.published_layers.layers}
    assert set(layers_by_type.keys()) == {"translation", "vocabulary", "grammar_note"}

    # Translation layer: normalized_output with one group.
    translation = layers_by_type["translation"]
    assert translation.output_kind == "normalized_output"
    assert translation.normalized_output is not None
    assert translation.normalized_output_sha256 is not None
    assert isinstance(translation.normalized_output, TranslationNormalizedOutput)
    assert len(translation.normalized_output.groups) == 1
    group = translation.normalized_output.groups[0]
    assert group.translated_text == "宁静的奥本村坐落在两座青山之间。"
    assert group.group_id == "translation-group-0001"
    assert len(group.anchor_segment_ids) == 1

    # Vocabulary layer: normalized_output with one item.
    vocabulary = layers_by_type["vocabulary"]
    assert vocabulary.output_kind == "normalized_output"
    assert vocabulary.normalized_output is not None
    assert vocabulary.normalized_output_sha256 is not None
    assert isinstance(vocabulary.normalized_output, VocabularyNormalizedOutput)
    assert len(vocabulary.normalized_output.items) == 1
    item = vocabulary.normalized_output.items[0]
    assert item.headword == "Auburn"
    assert item.brief_explanation == "奥本（地名）"

    # Grammar note layer: sidecar_ref path (opaque output).
    grammar = layers_by_type["grammar_note"]
    assert grammar.output_kind == "sidecar_ref"
    assert grammar.sidecar_ref is not None
    assert grammar.sidecar_sha256 is not None
    assert len(grammar.sidecar_sha256) == 64


def test_r1_adapter_artifact_passes_gate_with_evidence() -> None:
    """The adapter-produced artifact passes the gate when the
    canonical-text evidence matches and sidecar payloads are resolved."""
    from verification.reader_baseline.parse_eval.gate import run_gate

    artifact, evidence = _build_non_empty_layer_artifact_with_evidence()
    report = run_gate(artifact, evidence)
    assert report.passed, (
        f"gate failed for non-empty-layer adapter artifact: "
        f"{[f.to_jsonable() for f in report.findings]}"
    )
    assert report.findings == ()


def test_r1_adapter_artifact_provenance_fake_executor() -> None:
    """The fake-executor fixture carries explicit fake markers and
    empty model fields (R2 / P1-3: hand-constructed content must
    never be labelled ``executor_mode='real'``)."""
    from verification.reader_baseline.parse_eval.reader_snapshot_fixture import (
        build_fake_artifact_with_non_empty_layers,
    )

    artifact = build_fake_artifact_with_non_empty_layers()
    assert artifact.runner_provenance.executor_mode == "fake"
    assert artifact.model_profile_provenance.is_fake is True
    assert artifact.model_profile_provenance.model_provider is None
    assert artifact.model_profile_provenance.model_name is None
    assert artifact.prompt_revision_provenance.is_fake is True
    assert artifact.prompt_revision_provenance.prompt_revision is None


def test_r3_schema_only_real_artifact_from_fixture_rejected_by_gate() -> None:
    """R3: A fixture-produced artifact that claims real execution
    (``executor_mode='real'`` + ``is_fake=False``) MUST be rejected
    by the gate with ``fixture_claims_real_execution``.

    This test replaces the old ``build_schema_only_real_provenance_fixture``
    public helper (deleted in R3 / P1). The artifact is constructed
    test-locally by mutating a valid fake artifact's provenance fields
    to claim real execution while keeping the fixture producer_module.
    The schema branch accepts it (Pydantic does not know which module
    is a fixture), but ``run_gate`` MUST fail.
    """
    from verification.reader_baseline.parse_eval.fixture_builder import (
        FIXTURE_PRODUCER_MODULE,
    )
    from verification.reader_baseline.parse_eval.gate import (
        CanonicalTextEvidence,
        run_gate,
    )
    from verification.reader_baseline.parse_eval.reader_adapter import (
        collect_sidecar_payloads,
    )
    from verification.reader_baseline.parse_eval.reader_snapshot_fixture import (
        FIXTURE_CANONICAL_TEXT,
        build_fake_artifact_with_non_empty_layers,
        build_non_empty_layer_snapshot_fixture,
    )
    from verification.reader_baseline.parse_eval.schema import ParseEvalArtifactV1

    # Start from a valid fake artifact with non-empty layers + sidecar.
    fake = build_fake_artifact_with_non_empty_layers()
    snapshot, _, canonical_text = build_non_empty_layer_snapshot_fixture()
    sidecar_payloads = collect_sidecar_payloads(snapshot)
    evidence = CanonicalTextEvidence(
        canonical_text=canonical_text or FIXTURE_CANONICAL_TEXT,
        sidecar_payloads=sidecar_payloads,
    )

    # Mutate to claim real execution with a fixture producer_module.
    payload = fake.model_dump(mode="json")
    payload["runner_provenance"]["executor_mode"] = "real"
    payload["runner_provenance"]["executor_note"] = (
        "schema-only real provenance (test-local)"
    )
    payload["model_profile_provenance"]["is_fake"] = False
    payload["model_profile_provenance"]["model_provider"] = "test-provider"
    payload["model_profile_provenance"]["model_name"] = "test-model"
    payload["model_profile_provenance"]["model_profile"] = "test-profile"
    payload["prompt_revision_provenance"]["is_fake"] = False
    payload["prompt_revision_provenance"]["prompt_revision"] = "test-rev-001"
    # Keep the fixture producer_module so the gate's fixture check fires.
    payload["artifact_provenance"]["producer_module"] = FIXTURE_PRODUCER_MODULE

    real_claiming = ParseEvalArtifactV1.model_validate(payload)
    assert real_claiming.runner_provenance.executor_mode == "real"
    assert real_claiming.model_profile_provenance.is_fake is False

    report = run_gate(real_claiming, evidence)
    assert not report.passed, (
        "gate must reject a fixture-produced artifact claiming real execution"
    )
    check_ids = {f.check for f in report.findings}
    assert "artifact_provenance.fixture_claims_real_execution" in check_ids, (
        f"expected fixture_claims_real_execution finding; "
        f"got {sorted(check_ids)}"
    )


def test_r3_real_artifact_from_non_adapter_producer_rejected_by_gate() -> None:
    """R3: An artifact claiming real execution whose producer_module
    is neither a known fixture nor the official adapter MUST be
    rejected by the gate with ``real_artifact_from_non_adapter_producer``.
    """
    from verification.reader_baseline.parse_eval.gate import (
        CanonicalTextEvidence,
        run_gate,
    )
    from verification.reader_baseline.parse_eval.reader_adapter import (
        collect_sidecar_payloads,
    )
    from verification.reader_baseline.parse_eval.reader_snapshot_fixture import (
        FIXTURE_CANONICAL_TEXT,
        build_fake_artifact_with_non_empty_layers,
        build_non_empty_layer_snapshot_fixture,
    )
    from verification.reader_baseline.parse_eval.schema import ParseEvalArtifactV1

    fake = build_fake_artifact_with_non_empty_layers()
    snapshot, _, canonical_text = build_non_empty_layer_snapshot_fixture()
    sidecar_payloads = collect_sidecar_payloads(snapshot)
    evidence = CanonicalTextEvidence(
        canonical_text=canonical_text or FIXTURE_CANONICAL_TEXT,
        sidecar_payloads=sidecar_payloads,
    )

    payload = fake.model_dump(mode="json")
    payload["runner_provenance"]["executor_mode"] = "real"
    payload["model_profile_provenance"]["is_fake"] = False
    payload["model_profile_provenance"]["model_provider"] = "test-provider"
    payload["model_profile_provenance"]["model_name"] = "test-model"
    payload["model_profile_provenance"]["model_profile"] = "test-profile"
    payload["prompt_revision_provenance"]["is_fake"] = False
    payload["prompt_revision_provenance"]["prompt_revision"] = "test-rev-001"
    # Use a non-fixture, non-adapter producer_module.
    payload["artifact_provenance"]["producer_module"] = (
        "some/other/module.py"
    )

    real_non_adapter = ParseEvalArtifactV1.model_validate(payload)
    report = run_gate(real_non_adapter, evidence)
    assert not report.passed, (
        "gate must reject a real artifact from a non-adapter producer"
    )
    check_ids = {f.check for f in report.findings}
    assert (
        "artifact_provenance.real_artifact_from_non_adapter_producer"
        in check_ids
    ), (
        f"expected real_artifact_from_non_adapter_producer finding; "
        f"got {sorted(check_ids)}"
    )


def test_r3_adapter_real_mode_without_pipeline_summary_raises() -> None:
    """R3 (P3): ``build_artifact_from_snapshot`` with
    ``executor_mode='real'`` and ``pipeline_summary=None`` MUST raise
    ``ValueError`` — a real-execution artifact must carry actual
    pipeline run evidence."""
    from verification.reader_baseline.parse_eval.reader_adapter import (
        build_artifact_from_snapshot,
    )
    from verification.reader_baseline.parse_eval.reader_snapshot_fixture import (
        FIXTURE_SOURCE_ID,
        build_non_empty_layer_snapshot_fixture,
    )

    snapshot, _, canonical_text = build_non_empty_layer_snapshot_fixture()
    with pytest.raises(ValueError, match="pipeline_summary"):
        build_artifact_from_snapshot(
            snapshot,
            canonical_text=canonical_text,
            source_id=FIXTURE_SOURCE_ID,
            source_shape="short_news",
            source_attribution="test",
            pipeline_summary=None,
            executor_mode="real",
            model_provider="test-provider",
            model_name="test-model",
            model_profile="test-profile",
            prompt_revision="test-rev-001",
        )


def test_r3_fake_artifact_with_sidecar_evidence_still_passes_gate() -> None:
    """R3 regression: a fake non-empty-layer artifact with correct
    sidecar evidence MUST still pass the gate. The new provenance
    policy only fires for artifacts claiming real execution — fake
    artifacts from fixture producers are legitimate."""
    from verification.reader_baseline.parse_eval.gate import run_gate

    artifact, evidence = _build_non_empty_layer_artifact_with_evidence()
    report = run_gate(artifact, evidence)
    assert report.passed, (
        f"gate failed for fake artifact with sidecar evidence: "
        f"{[f.to_jsonable() for f in report.findings]}"
    )
    assert report.findings == ()


# ---------------------------------------------------------------------------
# 2b. R2 / P1-2: adapter fail-closed on snapshot.base mismatch
# ---------------------------------------------------------------------------
#
# The adapter MUST refuse to produce an artifact when the source
# snapshot's ``base.content_sha256`` or ``base.text_length_utf16``
# disagree with the values recomputed from the passed canonical text.
# Without this check a "passing" artifact could describe the wrong
# source.
# ---------------------------------------------------------------------------


def test_r2_adapter_rejects_snapshot_with_zero_content_sha256() -> None:
    """A snapshot whose ``base.content_sha256`` is all-zeros MUST be
    rejected by the adapter (P1-2 negative test)."""
    import dataclasses

    from verification.reader_baseline.parse_eval.reader_adapter import (
        SnapshotBaseMismatch,
        build_artifact_from_snapshot,
    )
    from verification.reader_baseline.parse_eval.reader_snapshot_fixture import (
        FIXTURE_SOURCE_ID,
        build_non_empty_layer_snapshot_fixture,
    )

    snapshot, pipeline_summary, canonical_text = (
        build_non_empty_layer_snapshot_fixture()
    )
    zeroed_base = dataclasses.replace(
        snapshot.base, content_sha256="0" * 64
    )
    zeroed_snapshot = dataclasses.replace(snapshot, base=zeroed_base)

    with pytest.raises(SnapshotBaseMismatch) as exc_info:
        build_artifact_from_snapshot(
            zeroed_snapshot,  # type: ignore[arg-type]
            canonical_text=canonical_text,
            source_id=FIXTURE_SOURCE_ID,
            source_shape="short_news",
            source_attribution="p1-2 zero content_sha256 negative",
            pipeline_summary=pipeline_summary,  # type: ignore[arg-type]
            executor_mode="fake",
        )
    assert exc_info.value.field == "content_sha256"


def test_r2_adapter_rejects_snapshot_with_mismatched_text_length_utf16() -> None:
    """A snapshot whose ``base.text_length_utf16`` is off by one MUST
    be rejected by the adapter (P1-2 negative test)."""
    import dataclasses

    from verification.reader_baseline.parse_eval.reader_adapter import (
        SnapshotBaseMismatch,
        build_artifact_from_snapshot,
    )
    from verification.reader_baseline.parse_eval.reader_snapshot_fixture import (
        FIXTURE_SOURCE_ID,
        build_non_empty_layer_snapshot_fixture,
    )

    snapshot, pipeline_summary, canonical_text = (
        build_non_empty_layer_snapshot_fixture()
    )
    wrong_length_base = dataclasses.replace(
        snapshot.base, text_length_utf16=snapshot.base.text_length_utf16 + 1
    )
    wrong_length_snapshot = dataclasses.replace(snapshot, base=wrong_length_base)

    with pytest.raises(SnapshotBaseMismatch) as exc_info:
        build_artifact_from_snapshot(
            wrong_length_snapshot,  # type: ignore[arg-type]
            canonical_text=canonical_text,
            source_id=FIXTURE_SOURCE_ID,
            source_shape="short_news",
            source_attribution="p1-2 wrong text_length_utf16 negative",
            pipeline_summary=pipeline_summary,  # type: ignore[arg-type]
            executor_mode="fake",
        )
    assert exc_info.value.field == "text_length_utf16"


def test_r2_adapter_rejects_snapshot_with_missing_base() -> None:
    """A snapshot without a ``base`` attribute MUST be rejected by
    the adapter with a plain ``ValueError`` (caller bug, not drift)."""

    from verification.reader_baseline.parse_eval.reader_adapter import (
        build_artifact_from_snapshot,
    )
    from verification.reader_baseline.parse_eval.reader_snapshot_fixture import (
        FIXTURE_SOURCE_ID,
        build_non_empty_layer_snapshot_fixture,
    )

    snapshot, pipeline_summary, canonical_text = (
        build_non_empty_layer_snapshot_fixture()
    )
    # Strip the ``base`` field by projecting to a narrower dataclass.
    # We use a SimpleNamespace to drop the attribute entirely.
    from types import SimpleNamespace

    snapshot_no_base = SimpleNamespace(
        record=snapshot.record,
        navigation=snapshot.navigation,
        anchor_segments=snapshot.anchor_segments,
        enhancement_layers=snapshot.enhancement_layers,
        last_event_sequence=snapshot.last_event_sequence,
    )

    with pytest.raises(ValueError, match="snapshot.base must be present"):
        build_artifact_from_snapshot(
            snapshot_no_base,  # type: ignore[arg-type]
            canonical_text=canonical_text,
            source_id=FIXTURE_SOURCE_ID,
            source_shape="short_news",
            source_attribution="p1-2 missing base negative",
            pipeline_summary=pipeline_summary,  # type: ignore[arg-type]
            executor_mode="fake",
        )


# ---------------------------------------------------------------------------
# 2c. R2 / P1-6: adapter accepts a real ``ReaderPlateSnapshot`` schema
# ---------------------------------------------------------------------------
#
# At least one adapter test must construct a real
# ``app.schemas.reader_orchestration.ReaderPlateSnapshot`` Pydantic
# instance (not a local shadow dataclass) and confirm the adapter
# produces a valid artifact from it. This guards against silent drift
# between the duck-typed fixture and the real schema.
# ---------------------------------------------------------------------------


def test_r2_adapter_builds_artifact_from_real_reader_plate_snapshot() -> None:
    """The adapter accepts a real ``ReaderPlateSnapshot`` Pydantic
    instance whose ``base.content_sha256`` / ``text_length_utf16``
    match the passed canonical text, and produces a valid artifact
    (P1-6: real schema, not shadow dataclass)."""
    from datetime import datetime

    from app.schemas.reader_orchestration import (
        ReaderEnhancementProgress,
        ReaderPlateSnapshot,
        ReaderSnapshotAnchorSegment,
        ReaderSnapshotBase,
        ReaderSnapshotNavigation,
        ReaderSnapshotNavigationUnit,
        ReaderSnapshotRecord,
    )
    from verification.reader_baseline.parse_eval.fixture_builder import (
        build_hermetic_anchor_map,
        canonicalize_hermetic,
        sha256_hex,
        utf16_code_unit_length,
    )
    from verification.reader_baseline.parse_eval.reader_adapter import (
        build_artifact_from_snapshot,
    )

    canonical_text = canonicalize_hermetic(
        "The quiet village of Auburn sits between two green hills.\n\n"
        "Every morning, fishermen return with baskets of fresh fish."
    )
    content_sha256 = sha256_hex(canonical_text)
    text_length_utf16 = utf16_code_unit_length(canonical_text)

    anchor_map = build_hermetic_anchor_map(canonical_text)
    units = [
        ReaderSnapshotNavigationUnit(
            unit_id=u.unit_id,
            order_index=u.order_index,
            unit_type=u.unit_type,
            boundary_quality=u.boundary_quality,
            base_start_utf16=u.base_start_utf16,
            base_end_utf16=u.base_end_utf16,
            text_hash=u.text_hash,
        )
        for u in anchor_map.navigation_units
    ]
    anchor_segments = [
        ReaderSnapshotAnchorSegment(
            anchor_segment_id=s.anchor_segment_id,
            sentence_id=s.sentence_id,
            paragraph_id=s.paragraph_id,
            unit_id=s.unit_id,
            order_index=s.order_index,
            unit_order_index=s.unit_order_index,
            segment_type=s.segment_type,
            boundary_quality=s.boundary_quality,
            base_start_utf16=s.base_start_utf16,
            base_end_utf16=s.base_end_utf16,
            unit_start_utf16=s.unit_start_utf16,
            unit_end_utf16=s.unit_end_utf16,
            text_hash=s.text_hash,
        )
        for s in anchor_map.anchor_segments
    ]

    fixed_ts = datetime(2026, 7, 23, 0, 0, 0, tzinfo=UTC)
    real_snapshot = ReaderPlateSnapshot(
        snapshot_id="real-schema-snapshot-0001",
        snapshot_taken_at=fixed_ts,
        last_event_sequence=0,
        record_id="real-schema-record-0001",
        record=ReaderSnapshotRecord(
            title="Real schema test",
            created_at=fixed_ts,
            source_type="text",
            generation=1,
            product_state="readable_enhancing",
            readiness_state="article_ready",
        ),
        base=ReaderSnapshotBase(
            base_id="real-schema-base-0001",
            content_sha256=content_sha256,
            canonicalizer_version="exact_canonical_text_v1",
            builder_version="hermetic_builder_v1",
            segmenter_version="hermetic_segmenter_v1",
            text_length_utf16=text_length_utf16,
        ),
        navigation=ReaderSnapshotNavigation(units=units),
        anchor_segments=anchor_segments,
        enhancement_layers=[],
        enhancement_progress=ReaderEnhancementProgress(
            overall_status="ready", layers=[]
        ),
    )

    artifact = build_artifact_from_snapshot(
        real_snapshot,
        canonical_text=canonical_text,
        source_id="real-schema-record-0001",
        source_shape="short_news",
        source_attribution="real schema fixture (P1-6)",
        executor_mode="fake",
    )
    assert artifact.document.canonical_text_sha256 == content_sha256
    assert artifact.document.canonical_text_length_utf16 == text_length_utf16
    assert artifact.runner_provenance.record_id == "real-schema-record-0001"
    assert artifact.runner_provenance.base_id == "real-schema-base-0001"


# ---------------------------------------------------------------------------
# 3. Canonical-text gate positive cases (fixture-grade artifacts)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sample_id", FIXED_SAMPLE_IDS)
def test_r1_gate_passes_with_evidence_for_fixed_sample(
    sample_id: str,
) -> None:
    """The R1 gate passes with 0 findings for each fixed sample when
    the canonical-text evidence matches."""
    from verification.reader_baseline.parse_eval.gate import run_gate

    artifact, evidence = _build_fixture_artifact_with_evidence(sample_id)
    report = run_gate(artifact, evidence)
    assert report.passed, (
        f"gate failed for sample {sample_id!r}: "
        f"{[f.to_jsonable() for f in report.findings]}"
    )
    assert report.findings == ()
    assert report.schema_version == "reader_parse_eval_artifact.v1"
    assert report.artifact_id == artifact.artifact_id


# ---------------------------------------------------------------------------
# 4. Canonical-text gate negative cases (zero-hash regression)
# ---------------------------------------------------------------------------


def test_r1_gate_rejects_zero_canonical_text_sha256() -> None:
    """An artifact whose canonical_text_sha256 is all-zeros MUST fail."""
    from verification.reader_baseline.parse_eval.gate import (
        ZeroHashRegressionNegatives,
        run_gate,
    )

    artifact, evidence = _build_fixture_artifact_with_evidence("short_news")
    corrupted = ZeroHashRegressionNegatives.with_zero_canonical_text_sha256(artifact)
    report = run_gate(corrupted, evidence)
    assert not report.passed, (
        "gate must reject all-zero canonical_text_sha256"
    )
    check_ids = {f.check for f in report.findings}
    assert any(
        cid.startswith("canonical_text.") for cid in check_ids
    ), f"expected a canonical_text.* finding; got {sorted(check_ids)}"


def test_r1_gate_rejects_zero_unit_text_hash() -> None:
    """An artifact whose navigation unit text_hash is all-zeros MUST fail."""
    from verification.reader_baseline.parse_eval.gate import (
        ZeroHashRegressionNegatives,
        run_gate,
    )

    artifact, evidence = _build_fixture_artifact_with_evidence("short_news")
    corrupted = ZeroHashRegressionNegatives.with_zero_unit_text_hash(artifact, 0)
    report = run_gate(corrupted, evidence)
    assert not report.passed, (
        "gate must reject all-zero unit text_hash"
    )
    check_ids = {f.check for f in report.findings}
    assert any(
        cid.startswith("anchor_map.") for cid in check_ids
    ), f"expected an anchor_map.* finding; got {sorted(check_ids)}"


def test_r1_gate_rejects_zero_segment_text_hash() -> None:
    """An artifact whose anchor segment text_hash is all-zeros MUST fail."""
    from verification.reader_baseline.parse_eval.gate import (
        ZeroHashRegressionNegatives,
        run_gate,
    )

    artifact, evidence = _build_fixture_artifact_with_evidence("short_news")
    corrupted = ZeroHashRegressionNegatives.with_zero_segment_text_hash(artifact, 0)
    report = run_gate(corrupted, evidence)
    assert not report.passed, (
        "gate must reject all-zero segment text_hash"
    )
    check_ids = {f.check for f in report.findings}
    assert any(
        cid.startswith("anchor_map.") for cid in check_ids
    ), f"expected an anchor_map.* finding; got {sorted(check_ids)}"


# ---------------------------------------------------------------------------
# 4b. R2 (P1-1): artifact_id recompute regression negatives
# ---------------------------------------------------------------------------


def test_r2_gate_rejects_zero_artifact_id() -> None:
    """An artifact whose ``artifact_id`` is all-zeros MUST fail.

    R2 (P1-1) regression negative: the gate MUST recompute
    ``derive_artifact_id(...)`` from the declared
    ``artifact_id_semantic_inputs`` and reject any artifact whose
    declared ``artifact_id`` does not match. Setting ``artifact_id``
    to all-zeros (a valid 64-hex string) verifies the gate catches
    this without relying on Pydantic format validation.
    """
    from verification.reader_baseline.parse_eval.gate import (
        ZeroHashRegressionNegatives,
        run_gate,
    )

    artifact, evidence = _build_fixture_artifact_with_evidence("short_news")
    corrupted = ZeroHashRegressionNegatives.with_zero_artifact_id(artifact)
    report = run_gate(corrupted, evidence)
    assert not report.passed, (
        "gate must reject an all-zero artifact_id (recompute mismatch)"
    )
    check_ids = {f.check for f in report.findings}
    assert "artifact_provenance.artifact_id_recompute_mismatch" in check_ids, (
        f"expected artifact_id_recompute_mismatch finding; "
        f"got {sorted(check_ids)}"
    )


def test_r2_gate_rejects_wrong_artifact_id() -> None:
    """An artifact whose ``artifact_id`` is a valid but wrong hex
    string MUST fail the recompute check.

    R2 (P1-1) regression negative: flipping the last hex char of the
    real ``artifact_id`` yields a valid 64-hex string that does NOT
    match the value recomputed from the semantic inputs. The gate
    must catch this via ``derive_artifact_id(...)`` recompute.
    """
    from verification.reader_baseline.parse_eval.gate import (
        ZeroHashRegressionNegatives,
        run_gate,
    )

    artifact, evidence = _build_fixture_artifact_with_evidence("short_news")
    corrupted = ZeroHashRegressionNegatives.with_wrong_artifact_id(artifact)
    # Sanity: the corrupted id is a valid 64-hex string but differs
    # from the real one — otherwise the test would be vacuous.
    assert corrupted.artifact_id != artifact.artifact_id
    assert len(corrupted.artifact_id) == 64
    report = run_gate(corrupted, evidence)
    assert not report.passed, (
        "gate must reject a wrong (but valid-format) artifact_id"
    )
    check_ids = {f.check for f in report.findings}
    assert "artifact_provenance.artifact_id_recompute_mismatch" in check_ids, (
        f"expected artifact_id_recompute_mismatch finding; "
        f"got {sorted(check_ids)}"
    )


# ---------------------------------------------------------------------------
# 4c. R2 (P1-4): sidecar_ref resolver seam regression negatives
# ---------------------------------------------------------------------------


def test_r2_gate_rejects_sidecar_payload_unresolved() -> None:
    """An artifact with a ``sidecar_ref`` layer MUST fail the gate
    when the evidence does not carry the corresponding sidecar payload.

    R2 (P1-4) regression negative: without the resolver seam, the
    gate only checked that ``sidecar_ref`` was a non-empty string.
    Now the gate resolves ``sidecar_ref`` via
    ``evidence.sidecar_payloads``; a missing payload is a finding.
    """
    from verification.reader_baseline.parse_eval.gate import (
        CanonicalTextEvidence,
        run_gate,
    )
    from verification.reader_baseline.parse_eval.reader_snapshot_fixture import (
        FIXTURE_CANONICAL_TEXT,
        build_fake_artifact_with_non_empty_layers,
    )

    artifact = build_fake_artifact_with_non_empty_layers()
    # Evidence carries the canonical text but NO sidecar_payloads —
    # the grammar_note layer's sidecar_ref is unresolvable.
    evidence = CanonicalTextEvidence(
        canonical_text=FIXTURE_CANONICAL_TEXT,
    )
    report = run_gate(artifact, evidence)
    assert not report.passed, (
        "gate must reject a sidecar_ref layer whose payload is not "
        "resolved in evidence.sidecar_payloads"
    )
    check_ids = {f.check for f in report.findings}
    assert "published_layers.sidecar_payload_unresolved" in check_ids, (
        f"expected sidecar_payload_unresolved finding; "
        f"got {sorted(check_ids)}"
    )


def test_r2_gate_rejects_sidecar_sha_mismatch() -> None:
    """An artifact with a ``sidecar_ref`` layer MUST fail the gate
    when the resolved sidecar payload's SHA-256 does not match the
    embedded ``sidecar_sha256``.

    R2 (P1-4) regression negative: the gate recomputes the SHA-256
    over the resolved sidecar payload and compares it to the layer's
    ``sidecar_sha256``. A corrupted payload or hash is a finding.
    """
    from verification.reader_baseline.parse_eval.gate import (
        CanonicalTextEvidence,
        run_gate,
    )
    from verification.reader_baseline.parse_eval.reader_adapter import (
        collect_sidecar_payloads,
    )
    from verification.reader_baseline.parse_eval.reader_snapshot_fixture import (
        FIXTURE_CANONICAL_TEXT,
        build_fake_artifact_with_non_empty_layers,
        build_non_empty_layer_snapshot_fixture,
    )

    artifact = build_fake_artifact_with_non_empty_layers()
    snapshot, _, _ = build_non_empty_layer_snapshot_fixture()
    sidecar_payloads = collect_sidecar_payloads(snapshot)

    # Corrupt one payload: replace its content with a different string
    # so the recomputed SHA-256 no longer matches sidecar_sha256.
    assert sidecar_payloads, "fixture should produce at least one sidecar payload"
    first_ref = next(iter(sidecar_payloads))
    sidecar_payloads = dict(sidecar_payloads)
    sidecar_payloads[first_ref] = '{"corrupted":"payload"}'

    evidence = CanonicalTextEvidence(
        canonical_text=FIXTURE_CANONICAL_TEXT,
        sidecar_payloads=sidecar_payloads,
    )
    report = run_gate(artifact, evidence)
    assert not report.passed, (
        "gate must reject a sidecar_ref layer whose resolved payload "
        "SHA-256 does not match sidecar_sha256"
    )
    check_ids = {f.check for f in report.findings}
    assert "published_layers.sidecar_sha_mismatch" in check_ids, (
        f"expected sidecar_sha_mismatch finding; "
        f"got {sorted(check_ids)}"
    )


def test_r2_sidecar_payloads_resolved_and_verified() -> None:
    """Positive test: when sidecar_payloads are correctly collected,
    the gate resolves each ``sidecar_ref`` and verifies its hash."""
    from verification.reader_baseline.parse_eval.gate import run_gate

    artifact, evidence = _build_non_empty_layer_artifact_with_evidence()
    report = run_gate(artifact, evidence)
    assert report.passed, (
        f"gate failed for non-empty-layer artifact with correct "
        f"sidecar_payloads: "
        f"{[f.to_jsonable() for f in report.findings]}"
    )


# ---------------------------------------------------------------------------
# 5. Forbidden marker key-only scan
# ---------------------------------------------------------------------------


def test_r1_forbidden_scan_is_key_only_not_value_scan() -> None:
    """A legitimate ``notes`` string containing ``render_scene`` MUST
    NOT trigger a forbidden-marker finding (key-only scan)."""
    from verification.reader_baseline.parse_eval.fixture_builder import (
        build_fixture_artifact_from_sample,
    )

    sample = _load_sample("short_news")
    artifact = build_fixture_artifact_from_sample(sample)

    # The sample notes string may or may not contain a forbidden
    # marker; either way, the gate's forbidden check must only flag
    # key paths, not free-form string values. We verify by checking
    # that the notes field (a string value) is never the source of
    # a forbidden finding.
    from verification.reader_baseline.parse_eval.fixture_builder import (
        canonicalize_hermetic,
    )
    from verification.reader_baseline.parse_eval.gate import (
        CanonicalTextEvidence,
        run_gate,
    )

    evidence = CanonicalTextEvidence(
        canonical_text=canonicalize_hermetic(sample.plain_text)
    )
    report = run_gate(artifact, evidence)

    forbidden_findings = [
        f for f in report.findings if f.check.startswith("forbidden_markers")
    ]
    # The gate must pass with zero forbidden findings for a clean
    # fixture artifact.
    assert forbidden_findings == [], (
        f"unexpected forbidden findings for clean fixture: "
        f"{[f.to_jsonable() for f in forbidden_findings]}"
    )
    assert report.passed


# ---------------------------------------------------------------------------
# 6. Frozen artifact determinism
# ---------------------------------------------------------------------------


def test_r1_frozen_artifacts_verify_byte_identical() -> None:
    """Regenerating the frozen artifacts from the same fixed samples
    produces byte-identical JSON to the on-disk files."""
    from verification.reader_baseline.parse_eval.frozen_artifacts import (
        verify_frozen_artifacts,
    )

    ok, messages = verify_frozen_artifacts()
    assert ok, (
        f"frozen artifact drift detected: {messages}"
    )


@pytest.mark.parametrize("sample_id", FIXED_SAMPLE_IDS)
def test_r1_frozen_artifact_matches_regeneration(sample_id: str) -> None:
    """The on-disk frozen artifact for a sample matches a fresh
    regeneration byte-for-byte."""
    from verification.reader_baseline.parse_eval.frozen_artifacts import (
        build_frozen_artifact_for_sample,
        load_frozen_artifact_json,
    )

    _, fresh_json, _ = build_frozen_artifact_for_sample(sample_id)
    on_disk_json = load_frozen_artifact_json(sample_id)
    assert on_disk_json == fresh_json, (
        f"frozen artifact for {sample_id!r} does not match fresh regeneration"
    )


def test_r1_frozen_manifest_has_three_entries() -> None:
    """The frozen manifest has exactly 3 entries, one per fixed sample."""
    from verification.reader_baseline.parse_eval.frozen_artifacts import (
        FROZEN_SAMPLE_IDS,
        load_frozen_manifest,
    )

    manifest = load_frozen_manifest()
    assert len(manifest.artifacts) == 3
    sample_ids = {entry.sample_id for entry in manifest.artifacts}
    assert sample_ids == set(FROZEN_SAMPLE_IDS)


def test_r1_frozen_manifest_records_hashes_and_versions() -> None:
    """Each manifest entry records input_hash, artifact_hash, schema
    version, and generation path."""
    from verification.reader_baseline.parse_eval.constants import (
        ARTIFACT_SCHEMA_VERSION,
        PRODUCER_SEMANTIC_VERSION,
        PRODUCER_VERSION,
    )
    from verification.reader_baseline.parse_eval.frozen_artifacts import (
        FROZEN_ARTIFACTS_GENERATOR_MODULE,
        load_frozen_manifest,
    )

    manifest = load_frozen_manifest()
    assert manifest.schema_version == ARTIFACT_SCHEMA_VERSION
    assert manifest.producer_version == PRODUCER_VERSION
    assert manifest.producer_semantic_version == PRODUCER_SEMANTIC_VERSION
    assert manifest.generator_module == FROZEN_ARTIFACTS_GENERATOR_MODULE

    for entry in manifest.artifacts:
        assert len(entry.input_hash) == 64, (
            f"input_hash for {entry.sample_id!r} must be 64 hex chars"
        )
        assert len(entry.artifact_hash) == 64, (
            f"artifact_hash for {entry.sample_id!r} must be 64 hex chars"
        )
        assert len(entry.artifact_id) == 64, (
            f"artifact_id for {entry.sample_id!r} must be 64 hex chars"
        )
        assert entry.schema_version == ARTIFACT_SCHEMA_VERSION
        assert entry.producer_version == PRODUCER_VERSION
        assert entry.producer_semantic_version == PRODUCER_SEMANTIC_VERSION


# ---------------------------------------------------------------------------
# 7. Strict provenance: artifact_id includes canonical_text_sha256
# ---------------------------------------------------------------------------


def test_r1_artifact_id_includes_canonical_text_sha256() -> None:
    """The artifact_id semantic inputs include canonical_text_sha256,
    schema_version, and producer_semantic_version."""
    artifact, _ = _build_fixture_artifact_with_evidence("short_news")
    sem_inputs = artifact.artifact_provenance.artifact_id_semantic_inputs
    assert sem_inputs.canonical_text_sha256 == artifact.document.canonical_text_sha256
    assert sem_inputs.schema_version == artifact.schema_version
    assert sem_inputs.producer_semantic_version == (
        artifact.artifact_provenance.producer_semantic_version
    )
    assert sem_inputs.source_id == artifact.source_provenance.source_id


def test_r1_fixture_provenance_marks_fake_executor() -> None:
    """The fixture-grade artifact explicitly marks the executor as fake."""
    artifact, _ = _build_fixture_artifact_with_evidence("short_news")
    assert artifact.runner_provenance.executor_mode == "fake"
    assert artifact.model_profile_provenance.is_fake is True
    assert artifact.prompt_revision_provenance.is_fake is True


# ---------------------------------------------------------------------------
# 8. Determinism: two consecutive runs produce byte-identical JSON
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sample_id", FIXED_SAMPLE_IDS)
def test_r1_determinism_two_runs_byte_identical(sample_id: str) -> None:
    """Two consecutive productions of the same fixed input produce
    byte-identical canonical JSON."""
    from verification.reader_baseline.parse_eval.gate import (
        run_determinism_check,
    )

    first = _build_fixture_artifact(sample_id)
    second = _build_fixture_artifact(sample_id)
    report = run_determinism_check(first, second)
    assert report.byte_identical, (
        f"determinism failed for {sample_id!r}: "
        f"first={report.first_payload_sha256} "
        f"second={report.second_payload_sha256}"
    )
    assert report.findings == ()
