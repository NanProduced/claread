"""Artifact extraction provider router (D6-I3S).

Routes :class:`ArtifactExtractionJobContext` to the appropriate extraction
provider based on ``content_type`` / ``source_filename``:

- ``text/plain``, ``text/markdown``, ``text/x-markdown``,
  ``application/octet-stream`` + ``.txt``/``.md`` →
  :class:`TextArtifactExtractionProvider`
- ``application/pdf`` → :class:`PdfArtifactExtractionProvider`
- ``image/*`` → fail-closed ``ocr_provider_unconfigured`` (OCR not yet
  implemented; this is the interface placeholder)
- anything else → fail-closed ``unsupported_artifact_content_type``

The router does NOT read or write the database — it only selects a provider
and delegates ``extract``. All provider errors (including
:class:`ArtifactExtractionError`) propagate unchanged.
"""

from __future__ import annotations

from .artifact_extraction_worker import (
    ArtifactExtractionError,
    ArtifactExtractionJobContext,
    ArtifactExtractionResult,
)
from .pdf_artifact_extraction_provider import (
    PdfArtifactExtractionProvider,
    PypdfPdfTextExtractor,
)
from .text_artifact_extraction_provider import (
    OCTET_STREAM_ALLOWED_EXTENSIONS,
    SUPPORTED_CONTENT_TYPES,
    StorageObjectReader,
    TextArtifactExtractionProvider,
)

FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED = "ocr_provider_unconfigured"
FAILURE_CODE_UNSUPPORTED_ARTIFACT_CONTENT_TYPE = "unsupported_artifact_content_type"


class ArtifactExtractionProviderRouter:
    """Content-type-based router for artifact extraction providers.

    Holds a text provider and a PDF provider, and delegates to the correct
    one based on the job context's ``content_type`` / ``source_filename``.
    Image and unknown content types fail closed with clear failure codes.
    """

    def __init__(
        self,
        *,
        text_provider: TextArtifactExtractionProvider,
        pdf_provider: PdfArtifactExtractionProvider,
    ) -> None:
        self._text_provider = text_provider
        self._pdf_provider = pdf_provider

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

        # Image path — OCR not configured (interface placeholder)
        if ct.startswith("image/"):
            raise ArtifactExtractionError(
                f"image content_type {content_type!r} requires OCR which is "
                f"not configured",
                retryable=False,
                failure_class="extraction",
                failure_code=FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED,
            )

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
) -> ArtifactExtractionProviderRouter:
    """Build a router with default text + PDF providers.

    The PDF provider uses :class:`PypdfPdfTextExtractor` which lazily imports
    ``pypdf``. If ``pypdf`` is not installed, the router is still
    constructable — PDF extraction fails closed on the first PDF job with
    ``pdf_extractor_unavailable``.
    """
    text_provider = TextArtifactExtractionProvider(reader=reader)
    pdf_provider = PdfArtifactExtractionProvider(reader=reader)
    return ArtifactExtractionProviderRouter(
        text_provider=text_provider,
        pdf_provider=pdf_provider,
    )
