"""Tests for D6-I3U: Real Qwen / DashScope OCR Adapter (env-gated).

Covers the real :class:`QwenOcrTextExtractor` adapter that delegates to an
injectable :class:`QwenOcrClient`. All tests use a fake client — no real
network calls are made. An opt-in smoke skeleton is env-gated at the end
of the file (``CLAREAD_OCR_SMOKE_ENABLED=1`` + ``DASHSCOPE_API_KEY`` +
``READER_OCR_QWEN_MODEL``).

Coverage map (per D6-I3U spec):

1. Fake client happy path → extracted_text / confidence / warnings correct.
2. No confidence → result allows ``None`` (not rejected).
3. Empty text → provider path finally raises ``ocr_no_text_detected``.
4. Timeout / 429 / 5xx → retryable ``ocr_backend_transient``.
5. 401 / 403 → terminal ``ocr_permission_denied``.
6. Malformed response → terminal ``ocr_response_invalid``.
7. Missing API key → ``ocr_provider_unconfigured``.
8. ``_build_ocr_extractor`` resolves ``DASHSCOPE_API_KEY`` via settings and
   does not log/return it.
9. OCR disabled → :class:`UnconfiguredOcrTextExtractor` unchanged.
10. Text / PDF router paths still work with OCR enabled but fake/unavailable.
11. Multi-block response → ``layout_order_uncertain`` warning.
12. 400 / unsupported image payload → terminal ``ocr_request_invalid``.
13. DashScope response parser handles string + list content shapes.
14. Settings: ``reader_ocr_qwen_model`` + ``reader_ocr_request_timeout_seconds``
    are wired through to the extractor.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.config.settings import Settings
from app.services.reader_orchestration.artifact_extraction_provider_router import (
    ArtifactExtractionProviderRouter,
    build_default_extraction_provider_router,
)
from app.services.reader_orchestration.artifact_extraction_worker import (
    ArtifactExtractionError,
    ArtifactExtractionJobContext,
)
from app.services.reader_orchestration.ocr_artifact_extraction_provider import (
    DEFAULT_QWEN_OCR_MODEL,
    DEFAULT_QWEN_OCR_TIMEOUT_SECONDS,
    DashScopeQwenOcrClient,
    EXTRACTOR_NAME_QWEN,
    FAILURE_CODE_OCR_BACKEND_TRANSIENT,
    FAILURE_CODE_OCR_NO_TEXT_DETECTED,
    FAILURE_CODE_OCR_PERMISSION_DENIED,
    FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED,
    FAILURE_CODE_OCR_REQUEST_INVALID,
    FAILURE_CODE_OCR_RESPONSE_INVALID,
    FAILURE_CODE_OCR_SDK_UNAVAILABLE,
    OcrArtifactExtractionProvider,
    OcrTextExtractionResult,
    QwenOcrClientError,
    QwenOcrInvalidRequestError,
    QwenOcrPermissionError,
    QwenOcrResponse,
    QwenOcrResponseInvalidError,
    QwenOcrSdkUnavailableError,
    QwenOcrTextExtractor,
    QwenOcrTransientError,
    UnconfiguredOcrTextExtractor,
    _build_data_url,
    _classify_exception,
    _classify_status_error,
    _parse_dashscope_response,
)
from app.services.reader_orchestration.pdf_artifact_extraction_provider import (
    PdfArtifactExtractionProvider,
    PdfTextExtractionResult,
)
from app.services.reader_orchestration.text_artifact_extraction_provider import (
    EXTRACTOR_NAME as TEXT_EXTRACTOR_NAME,
    StorageObjectReadResult,
    TextArtifactExtractionProvider,
)

pytestmark = pytest.mark.anyio

# Fixed UUIDs for deterministic contexts
_USER_ID = UUID("00000000-0000-0000-0000-00000000f001")
_RECORD_ID = UUID("00000000-0000-0000-0000-00000000f002")
_ORIGINAL_INPUT_ID = UUID("00000000-0000-0000-0000-00000000f003")
_ARTIFACT_ID = UUID("00000000-0000-0000-0000-00000000f004")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeStorageObjectReader:
    """In-memory storage reader that returns pre-configured bytes."""

    def __init__(
        self,
        *,
        data: bytes,
        error: Exception | None = None,
    ) -> None:
        self._data = data
        self._error = error
        self.calls: list[dict[str, str]] = []

    async def read_object(
        self,
        *,
        bucket: str,
        endpoint: str,
        object_key: str,
    ) -> StorageObjectReadResult:
        self.calls.append(
            {"bucket": bucket, "endpoint": endpoint, "object_key": object_key}
        )
        if self._error is not None:
            raise self._error
        return StorageObjectReadResult(data=self._data, byte_size=len(self._data))


class FakeQwenOcrClient:
    """Fake :class:`QwenOcrClient` returning pre-configured responses or errors.

    Records all calls so tests can assert the extractor forwarded the right
    args (image_data, content_type, model, timeout_seconds).
    """

    def __init__(
        self,
        *,
        response: QwenOcrResponse | None = None,
        error: QwenOcrClientError | None = None,
    ) -> None:
        self._response = response
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def recognize(
        self,
        *,
        image_data: bytes,
        content_type: str,
        model: str,
        timeout_seconds: int,
    ) -> QwenOcrResponse:
        self.calls.append(
            {
                "image_data": image_data,
                "content_type": content_type,
                "model": model,
                "timeout_seconds": timeout_seconds,
            }
        )
        if self._error is not None:
            raise self._error
        assert self._response is not None, "FakeQwenOcrClient misconfigured"
        return self._response


class _FakePdfExtractor:
    """Minimal fake PDF extractor for router tests (never called in OCR tests)."""

    def extract_text(self, data: bytes) -> PdfTextExtractionResult:  # pragma: no cover
        raise AssertionError("PDF extractor should not be called in OCR tests")


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------


def _make_context(
    *,
    content_type: str | None = "image/png",
    source_filename: str = "scan.png",
    byte_size: int | None = None,
    content_sha256: str | None = None,
    object_key: str = "dev/test/scan.png",
) -> ArtifactExtractionJobContext:
    return ArtifactExtractionJobContext(
        job_id=uuid4(),
        run_id=uuid4(),
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        original_input_id=_ORIGINAL_INPUT_ID,
        source_artifact_id=_ARTIFACT_ID,
        artifact_kind="original_upload",
        storage_provider="oss",
        bucket="claread-dev",
        endpoint="https://oss-cn-shenzhen.aliyuncs.com",
        object_key=object_key,
        content_type=content_type,
        byte_size=byte_size,
        content_sha256=content_sha256,
        source_filename=source_filename,
        expected_generation=1,
        operation_fingerprint="input_artifact_extraction_v1",
    )


def _png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n fake png body"


# ---------------------------------------------------------------------------
# 1. Fake client happy path
# ---------------------------------------------------------------------------


def test_qwen_extractor_happy_path_returns_result_with_confidence() -> None:
    """Fake client happy path: extracted_text / confidence / warnings correct."""
    fake_client = FakeQwenOcrClient(
        response=QwenOcrResponse(
            extracted_text="Hello OCR world.",
            text_confidence=0.95,
            layout_confidence=0.88,
        )
    )
    extractor = QwenOcrTextExtractor(
        api_key="sk-fake",
        client=fake_client,
        model="qwen3.5-ocr",
        timeout_seconds=60,
    )

    result = extractor.extract_text(_png_bytes(), content_type="image/png")

    assert result.extracted_text == "Hello OCR world."
    assert result.extractor_name == EXTRACTOR_NAME_QWEN
    assert result.text_confidence == 0.95
    assert result.layout_confidence == 0.88
    assert result.warnings is None
    # Client received the right args.
    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert call["content_type"] == "image/png"
    assert call["model"] == "qwen3.5-ocr"
    assert call["timeout_seconds"] == 60


def test_qwen_extractor_forwards_model_and_timeout_from_settings() -> None:
    """Model + timeout are configurable via constructor (settings-driven)."""
    fake_client = FakeQwenOcrClient(
        response=QwenOcrResponse(extracted_text="text")
    )
    extractor = QwenOcrTextExtractor(
        api_key="sk-fake",
        client=fake_client,
        model="qwen-vl-ocr",
        timeout_seconds=120,
    )

    extractor.extract_text(b"img", content_type="image/png")

    assert fake_client.calls[0]["model"] == "qwen-vl-ocr"
    assert fake_client.calls[0]["timeout_seconds"] == 120


def test_qwen_extractor_preserves_client_warnings() -> None:
    """Client-level warnings (e.g. layout_order_uncertain) are preserved."""
    fake_client = FakeQwenOcrClient(
        response=QwenOcrResponse(
            extracted_text="Text with uncertain layout.",
            warnings=["layout_order_uncertain: model flagged low-confidence order"],
        )
    )
    extractor = QwenOcrTextExtractor(api_key="sk-fake", client=fake_client)

    result = extractor.extract_text(b"img", content_type="image/png")

    assert result.warnings is not None
    assert any("layout_order_uncertain" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# 2. No confidence → result allows None
# ---------------------------------------------------------------------------


def test_qwen_extractor_no_confidence_allows_none() -> None:
    """When the model returns no confidence, the result has None — NOT rejected."""
    fake_client = FakeQwenOcrClient(
        response=QwenOcrResponse(
            extracted_text="Text without confidence scores.",
            text_confidence=None,
            layout_confidence=None,
        )
    )
    extractor = QwenOcrTextExtractor(api_key="sk-fake", client=fake_client)

    result = extractor.extract_text(b"img", content_type="image/png")

    assert result.extracted_text == "Text without confidence scores."
    assert result.text_confidence is None
    assert result.layout_confidence is None


# ---------------------------------------------------------------------------
# 3. Empty text → provider path finally raises ocr_no_text_detected
# ---------------------------------------------------------------------------


async def test_qwen_extractor_empty_text_provider_raises_ocr_no_text_detected() -> None:
    """When the client returns empty text, the provider (not the extractor)
    raises ``ocr_no_text_detected``. The extractor itself returns the empty
    result; the provider's empty-text gate handles the fail-closed."""
    raw_bytes = _png_bytes()
    reader = FakeStorageObjectReader(data=raw_bytes)
    fake_client = FakeQwenOcrClient(
        response=QwenOcrResponse(extracted_text="   ")  # whitespace only
    )
    extractor = QwenOcrTextExtractor(api_key="sk-fake", client=fake_client)
    provider = OcrArtifactExtractionProvider(reader=reader, extractor=extractor)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(_make_context(byte_size=None, content_sha256=None))

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_NO_TEXT_DETECTED


