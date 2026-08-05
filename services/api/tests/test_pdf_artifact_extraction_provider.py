# task-history: D6-I3S (renamed from test_d6_i3s_pdf_artifact_extraction_provider.py)
"""Tests for the PDF text extraction provider + provider router.

Covers:

1. :class:`PdfArtifactExtractionProvider` happy path with a fake storage
   reader and a fake :class:`PdfTextExtractor` — extracted text, quality
   metadata, and warnings are verified.
2. Validation rules: ``byte_size`` / ``content_sha256`` mismatch fail
   closed; unsupported ``content_type`` fails closed; empty extracted text
   fails closed with ``pdf_no_extractable_text``.
3. :class:`PypdfPdfTextExtractor` raises ``pdf_extractor_unavailable`` when
   ``pypdf`` cannot be imported (the default SDK-missing path).
4. :class:`ArtifactExtractionProviderRouter` routes by content_type:
   text/markdown → text provider, ``application/pdf`` → pdf provider,
   ``image/*`` → ``ocr_provider_unconfigured``, unknown →
   ``unsupported_artifact_content_type``.
5. Worker entry regression: when a storage reader is wired in,
   :func:`build_pipeline_service` injects a router; the text path still
   works end-to-end through the provider layer.

No real PDF SDK is used. The ``pypdf`` import path is exercised via a
``builtins.__import__`` patch so the SDK-missing branch is covered even
when ``pypdf`` happens to be installed in the local environment.
"""

from __future__ import annotations

import builtins
import hashlib
import sys
from uuid import UUID, uuid4

import pytest

from app.services.reader_orchestration.artifact_extraction_provider_router import (
    ArtifactExtractionProviderRouter,
    FAILURE_CODE_UNSUPPORTED_ARTIFACT_CONTENT_TYPE,
    build_default_extraction_provider_router,
)
from app.services.reader_orchestration.artifact_extraction_worker import (
    ArtifactExtractionError,
    ArtifactExtractionJobContext,
)
from app.services.reader_orchestration.ocr_artifact_extraction_provider import (
    FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED,
    OcrArtifactExtractionProvider,
)
from app.services.reader_orchestration.pdf_artifact_extraction_provider import (
    EXTRACTOR_NAME,
    FAILURE_CODE_PDF_EXTRACTOR_UNAVAILABLE,
    FAILURE_CODE_PDF_NO_EXTRACTABLE_TEXT,
    FAILURE_CODE_PDF_EXTRACTION_ERROR,
    FAILURE_CODE_UNSUPPORTED_CONTENT_TYPE as PDF_UNSUPPORTED_CT,
    PdfArtifactExtractionProvider,
    PdfTextExtractionResult,
    PdfTextExtractor,
    PypdfPdfTextExtractor,
)
from app.services.reader_orchestration.text_artifact_extraction_provider import (
    EXTRACTOR_NAME as TEXT_EXTRACTOR_NAME,
    FAILURE_CODE_BYTE_SIZE_MISMATCH,
    FAILURE_CODE_SHA256_MISMATCH,
    StorageObjectReadResult,
    TextArtifactExtractionProvider,
)

pytestmark = [pytest.mark.anyio, pytest.mark.chain_reader_parse, pytest.mark.seam_pure_unit, pytest.mark.life_permanent_regression]

# Fixed UUIDs for deterministic contexts
_USER_ID = UUID("00000000-0000-0000-0000-00000000d001")
_RECORD_ID = UUID("00000000-0000-0000-0000-00000000d002")
_ORIGINAL_INPUT_ID = UUID("00000000-0000-0000-0000-00000000d003")
_ARTIFACT_ID = UUID("00000000-0000-0000-0000-00000000d004")
_RUN_ID = UUID("00000000-0000-0000-0000-00000000d005")


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


class FakePdfTextExtractor:
    """Fake :class:`PdfTextExtractor` returning pre-configured pages."""

    def __init__(
        self,
        *,
        pages: list[str],
        extractor_name: str = EXTRACTOR_NAME,
        error: Exception | None = None,
    ) -> None:
        self._pages = pages
        self._extractor_name = extractor_name
        self._error = error
        self.calls: list[bytes] = []

    def extract_text(self, data: bytes) -> PdfTextExtractionResult:
        self.calls.append(data)
        if self._error is not None:
            raise self._error
        return PdfTextExtractionResult(
            pages=list(self._pages),
            extractor_name=self._extractor_name,
        )


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------


