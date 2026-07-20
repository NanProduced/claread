"""R7-1 rework: extracted artifact materialization language propagation.

Hermetic (no-DB) regression proving that the stable-document
materialization path resolves the record's authoritative language and
requests the AUTO sentence-segmentation policy, so English extracted
artifacts reach the parser-backed spaCy main path while non-English
records stay on the named regex v2 fallback.

The DB boundary (lock / suitability / freeze persistence / active-base
/ RAG auto-ensure / event publish) is mocked; the real
``normalize_input_document`` + ``build_stable_document_freeze_plan`` +
base builder run, so the captured ``language`` / ``segmenter_version``
are exactly what the production code computes.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.schemas.reader_input_adapter import InputSuitabilityResult
from app.services.reader_orchestration import (
    base_builder as base_builder_module,
)
from app.services.reader_orchestration import (
    extracted_artifact_materialization_service as materialization_module,
)
from app.services.reader_orchestration._text import resolve_default_reader_language
from app.services.reader_orchestration.base_builder import (
    AUTO_SEGMENTER_POLICY,
    REGEX_V2_SEGMENTER_VERSION,
    SENTENCE_PROVIDER_REGEX_V2,
    SENTENCE_PROVIDER_SPACY,
    SPACY_EN_SENTENCE_SEGMENTER_VERSION,
    build_reading_base_from_canonical_text,
)
from app.services.reader_orchestration.document_freeze_persistence import (
    StableDocumentFreezePersistenceResult,
)
from app.services.reader_orchestration.extracted_artifact_materialization_service import (
    ExtractedArtifactMaterializationService,
)

pytestmark = pytest.mark.anyio

_ARTICLE_TEXT = (
    "The committee reviewed the quarterly export figures during a long "
    "morning session at the municipal chamber downtown. Several members "
    "questioned the headline growth claim because street-level surveys "
    "told a more cautious story about household spending this season. "
    "The chair promised a revised briefing before the next scheduled "
    "vote and asked staff to reconcile the two data sources first. "
    "Analysts expect the corrected numbers to narrow the gap between "
    "the official ledger and the field interviews collected last month. "
    "A follow-up hearing will be announced after the reconciliation is "
    "complete, according to the brief statement released late afternoon."
)


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        (None, "en"),
        ("", "en"),
        ("   ", "en"),
        ("en", "en"),
        (" en-US ", "en-US"),
        ("zh", "zh"),
        ("fr", "fr"),
    ],
)
def test_resolve_default_reader_language_uses_reader_wide_default(
    language: str | None, expected: str
) -> None:
    assert resolve_default_reader_language(language) == expected


def _canned_rows(*, record_language: str | None) -> list[dict]:
    record_id = UUID("33333333-3333-3333-3333-333333333333")
    input_id = UUID("11111111-1111-1111-1111-111111111111")
    artifact_id = UUID("22222222-2222-2222-2222-222222222222")
    user_id = UUID("44444444-4444-4444-4444-444444444444")
    record_row = {
        "active_base_id": None,
        "lifecycle_status": "active",
        "product_state": "processing",
        "readiness_state": "submitted",
        "language": record_language,
    }
    input_row = {
        "id": input_id,
        "reading_record_id": record_id,
        "user_id": user_id,
        "source_text": _ARTICLE_TEXT,
        "source_ref_json": {},
        "metadata_json": {"origin": "r7-1-test"},
        "content_sha256": "0" * 64,
    }
    artifact_row = {
        "id": artifact_id,
        "reading_record_id": record_id,
        "original_input_id": input_id,
        "user_id": user_id,
        "artifact_kind": "original_upload",
        "storage_provider": "oss",
        "bucket": "test-bucket",
        "object_key": "objects/article.txt",
        "endpoint": "oss-test.example.com",
        "content_type": "text/plain",
        "byte_size": len(_ARTICLE_TEXT.encode("utf-8")),
        "content_sha256": "0" * 64,
        "source_filename": "article.txt",
        "status": "available",
    }
    return [record_row, input_row, artifact_row]


async def _run_materialization(record_language: str | None) -> dict:
    """Drive the stable path with mocked boundaries; return the kwargs
    captured at the ``persist_stable_document_freeze_plan`` seam."""
    record_id = UUID("33333333-3333-3333-3333-333333333333")
    user_id = UUID("44444444-4444-4444-4444-444444444444")

    conn = MagicMock()
    conn.is_in_transaction.return_value = True
    conn.fetchrow = AsyncMock(side_effect=_canned_rows(record_language=record_language))

    freeze_result = StableDocumentFreezePersistenceResult(
        stable_document_id=uuid4(),
        base_id=uuid4(),
        reading_record_id=record_id,
        record_generation=1,
        document_version=1,
        content_sha256="0" * 64,
        canonical_text_sha256="1" * 64,
        block_count=1,
        candidate_confirmed=False,
        idempotent_noop=False,
    )
    persist_mock = AsyncMock(return_value=freeze_result)
    suitability = InputSuitabilityResult(
        outcome="stable_document_ready",
        source_type="txt_file",
        word_count=64,
        english_word_ratio=1.0,
        natural_language_score=1.0,
    )

    repository = SimpleNamespace(
        set_active_base_and_mark_article_ready=AsyncMock(return_value=None),
    )
    event_runtime = SimpleNamespace(
        publish_event_in_transaction=AsyncMock(
            return_value=SimpleNamespace(event_id=uuid4(), sequence=3)
        ),
    )
    auto_ensure = SimpleNamespace(
        ensure_in_transaction=AsyncMock(
            return_value=SimpleNamespace(status="skipped", reason_code="test_stub")
        ),
    )
    service = ExtractedArtifactMaterializationService(
        repository=repository,  # type: ignore[arg-type]
        event_runtime=event_runtime,  # type: ignore[arg-type]
        auto_ensure_service=auto_ensure,  # type: ignore[arg-type]
    )

    with (
        mock.patch.object(
            materialization_module,
            "lock_record_for_candidate_write",
            AsyncMock(return_value=None),
        ),
        mock.patch.object(
            materialization_module,
            "evaluate_input_suitability",
            return_value=suitability,
        ),
        mock.patch.object(
            materialization_module,
            "persist_stable_document_freeze_plan",
            persist_mock,
        ),
    ):
        result = await service.materialize_extracted_artifact_in_transaction(
            conn,  # type: ignore[arg-type]
            reading_record_id=record_id,
            original_input_id=UUID("11111111-1111-1111-1111-111111111111"),
            source_artifact_id=UUID("22222222-2222-2222-2222-222222222222"),
            user_id=user_id,
            expected_generation=1,
        )

    assert result.outcome == "stable_document_ready"
    persist_mock.assert_awaited_once()
    return persist_mock.await_args.kwargs


class _WholeTextSentencePipeline:
    def __call__(self, text: str) -> SimpleNamespace:
        sentence = SimpleNamespace(start_char=0, end_char=len(text))
        return SimpleNamespace(sents=[sentence])


async def test_english_extracted_artifact_requests_auto_policy_and_reaches_spacy(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        base_builder_module,
        "_load_spacy_sentence_pipeline",
        lambda: _WholeTextSentencePipeline(),
    )
    kwargs = await _run_materialization("en")

    # The record's authoritative language is propagated (never guessed
    # from the body), and the AUTO policy is requested.
    assert kwargs["language"] == "en"
    assert kwargs["segmenter_version"] == AUTO_SEGMENTER_POLICY

    # With the captured kwargs the base builder resolves the spaCy
    # main path (parser-backed en_core_web_sm).
    build = build_reading_base_from_canonical_text(
        reading_record_id="r", base_id="b", canonical_text=_ARTICLE_TEXT,
        language=kwargs["language"], segmenter_version=kwargs["segmenter_version"],
    )
    assert build.base.segmenter_version == SPACY_EN_SENTENCE_SEGMENTER_VERSION
    assert all(
        unit.sentence_provider in (SENTENCE_PROVIDER_SPACY, None)
        for unit in build.units
    )


async def test_non_english_extracted_artifact_stays_on_regex_v2() -> None:
    kwargs = await _run_materialization("zh")

    assert kwargs["language"] == "zh"
    assert kwargs["segmenter_version"] == AUTO_SEGMENTER_POLICY

    build = build_reading_base_from_canonical_text(
        reading_record_id="r", base_id="b", canonical_text=_ARTICLE_TEXT,
        language=kwargs["language"], segmenter_version=kwargs["segmenter_version"],
    )
    assert build.base.segmenter_version == REGEX_V2_SEGMENTER_VERSION
    sentence_stage_providers = {
        unit.sentence_provider for unit in build.units if unit.sentence_provider
    }
    assert sentence_stage_providers <= {SENTENCE_PROVIDER_REGEX_V2}
    assert SENTENCE_PROVIDER_SPACY not in sentence_stage_providers


async def test_missing_record_language_uses_reader_wide_default_en() -> None:
    kwargs = await _run_materialization(None)
    assert kwargs["language"] == "en"
    assert kwargs["segmenter_version"] == AUTO_SEGMENTER_POLICY