# ---------------------------------------------------------------------------
# 4. Timeout / 429 / 5xx → retryable ocr_backend_transient
# ---------------------------------------------------------------------------


def test_qwen_extractor_timeout_maps_to_retryable_transient() -> None:
    """Timeout exception from the client → retryable ``ocr_backend_transient``."""
    fake_client = FakeQwenOcrClient(
        error=QwenOcrTransientError("dashscope request timed out")
    )
    extractor = QwenOcrTextExtractor(api_key="sk-fake", client=fake_client)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        extractor.extract_text(b"img", content_type="image/png")

    assert exc_info.value.retryable is True
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_BACKEND_TRANSIENT


def test_qwen_extractor_429_maps_to_retryable_transient() -> None:
    """429 from the client → retryable ``ocr_backend_transient``."""
    fake_client = FakeQwenOcrClient(
        error=QwenOcrTransientError("dashscope backend error (status=429, code=Throttling)")
    )
    extractor = QwenOcrTextExtractor(api_key="sk-fake", client=fake_client)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        extractor.extract_text(b"img", content_type="image/png")

    assert exc_info.value.retryable is True
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_BACKEND_TRANSIENT


def test_qwen_extractor_500_maps_to_retryable_transient() -> None:
    """5xx from the client → retryable ``ocr_backend_transient``."""
    fake_client = FakeQwenOcrClient(
        error=QwenOcrTransientError("dashscope backend error (status=500, code=InternalError)")
    )
    extractor = QwenOcrTextExtractor(api_key="sk-fake", client=fake_client)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        extractor.extract_text(b"img", content_type="image/png")

    assert exc_info.value.retryable is True
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_BACKEND_TRANSIENT


