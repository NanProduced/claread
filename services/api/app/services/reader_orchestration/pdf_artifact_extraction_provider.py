"""Deterministic PDF text extraction provider.

Wraps an injectable :class:`StorageObjectReader` to download object bytes and
a :class:`PdfTextExtractor` to extract copyable text from PDF documents.

Supports ``application/pdf`` only. Scanned/image-only PDFs (no extractable
text) are rejected with ``pdf_no_extractable_text`` — OCR is explicitly out
of scope for this provider.

Validation rules (all fail-terminal, non-retryable):

- **Content type**: must be ``application/pdf``.
- **byte_size**: if the artifact metadata carries ``byte_size``, the
  downloaded bytes must match exactly.
- **content_sha256**: if the artifact metadata carries ``content_sha256``,
  the downloaded bytes must hash to the same value.
- **SDK availability**: if ``pypdf`` is not installed,
  ``pdf_extractor_unavailable`` is raised.
- **Empty text**: if the PDF has no extractable text (scanned/image-only),
  ``pdf_no_extractable_text`` is raised.

The returned :class:`ArtifactExtractionResult` carries quality metadata
(content_type, page_count, extractor_name, has_extractable_text, byte_size,
content_sha256_verified) and non-fatal warnings (empty_pages,
low_text_density).
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from typing import Any, Protocol

from .artifact_extraction_worker import (
    ArtifactExtractionError,
    ArtifactExtractionJobContext,
    ArtifactExtractionResult,
)
from .text_artifact_extraction_provider import (
    FAILURE_CODE_BYTE_SIZE_MISMATCH,
    FAILURE_CODE_SHA256_MISMATCH,
    FAILURE_CODE_STORAGE_READ_ERROR,
    StorageObjectReadResult,
    StorageObjectReader,
)

EXTRACTOR_NAME = "deterministic_pdf_text_extractor_v1"

SUPPORTED_CONTENT_TYPE = "application/pdf"

FAILURE_CODE_PDF_EXTRACTOR_UNAVAILABLE = "pdf_extractor_unavailable"
FAILURE_CODE_PDF_NO_EXTRACTABLE_TEXT = "pdf_no_extractable_text"
FAILURE_CODE_PDF_EXTRACTION_ERROR = "pdf_extraction_error"
FAILURE_CODE_UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"

# Heuristic: if extracted text length is less than this fraction of byte_size,
# flag low_text_density (deterministic signal, not a hard failure).
_LOW_TEXT_DENSITY_RATIO = 0.05


@dataclass(frozen=True, slots=True)
class PdfTextExtractionResult:
    """Result of extracting text from a PDF document.

    ``pages`` is an ordered list of per-page text (one entry per page).
    Entries may be empty strings for pages with no copyable text.
    """

    pages: list[str]
    extractor_name: str


class PdfTextExtractor(Protocol):
    """Extracts copyable text from PDF bytes (no OCR)."""

    def extract_text(self, data: bytes) -> PdfTextExtractionResult: ...


class PypdfPdfTextExtractor:
    """Default PDF text extractor using ``pypdf`` (lazy import).

    Fail-closed: if ``pypdf`` is not installed, :meth:`extract_text` raises
    :class:`ArtifactExtractionError` with ``retryable=False`` and
    ``failure_code=pdf_extractor_unavailable``.
    """

    def extract_text(self, data: bytes) -> PdfTextExtractionResult:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ArtifactExtractionError(
                "pypdf is not installed; install 'pypdf' to enable "
                "PDF text extraction",
                retryable=False,
                failure_class="extraction",
                failure_code=FAILURE_CODE_PDF_EXTRACTOR_UNAVAILABLE,
            ) from exc

        try:
            reader = PdfReader(io.BytesIO(data))
            pages: list[str] = []
            for page in reader.pages:
                text = page.extract_text() or ""
                pages.append(text)
        except ArtifactExtractionError:
            raise
        except Exception as exc:
            raise ArtifactExtractionError(
                f"pypdf failed to extract text: {exc}",
                retryable=False,
                failure_class="extraction",
                failure_code=FAILURE_CODE_PDF_EXTRACTION_ERROR,
            ) from exc

        return PdfTextExtractionResult(
            pages=pages,
            extractor_name=EXTRACTOR_NAME,
        )


class PdfArtifactExtractionProvider:
    """Deterministic PDF text extraction provider.

    Downloads object bytes via an injected :class:`StorageObjectReader`,
    validates content_type / sha256 / byte_size, and delegates text
    extraction to an injected :class:`PdfTextExtractor`. Returns an
    :class:`ArtifactExtractionResult` with per-page text joined by blank
    lines.
    """

    def __init__(
        self,
        *,
        reader: StorageObjectReader,
        extractor: PdfTextExtractor | None = None,
    ) -> None:
        self._reader = reader
        self._extractor = extractor or PypdfPdfTextExtractor()

    async def extract(
        self,
        context: ArtifactExtractionJobContext,
    ) -> ArtifactExtractionResult:
        content_type = context.content_type
        source_filename = context.source_filename or ""

        # 1. Content type gate
        ct = (content_type or "").strip().lower().split(";")[0].strip()
        if ct != SUPPORTED_CONTENT_TYPE:
            raise ArtifactExtractionError(
                f"unsupported content_type {content_type!r} for "
                f"PDF extraction (source_filename={source_filename!r})",
                retryable=False,
                failure_class="extraction",
                failure_code=FAILURE_CODE_UNSUPPORTED_CONTENT_TYPE,
            )

        # 2. Download bytes via injected reader
        try:
            read_result = await self._reader.read_object(
                bucket=context.bucket,
                endpoint=context.endpoint,
                object_key=context.object_key,
            )
        except ArtifactExtractionError:
            raise
        except Exception as exc:
            raise ArtifactExtractionError(
                f"storage read failed for object_key={context.object_key!r}: {exc}",
                retryable=True,
                failure_class="extraction",
                failure_code=FAILURE_CODE_STORAGE_READ_ERROR,
            ) from exc

        raw_bytes = read_result.data

        # 3. byte_size validation
        if context.byte_size is not None:
            actual_size = len(raw_bytes)
            if actual_size != context.byte_size:
                raise ArtifactExtractionError(
                    f"byte_size mismatch: expected {context.byte_size}, "
                    f"got {actual_size}",
                    retryable=False,
                    failure_class="extraction",
                    failure_code=FAILURE_CODE_BYTE_SIZE_MISMATCH,
                )

        # 4. content_sha256 validation
        content_sha256_verified = False
        if context.content_sha256 is not None:
            actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
            if actual_sha256 != context.content_sha256:
                raise ArtifactExtractionError(
                    f"sha256 mismatch: expected {context.content_sha256}, "
                    f"got {actual_sha256}",
                    retryable=False,
                    failure_class="extraction",
                    failure_code=FAILURE_CODE_SHA256_MISMATCH,
                )
            content_sha256_verified = True

        # 5. Extract text via injected extractor
        try:
            extraction = self._extractor.extract_text(raw_bytes)
        except ArtifactExtractionError:
            raise
        except Exception as exc:
            raise ArtifactExtractionError(
                f"PDF text extraction failed: {exc}",
                retryable=False,
                failure_class="extraction",
                failure_code=FAILURE_CODE_PDF_EXTRACTION_ERROR,
            ) from exc

        # 6. Join pages and check non-empty
        pages = extraction.pages
        page_count = len(pages)
        extracted_text = "\n\n".join(pages).strip()

        if not extracted_text:
            raise ArtifactExtractionError(
                "PDF has no extractable text (scanned/image-only); "
                "OCR is not configured",
                retryable=False,
                failure_class="extraction",
                failure_code=FAILURE_CODE_PDF_NO_EXTRACTABLE_TEXT,
            )

        # 7. Build quality + warnings
        warnings: list[str] = []
        empty_page_indices = [
            i for i, page_text in enumerate(pages) if not page_text.strip()
        ]
        if empty_page_indices:
            warnings.append(
                f"empty_pages: {len(empty_page_indices)} of {page_count} pages "
                f"have no extractable text (indices: {empty_page_indices})"
            )

        if raw_bytes and len(extracted_text) / len(raw_bytes) < _LOW_TEXT_DENSITY_RATIO:
            warnings.append(
                f"low_text_density: extracted {len(extracted_text)} chars from "
                f"{len(raw_bytes)} bytes ({len(extracted_text) / len(raw_bytes):.1%})"
            )

        quality: dict[str, Any] = {
            "content_type": content_type,
            "page_count": page_count,
            "extractor_name": extraction.extractor_name,
            "has_extractable_text": True,
            "byte_size": len(raw_bytes),
            "content_sha256_verified": content_sha256_verified,
            "source_filename": source_filename,
        }

        return ArtifactExtractionResult(
            extracted_text=extracted_text,
            extractor_name=extraction.extractor_name,
            quality=quality,
            warnings=warnings if warnings else None,
        )
