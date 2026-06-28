"""OCR-based image artifact extraction provider (D6-I3T).

Wraps an injectable :class:`StorageObjectReader` to download image bytes and
a :class:`OcrTextExtractor` to recognise text in the image. Supports
``image/png``, ``image/jpeg``, ``image/webp``, ``image/tiff``. Other image
subtypes fail closed with ``unsupported_ocr_image_type``.

The default :class:`UnconfiguredOcrTextExtractor` always raises
``ocr_provider_unconfigured`` (terminal) — real OCR adapters (Qwen /
DashScope) are env/config gated and deferred to a later round. This means
the router can always wire an OCR provider; image jobs fail closed when
no real extractor is configured, instead of falling through to the
router-level "unknown content type" branch.

Validation rules (all fail-terminal, non-retryable unless noted):

- **Content type**: must be one of the supported image subtypes above.
  Non-image content types are also rejected here (the OCR provider is
  only reached for ``image/*`` via the router, but defends itself).
- **byte_size**: if the artifact metadata carries ``byte_size``, the
  downloaded bytes must match exactly.
- **content_sha256**: if the artifact metadata carries ``content_sha256``,
  the downloaded bytes must hash to the same value.
- **Extractor availability**: if no real OCR extractor is configured,
  ``ocr_provider_unconfigured`` is raised (terminal).
- **Empty text**: if OCR returns no text, ``ocr_no_text_detected`` is
  raised (terminal).
- **Retryable extractor errors**: if the extractor raises
  :class:`ArtifactExtractionError` with ``retryable=True`` (e.g. network
  timeout), the provider preserves the retryable flag.

Confidence signals (non-fatal):

- ``text_confidence`` / ``layout_confidence`` are surfaced in ``quality``.
- If ``text_confidence < min_text_confidence`` → ``ocr_low_confidence``
  warning.
- If ``layout_confidence < min_layout_confidence`` →
  ``layout_order_uncertain`` warning.
- Extractor-level warnings (e.g. ``empty_regions``) are preserved
  verbatim.

Low-confidence results are NOT rejected here — the provider still returns
the extracted text. Downstream materialization is responsible for routing
``ocr_text`` inputs to ``candidate_document_required`` instead of
``stable_document_ready``.
"""

from __future__ import annotations

import hashlib
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

EXTRACTOR_NAME_UNCONFIGURED = "unconfigured_ocr_text_extractor"
EXTRACTOR_NAME_QWEN = "qwen_ocr_text_extractor_v1"

SUPPORTED_CONTENT_TYPES: frozenset[str] = frozenset({
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/tiff",
})

FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED = "ocr_provider_unconfigured"
FAILURE_CODE_OCR_NO_TEXT_DETECTED = "ocr_no_text_detected"
FAILURE_CODE_OCR_EXTRACTION_ERROR = "ocr_extraction_error"
FAILURE_CODE_UNSUPPORTED_OCR_IMAGE_TYPE = "unsupported_ocr_image_type"

DEFAULT_MIN_TEXT_CONFIDENCE = 0.75
DEFAULT_MIN_LAYOUT_CONFIDENCE = 0.65


@dataclass(frozen=True, slots=True)
class OcrTextExtractionResult:
    """Result of running OCR on image bytes.

    ``extracted_text`` is the recognised text in natural reading order.
    ``text_confidence`` / ``layout_confidence`` are optional floats in
    ``[0.0, 1.0]`` — ``None`` means the extractor did not report a score.

    ``warnings`` carries extractor-level signals (e.g. ``empty_regions``)
    that the provider preserves verbatim in the final result.
    """

    extracted_text: str
    extractor_name: str
    text_confidence: float | None = None
    layout_confidence: float | None = None
    warnings: list[str] | None = None


class OcrTextExtractor(Protocol):
    """Recognises text in image bytes via OCR."""

    def extract_text(
        self,
        data: bytes,
        *,
        content_type: str,
    ) -> OcrTextExtractionResult: ...


class UnconfiguredOcrTextExtractor:
    """Default OCR extractor that fails closed on every call.

    Used when OCR is not enabled in config or when the configured provider
    name is unknown. Raises ``ocr_provider_unconfigured`` (terminal).
    """

    def extract_text(
        self,
        data: bytes,
        *,
        content_type: str,
    ) -> OcrTextExtractionResult:
        raise ArtifactExtractionError(
            "OCR provider is not configured; set reader_ocr_provider_enabled=true "
            "and configure an OCR backend (e.g. DASHSCOPE_API_KEY for qwen)",
            retryable=False,
            failure_class="configuration",
            failure_code=FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED,
        )