def test_qwen_extractor_503_maps_to_retryable_transient() -> None:
    """503 from the client → retryable ``ocr_backend_transient``."""
    fake_client = FakeQwenOcrClient(
        error=QwenOcrTransientError("dashscope backend error (status=503)")
    )
    extractor = QwenOcrTextExtractor(api_key="sk-fake", client=fake_client)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        extractor.extract_text(b"img", content_type="image/png")

    assert exc_info.value.retryable is True
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_BACKEND_TRANSIENT


# ---------------------------------------------------------------------------
# 5. 401 / 403 → terminal ocr_permission_denied
# ---------------------------------------------------------------------------


def test_qwen_extractor_401_maps_to_terminal_permission_denied() -> None:
    """401 from the client → terminal ``ocr_permission_denied``."""
    fake_client = FakeQwenOcrClient(
        error=QwenOcrPermissionError("dashscope auth failed (status=401)")
    )
    extractor = QwenOcrTextExtractor(api_key="sk-fake", client=fake_client)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        extractor.extract_text(b"img", content_type="image/png")

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_PERMISSION_DENIED
    assert exc_info.value.failure_class == "configuration"


def test_qwen_extractor_403_maps_to_terminal_permission_denied() -> None:
    """403 from the client → terminal ``ocr_permission_denied``."""
    fake_client = FakeQwenOcrClient(
        error=QwenOcrPermissionError("dashscope auth failed (status=403)")
    )
    extractor = QwenOcrTextExtractor(api_key="sk-fake", client=fake_client)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        extractor.extract_text(b"img", content_type="image/png")

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_PERMISSION_DENIED


# ---------------------------------------------------------------------------
# 6. Malformed response → terminal ocr_response_invalid
# ---------------------------------------------------------------------------


def test_qwen_extractor_malformed_response_maps_to_terminal_response_invalid() -> None:
    """Malformed response from the client → terminal ``ocr_response_invalid``."""
    fake_client = FakeQwenOcrClient(
        error=QwenOcrResponseInvalidError("response missing 'output' field")
    )
    extractor = QwenOcrTextExtractor(api_key="sk-fake", client=fake_client)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        extractor.extract_text(b"img", content_type="image/png")

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_RESPONSE_INVALID


# ---------------------------------------------------------------------------
# 7. Missing API key → ocr_provider_unconfigured
# ---------------------------------------------------------------------------


def test_qwen_extractor_missing_api_key_raises_provider_unconfigured() -> None:
    """Missing API key → ``ocr_provider_unconfigured`` (terminal). The
    extractor never calls the client when the key is absent."""
    fake_client = FakeQwenOcrClient(
        response=QwenOcrResponse(extracted_text="should not be reached")
    )
    extractor = QwenOcrTextExtractor(api_key=None, client=fake_client)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        extractor.extract_text(b"img", content_type="image/png")

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED
    assert exc_info.value.failure_class == "configuration"
    # Client must NOT have been called when the key is missing.
    assert fake_client.calls == []


def test_qwen_extractor_empty_api_key_raises_provider_unconfigured() -> None:
    """Empty-string API key is treated the same as None."""
    extractor = QwenOcrTextExtractor(api_key="", client=FakeQwenOcrClient())

    with pytest.raises(ArtifactExtractionError) as exc_info:
        extractor.extract_text(b"img", content_type="image/png")

    assert exc_info.value.failure_code == FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED


# ---------------------------------------------------------------------------
# 8. _build_ocr_extractor resolves DASHSCOPE_API_KEY via settings
# ---------------------------------------------------------------------------


