"""Tests for D6-I3T OCR Provider Foundation + Image Artifact Extraction Path.

Covers:

1. :class:`OcrArtifactExtractionProvider` happy path with a fake storage
   reader and a fake :class:`OcrTextExtractor` — extracted text, quality
   metadata, and warnings are verified for ``image/png`` and ``image/jpeg``.
2. Validation rules: ``byte_size`` / ``content_sha256`` mismatch fail
   closed; unsupported image subtype (``image/gif``) fails closed with
   ``unsupported_ocr_image_type``; non-image content type fails closed.
3. Empty OCR text → ``ocr_no_text_detected`` (terminal).
4. Low confidence: provider still returns text but adds
   ``ocr_low_confidence`` / ``layout_order_uncertain`` warnings + quality
   flags (downstream materialization handles candidate_document_required).
5. Extractor retryable error preserved (``retryable=True``); extractor
   terminal error preserved.
6. :class:`UnconfiguredOcrTextExtractor` → ``ocr_provider_unconfigured``
   (terminal). :class:`QwenOcrTextExtractor` stub also fails closed
   (no API key / not-yet-implemented).
7. :class:`ArtifactExtractionProviderRouter` routes ``image/png`` to the
   OCR provider; text/pdf paths still work without OCR SDK/config.
8. Worker entry regression: with a storage reader, the router injects an
   OCR provider; default settings (OCR disabled) → image jobs fail closed
   with ``ocr_provider_unconfigured``; text path still works.

No real OCR network calls are made. All extractors are fakes or stubs.
"""

from __future__ import annotations

import hashlib
from uuid import UUID, uuid4

import pytest

from app.config.settings import Settings
from app.services.reader_orchestration.artifact_extraction_provider_router import (
    ArtifactExtractionProviderRouter,
    FAILURE_CODE_UNSUPPORTED_ARTIFACT_CONTENT_TYPE,
    build_default_extraction_provider_router,
)
from app.services.reader_orchestration.artifact_extraction_worker import (
    ArtifactExtractionError,
    ArtifactExtractionJobContext,
    UnconfiguredArtifactExtractionProvider,
)
from app.services.reader_orchestration.ocr_artifact_extraction_provider import (
    DEFAULT_MIN_LAYOUT_CONFIDENCE,
    DEFAULT_MIN_TEXT_CONFIDENCE,
    EXTRACTOR_NAME_QWEN,
    EXTRACTOR_NAME_UNCONFIGURED,
    FAILURE_CODE_OCR_EXTRACTION_ERROR,
    FAILURE_CODE_OCR_NO_TEXT_DETECTED,
    FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED,
    FAILURE_CODE_UNSUPPORTED_OCR_IMAGE_TYPE,
    OcrArtifactExtractionProvider,
    OcrTextExtractionResult,
    OcrTextExtractor,
    QwenOcrTextExtractor,
    UnconfiguredOcrTextExtractor,
)
from app.services.reader_orchestration.pdf_artifact_extraction_provider import (
    PdfArtifactExtractionProvider,
    PdfTextExtractionResult,
)
from app.services.reader_orchestration.text_artifact_extraction_provider import (
    EXTRACTOR_NAME as TEXT_EXTRACTOR_NAME,
    FAILURE_CODE_BYTE_SIZE_MISMATCH,
    FAILURE_CODE_SHA256_MISMATCH,
    FAILURE_CODE_STORAGE_READ_ERROR,
    StorageObjectReadResult,
    TextArtifactExtractionProvider,
)

pytestmark = pytest.mark.anyio

# Fixed UUIDs for deterministic contexts
_USER_ID = UUID("00000000-0000-0000-0000-00000000e001")
_RECORD_ID = UUID("00000000-0000-0000-0000-00000000e002")
_ORIGINAL_INPUT_ID = UUID("00000000-0000-0000-0000-00000000e003")
_ARTIFACT_ID = UUID("00000000-0000-0000-0000-00000000e004")


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