class QwenOcrTextExtractor:
    """Qwen / DashScope OCR extractor stub (D6-I3T).

    This is a **stub** — the real DashScope network call is deferred to a
    later round. The stub exists so that the worker can be wired with a
    named extractor when ``reader_ocr_provider_name == "qwen"``.

    Behaviour:

    - If ``api_key`` is empty → ``ocr_provider_unconfigured`` (terminal).
    - If ``api_key`` is set → ``ocr_provider_unconfigured`` (terminal) with
      a "not yet implemented" message. This is intentional: the stub never
      makes a real network call. When the real adapter is implemented,
      only this second branch needs to be replaced.

    The stub is constructable without any optional SDK installed.
    """

    EXTRACTOR_NAME = EXTRACTOR_NAME_QWEN

    def __init__(
        self,
        *,
        api_key: str | None,
        min_text_confidence: float = DEFAULT_MIN_TEXT_CONFIDENCE,
        min_layout_confidence: float = DEFAULT_MIN_LAYOUT_CONFIDENCE,
    ) -> None:
        self._api_key = api_key
        self._min_text_confidence = min_text_confidence
        self._min_layout_confidence = min_layout_confidence

    def extract_text(
        self,
        data: bytes,
        *,
        content_type: str,
    ) -> OcrTextExtractionResult:
        if not self._api_key:
            raise ArtifactExtractionError(
                "Qwen OCR requires DASHSCOPE_API_KEY; not configured",
                retryable=False,
                failure_class="configuration",
                failure_code=FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED,
            )
        # Real DashScope call is deferred — keep the stub fail-closed so
        # no network is touched in tests or default local runs.
        raise ArtifactExtractionError(
            "Qwen OCR network call is not yet implemented (D6-I3T stub); "
            "wire a real adapter or inject a fake extractor for tests",
            retryable=False,
            failure_class="configuration",
            failure_code=FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED,
        )


class OcrArtifactExtractionProvider:
    """OCR-based image text extraction provider.

    Downloads image bytes via an injected :class:`StorageObjectReader`,
    validates content_type / sha256 / byte_size, and delegates text
    recognition to an injected :class:`OcrTextExtractor`. Returns an
    :class:`ArtifactExtractionResult` carrying the recognised text plus
    confidence signals in ``quality``.

    Confidence thresholds do NOT reject results — they only add warnings.
    Downstream materialization is responsible for gating low-confidence
    OCR text into ``candidate_document_required``.
    """

    def __init__(
        self,
        *,
        reader: StorageObjectReader,
        extractor: OcrTextExtractor | None = None,
        min_text_confidence: float = DEFAULT_MIN_TEXT_CONFIDENCE,
        min_layout_confidence: float = DEFAULT_MIN_LAYOUT_CONFIDENCE,
    ) -> None:
        self._reader = reader
        self._extractor = extractor or UnconfiguredOcrTextExtractor()
        self._min_text_confidence = min_text_confidence
        self._min_layout_confidence = min_layout_confidence

    async def extract(
        self,
        context: ArtifactExtractionJobContext,
    ) -> ArtifactExtractionResult:
        content_type = context.content_type
        source_filename = context.source_filename or ""

        # 1. Content type gate
        ct = (content_type or "").strip().lower().split(";")[0].strip()
        if ct not in SUPPORTED_CONTENT_TYPES:
            raise ArtifactExtractionError(
                f"unsupported image content_type {content_type!r} for OCR "
                f"(source_filename={source_filename!r}); supported types: "
                f"{sorted(SUPPORTED_CONTENT_TYPES)}",
                retryable=False,
                failure_class="extraction",
                failure_code=FAILURE_CODE_UNSUPPORTED_OCR_IMAGE_TYPE,
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

        # 5. Extract text via injected OCR extractor
        try:
            extraction = self._extractor.extract_text(raw_bytes, content_type=ct)
        except ArtifactExtractionError:
            # Preserve retryable flag + failure_code from the extractor
            # (e.g. ocr_provider_unconfigured, retryable network errors).
            raise
        except Exception as exc:
            raise ArtifactExtractionError(
                f"OCR extraction failed: {exc}",
                retryable=False,
                failure_class="extraction",
                failure_code=FAILURE_CODE_OCR_EXTRACTION_ERROR,
            ) from exc

        # 6. Check non-empty
        extracted_text = (extraction.extracted_text or "").strip()
        if not extracted_text:
            raise ArtifactExtractionError(
                "OCR detected no text in image (empty or non-text image)",
                retryable=False,
                failure_class="extraction",
                failure_code=FAILURE_CODE_OCR_NO_TEXT_DETECTED,
            )

        # 7. Build quality + warnings
        warnings: list[str] = list(extraction.warnings or [])

        text_conf = extraction.text_confidence
        layout_conf = extraction.layout_confidence

        if text_conf is not None and text_conf < self._min_text_confidence:
            warnings.append(
                f"ocr_low_confidence: text_confidence={text_conf:.3f} < "
                f"{self._min_text_confidence:.3f}"
            )

        if layout_conf is not None and layout_conf < self._min_layout_confidence:
            warnings.append(
                f"layout_order_uncertain: layout_confidence={layout_conf:.3f} < "
                f"{self._min_layout_confidence:.3f}"
            )

        quality: dict[str, Any] = {
            "content_type": content_type,
            "extractor_name": extraction.extractor_name,
            "text_confidence": text_conf,
            "layout_confidence": layout_conf,
            "has_text": True,
            "image_byte_size": len(raw_bytes),
            "content_sha256_verified": content_sha256_verified,
            "source_filename": source_filename,
        }

        return ArtifactExtractionResult(
            extracted_text=extracted_text,
            extractor_name=extraction.extractor_name,
            quality=quality,
            warnings=warnings if warnings else None,
        )