def test_build_ocr_extractor_reads_api_key_from_settings_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_build_ocr_extractor`` resolves ``DASHSCOPE_API_KEY`` through
    ``Settings.resolve_external_env_var``. The key is never stored in settings
    defaults, never logged, and never appears in the extractor's repr or public
    attributes."""
    from scripts.run_reader_artifact_pipeline_worker import _build_ocr_extractor

    test_key = "sk-resolved-key-not-in-settings"
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    def _fake_resolve_external_env_var(
        self: Settings,
        env_name: str,
        *,
        fallback: str = "",
    ) -> str:
        if env_name == "DASHSCOPE_API_KEY":
            return test_key
        return fallback

    monkeypatch.setattr(
        Settings,
        "resolve_external_env_var",
        _fake_resolve_external_env_var,
    )

    settings = Settings(
        reader_ocr_provider_enabled=True,
        reader_ocr_provider_name="qwen",
        reader_ocr_qwen_model="qwen3.5-ocr",
        reader_ocr_request_timeout_seconds=60,
    )
    # Settings must NOT carry the API key as a field.
    assert not hasattr(settings, "dashscope_api_key")

    extractor = _build_ocr_extractor(settings)
    assert isinstance(extractor, QwenOcrTextExtractor)
    # The API key must NOT appear in the extractor's repr.
    assert test_key not in repr(extractor)
    # The extractor holds a DashScopeQwenOcrClient (real adapter).
    assert isinstance(extractor._client, DashScopeQwenOcrClient)


def test_build_ocr_extractor_missing_env_key_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``DASHSCOPE_API_KEY`` cannot be resolved, the constructed extractor
    fails closed with ``ocr_provider_unconfigured`` on first call."""
    from scripts.run_reader_artifact_pipeline_worker import _build_ocr_extractor

    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setattr(
        Settings,
        "resolve_external_env_var",
        lambda self, env_name, *, fallback="": fallback,
    )

    settings = Settings(
        reader_ocr_provider_enabled=True,
        reader_ocr_provider_name="qwen",
    )
    extractor = _build_ocr_extractor(settings)
    assert isinstance(extractor, QwenOcrTextExtractor)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        extractor.extract_text(b"img", content_type="image/png")
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED


def test_build_ocr_extractor_does_not_log_api_key(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The API key must never appear in log output during _build_ocr_extractor."""
    from scripts.run_reader_artifact_pipeline_worker import _build_ocr_extractor

    test_key = "sk-secret-key-must-not-leak"
    monkeypatch.setenv("DASHSCOPE_API_KEY", test_key)

    settings = Settings(
        reader_ocr_provider_enabled=True,
        reader_ocr_provider_name="qwen",
    )

    with caplog.at_level(logging.DEBUG, logger="scripts.run_reader_artifact_pipeline_worker"):
        _build_ocr_extractor(settings)

    # The API key must not appear in any log line.
    for record in caplog.records:
        assert test_key not in record.getMessage()
        assert test_key not in str(record.__dict__)


def test_build_ocr_extractor_wires_model_and_timeout_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model + timeout from settings are forwarded to the extractor."""
    from scripts.run_reader_artifact_pipeline_worker import _build_ocr_extractor

    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-fake")

    settings = Settings(
        reader_ocr_provider_enabled=True,
        reader_ocr_provider_name="qwen",
        reader_ocr_qwen_model="qwen-vl-ocr",
        reader_ocr_request_timeout_seconds=99,
    )
    extractor = _build_ocr_extractor(settings)
    assert isinstance(extractor, QwenOcrTextExtractor)
    assert extractor._model == "qwen-vl-ocr"
    assert extractor._timeout_seconds == 99


# ---------------------------------------------------------------------------
# 9. OCR disabled → UnconfiguredOcrTextExtractor unchanged
# ---------------------------------------------------------------------------


def test_build_ocr_extractor_disabled_returns_unconfigured() -> None:
    """OCR disabled (default) → UnconfiguredOcrTextExtractor (unchanged)."""
    from scripts.run_reader_artifact_pipeline_worker import _build_ocr_extractor

    extractor = _build_ocr_extractor(Settings(reader_ocr_provider_enabled=False))
    assert isinstance(extractor, UnconfiguredOcrTextExtractor)


def test_unconfigured_extractor_still_fails_closed() -> None:
    """The UnconfiguredOcrTextExtractor contract is unchanged by D6-I3U."""
    extractor = UnconfiguredOcrTextExtractor()
    with pytest.raises(ArtifactExtractionError) as exc_info:
        extractor.extract_text(b"img", content_type="image/png")
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED


# ---------------------------------------------------------------------------
# 10. Text / PDF router paths still work with OCR enabled but fake/unavailable
# ---------------------------------------------------------------------------


async def test_router_text_path_works_with_ocr_enabled_fake_client() -> None:
    """Text path is NOT affected by OCR configuration. Even with OCR enabled
    (but using a fake client), text extraction succeeds."""
    raw_bytes = b"Hello text via router."
    reader = FakeStorageObjectReader(data=raw_bytes)
    fake_ocr_client = FakeQwenOcrClient(
        response=QwenOcrResponse(extracted_text="should not be called for text")
    )
    ocr_extractor = QwenOcrTextExtractor(api_key="sk-fake", client=fake_ocr_client)
    router = ArtifactExtractionProviderRouter(
        text_provider=TextArtifactExtractionProvider(reader=reader),
        pdf_provider=PdfArtifactExtractionProvider(reader=reader, extractor=_FakePdfExtractor()),
        ocr_provider=OcrArtifactExtractionProvider(reader=reader, extractor=ocr_extractor),
    )

    result = await router.extract(
        _make_context(
            content_type="text/plain",
            source_filename="notes.txt",
            byte_size=None,
            content_sha256=None,
        )
    )

    assert result.extracted_text == "Hello text via router."
    assert result.extractor_name == TEXT_EXTRACTOR_NAME
    # OCR client must NOT have been called for text path.
    assert fake_ocr_client.calls == []


async def test_router_pdf_path_works_with_ocr_enabled_fake_client() -> None:
    """PDF path is NOT affected by OCR configuration."""

    class _FakePdfExtractorWithText:
        def extract_text(self, data: bytes) -> PdfTextExtractionResult:
            return PdfTextExtractionResult(
                pages=["PDF page text."],
                extractor_name="deterministic_pdf_text_extractor_v1",
            )

    raw_bytes = b"%PDF-1.4 bytes"
    reader = FakeStorageObjectReader(data=raw_bytes)
    fake_ocr_client = FakeQwenOcrClient(
        response=QwenOcrResponse(extracted_text="should not be called for pdf")
    )
    ocr_extractor = QwenOcrTextExtractor(api_key="sk-fake", client=fake_ocr_client)
    router = ArtifactExtractionProviderRouter(
        text_provider=TextArtifactExtractionProvider(reader=reader),
        pdf_provider=PdfArtifactExtractionProvider(
            reader=reader, extractor=_FakePdfExtractorWithText()
        ),
        ocr_provider=OcrArtifactExtractionProvider(reader=reader, extractor=ocr_extractor),
    )

    result = await router.extract(
        _make_context(
            content_type="application/pdf",
            source_filename="doc.pdf",
            byte_size=None,
            content_sha256=None,
        )
    )

    assert result.extracted_text == "PDF page text."
    assert fake_ocr_client.calls == []


async def test_router_image_path_with_fake_client_succeeds() -> None:
    """Image path with a real QwenOcrTextExtractor + fake client succeeds
    end-to-end through the router."""
    raw_bytes = _png_bytes()
    reader = FakeStorageObjectReader(data=raw_bytes)
    fake_client = FakeQwenOcrClient(
        response=QwenOcrResponse(
            extracted_text="OCR via router + fake client.",
            text_confidence=0.90,
            layout_confidence=0.85,
        )
    )
    ocr_extractor = QwenOcrTextExtractor(api_key="sk-fake", client=fake_client)
    router = ArtifactExtractionProviderRouter(
        text_provider=TextArtifactExtractionProvider(reader=reader),
        pdf_provider=PdfArtifactExtractionProvider(reader=reader, extractor=_FakePdfExtractor()),
        ocr_provider=OcrArtifactExtractionProvider(reader=reader, extractor=ocr_extractor),
    )

    result = await router.extract(
        _make_context(content_type="image/png", byte_size=None, content_sha256=None)
    )

    assert result.extracted_text == "OCR via router + fake client."
    assert result.quality is not None
    assert result.quality["extractor_name"] == EXTRACTOR_NAME_QWEN
    assert len(fake_client.calls) == 1


# ---------------------------------------------------------------------------
# 11. Multi-block response → layout_order_uncertain warning
# ---------------------------------------------------------------------------


def test_parse_dashscope_response_multi_block_adds_layout_warning() -> None:
    """When the DashScope response has multiple text blocks, the parser adds
    a ``layout_order_uncertain`` warning (reading order may be ambiguous)."""

    @dataclass
    class _Message:
        content: list[dict[str, str]]

    @dataclass
    class _Choice:
        message: _Message

    @dataclass
    class _Output:
        choices: list[_Choice]

    @dataclass
    class _Resp:
        output: _Output
        status_code: int = 200

    resp = _Resp(
        output=_Output(
            choices=[
                _Choice(
                    message=_Message(
                        content=[
                            {"text": "First block."},
                            {"text": "Second block."},
                            {"text": "Third block."},
                        ]
                    )
                )
            ]
        )
    )

    result = _parse_dashscope_response(resp)

    assert result.extracted_text == "First block.\nSecond block.\nThird block."
    assert result.warnings is not None
    assert any("layout_order_uncertain" in w for w in result.warnings)


def test_parse_dashscope_response_single_block_no_layout_warning() -> None:
    """Single text block → no ``layout_order_uncertain`` warning."""

    @dataclass
    class _Message:
        content: list[dict[str, str]]

    @dataclass
    class _Choice:
        message: _Message

    @dataclass
    class _Output:
        choices: list[_Choice]

    @dataclass
    class _Resp:
        output: _Output
        status_code: int = 200

    resp = _Resp(
        output=_Output(
            choices=[
                _Choice(
                    message=_Message(content=[{"text": "Only one block."}])
                )
            ]
        )
    )

    result = _parse_dashscope_response(resp)

    assert result.extracted_text == "Only one block."
    assert result.warnings is None


def test_parse_dashscope_response_string_content() -> None:
    """DashScope may return content as a plain string (not a list of dicts)."""

    @dataclass
    class _Message:
        content: str

    @dataclass
    class _Choice:
        message: _Message

    @dataclass
    class _Output:
        choices: list[_Choice]

    @dataclass
    class _Resp:
        output: _Output
        status_code: int = 200

    resp = _Resp(
        output=_Output(
            choices=[_Choice(message=_Message(content="Plain string text."))]
        )
    )

    result = _parse_dashscope_response(resp)

    assert result.extracted_text == "Plain string text."


# ---------------------------------------------------------------------------
# 12. 400 / unsupported image payload → terminal ocr_request_invalid
# ---------------------------------------------------------------------------


def test_qwen_extractor_400_maps_to_terminal_request_invalid() -> None:
    """400 from the client → terminal ``ocr_request_invalid``."""
    fake_client = FakeQwenOcrClient(
        error=QwenOcrInvalidRequestError("dashscope invalid request (status=400)")
    )
    extractor = QwenOcrTextExtractor(api_key="sk-fake", client=fake_client)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        extractor.extract_text(b"img", content_type="image/png")

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_REQUEST_INVALID


# ---------------------------------------------------------------------------
# 13. DashScope response parser: error cases
# ---------------------------------------------------------------------------


def test_parse_dashscope_response_missing_output_raises_response_invalid() -> None:
    """Missing 'output' field → ``ocr_response_invalid``."""

    @dataclass
    class _Resp:
        status_code: int = 200

    with pytest.raises(QwenOcrResponseInvalidError, match="missing 'output'"):
        _parse_dashscope_response(_Resp())


def test_parse_dashscope_response_no_choices_raises_response_invalid() -> None:
    """Empty choices → ``ocr_response_invalid``."""

    @dataclass
    class _Output:
        choices: list

    @dataclass
    class _Resp:
        output: _Output
        status_code: int = 200

    with pytest.raises(QwenOcrResponseInvalidError, match="no choices"):
        _parse_dashscope_response(_Resp(output=_Output(choices=[])))


def test_parse_dashscope_response_unexpected_content_type_raises() -> None:
    """Unexpected content type (int) → ``ocr_response_invalid``."""

    @dataclass
    class _Message:
        content: int

    @dataclass
    class _Choice:
        message: _Message

    @dataclass
    class _Output:
        choices: list[_Choice]

    @dataclass
    class _Resp:
        output: _Output
        status_code: int = 200

    resp = _Resp(
        output=_Output(choices=[_Choice(message=_Message(content=42))])
    )

    with pytest.raises(QwenOcrResponseInvalidError, match="unexpected content type"):
        _parse_dashscope_response(resp)


# ---------------------------------------------------------------------------
# 14. SDK unavailable + error classification helpers
# ---------------------------------------------------------------------------


def test_classify_exception_timeout_returns_transient() -> None:
    """A timeout-like exception is classified as transient."""
    exc = TimeoutError("request timed out")
    result = _classify_exception(exc)
    assert isinstance(result, QwenOcrTransientError)
    assert result.retryable is True


def test_classify_exception_connection_returns_transient() -> None:
    """A connection-like exception is classified as transient."""

    class _ConnectionError(Exception):
        pass

    result = _classify_exception(_ConnectionError("connection reset"))
    assert isinstance(result, QwenOcrTransientError)


def test_classify_exception_unknown_defaults_to_transient() -> None:
    """Unknown exceptions default to transient (safer to retry)."""
    result = _classify_exception(RuntimeError("something weird"))
    assert isinstance(result, QwenOcrTransientError)


def test_classify_status_error_401_returns_permission() -> None:
    result = _classify_status_error(401, "Unauthorized")
    assert isinstance(result, QwenOcrPermissionError)


def test_classify_status_error_403_returns_permission() -> None:
    result = _classify_status_error(403, "Forbidden")
    assert isinstance(result, QwenOcrPermissionError)


def test_classify_status_error_400_returns_invalid_request() -> None:
    result = _classify_status_error(400, "BadRequest")
    assert isinstance(result, QwenOcrInvalidRequestError)


def test_classify_status_error_429_returns_transient() -> None:
    result = _classify_status_error(429, "Throttling")
    assert isinstance(result, QwenOcrTransientError)


def test_classify_status_error_500_returns_transient() -> None:
    result = _classify_status_error(500, "InternalError")
    assert isinstance(result, QwenOcrTransientError)


def test_classify_status_error_unknown_returns_transient() -> None:
    """Unknown status codes default to transient."""
    result = _classify_status_error(418, "ImATeapot")
    assert isinstance(result, QwenOcrTransientError)


def test_build_data_url_encodes_bytes() -> None:
    """``_build_data_url`` produces a valid base64 data URL."""
    data = b"fake png"
    url = _build_data_url(data, "image/png")
    assert url.startswith("data:image/png;base64,")
    # The base64 portion decodes back to the original bytes.
    import base64

    encoded = url.split(",", 1)[1]
    assert base64.b64decode(encoded) == data


# ---------------------------------------------------------------------------
# 15. DashScopeQwenOcrClient: SDK unavailable path
# ---------------------------------------------------------------------------


def test_dashscope_client_recognize_raises_sdk_unavailable_when_import_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the dashscope SDK cannot be imported, ``recognize`` raises
    ``QwenOcrSdkUnavailableError`` (terminal). This is tested by patching
    ``builtins.__import__`` to raise ``ImportError`` for ``dashscope``."""
    client = DashScopeQwenOcrClient(api_key="sk-fake")

    real_import = __import__

    def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "dashscope":
            raise ImportError("No module named 'dashscope'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _fake_import)

    with pytest.raises(QwenOcrSdkUnavailableError) as exc_info:
        client.recognize(
            image_data=b"img",
            content_type="image/png",
            model="qwen3.5-ocr",
            timeout_seconds=60,
        )

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_SDK_UNAVAILABLE


def test_dashscope_client_recognize_sdk_unavailable_maps_to_artifact_error() -> None:
    """When the SDK is unavailable, the extractor maps the error to
    ``ArtifactExtractionError`` with ``ocr_sdk_unavailable``."""

    class _SdkUnavailableClient:
        def recognize(
            self,
            *,
            image_data: bytes,
            content_type: str,
            model: str,
            timeout_seconds: int,
        ) -> QwenOcrResponse:
            raise QwenOcrSdkUnavailableError("dashscope SDK not installed")

    extractor = QwenOcrTextExtractor(
        api_key="sk-fake",
        client=_SdkUnavailableClient(),  # type: ignore[arg-type]
    )

    with pytest.raises(ArtifactExtractionError) as exc_info:
        extractor.extract_text(b"img", content_type="image/png")

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_SDK_UNAVAILABLE
    assert exc_info.value.failure_class == "configuration"


def test_dashscope_client_requires_non_empty_api_key() -> None:
    """``DashScopeQwenOcrClient`` rejects empty API keys at construction."""
    with pytest.raises(ValueError, match="non-empty api_key"):
        DashScopeQwenOcrClient(api_key="")
    with pytest.raises(ValueError, match="non-empty api_key"):
        DashScopeQwenOcrClient(api_key=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 16. Default model + timeout constants
# ---------------------------------------------------------------------------


def test_default_qwen_ocr_model_is_qwen3_5_ocr() -> None:
    """The default model name is ``qwen3.5-ocr`` (not hardcoded in the
    domain contract — overridable via settings)."""
    assert DEFAULT_QWEN_OCR_MODEL == "qwen3.5-ocr"


def test_default_qwen_ocr_timeout_is_60_seconds() -> None:
    assert DEFAULT_QWEN_OCR_TIMEOUT_SECONDS == 60


def test_qwen_extractor_default_model_and_timeout() -> None:
    """When model/timeout are not specified, defaults are used."""
    fake_client = FakeQwenOcrClient(
        response=QwenOcrResponse(extracted_text="text")
    )
    extractor = QwenOcrTextExtractor(api_key="sk-fake", client=fake_client)

    extractor.extract_text(b"img", content_type="image/png")

    assert fake_client.calls[0]["model"] == DEFAULT_QWEN_OCR_MODEL
    assert fake_client.calls[0]["timeout_seconds"] == DEFAULT_QWEN_OCR_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# 17. Settings: new fields have correct defaults
# ---------------------------------------------------------------------------


def test_settings_default_reader_ocr_qwen_model() -> None:
    """Settings defaults: ``reader_ocr_qwen_model = 'qwen3.5-ocr'``."""
    settings = Settings()
    assert settings.reader_ocr_qwen_model == "qwen3.5-ocr"


def test_settings_default_reader_ocr_request_timeout_seconds() -> None:
    """Settings defaults: ``reader_ocr_request_timeout_seconds = 60``."""
    settings = Settings()
    assert settings.reader_ocr_request_timeout_seconds == 60


def test_settings_ocr_qwen_model_overridable() -> None:
    """Settings model name is overridable (not hardcoded)."""
    settings = Settings(reader_ocr_qwen_model="qwen-vl-ocr")
    assert settings.reader_ocr_qwen_model == "qwen-vl-ocr"


def test_settings_ocr_timeout_overridable() -> None:
    settings = Settings(reader_ocr_request_timeout_seconds=120)
    assert settings.reader_ocr_request_timeout_seconds == 120


# ---------------------------------------------------------------------------
# 18. API key never leaks in error messages
# ---------------------------------------------------------------------------


def test_api_key_not_in_error_message_on_permission_denied() -> None:
    """The API key must never appear in error messages."""
    test_key = "sk-super-secret-key-12345"
    fake_client = FakeQwenOcrClient(
        error=QwenOcrPermissionError("dashscope auth failed (status=401)")
    )
    extractor = QwenOcrTextExtractor(api_key=test_key, client=fake_client)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        extractor.extract_text(b"img", content_type="image/png")

    assert test_key not in str(exc_info.value)
    assert test_key not in repr(exc_info.value)


def test_api_key_not_in_error_message_on_transient() -> None:
    """The API key must never appear in error messages (transient path)."""
    test_key = "sk-super-secret-key-67890"
    fake_client = FakeQwenOcrClient(
        error=QwenOcrTransientError("dashscope request timed out")
    )
    extractor = QwenOcrTextExtractor(api_key=test_key, client=fake_client)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        extractor.extract_text(b"img", content_type="image/png")

    assert test_key not in str(exc_info.value)


def test_api_key_not_in_extractor_repr() -> None:
    """The API key must not appear in the extractor's repr."""
    test_key = "sk-repr-leak-check-key"
    extractor = QwenOcrTextExtractor(api_key=test_key, client=FakeQwenOcrClient())
    assert test_key not in repr(extractor)


# ---------------------------------------------------------------------------
# 19. build_default_extraction_provider_router with real Qwen extractor
# ---------------------------------------------------------------------------


def test_build_default_router_accepts_real_qwen_extractor() -> None:
    """The default factory wires a real QwenOcrTextExtractor when provided."""
    fake_client = FakeQwenOcrClient(
        response=QwenOcrResponse(extracted_text="text")
    )
    ocr_extractor = QwenOcrTextExtractor(api_key="sk-fake", client=fake_client)
    reader = FakeStorageObjectReader(data=b"")
    router = build_default_extraction_provider_router(
        reader=reader,
        ocr_extractor=ocr_extractor,
    )

    assert isinstance(router, ArtifactExtractionProviderRouter)
    assert isinstance(router._ocr_provider, OcrArtifactExtractionProvider)
    assert router._ocr_provider._extractor is ocr_extractor


# ---------------------------------------------------------------------------
# 20. Event loop non-blocking regression (asyncio.to_thread)
# ---------------------------------------------------------------------------


async def test_ocr_provider_extract_text_does_not_block_event_loop() -> None:
    """Regression (P2): ``extract_text`` runs in ``asyncio.to_thread`` so a
    real DashScope call (which may block for up to
    ``reader_ocr_request_timeout_seconds``) does NOT block the artifact
    pipeline worker's event loop.

    A slow fake extractor blocks the calling thread for 0.2s using
    ``time.sleep`` (NOT ``asyncio.sleep``). A concurrent tracker task
    records event-loop ticks every 50ms. If the event loop were blocked,
    all ticks would land AFTER the extractor finishes. The test asserts
    at least one tick landed DURING the extractor's blocking window.
    """

    class _SlowBlockingExtractor:
        """Simulates a slow OCR call that blocks the calling thread."""

        def __init__(self) -> None:
            self.started_at: float | None = None
            self.finished_at: float | None = None

        def extract_text(
            self, data: bytes, *, content_type: str
        ) -> OcrTextExtractionResult:
            self.started_at = time.monotonic()
            time.sleep(0.2)  # blocking — simulates real DashScope I/O
            self.finished_at = time.monotonic()
            return OcrTextExtractionResult(
                extracted_text="slow OCR result",
                extractor_name="slow_fake_extractor",
            )

    slow_extractor = _SlowBlockingExtractor()
    raw_bytes = _png_bytes()
    reader = FakeStorageObjectReader(data=raw_bytes)
    provider = OcrArtifactExtractionProvider(
        reader=reader, extractor=slow_extractor
    )
    context = _make_context(byte_size=None, content_sha256=None)

    loop_ticks: list[float] = []

    async def _track_loop() -> None:
        """Record event-loop ticks every 50ms while OCR runs."""
        for _ in range(4):
            await asyncio.sleep(0.05)
            loop_ticks.append(time.monotonic())

    tracker = asyncio.create_task(_track_loop())
    result = await provider.extract(context)
    await tracker

    assert result.extracted_text == "slow OCR result"
    assert slow_extractor.started_at is not None
    assert slow_extractor.finished_at is not None

    # At least one loop tick must land DURING the extractor's blocking
    # window. If extract_text blocked the event loop, all ticks would
    # land after finished_at.
    ticks_during_block = [
        t for t in loop_ticks
        if slow_extractor.started_at <= t <= slow_extractor.finished_at
    ]
    assert len(ticks_during_block) >= 1, (
        f"Event loop was blocked during OCR call; "
        f"started={slow_extractor.started_at}, "
        f"finished={slow_extractor.finished_at}, "
        f"ticks={loop_ticks}"
    )


async def test_ocr_provider_preserves_artifact_error_from_thread() -> None:
    """ArtifactExtractionError raised inside the thread propagates through
    asyncio.to_thread with retryable + failure_code intact."""

    class _FailingExtractor:
        def extract_text(
            self, data: bytes, *, content_type: str
        ) -> OcrTextExtractionResult:
            raise ArtifactExtractionError(
                "simulated transient failure",
                retryable=True,
                failure_class="extraction",
                failure_code=FAILURE_CODE_OCR_BACKEND_TRANSIENT,
            )

    raw_bytes = _png_bytes()
    reader = FakeStorageObjectReader(data=raw_bytes)
    provider = OcrArtifactExtractionProvider(
        reader=reader, extractor=_FailingExtractor()
    )

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(_make_context(byte_size=None, content_sha256=None))

    assert exc_info.value.retryable is True
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_BACKEND_TRANSIENT


# ---------------------------------------------------------------------------
# 21. Opt-in smoke skeleton (env-gated, no network by default)
# ---------------------------------------------------------------------------


def test_qwen_ocr_smoke_skeleton_env_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opt-in smoke skeleton. Skipped unless ALL of:
    - ``CLAREAD_OCR_SMOKE_ENABLED=1``
    - ``DASHSCOPE_API_KEY`` (non-empty)
    - ``READER_OCR_QWEN_MODEL`` (non-empty)

    This test never runs in the default pytest suite. It exists as a
    skeleton for manual smoke validation against the real DashScope API.
    """
    smoke_enabled = os.environ.get("CLAREAD_OCR_SMOKE_ENABLED", "") == "1"
    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    model = os.environ.get("READER_OCR_QWEN_MODEL", "")

    if not (smoke_enabled and api_key and model):
        pytest.skip(
            "Qwen OCR smoke test requires CLAREAD_OCR_SMOKE_ENABLED=1, "
            "DASHSCOPE_API_KEY, and READER_OCR_QWEN_MODEL env vars"
        )

    # Real smoke path — only runs when explicitly opted in.
    # Uses a tiny 1x1 PNG to minimise API cost.
    client = DashScopeQwenOcrClient(api_key=api_key)
    tiny_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    response = client.recognize(
        image_data=tiny_png,
        content_type="image/png",
        model=model,
        timeout_seconds=60,
    )
    # We don't assert on the extracted text (a 1x1 PNG may yield empty text).
    # The smoke test just verifies the call completes without raising.
    assert isinstance(response, QwenOcrResponse)