def _make_context(
    *,
    content_type: str | None = "application/pdf",
    source_filename: str = "doc.pdf",
    byte_size: int | None = None,
    content_sha256: str | None = None,
    object_key: str = "dev/test/doc.pdf",
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


# ---------------------------------------------------------------------------
# Provider happy path
# ---------------------------------------------------------------------------


async def test_pdf_happy_path_multi_page_text_quality() -> None:
    """Multi-page PDF: extracted text is pages joined by blank lines;
    quality metadata records page_count, extractor_name, has_extractable_text,
    content_sha256_verified, byte_size, source_filename."""
    page1 = "Page one content with enough text to avoid density warnings."
    page2 = "Page two content also with enough text to avoid density warnings."
    raw_bytes = b"%PDF-1.4 fake pdf bytes that are long enough"
    # Pad raw_bytes so the text/bytes ratio stays above the low-text-density
    # threshold (0.05). Total text length ~ 100 chars; raw_bytes < 2000 keeps
    # ratio above 0.05.
    raw_bytes = raw_bytes + b" " * 100
    reader = FakeStorageObjectReader(data=raw_bytes)
    extractor = FakePdfTextExtractor(pages=[page1, page2])
    provider = PdfArtifactExtractionProvider(reader=reader, extractor=extractor)

    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    context = _make_context(
        byte_size=len(raw_bytes),
        content_sha256=sha256,
    )

    result = await provider.extract(context)

    assert result.extracted_text == f"{page1}\n\n{page2}"
    assert result.extractor_name == EXTRACTOR_NAME
    assert result.quality is not None
    assert result.quality["content_type"] == "application/pdf"
    assert result.quality["page_count"] == 2
    assert result.quality["extractor_name"] == EXTRACTOR_NAME
    assert result.quality["has_extractable_text"] is True
    assert result.quality["byte_size"] == len(raw_bytes)
    assert result.quality["content_sha256_verified"] is True
    assert result.quality["source_filename"] == "doc.pdf"
    # Happy path: no empty pages, density is high → no warnings.
    assert result.warnings is None
    # Reader was called with the right object_key.
    assert len(reader.calls) == 1
    assert reader.calls[0]["object_key"] == "dev/test/doc.pdf"
    # Extractor received the raw bytes.
    assert len(extractor.calls) == 1
    assert extractor.calls[0] == raw_bytes


async def test_pdf_happy_path_no_byte_size_no_sha256_still_succeeds() -> None:
    """When byte_size / content_sha256 are absent, validation is skipped
    but content_sha256_verified is reported as False."""
    page = "Single page PDF with sufficient text content for the test."
    raw_bytes = b"%PDF-1.4 minimal"
    reader = FakeStorageObjectReader(data=raw_bytes)
    extractor = FakePdfTextExtractor(pages=[page])
    provider = PdfArtifactExtractionProvider(reader=reader, extractor=extractor)

    result = await provider.extract(_make_context(byte_size=None, content_sha256=None))

    assert result.extracted_text == page
    assert result.quality is not None
    assert result.quality["content_sha256_verified"] is False
    assert result.quality["byte_size"] == len(raw_bytes)


async def test_pdf_happy_path_with_content_type_charset_suffix() -> None:
    """``application/pdf; charset=binary`` is normalised to ``application/pdf``."""
    raw_bytes = b"%PDF-1.4" + b" " * 80
    reader = FakeStorageObjectReader(data=raw_bytes)
    extractor = FakePdfTextExtractor(pages=["Some text content long enough."])
    provider = PdfArtifactExtractionProvider(reader=reader, extractor=extractor)

    result = await provider.extract(
        _make_context(content_type="application/pdf; charset=binary")
    )

    assert result.extracted_text == "Some text content long enough."
    assert result.quality is not None
    assert result.quality["content_type"] == "application/pdf; charset=binary"


async def test_pdf_warning_empty_pages_when_some_pages_blank() -> None:
    """When at least one page has no extractable text (but the overall
    extracted text is non-empty), an ``empty_pages`` warning is emitted."""
    raw_bytes = b"%PDF-1.4 with one blank page"
    reader = FakeStorageObjectReader(data=raw_bytes)
    extractor = FakePdfTextExtractor(
        pages=["Page with text content long enough.", "", "Another page with text."]
    )
    provider = PdfArtifactExtractionProvider(reader=reader, extractor=extractor)

    result = await provider.extract(_make_context(byte_size=None, content_sha256=None))

    assert result.warnings is not None
    assert any(w.startswith("empty_pages:") for w in result.warnings)
    # page_count reflects all 3 pages, even the empty one.
    assert result.quality is not None
    assert result.quality["page_count"] == 3


async def test_pdf_warning_low_text_density_when_text_is_sparse() -> None:
    """When extracted text is less than 5% of raw byte size, a
    ``low_text_density`` warning is emitted (non-fatal)."""
    short_text = "tiny"
    # Pad raw_bytes so len(text)/len(bytes) < 0.05 (4/1000 = 0.4%).
    raw_bytes = b"%PDF-1.4" + b"\x00" * 1000
    reader = FakeStorageObjectReader(data=raw_bytes)
    extractor = FakePdfTextExtractor(pages=[short_text])
    provider = PdfArtifactExtractionProvider(reader=reader, extractor=extractor)

    result = await provider.extract(_make_context(byte_size=None, content_sha256=None))

    assert result.extracted_text == "tiny"
    assert result.warnings is not None
    assert any(w.startswith("low_text_density:") for w in result.warnings)


# ---------------------------------------------------------------------------
# Provider validation: fail-closed paths
# ---------------------------------------------------------------------------


async def test_pdf_unsupported_content_type_text_plain_fails_closed() -> None:
    """A non-PDF content_type is rejected before any storage read."""
    reader = FakeStorageObjectReader(data=b"")
    extractor = FakePdfTextExtractor(pages=["should not be called"])
    provider = PdfArtifactExtractionProvider(reader=reader, extractor=extractor)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(
            _make_context(content_type="text/plain", source_filename="doc.txt")
        )

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == PDF_UNSUPPORTED_CT
    # Reader must not have been called for an unsupported content_type.
    assert reader.calls == []
    assert extractor.calls == []


async def test_pdf_unsupported_content_type_none_fails_closed() -> None:
    reader = FakeStorageObjectReader(data=b"")
    extractor = FakePdfTextExtractor(pages=["x"])
    provider = PdfArtifactExtractionProvider(reader=reader, extractor=extractor)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(_make_context(content_type=None))

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == PDF_UNSUPPORTED_CT


async def test_pdf_byte_size_mismatch_fails_closed() -> None:
    raw_bytes = b"%PDF-1.4 some bytes"
    reader = FakeStorageObjectReader(data=raw_bytes)
    extractor = FakePdfTextExtractor(pages=["x"])
    provider = PdfArtifactExtractionProvider(reader=reader, extractor=extractor)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(
            _make_context(byte_size=len(raw_bytes) + 100, content_sha256=None)
        )

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_BYTE_SIZE_MISMATCH
    # Extractor must not be called when byte_size validation fails.
    assert extractor.calls == []


async def test_pdf_sha256_mismatch_fails_closed() -> None:
    raw_bytes = b"%PDF-1.4 some bytes"
    reader = FakeStorageObjectReader(data=raw_bytes)
    extractor = FakePdfTextExtractor(pages=["x"])
    provider = PdfArtifactExtractionProvider(reader=reader, extractor=extractor)

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


async def test_pdf_empty_extracted_text_fails_closed() -> None:
    """All pages blank → joined text is empty → ``pdf_no_extractable_text``."""
    raw_bytes = b"%PDF-1.4 scanned pdf"
    reader = FakeStorageObjectReader(data=raw_bytes)
    extractor = FakePdfTextExtractor(pages=["", "   ", ""])
    provider = PdfArtifactExtractionProvider(reader=reader, extractor=extractor)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(_make_context(byte_size=None, content_sha256=None))

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_PDF_NO_EXTRACTABLE_TEXT


async def test_pdf_storage_read_error_wrapped_as_retryable() -> None:
    """A non-ArtifactExtractionError raised by the reader is wrapped as
    ``storage_read_error`` and marked retryable."""

    class _Boom(Exception):
        pass

    reader = FakeStorageObjectReader(data=b"", error=_Boom("network down"))
    extractor = FakePdfTextExtractor(pages=["x"])
    provider = PdfArtifactExtractionProvider(reader=reader, extractor=extractor)

    from app.services.reader_orchestration.text_artifact_extraction_provider import (
        FAILURE_CODE_STORAGE_READ_ERROR,
    )

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(_make_context(byte_size=None, content_sha256=None))

    assert exc_info.value.retryable is True
    assert exc_info.value.failure_code == FAILURE_CODE_STORAGE_READ_ERROR


async def test_pdf_extractor_unexpected_error_wrapped_as_pdf_extraction_error() -> None:
    """A non-ArtifactExtractionError raised by the extractor is wrapped as
    ``pdf_extraction_error`` (non-retryable)."""
    raw_bytes = b"%PDF-1.4 bytes"

    class _ExtractorBoom(Exception):
        pass

    reader = FakeStorageObjectReader(data=raw_bytes)
    extractor = FakePdfTextExtractor(pages=[], error=_ExtractorBoom("bad pdf"))
    provider = PdfArtifactExtractionProvider(reader=reader, extractor=extractor)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(_make_context(byte_size=None, content_sha256=None))

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_PDF_EXTRACTION_ERROR


# ---------------------------------------------------------------------------
# PypdfPdfTextExtractor — SDK missing path (default extractor)
# ---------------------------------------------------------------------------


def test_pypdf_extractor_raises_pdf_extractor_unavailable_when_sdk_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``pypdf`` cannot be imported, the default extractor raises
    ``pdf_extractor_unavailable`` (retryable=False)."""

    # Ensure pypdf is not cached in sys.modules even if installed locally.
    monkeypatch.delitem(sys.modules, "pypdf", raising=False)

    original_import = builtins.__import__

    def _block_pypdf(name: str, *args: object, **kwargs: object) -> object:
        if name == "pypdf":
            raise ImportError("simulated: pypdf not installed")
        return original_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", _block_pypdf)

    extractor = PypdfPdfTextExtractor()
    with pytest.raises(ArtifactExtractionError) as exc_info:
        extractor.extract_text(b"%PDF-1.4 anything")

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_PDF_EXTRACTOR_UNAVAILABLE


async def test_pdf_provider_default_extractor_propagates_pdf_extractor_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the default extractor is used (no extractor= kwarg) and pypdf is
    missing, the provider raises ``pdf_extractor_unavailable`` on a real PDF
    job — proving the router can be constructed without pypdf installed."""
    raw_bytes = b"%PDF-1.4 bytes"
    reader = FakeStorageObjectReader(data=raw_bytes)

    monkeypatch.delitem(sys.modules, "pypdf", raising=False)
    original_import = builtins.__import__

    def _block_pypdf(name: str, *args: object, **kwargs: object) -> object:
        if name == "pypdf":
            raise ImportError("simulated: pypdf not installed")
        return original_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", _block_pypdf)

    # Default extractor (PypdfPdfTextExtractor) is constructed automatically.
    provider = PdfArtifactExtractionProvider(reader=reader)

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(_make_context(byte_size=None, content_sha256=None))

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_PDF_EXTRACTOR_UNAVAILABLE


# ---------------------------------------------------------------------------
# ArtifactExtractionProviderRouter
# ---------------------------------------------------------------------------


async def test_router_routes_text_plain_to_text_provider() -> None:
    """``text/plain`` is delegated to the text provider."""
    raw_bytes = b"Hello text."
    reader = FakeStorageObjectReader(data=raw_bytes)
    text_provider = TextArtifactExtractionProvider(reader=reader)
    pdf_provider = PdfArtifactExtractionProvider(
        reader=reader,
        extractor=FakePdfTextExtractor(pages=["should not be called"]),
    )
    router = ArtifactExtractionProviderRouter(
        text_provider=text_provider,
        pdf_provider=pdf_provider,
        ocr_provider=OcrArtifactExtractionProvider(reader=reader),
    )

    result = await router.extract(
        _make_context(content_type="text/plain", source_filename="notes.txt")
    )

    assert result.extractor_name == TEXT_EXTRACTOR_NAME
    assert result.extracted_text == "Hello text."


async def test_router_routes_text_markdown_to_text_provider() -> None:
    md = b"# Title\n\nbody"
    reader = FakeStorageObjectReader(data=md)
    text_provider = TextArtifactExtractionProvider(reader=reader)
    pdf_provider = PdfArtifactExtractionProvider(
        reader=reader,
        extractor=FakePdfTextExtractor(pages=["should not be called"]),
    )
    router = ArtifactExtractionProviderRouter(
        text_provider=text_provider,
        pdf_provider=pdf_provider,
        ocr_provider=OcrArtifactExtractionProvider(reader=reader),
    )

    result = await router.extract(
        _make_context(content_type="text/markdown", source_filename="readme.md")
    )

    assert result.extractor_name == TEXT_EXTRACTOR_NAME
    assert result.extracted_text == md.decode("utf-8")


async def test_router_routes_text_x_markdown_to_text_provider() -> None:
    md = b"## Section"
    reader = FakeStorageObjectReader(data=md)
    text_provider = TextArtifactExtractionProvider(reader=reader)
    pdf_provider = PdfArtifactExtractionProvider(
        reader=reader,
        extractor=FakePdfTextExtractor(pages=["should not be called"]),
    )
    router = ArtifactExtractionProviderRouter(
        text_provider=text_provider,
        pdf_provider=pdf_provider,
        ocr_provider=OcrArtifactExtractionProvider(reader=reader),
    )

    result = await router.extract(
        _make_context(
            content_type="text/x-markdown", source_filename="page.x.md"
        )
    )

    assert result.extractor_name == TEXT_EXTRACTOR_NAME


async def test_router_routes_octet_stream_with_txt_to_text_provider() -> None:
    raw = b"octet-stream text"
    reader = FakeStorageObjectReader(data=raw)
    text_provider = TextArtifactExtractionProvider(reader=reader)
    pdf_provider = PdfArtifactExtractionProvider(
        reader=reader,
        extractor=FakePdfTextExtractor(pages=["should not be called"]),
    )
    router = ArtifactExtractionProviderRouter(
        text_provider=text_provider,
        pdf_provider=pdf_provider,
        ocr_provider=OcrArtifactExtractionProvider(reader=reader),
    )

    result = await router.extract(
        _make_context(
            content_type="application/octet-stream",
            source_filename="unknown.txt",
        )
    )

    assert result.extractor_name == TEXT_EXTRACTOR_NAME
    assert result.warnings is not None
    assert any("application/octet-stream" in w for w in result.warnings)


async def test_router_routes_application_pdf_to_pdf_provider() -> None:
    """``application/pdf`` is delegated to the PDF provider."""
    raw_bytes = b"%PDF-1.4 bytes"
    reader = FakeStorageObjectReader(data=raw_bytes)
    extractor = FakePdfTextExtractor(pages=["PDF page text content."])
    text_provider = TextArtifactExtractionProvider(reader=reader)
    pdf_provider = PdfArtifactExtractionProvider(reader=reader, extractor=extractor)
    router = ArtifactExtractionProviderRouter(
        text_provider=text_provider,
        pdf_provider=pdf_provider,
        ocr_provider=OcrArtifactExtractionProvider(reader=reader),
    )

    result = await router.extract(
        _make_context(content_type="application/pdf", source_filename="doc.pdf")
    )

    assert result.extractor_name == EXTRACTOR_NAME
    assert result.extracted_text == "PDF page text content."


async def test_router_image_content_type_fails_closed_ocr_unconfigured() -> None:
    """``image/png`` (and any ``image/*``) delegates to the OCR provider,
    which uses the default :class:`UnconfiguredOcrTextExtractor` and fails
    closed with ``ocr_provider_unconfigured``.

    Note: the OCR provider downloads bytes via the reader before calling
    the extractor, so ``reader.calls`` is non-empty. The unconfigured
    extractor raises before any real OCR happens.
    """
    reader = FakeStorageObjectReader(data=b"")
    text_provider = TextArtifactExtractionProvider(reader=reader)
    pdf_provider = PdfArtifactExtractionProvider(
        reader=reader,
        extractor=FakePdfTextExtractor(pages=["should not be called"]),
    )
    router = ArtifactExtractionProviderRouter(
        text_provider=text_provider,
        pdf_provider=pdf_provider,
        ocr_provider=OcrArtifactExtractionProvider(reader=reader),
    )

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await router.extract(
            _make_context(
                content_type="image/png", source_filename="scan.png"
            )
        )

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED
    # Reader IS called (OCR provider downloads bytes before extractor),
    # but the PDF extractor must never be called.
    assert len(reader.calls) == 1


async def test_router_image_jpeg_also_fails_closed() -> None:
    """Any ``image/*`` subtype delegates to the OCR provider — verify with
    ``image/jpeg`` using the default UnconfiguredOcrTextExtractor."""
    reader = FakeStorageObjectReader(data=b"")
    router = ArtifactExtractionProviderRouter(
        text_provider=TextArtifactExtractionProvider(reader=reader),
        pdf_provider=PdfArtifactExtractionProvider(
            reader=reader,
            extractor=FakePdfTextExtractor(pages=["x"]),
        ),
        ocr_provider=OcrArtifactExtractionProvider(reader=reader),
    )

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await router.extract(
            _make_context(
                content_type="image/jpeg", source_filename="photo.jpg"
            )
        )

    assert exc_info.value.failure_code == FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED


async def test_router_unknown_content_type_fails_closed() -> None:
    """An unknown content_type fails closed with
    ``unsupported_artifact_content_type``."""
    reader = FakeStorageObjectReader(data=b"")
    router = ArtifactExtractionProviderRouter(
        text_provider=TextArtifactExtractionProvider(reader=reader),
        pdf_provider=PdfArtifactExtractionProvider(
            reader=reader,
            extractor=FakePdfTextExtractor(pages=["x"]),
        ),
        ocr_provider=OcrArtifactExtractionProvider(reader=reader),
    )

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await router.extract(
            _make_context(
                content_type="application/vnd.ms-excel",
                source_filename="sheet.xls",
            )
        )

    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_UNSUPPORTED_ARTIFACT_CONTENT_TYPE
    assert reader.calls == []


async def test_router_octet_stream_without_txt_md_extension_fails_closed() -> None:
    """``application/octet-stream`` without a ``.txt``/``.md`` extension falls
    through to the unknown branch."""
    reader = FakeStorageObjectReader(data=b"")
    router = ArtifactExtractionProviderRouter(
        text_provider=TextArtifactExtractionProvider(reader=reader),
        pdf_provider=PdfArtifactExtractionProvider(
            reader=reader,
            extractor=FakePdfTextExtractor(pages=["x"]),
        ),
        ocr_provider=OcrArtifactExtractionProvider(reader=reader),
    )

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await router.extract(
            _make_context(
                content_type="application/octet-stream",
                source_filename="binary.dat",
            )
        )

    assert exc_info.value.failure_code == FAILURE_CODE_UNSUPPORTED_ARTIFACT_CONTENT_TYPE


async def test_router_propagates_provider_error_unchanged() -> None:
    """If the chosen provider raises an :class:`ArtifactExtractionError`, the
    router propagates it without re-wrapping (preserves failure_code)."""
    raw_bytes = b"%PDF-1.4"
    reader = FakeStorageObjectReader(data=raw_bytes)
    # Empty pages → triggers pdf_no_extractable_text inside the pdf provider.
    pdf_provider = PdfArtifactExtractionProvider(
        reader=reader,
        extractor=FakePdfTextExtractor(pages=["", ""]),
    )
    router = ArtifactExtractionProviderRouter(
        text_provider=TextArtifactExtractionProvider(reader=reader),
        pdf_provider=pdf_provider,
        ocr_provider=OcrArtifactExtractionProvider(reader=reader),
    )

    with pytest.raises(ArtifactExtractionError) as exc_info:
        await router.extract(
            _make_context(content_type="application/pdf", source_filename="x.pdf")
        )

    # Error must be the original PDF error, not a router-level wrap.
    assert exc_info.value.failure_code == FAILURE_CODE_PDF_NO_EXTRACTABLE_TEXT


# ---------------------------------------------------------------------------
# build_default_extraction_provider_router
# ---------------------------------------------------------------------------


def test_build_default_router_constructs_text_and_pdf_providers() -> None:
    """The default factory builds a router holding text + PDF + OCR providers.

    The router is constructable without ``pypdf`` installed and without OCR
    config — PDF fails closed on the first PDF job, and image jobs fail
    closed on the first extract call via :class:`UnconfiguredOcrTextExtractor`.
    """
    reader = FakeStorageObjectReader(data=b"")
    router = build_default_extraction_provider_router(reader=reader)

    assert isinstance(router, ArtifactExtractionProviderRouter)
    # Internal providers are wired (text + pdf + ocr). We check types via
    # the private attributes; this is acceptable for a structural smoke test.
    assert isinstance(router._text_provider, TextArtifactExtractionProvider)
    assert isinstance(router._pdf_provider, PdfArtifactExtractionProvider)
    assert isinstance(router._ocr_provider, OcrArtifactExtractionProvider)


# ---------------------------------------------------------------------------
# Worker entry regression: router injection
# ---------------------------------------------------------------------------


def test_build_pipeline_service_injects_router_when_reader_present() -> None:
    """When a storage reader is supplied, ``build_pipeline_service`` wires a
    :class:`ArtifactExtractionProviderRouter` into the extraction worker
    (NOT ``UnconfiguredArtifactExtractionProvider``)."""
    from scripts.run_reader_artifact_pipeline_worker import build_pipeline_service
    from app.config.settings import Settings

    reader = FakeStorageObjectReader(data=b"")
    service = build_pipeline_service(
        settings=Settings(),
        pool=object(),  # service stores it without using it in this test
        storage_reader=reader,  # type: ignore[arg-type]
    )

    # The extraction worker must exist and hold a router provider.
    extraction_worker = service._extraction_worker
    assert extraction_worker is not None
    provider = extraction_worker._provider
    assert isinstance(provider, ArtifactExtractionProviderRouter)


def test_build_pipeline_service_fail_closed_without_reader() -> None:
    """Without a storage reader, ``build_pipeline_service`` falls back to
    ``UnconfiguredArtifactExtractionProvider`` (fail-closed)."""
    from scripts.run_reader_artifact_pipeline_worker import build_pipeline_service
    from app.config.settings import Settings
    from app.services.reader_orchestration.artifact_extraction_worker import (
        UnconfiguredArtifactExtractionProvider,
    )

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


async def test_build_pipeline_service_router_text_path_still_works() -> None:
    """End-to-end through the router: a text upload extraction via the
    pipeline service's router provider still produces extracted text.

    This is the regression check that wiring the router did not break the
    text path that previously went through ``TextArtifactExtractionProvider``
    directly.
    """
    from scripts.run_reader_artifact_pipeline_worker import build_pipeline_service
    from app.config.settings import Settings

    text_bytes = b"Hello through the router."
    reader = FakeStorageObjectReader(data=text_bytes)
    service = build_pipeline_service(
        settings=Settings(),
        pool=object(),
        storage_reader=reader,  # type: ignore[arg-type]
    )

    # Reach into the service to call the provider directly — we are NOT
    # driving the full worker (which needs a DB pool). The point is to
    # prove the router delegates text content_type to the text provider
    # and produces a successful extraction result.
    provider = service._extraction_worker._provider
    assert isinstance(provider, ArtifactExtractionProviderRouter)

    result = await provider.extract(
        _make_context(content_type="text/plain", source_filename="hello.txt")
    )

    assert result.extracted_text == "Hello through the router."
    assert result.extractor_name == TEXT_EXTRACTOR_NAME