class FakeOcrTextExtractor:
    """Fake :class:`OcrTextExtractor` returning pre-configured results."""

    def __init__(
        self,
        *,
        extracted_text: str = "Recognised text from image.",
        extractor_name: str = "fake_ocr_text_extractor_v1",
        text_confidence: float | None = 0.92,
        layout_confidence: float | None = 0.88,
        warnings: list[str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._extracted_text = extracted_text
        self._extractor_name = extractor_name
        self._text_confidence = text_confidence
        self._layout_confidence = layout_confidence
        self._warnings = warnings
        self._error = error
        self.calls: list[tuple[bytes, str]] = []

    def extract_text(
        self,
        data: bytes,
        *,
        content_type: str,
    ) -> OcrTextExtractionResult:
        self.calls.append((data, content_type))
        if self._error is not None:
            raise self._error
        return OcrTextExtractionResult(
            extracted_text=self._extracted_text,
            extractor_name=self._extractor_name,
            text_confidence=self._text_confidence,
            layout_confidence=self._layout_confidence,
            warnings=list(self._warnings) if self._warnings is not None else None,
        )


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
    """Minimal bytes representing a fake PNG (not a real PNG)."""
    return b"\x89PNG\r\n\x1a\n fake png body"


# ---------------------------------------------------------------------------
# Provider happy path
# ---------------------------------------------------------------------------


async def test_ocr_happy_path_image_png_text_quality() -> None:
    """image/png with a fake extractor: extracted text / quality / warnings
    are populated correctly. Confidence above thresholds → no warnings."""
    raw_bytes = _png_bytes()
    reader = FakeStorageObjectReader(data=raw_bytes)
    extractor = FakeOcrTextExtractor(
        extracted_text="Hello OCR world.",
        text_confidence=0.95,
        layout_confidence=0.90,
    )
    provider = OcrArtifactExtractionProvider(reader=reader, extractor=extractor)

    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    context = _make_context(byte_size=len(raw_bytes), content_sha256=sha256)

    result = await provider.extract(context)

    assert result.extracted_text == "Hello OCR world."
    assert result.extractor_name == "fake_ocr_text_extractor_v1"
    assert result.quality is not None
    assert result.quality["content_type"] == "image/png"
    assert result.quality["extractor_name"] == "fake_ocr_text_extractor_v1"
    assert result.quality["text_confidence"] == 0.95
    assert result.quality["layout_confidence"] == 0.90
    assert result.quality["has_text"] is True
    assert result.quality["image_byte_size"] == len(raw_bytes)
    assert result.quality["content_sha256_verified"] is True
    assert result.quality["source_filename"] == "scan.png"
    # High confidence → no warnings.
    assert result.warnings is None
    # Reader called with right object_key.
    assert len(reader.calls) == 1
    assert reader.calls[0]["object_key"] == "dev/test/scan.png"
    # Extractor received the raw bytes + normalised content_type.
    assert len(extractor.calls) == 1
    assert extractor.calls[0][0] == raw_bytes
    assert extractor.calls[0][1] == "image/png"


async def test_ocr_happy_path_image_jpeg() -> None:
    """image/jpeg is a supported content type."""
    raw_bytes = b"\xff\xd8\xff\xe0 fake jpeg"
    reader = FakeStorageObjectReader(data=raw_bytes)
    extractor = FakeOcrTextExtractor(
        extracted_text="JPEG text.",
        text_confidence=0.88,
        layout_confidence=0.80,
    )
    provider = OcrArtifactExtractionProvider(reader=reader, extractor=extractor)

    result = await provider.extract(
        _make_context(
            content_type="image/jpeg",
            source_filename="photo.jpg",
            byte_size=None,
            content_sha256=None,
        )
    )

    assert result.extracted_text == "JPEG text."
    assert result.quality is not None
    assert result.quality["content_type"] == "image/jpeg"
    assert result.quality["image_byte_size"] == len(raw_bytes)
    assert result.quality["content_sha256_verified"] is False


async def test_ocr_happy_path_image_webp_and_tiff() -> None:
    """image/webp and image/tiff are also supported."""
    for ct, filename in (("image/webp", "img.webp"), ("image/tiff", "scan.tiff")):
        raw_bytes = b"fake " + ct.encode()
        reader = FakeStorageObjectReader(data=raw_bytes)
        extractor = FakeOcrTextExtractor(extracted_text=f"Text from {ct}.")
        provider = OcrArtifactExtractionProvider(reader=reader, extractor=extractor)

        result = await provider.extract(
            _make_context(
                content_type=ct,
                source_filename=filename,
                byte_size=None,
                content_sha256=None,
            )
        )

        assert result.extracted_text == f"Text from {ct}."
        assert result.quality is not None
        assert result.quality["content_type"] == ct


async def test_ocr_happy_path_with_content_type_charset_suffix() -> None:
    """``image/png; charset=binary`` is normalised to ``image/png``."""
    raw_bytes = _png_bytes()
    reader = FakeStorageObjectReader(data=raw_bytes)
    extractor = FakeOcrTextExtractor(extracted_text="Normalised CT text.")
    provider = OcrArtifactExtractionProvider(reader=reader, extractor=extractor)

    result = await provider.extract(
        _make_context(
            content_type="image/png; charset=binary",
            byte_size=None,
            content_sha256=None,
        )
    )

    assert result.extracted_text == "Normalised CT text."
    assert extractor.calls[0][1] == "image/png"


# ---------------------------------------------------------------------------
# Provider: confidence warnings (non-fatal)
# ---------------------------------------------------------------------------


async def test_ocr_low_text_confidence_adds_warning() -> None:
    """text_confidence below threshold → ocr_low_confidence warning; result
    still returned (downstream gates to candidate_document_required)."""
    raw_bytes = _png_bytes()
    reader = FakeStorageObjectReader(data=raw_bytes)
    extractor = FakeOcrTextExtractor(
        extracted_text="Low confidence text.",
        text_confidence=0.40,  # below default 0.75
        layout_confidence=0.90,  # above threshold
    )
    provider = OcrArtifactExtractionProvider(
        reader=reader,
        extractor=extractor,
        min_text_confidence=0.75,
        min_layout_confidence=0.65,
    )

    result = await provider.extract(
        _make_context(byte_size=None, content_sha256=None)
    )

    assert result.extracted_text == "Low confidence text."
    assert result.warnings is not None
    assert any(w.startswith("ocr_low_confidence:") for w in result.warnings)
    assert not any(w.startswith("layout_order_uncertain:") for w in result.warnings)
    # Quality still records the confidence values.
    assert result.quality is not None
    assert result.quality["text_confidence"] == 0.40
    assert result.quality["has_text"] is True


async def test_ocr_low_layout_confidence_adds_warning() -> None:
    """layout_confidence below threshold → layout_order_uncertain warning."""
    raw_bytes = _png_bytes()
    reader = FakeStorageObjectReader(data=raw_bytes)
    extractor = FakeOcrTextExtractor(
        extracted_text="Uncertain layout text.",
        text_confidence=0.90,
        layout_confidence=0.30,  # below default 0.65
    )
    provider = OcrArtifactExtractionProvider(reader=reader, extractor=extractor)

    result = await provider.extract(
        _make_context(byte_size=None, content_sha256=None)
    )

    assert result.warnings is not None
    assert any(w.startswith("layout_order_uncertain:") for w in result.warnings)
    assert not any(w.startswith("ocr_low_confidence:") for w in result.warnings)


async def test_ocr_extractor_warnings_preserved() -> None:
    """Extractor-level warnings (e.g. empty_regions) are preserved verbatim."""
    raw_bytes = _png_bytes()
    reader = FakeStorageObjectReader(data=raw_bytes)
    extractor = FakeOcrTextExtractor(
        extracted_text="Text with empty regions.",
        text_confidence=0.90,
        layout_confidence=0.90,
        warnings=["empty_regions: 2 of 5 regions have no text"],
    )
    provider = OcrArtifactExtractionProvider(reader=reader, extractor=extractor)

    result = await provider.extract(
        _make_context(byte_size=None, content_sha256=None)
    )

    assert result.warnings is not None
    assert any(w.startswith("empty_regions:") for w in result.warnings)


async def test_ocr_custom_thresholds_via_provider_kwargs() -> None:
    """Provider accepts custom min_text_confidence / min_layout_confidence."""
    raw_bytes = _png_bytes()
    reader = FakeStorageObjectReader(data=raw_bytes)
    extractor = FakeOcrTextExtractor(
        extracted_text="Borderline text.",
        text_confidence=0.50,
        layout_confidence=0.50,
    )
    # Lower thresholds so 0.50 is above both → no warnings.
    provider = OcrArtifactExtractionProvider(
        reader=reader,
        extractor=extractor,
        min_text_confidence=0.40,
        min_layout_confidence=0.40,
    )

    result = await provider.extract(
        _make_context(byte_size=None, content_sha256=None)
    )

    assert result.warnings is None


# ---------------------------------------------------------------------------
# Provider: fail-closed paths
# ---------------------------------------------------------------------------


async def test_ocr_unsupported_image_subtype_gif_fails_closed() -> None:
    """image/gif is not in the supported set → unsupported_ocr_image_type."""
    reader = FakeStorageObjectReader(data=b"")
    extractor = FakeOcrTextExtractor(extracted_text="should not be called")
    provider = OcrArtifactExtractionProvider(reader=reader, extractor=extractor)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(
            _make_context(
                content_type="image/gif",
                source_filename="anim.gif",
                byte_size=None,
                content_sha256=None,
            )
        )

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_UNSUPPORTED_OCR_IMAGE_TYPE
    # Reader must not have been called for an unsupported content_type.
    assert reader.calls == []
    assert extractor.calls == []


async def test_ocr_non_image_content_type_fails_closed() -> None:
    """OCR provider only accepts image/* — text/plain is rejected."""
    reader = FakeStorageObjectReader(data=b"")
    extractor = FakeOcrTextExtractor(extracted_text="should not be called")
    provider = OcrArtifactExtractionProvider(reader=reader, extractor=extractor)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(
            _make_context(
                content_type="text/plain",
                source_filename="notes.txt",
                byte_size=None,
                content_sha256=None,
            )
        )

    assert exc_info.value.failure_code == FAILURE_CODE_UNSUPPORTED_OCR_IMAGE_TYPE
    assert reader.calls == []


async def test_ocr_byte_size_mismatch_fails_closed() -> None:
    raw_bytes = _png_bytes()
    reader = FakeStorageObjectReader(data=raw_bytes)
    extractor = FakeOcrTextExtractor(extracted_text="should not be called")
    provider = OcrArtifactExtractionProvider(reader=reader, extractor=extractor)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(
            _make_context(
                byte_size=len(raw_bytes) + 100,
                content_sha256=None,
            )
        )

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_BYTE_SIZE_MISMATCH
    # Extractor must not be called when byte_size validation fails.
    assert extractor.calls == []


async def test_ocr_sha256_mismatch_fails_closed() -> None:
    raw_bytes = _png_bytes()
    reader = FakeStorageObjectReader(data=raw_bytes)
    extractor = FakeOcrTextExtractor(extracted_text="should not be called")
    provider = OcrArtifactExtractionProvider(reader=reader, extractor=extractor)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(
            _make_context(
                byte_size=None,
                content_sha256="0" * 64,  # wrong sha256
            )
        )

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_SHA256_MISMATCH
    assert extractor.calls == []


async def test_ocr_empty_text_fails_closed() -> None:
    """OCR returns empty text → ocr_no_text_detected (terminal)."""
    raw_bytes = _png_bytes()
    reader = FakeStorageObjectReader(data=raw_bytes)
    extractor = FakeOcrTextExtractor(
        extracted_text="   ",  # whitespace only
        text_confidence=0.10,
    )
    provider = OcrArtifactExtractionProvider(reader=reader, extractor=extractor)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(
            _make_context(byte_size=None, content_sha256=None)
        )

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_NO_TEXT_DETECTED


async def test_ocr_storage_read_error_wrapped_as_retryable() -> None:
    """A non-ArtifactExtractionError raised by the reader is wrapped as
    ``storage_read_error`` and marked retryable."""

    class _Boom(Exception):
        pass

    reader = FakeStorageObjectReader(data=b"", error=_Boom("network down"))
    extractor = FakeOcrTextExtractor(extracted_text="should not be called")
    provider = OcrArtifactExtractionProvider(reader=reader, extractor=extractor)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(
            _make_context(byte_size=None, content_sha256=None)
        )

    assert exc_info.value.retryable is True
    assert exc_info.value.failure_code == FAILURE_CODE_STORAGE_READ_ERROR


async def test_ocr_extractor_retryable_error_preserved() -> None:
    """If the extractor raises ArtifactExtractionError(retryable=True), the
    provider preserves the retryable flag + failure_code unchanged."""

    class _RetryableExtractor:
        def extract_text(self, data: bytes, *, content_type: str) -> OcrTextExtractionResult:
            raise ArtifactExtractionError(
                "transient OCR backend error",
                retryable=True,
                failure_class="extraction",
                failure_code="ocr_backend_transient",
            )

    raw_bytes = _png_bytes()
    reader = FakeStorageObjectReader(data=raw_bytes)
    provider = OcrArtifactExtractionProvider(
        reader=reader, extractor=_RetryableExtractor()  # type: ignore[arg-type]
    )

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(
            _make_context(byte_size=None, content_sha256=None)
        )

    assert exc_info.value.retryable is True
    # failure_code must be the extractor's, not re-wrapped.
    assert exc_info.value.failure_code == "ocr_backend_transient"


async def test_ocr_extractor_terminal_error_preserved() -> None:
    """If the extractor raises ArtifactExtractionError(retryable=False), the
    provider preserves the terminal flag + failure_code unchanged."""

    class _TerminalExtractor:
        def extract_text(self, data: bytes, *, content_type: str) -> OcrTextExtractionResult:
            raise ArtifactExtractionError(
                "OCR permission denied",
                retryable=False,
                failure_class="extraction",
                failure_code="ocr_permission_denied",
            )

    raw_bytes = _png_bytes()
    reader = FakeStorageObjectReader(data=raw_bytes)
    provider = OcrArtifactExtractionProvider(
        reader=reader, extractor=_TerminalExtractor()  # type: ignore[arg-type]
    )

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(
            _make_context(byte_size=None, content_sha256=None)
        )

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == "ocr_permission_denied"


async def test_ocr_extractor_unexpected_error_wrapped_as_terminal() -> None:
    """A non-ArtifactExtractionError raised by the extractor is wrapped as
    ``ocr_extraction_error`` (terminal)."""

    class _BoomExtractor:
        def extract_text(self, data: bytes, *, content_type: str) -> OcrTextExtractionResult:
            raise RuntimeError("unexpected extractor crash")

    raw_bytes = _png_bytes()
    reader = FakeStorageObjectReader(data=raw_bytes)
    provider = OcrArtifactExtractionProvider(
        reader=reader, extractor=_BoomExtractor()  # type: ignore[arg-type]
    )

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(
            _make_context(byte_size=None, content_sha256=None)
        )

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_EXTRACTION_ERROR


# ---------------------------------------------------------------------------
# UnconfiguredOcrTextExtractor / QwenOcrTextExtractor stubs
# ---------------------------------------------------------------------------


def test_unconfigured_ocr_extractor_raises_ocr_provider_unconfigured() -> None:
    """The default unconfigured extractor fails closed on every call."""
    extractor = UnconfiguredOcrTextExtractor()
    with pytest.raises(ArtifactExtractionError) as exc_info:
        extractor.extract_text(b"anything", content_type="image/png")

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED


async def test_ocr_provider_default_extractor_unconfigured_fails_closed() -> None:
    """When no extractor is injected, the provider uses
    :class:`UnconfiguredOcrTextExtractor` → ``ocr_provider_unconfigured``."""
    raw_bytes = _png_bytes()
    reader = FakeStorageObjectReader(data=raw_bytes)
    # No extractor= kwarg → default UnconfiguredOcrTextExtractor.
    provider = OcrArtifactExtractionProvider(reader=reader)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(
            _make_context(byte_size=None, content_sha256=None)
        )

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED


def test_qwen_extractor_without_api_key_fails_closed() -> None:
    """QwenOcrTextExtractor stub without API key → ocr_provider_unconfigured."""
    extractor = QwenOcrTextExtractor(api_key=None)
    with pytest.raises(ArtifactExtractionError) as exc_info:
        extractor.extract_text(b"image bytes", content_type="image/png")

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED


def test_qwen_extractor_with_api_key_still_fails_closed_stub() -> None:
    """QwenOcrTextExtractor stub with API key still fails closed — the real
    DashScope call is deferred to a later round."""
    extractor = QwenOcrTextExtractor(api_key="sk-fake-key")
    with pytest.raises(ArtifactExtractionError) as exc_info:
        extractor.extract_text(b"image bytes", content_type="image/png")

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED


# ---------------------------------------------------------------------------
# ArtifactExtractionProviderRouter: image/* delegation
# ---------------------------------------------------------------------------


async def test_router_routes_image_png_to_ocr_provider() -> None:
    """image/png is delegated to the OCR provider and returns a result."""
    raw_bytes = _png_bytes()
    reader = FakeStorageObjectReader(data=raw_bytes)
    extractor = FakeOcrTextExtractor(
        extracted_text="OCR via router.",
        text_confidence=0.90,
        layout_confidence=0.85,
    )
    text_provider = TextArtifactExtractionProvider(reader=reader)
    pdf_provider = PdfArtifactExtractionProvider(reader=reader, extractor=_FakePdfExtractor())
    ocr_provider = OcrArtifactExtractionProvider(reader=reader, extractor=extractor)
    router = ArtifactExtractionProviderRouter(
        text_provider=text_provider,
        pdf_provider=pdf_provider,
        ocr_provider=ocr_provider,
    )

    result = await router.extract(
        _make_context(content_type="image/png", source_filename="scan.png")
    )

    assert result.extracted_text == "OCR via router."
    assert result.quality is not None
    assert result.quality["content_type"] == "image/png"
    # OCR extractor was called.
    assert len(extractor.calls) == 1


async def test_router_image_gif_fails_closed_unsupported_ocr_image_type() -> None:
    """image/gif routes to OCR provider, which rejects it with
    ``unsupported_ocr_image_type`` (not router-level unknown)."""
    reader = FakeStorageObjectReader(data=b"")
    extractor = FakeOcrTextExtractor(extracted_text="should not be called")
    router = ArtifactExtractionProviderRouter(
        text_provider=TextArtifactExtractionProvider(reader=reader),
        pdf_provider=PdfArtifactExtractionProvider(reader=reader, extractor=_FakePdfExtractor()),
        ocr_provider=OcrArtifactExtractionProvider(reader=reader, extractor=extractor),
    )

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await router.extract(
            _make_context(
                content_type="image/gif",
                source_filename="anim.gif",
                byte_size=None,
                content_sha256=None,
            )
        )

    assert exc_info.value.failure_code == FAILURE_CODE_UNSUPPORTED_OCR_IMAGE_TYPE
    assert extractor.calls == []


async def test_router_image_with_unconfigured_extractor_fails_closed() -> None:
    """image/png with default UnconfiguredOcrTextExtractor → ocr_provider_unconfigured
    (preserves the D6-I3S fail-closed contract)."""
    raw_bytes = _png_bytes()
    reader = FakeStorageObjectReader(data=raw_bytes)
    # OCR provider with no extractor → UnconfiguredOcrTextExtractor.
    router = ArtifactExtractionProviderRouter(
        text_provider=TextArtifactExtractionProvider(reader=reader),
        pdf_provider=PdfArtifactExtractionProvider(reader=reader, extractor=_FakePdfExtractor()),
        ocr_provider=OcrArtifactExtractionProvider(reader=reader),
    )

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await router.extract(
            _make_context(
                content_type="image/png",
                source_filename="scan.png",
                byte_size=None,
                content_sha256=None,
            )
        )

    assert exc_info.value.failure_code == FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED


async def test_router_unknown_content_type_still_unsupported_artifact() -> None:
    """Non-image, non-text, non-pdf content types still fail closed at the
    router level with ``unsupported_artifact_content_type``."""
    reader = FakeStorageObjectReader(data=b"")
    router = ArtifactExtractionProviderRouter(
        text_provider=TextArtifactExtractionProvider(reader=reader),
        pdf_provider=PdfArtifactExtractionProvider(reader=reader, extractor=_FakePdfExtractor()),
        ocr_provider=OcrArtifactExtractionProvider(reader=reader),
    )

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await router.extract(
            _make_context(
                content_type="application/vnd.ms-excel",
                source_filename="sheet.xls",
                byte_size=None,
                content_sha256=None,
            )
        )

    assert exc_info.value.failure_code == FAILURE_CODE_UNSUPPORTED_ARTIFACT_CONTENT_TYPE


async def test_router_text_path_still_works_without_ocr_config() -> None:
    """Text path is NOT affected by OCR configuration — even with
    UnconfiguredOcrTextExtractor, text extraction succeeds."""
    raw_bytes = b"Hello text via router."
    reader = FakeStorageObjectReader(data=raw_bytes)
    router = ArtifactExtractionProviderRouter(
        text_provider=TextArtifactExtractionProvider(reader=reader),
        pdf_provider=PdfArtifactExtractionProvider(reader=reader, extractor=_FakePdfExtractor()),
        ocr_provider=OcrArtifactExtractionProvider(reader=reader),  # unconfigured
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


async def test_router_pdf_path_still_works_without_ocr_config() -> None:
    """PDF path is NOT affected by OCR configuration."""

    class _FakePdfExtractorWithText:
        def extract_text(self, data: bytes) -> PdfTextExtractionResult:
            return PdfTextExtractionResult(
                pages=["PDF page text."],
                extractor_name="deterministic_pdf_text_extractor_v1",
            )

    raw_bytes = b"%PDF-1.4 bytes"
    reader = FakeStorageObjectReader(data=raw_bytes)
    router = ArtifactExtractionProviderRouter(
        text_provider=TextArtifactExtractionProvider(reader=reader),
        pdf_provider=PdfArtifactExtractionProvider(
            reader=reader, extractor=_FakePdfExtractorWithText()
        ),
        ocr_provider=OcrArtifactExtractionProvider(reader=reader),  # unconfigured
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


# ---------------------------------------------------------------------------
# build_default_extraction_provider_router
# ---------------------------------------------------------------------------


def test_build_default_router_constructs_all_three_providers() -> None:
    """The default factory builds a router holding text + PDF + OCR providers.

    The router is constructable without pypdf installed and without OCR
    config — image jobs fail closed on the first extract call.
    """
    reader = FakeStorageObjectReader(data=b"")
    router = build_default_extraction_provider_router(reader=reader)

    assert isinstance(router, ArtifactExtractionProviderRouter)
    assert isinstance(router._text_provider, TextArtifactExtractionProvider)
    assert isinstance(router._pdf_provider, PdfArtifactExtractionProvider)
    assert isinstance(router._ocr_provider, OcrArtifactExtractionProvider)


def test_build_default_router_accepts_custom_ocr_extractor() -> None:
    """The factory wires a custom OCR extractor when provided."""

    class _CustomExtractor:
        def extract_text(self, data: bytes, *, content_type: str) -> OcrTextExtractionResult:
            return OcrTextExtractionResult(
                extracted_text="custom",
                extractor_name="custom_v1",
            )

    reader = FakeStorageObjectReader(data=b"")
    router = build_default_extraction_provider_router(
        reader=reader,
        ocr_extractor=_CustomExtractor(),  # type: ignore[arg-type]
    )

    assert isinstance(router._ocr_provider, OcrArtifactExtractionProvider)


# ---------------------------------------------------------------------------
# Worker entry regression: OCR provider wiring
# ---------------------------------------------------------------------------


def test_build_pipeline_service_injects_router_with_ocr_provider() -> None:
    """With a storage reader, ``build_pipeline_service`` wires a router that
    holds an OCR provider (using UnconfiguredOcrTextExtractor by default
    since OCR is disabled in default settings)."""
    from scripts.run_reader_artifact_pipeline_worker import build_pipeline_service

    reader = FakeStorageObjectReader(data=b"")
    service = build_pipeline_service(
        settings=Settings(),  # OCR disabled by default
        pool=object(),
        storage_reader=reader,  # type: ignore[arg-type]
    )

    extraction_worker = service._extraction_worker
    assert extraction_worker is not None
    provider = extraction_worker._provider
    assert isinstance(provider, ArtifactExtractionProviderRouter)
    # OCR provider is wired (default UnconfiguredOcrTextExtractor).
    assert isinstance(provider._ocr_provider, OcrArtifactExtractionProvider)


def test_build_pipeline_service_fail_closed_without_reader() -> None:
    """Without a storage reader, ``build_pipeline_service`` falls back to
    ``UnconfiguredArtifactExtractionProvider`` (fail-closed) — no OCR wiring."""
    from scripts.run_reader_artifact_pipeline_worker import build_pipeline_service

    service = build_pipeline_service(
        settings=Settings(),
        pool=object(),
        storage_reader=None,
    )

    extraction_worker = service._extraction_worker
    assert extraction_worker is not None
    assert isinstance(
        extraction_worker._provider, UnconfiguredArtifactExtractionProvider
    )


def test_build_ocr_extractor_default_disabled_returns_unconfigured() -> None:
    """Default settings (OCR disabled) → UnconfiguredOcrTextExtractor."""
    from scripts.run_reader_artifact_pipeline_worker import _build_ocr_extractor

    extractor = _build_ocr_extractor(Settings())
    assert isinstance(extractor, UnconfiguredOcrTextExtractor)


def test_build_ocr_extractor_enabled_qwen_returns_qwen_stub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When OCR is enabled with provider_name='qwen', the worker builds a
    QwenOcrTextExtractor stub. The stub still fails closed (no real network)."""
    from scripts.run_reader_artifact_pipeline_worker import _build_ocr_extractor

    # Ensure DASHSCOPE_API_KEY is not set in the test environment.
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    settings = Settings(
        reader_ocr_provider_enabled=True,
        reader_ocr_provider_name="qwen",
    )
    extractor = _build_ocr_extractor(settings)
    assert isinstance(extractor, QwenOcrTextExtractor)
    # Stub without API key → ocr_provider_unconfigured.
    with pytest.raises(ArtifactExtractionError) as exc_info:
        extractor.extract_text(b"img", content_type="image/png")
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED


def test_build_ocr_extractor_enabled_qwen_with_api_key_still_fails_closed_stub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even with DASHSCOPE_API_KEY set, the Qwen stub fails closed (real
    network call is deferred to a later round)."""
    from scripts.run_reader_artifact_pipeline_worker import _build_ocr_extractor

    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-fake-test-key")

    settings = Settings(
        reader_ocr_provider_enabled=True,
        reader_ocr_provider_name="qwen",
    )
    extractor = _build_ocr_extractor(settings)
    assert isinstance(extractor, QwenOcrTextExtractor)
    with pytest.raises(ArtifactExtractionError) as exc_info:
        extractor.extract_text(b"img", content_type="image/png")
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED


def test_build_ocr_extractor_unknown_provider_name_falls_back_to_unconfigured() -> None:
    """Unknown provider name → UnconfiguredOcrTextExtractor (fail-closed)."""
    from scripts.run_reader_artifact_pipeline_worker import _build_ocr_extractor

    settings = Settings(
        reader_ocr_provider_enabled=True,
        reader_ocr_provider_name="unknown_vendor",
    )
    extractor = _build_ocr_extractor(settings)
    assert isinstance(extractor, UnconfiguredOcrTextExtractor)


async def test_build_pipeline_service_default_ocr_unconfigured_image_job_fails_closed() -> None:
    """End-to-end: with default settings (OCR disabled), an image job through
    the router fails closed with ``ocr_provider_unconfigured``."""
    from scripts.run_reader_artifact_pipeline_worker import build_pipeline_service

    raw_bytes = _png_bytes()
    reader = FakeStorageObjectReader(data=raw_bytes)
    service = build_pipeline_service(
        settings=Settings(),  # OCR disabled
        pool=object(),
        storage_reader=reader,  # type: ignore[arg-type]
    )

    provider = service._extraction_worker._provider
    assert isinstance(provider, ArtifactExtractionProviderRouter)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(
            _make_context(
                content_type="image/png",
                source_filename="scan.png",
                byte_size=None,
                content_sha256=None,
            )
        )

    assert exc_info.value.failure_code == FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED


async def test_build_pipeline_service_text_path_still_works_with_ocr_disabled() -> None:
    """End-to-end: text extraction through the router still works when OCR
    is disabled (regression check — OCR wiring must not break text path)."""
    from scripts.run_reader_artifact_pipeline_worker import build_pipeline_service

    text_bytes = b"Hello with OCR disabled."
    reader = FakeStorageObjectReader(data=text_bytes)
    service = build_pipeline_service(
        settings=Settings(),  # OCR disabled
        pool=object(),
        storage_reader=reader,  # type: ignore[arg-type]
    )

    provider = service._extraction_worker._provider
    assert isinstance(provider, ArtifactExtractionProviderRouter)

    result = await provider.extract(
        _make_context(
            content_type="text/plain",
            source_filename="hello.txt",
            byte_size=None,
            content_sha256=None,
        )
    )

    assert result.extracted_text == "Hello with OCR disabled."
    assert result.extractor_name == TEXT_EXTRACTOR_NAME
