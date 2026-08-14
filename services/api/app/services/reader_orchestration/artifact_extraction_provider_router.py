"""Artifact extraction provider router.

Routes :class:`ArtifactExtractionJobContext` to the appropriate extraction
provider based on ``content_type`` / ``source_filename``:

- ``text/plain``, ``text/markdown``, ``text/x-markdown``,
  ``application/octet-stream`` + ``.txt``/``.md`` →
  :class:`TextArtifactExtractionProvider`
- ``application/pdf`` → :class:`PdfArtifactExtractionProvider`
- ``image/*`` → :class:`OcrArtifactExtractionProvider` (which itself
  fails closed with ``ocr_provider_unconfigured`` when no real OCR
  extractor is wired, or ``unsupported_ocr_image_type`` for unsupported
  image subtypes like ``image/gif``)
- anything else → fail-closed ``unsupported_artifact_content_type``

The router does NOT read or write the database — it only selects a provider
and delegates ``extract``. All provider errors (including
:class:`ArtifactExtractionError`) propagate unchanged.

The router is constructable without any optional SDK installed: ``pypdf``
is lazy-imported inside :class:`PypdfPdfTextExtractor`, and the default
:class:`UnconfiguredOcrTextExtractor` never touches the network.
"""

from __future__ import annotations

from .artifact_extraction_worker import (
    ArtifactExtractionError,
    ArtifactExtractionJobContext,
    ArtifactExtractionResult,
)
from .ocr_artifact_extraction_provider import (
    DEFAULT_MIN_LAYOUT_CONFIDENCE,
    DEFAULT_MIN_TEXT_CONFIDENCE,
    OcrArtifactExtractionProvider,
    OcrTextExtractor,
)
from .pdf_artifact_extraction_provider import (
    PdfArtifactExtractionProvider,
)
from .text_artifact_extraction_provider import (
    OCTET_STREAM_ALLOWED_EXTENSIONS,
    SUPPORTED_CONTENT_TYPES,
    StorageObjectReader,
    TextArtifactExtractionProvider,
)

FAILURE_CODE_UNSUPPORTED_ARTIFACT_CONTENT_TYPE = "unsupported_artifact_content_type"


class ArtifactExtractionProviderRouter:
    """Content-type-based router for artifact extraction providers.

    Holds a text provider, a PDF provider, and an OCR provider, and
    delegates to the correct one based on the job context's
    ``content_type`` / ``source_filename``. Unknown content types fail
    closed with ``unsupported_artifact_content_type``.

    Image content types are delegated to the OCR provider — the OCR
    provider is responsible for subtype validation
    (``unsupported_ocr_image_type``) and configuration fail-closed
    (``ocr_provider_unconfigured``).
    """

    def __init__(
        self,
        *,
        text_provider: TextArtifactExtractionProvider,
        pdf_provider: PdfArtifactExtractionProvider,
        ocr_provider: OcrArtifactExtractionProvider,
    ) -> None:
        self._text_provider = text_provider
        self._pdf_provider = pdf_provider
        self._ocr_provider = ocr_provider

    async def extract(
        self,
        context: ArtifactExtractionJobContext,
    ) -> ArtifactExtractionResult:
        content_type = context.content_type
        source_filename = context.source_filename or ""
        ct = (content_type or "").strip().lower().split(";")[0].strip()

        # Text / markdown path
        if ct in SUPPORTED_CONTENT_TYPES:
            return await self._text_provider.extract(context)

        # Octet-stream allowed only with .txt/.md extension
        if ct == "application/octet-stream":
            lower_name = source_filename.lower()
            if any(lower_name.endswith(ext) for ext in OCTET_STREAM_ALLOWED_EXTENSIONS):
                return await self._text_provider.extract(context)

        # PDF path
        if ct == "application/pdf":
            return await self._pdf_provider.extract(context)

        # Image path — delegate to OCR provider (which handles supported
        # subtype validation + unconfigured fail-closed).
        if ct.startswith("image/"):
            return await self._ocr_provider.extract(context)

        # Unknown content type
        raise ArtifactExtractionError(
            f"unsupported artifact content_type {content_type!r} "
            f"(source_filename={source_filename!r})",
            retryable=False,
            failure_class="extraction",
            failure_code=FAILURE_CODE_UNSUPPORTED_ARTIFACT_CONTENT_TYPE,
        )


def build_default_extraction_provider_router(
    *,
    reader: StorageObjectReader,
    ocr_extractor: OcrTextExtractor | None = None,
    ocr_min_text_confidence: float = DEFAULT_MIN_TEXT_CONFIDENCE,
    ocr_min_layout_confidence: float = DEFAULT_MIN_LAYOUT_CONFIDENCE,
) -> ArtifactExtractionProviderRouter:
    """Build a router with default text + PDF + OCR providers.

    The PDF provider uses :class:`PypdfPdfTextExtractor` which lazily imports
    ``pypdf``. If ``pypdf`` is not installed, the router is still
    constructable — PDF extraction fails closed on the first PDF job with
    ``pdf_extractor_unavailable``.

    The OCR provider uses the supplied ``ocr_extractor`` (or
    :class:`UnconfiguredOcrTextExtractor` by default). When unconfigured,
    image jobs fail closed on the first extract call with
    ``ocr_provider_unconfigured``.

    Text and PDF paths are NOT affected by the OCR extractor — they work
    regardless of OCR configuration.
    """
    text_provider = TextArtifactExtractionProvider(reader=reader)
    pdf_provider = PdfArtifactExtractionProvider(reader=reader)
    ocr_provider = OcrArtifactExtractionProvider(
        reader=reader,
        extractor=ocr_extractor,
        min_text_confidence=ocr_min_text_confidence,
        min_layout_confidence=ocr_min_layout_confidence,
    )
    return ArtifactExtractionProviderRouter(
        text_provider=text_provider,
        pdf_provider=pdf_provider,
        ocr_provider=ocr_provider,
    )
