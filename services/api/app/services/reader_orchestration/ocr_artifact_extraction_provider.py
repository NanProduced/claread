"""OCR-based image artifact extraction provider (D6-I3T + D6-I3U).

Wraps an injectable :class:`StorageObjectReader` to download image bytes and
a :class:`OcrTextExtractor` to recognise text in the image. Supports
``image/png``, ``image/jpeg``, ``image/webp``, ``image/tiff``. Other image
subtypes fail closed with ``unsupported_ocr_image_type``.

The default :class:`UnconfiguredOcrTextExtractor` always raises
``ocr_provider_unconfigured`` (terminal). The real
:class:`QwenOcrTextExtractor` (D6-I3U) delegates to an injectable
:class:`QwenOcrClient` (default :class:`DashScopeQwenOcrClient`) so tests
can wire a fake client without touching the network. The DashScope client
lazily imports the SDK and never logs the API key.

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

import asyncio
import base64
import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

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

logger = logging.getLogger(__name__)

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
# D6-I3U: real Qwen OCR adapter error classification
FAILURE_CODE_OCR_BACKEND_TRANSIENT = "ocr_backend_transient"
FAILURE_CODE_OCR_PERMISSION_DENIED = "ocr_permission_denied"
FAILURE_CODE_OCR_REQUEST_INVALID = "ocr_request_invalid"
FAILURE_CODE_OCR_RESPONSE_INVALID = "ocr_response_invalid"
FAILURE_CODE_OCR_SDK_UNAVAILABLE = "ocr_sdk_unavailable"

DEFAULT_MIN_TEXT_CONFIDENCE = 0.75
DEFAULT_MIN_LAYOUT_CONFIDENCE = 0.65
DEFAULT_QWEN_OCR_MODEL = "qwen3.5-ocr"
DEFAULT_QWEN_OCR_TIMEOUT_SECONDS = 60


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


# ---------------------------------------------------------------------------
# D6-I3U: Qwen OCR client protocol + DashScope implementation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QwenOcrResponse:
    """Raw response from a Qwen OCR client call, before normalisation.

    ``extracted_text`` is the recognised text in natural reading order
    (multi-region / multi-page outputs are joined by the client).

    ``text_confidence`` / ``layout_confidence`` are optional floats in
    ``[0.0, 1.0]`` — ``None`` means the model did not report a score.
    Models that do not return confidence signals leave them as ``None``;
    the provider does NOT reject ``None`` confidence.

    ``warnings`` carries model-level signals (e.g. ``layout_order_uncertain``)
    that the extractor preserves verbatim.
    """

    extracted_text: str
    text_confidence: float | None = None
    layout_confidence: float | None = None
    warnings: list[str] | None = None


class QwenOcrClientError(Exception):
    """Base error for Qwen OCR client failures.

    Subclasses set ``retryable`` and ``failure_code`` so the extractor can
    map them to :class:`ArtifactExtractionError` without inspecting the
    exception type.
    """

    retryable: bool = False
    failure_code: str = FAILURE_CODE_OCR_EXTRACTION_ERROR


class QwenOcrTransientError(QwenOcrClientError):
    """Retryable backend error (timeout, 429, 5xx, connection reset)."""

    retryable = True
    failure_code = FAILURE_CODE_OCR_BACKEND_TRANSIENT


class QwenOcrPermissionError(QwenOcrClientError):
    """Terminal permission / auth error (401, 403)."""

    retryable = False
    failure_code = FAILURE_CODE_OCR_PERMISSION_DENIED


class QwenOcrInvalidRequestError(QwenOcrClientError):
    """Terminal request-format error (400, unsupported image payload)."""

    retryable = False
    failure_code = FAILURE_CODE_OCR_REQUEST_INVALID


class QwenOcrResponseInvalidError(QwenOcrClientError):
    """Terminal response parse error (malformed JSON, missing fields)."""

    retryable = False
    failure_code = FAILURE_CODE_OCR_RESPONSE_INVALID


class QwenOcrSdkUnavailableError(QwenOcrClientError):
    """Terminal SDK-missing error (dashscope not installed)."""

    retryable = False
    failure_code = FAILURE_CODE_OCR_SDK_UNAVAILABLE


@runtime_checkable
class QwenOcrClient(Protocol):
    """Minimal Qwen OCR client protocol (sync, SDK-agnostic).

    Implementations wrap a DashScope / Qwen VL OCR call. The protocol is
    sync because :class:`OcrTextExtractor` is sync — real network calls
    block the calling thread, which is acceptable for the standalone
    artifact pipeline worker process.

    ``recognize`` must NOT include the API key in raised exceptions or
    returned data. Image bytes are passed inline (already downloaded by
    the provider); the client must not re-download from object storage.
    """

    def recognize(
        self,
        *,
        image_data: bytes,
        content_type: str,
        model: str,
        timeout_seconds: int,
    ) -> QwenOcrResponse: ...


def _build_data_url(image_data: bytes, content_type: str) -> str:
    """Build a base64 data URL from image bytes + content_type."""
    encoded = base64.b64encode(image_data).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def _classify_exception(exc: Exception) -> QwenOcrClientError:
    """Classify a generic SDK/network exception into a typed client error.

    Never includes the API key. Inspects exception type name and message
    for timeout / connection signals; falls back to transient (safer to
    retry than to terminal-fail a retryable network blip).
    """
    exc_name = type(exc).__name__.lower()
    exc_msg = str(exc).lower()

    if "timeout" in exc_name or "timeout" in exc_msg or "timed out" in exc_msg:
        return QwenOcrTransientError("dashscope request timed out")
    if "connection" in exc_name or "connection" in exc_msg:
        return QwenOcrTransientError("dashscope connection error")

    # Some DashScope exceptions carry a status_code attribute
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        if status in (401, 403):
            return QwenOcrPermissionError(f"dashscope auth failed (status={status})")
        if status == 400:
            return QwenOcrInvalidRequestError(f"dashscope invalid request (status={status})")
        if status == 429 or 500 <= status < 600:
            return QwenOcrTransientError(f"dashscope backend error (status={status})")

    # Default: treat unknown errors as transient so the worker retries.
    return QwenOcrTransientError("dashscope call failed")


def _classify_status_error(status: int, code: str) -> QwenOcrClientError:
    """Classify a non-200 response status code into a typed client error."""
    if status in (401, 403):
        return QwenOcrPermissionError(f"dashscope auth failed (status={status}, code={code})")
    if status == 400:
        return QwenOcrInvalidRequestError(f"dashscope invalid request (status={status}, code={code})")
    if status == 429 or 500 <= status < 600:
        return QwenOcrTransientError(f"dashscope backend error (status={status}, code={code})")
    # Unknown status — treat as transient to be safe.
    return QwenOcrTransientError(f"dashscope unexpected status (status={status}, code={code})")


def _parse_dashscope_response(resp: Any) -> QwenOcrResponse:
    """Parse a DashScope MultiModalConversation response into QwenOcrResponse.

    Handles response shapes from qwen-vl / qwen-ocr model families:
    - ``resp.output.choices[0].message.content`` may be a list of
      ``[{"text": "..."}]`` dicts or a plain string.
    - Multi-region / multi-page outputs are joined in model-given reading
      order with a newline separator.
    - Confidence signals are optional; missing confidence → ``None``.
    - Adds ``layout_order_uncertain`` warning when multiple text blocks
      are joined (reading order may be ambiguous).
    """
    try:
        output = getattr(resp, "output", None)
        if output is None:
            raise QwenOcrResponseInvalidError("response missing 'output' field")

        choices = getattr(output, "choices", None) or []
        if not choices:
            raise QwenOcrResponseInvalidError("response has no choices")

        choice = choices[0]
        message = getattr(choice, "message", None)
        if message is None:
            raise QwenOcrResponseInvalidError("response choice has no message")

        content = getattr(message, "content", None)
        if content is None:
            raise QwenOcrResponseInvalidError("response message has no content")

        text_parts: list[str] = []
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    text_val = item.get("text")
                    if text_val:
                        text_parts.append(str(text_val))
                elif isinstance(item, str):
                    text_parts.append(item)
        else:
            raise QwenOcrResponseInvalidError(
                f"unexpected content type: {type(content).__name__}"
            )

        extracted_text = "\n".join(text_parts).strip()

        # Confidence is optional — DashScope OCR models do not always
        # return a numeric score. None is acceptable; the provider does
        # NOT reject None confidence.
        text_confidence: float | None = None
        layout_confidence: float | None = None
        warnings: list[str] = []

        # If the model returned multiple text blocks, flag that reading
        # order may be uncertain (the model did its best, but downstream
        # materialization should treat low-confidence layouts carefully).
        if len(text_parts) > 1:
            warnings.append("layout_order_uncertain: multi-block reading order inferred")

        return QwenOcrResponse(
            extracted_text=extracted_text,
            text_confidence=text_confidence,
            layout_confidence=layout_confidence,
            warnings=warnings if warnings else None,
        )
    except QwenOcrClientError:
        raise
    except Exception as exc:
        raise QwenOcrResponseInvalidError(f"failed to parse dashscope response: {exc}") from exc


class DashScopeQwenOcrClient:
    """Real DashScope-backed Qwen OCR client (D6-I3U).

    Lazily imports the ``dashscope`` SDK on first ``recognize`` call so
    the module is importable without the SDK installed (tests use a fake
    client; the worker entry only constructs this when OCR is enabled).

    Uses the sync :meth:`dashscope.MultiModalConversation.call` API with
    a base64 data URL. The API key is passed to the SDK only — it never
    appears in logs, error messages, or the returned
    :class:`QwenOcrResponse`.
    """

    def __init__(self, *, api_key: str) -> None:
        if not api_key:
            raise ValueError("DashScopeQwenOcrClient requires a non-empty api_key")
        self._api_key = api_key

    def recognize(
        self,
        *,
        image_data: bytes,
        content_type: str,
        model: str,
        timeout_seconds: int,
    ) -> QwenOcrResponse:
        try:
            import dashscope
        except ImportError as exc:
            raise QwenOcrSdkUnavailableError(
                "dashscope SDK not installed; install with: pip install dashscope"
            ) from exc

        data_url = _build_data_url(image_data, content_type)
        messages = [
            {
                "role": "user",
                "content": [
                    {"image": data_url},
                    {
                        "text": (
                            "Extract all text from this image. Return the "
                            "recognised text in natural reading order."
                        )
                    },
                ],
            }
        ]

        try:
            resp = dashscope.MultiModalConversation.call(
                model=model,
                messages=messages,
                api_key=self._api_key,
                timeout=timeout_seconds,
                result_format="message",
            )
        except Exception as exc:
            raise _classify_exception(exc) from exc

        status = getattr(resp, "status_code", None)
        if status != 200:
            code = getattr(resp, "code", "") or ""
            raise _classify_status_error(int(status) if status is not None else 0, code)

        return _parse_dashscope_response(resp)


# ---------------------------------------------------------------------------
# QwenOcrTextExtractor (real adapter, D6-I3U)
# ---------------------------------------------------------------------------


class QwenOcrTextExtractor:
    """Qwen / DashScope OCR extractor (D6-I3U real adapter).

    Delegates the actual OCR call to an injectable :class:`QwenOcrClient`.
    When no client is injected, :class:`DashScopeQwenOcrClient` is
    constructed lazily on first ``extract_text`` call (so the SDK is only
    imported when a real call is needed).

    Behaviour:

    - If ``api_key`` is empty → ``ocr_provider_unconfigured`` (terminal).
      This preserves the D6-I3T fail-closed contract for missing
      ``DASHSCOPE_API_KEY``.
    - If ``api_key`` is set → delegates to ``client.recognize(...)`` and
      maps :class:`QwenOcrClientError` subclasses to
      :class:`ArtifactExtractionError` with the matching ``retryable`` /
      ``failure_code``.
    - The API key is never included in error messages, logs, or the
      returned :class:`OcrTextExtractionResult`.

    The extractor is constructable without the dashscope SDK installed
    (the SDK is only imported inside ``DashScopeQwenOcrClient.recognize``).
    """

    EXTRACTOR_NAME = EXTRACTOR_NAME_QWEN

    def __init__(
        self,
        *,
        api_key: str | None,
        client: QwenOcrClient | None = None,
        model: str = DEFAULT_QWEN_OCR_MODEL,
        timeout_seconds: int = DEFAULT_QWEN_OCR_TIMEOUT_SECONDS,
        min_text_confidence: float = DEFAULT_MIN_TEXT_CONFIDENCE,
        min_layout_confidence: float = DEFAULT_MIN_LAYOUT_CONFIDENCE,
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._model = model
        self._timeout_seconds = timeout_seconds
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

        client: QwenOcrClient = self._client or DashScopeQwenOcrClient(
            api_key=self._api_key
        )

        try:
            response = client.recognize(
                image_data=data,
                content_type=content_type,
                model=self._model,
                timeout_seconds=self._timeout_seconds,
            )
        except QwenOcrClientError as exc:
            # Map client error to ArtifactExtractionError preserving
            # retryable + failure_code. Never include the API key.
            failure_class = (
                "extraction" if exc.retryable else _failure_class_for_code(exc.failure_code)
            )
            raise ArtifactExtractionError(
                f"Qwen OCR client error: {exc}",
                retryable=exc.retryable,
                failure_class=failure_class,
                failure_code=exc.failure_code,
            ) from exc

        warnings: list[str] = list(response.warnings or [])

        return OcrTextExtractionResult(
            extracted_text=response.extracted_text,
            extractor_name=EXTRACTOR_NAME_QWEN,
            text_confidence=response.text_confidence,
            layout_confidence=response.layout_confidence,
            warnings=warnings if warnings else None,
        )


def _failure_class_for_code(failure_code: str) -> str:
    """Map a terminal OCR failure code to its failure_class.

    Configuration-class errors (permission, SDK missing, unconfigured)
    are surfaced as ``configuration`` so operators can distinguish
    config/setup issues from extraction-time data issues.
    """
    if failure_code in (
        FAILURE_CODE_OCR_PERMISSION_DENIED,
        FAILURE_CODE_OCR_SDK_UNAVAILABLE,
        FAILURE_CODE_OCR_PROVIDER_UNCONFIGURED,
    ):
        return "configuration"
    return "extraction"


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

        # 5. Extract text via injected OCR extractor.
        # Wrap the sync call in asyncio.to_thread so a real DashScope call
        # (which may block for up to reader_ocr_request_timeout_seconds)
        # does NOT block the artifact pipeline worker's event loop. The
        # OcrTextExtractor Protocol stays sync; only the provider offloads
        # the call to a worker thread. ArtifactExtractionError raised
        # inside the thread propagates through to_thread unchanged.
        try:
            extraction = await asyncio.to_thread(
                self._extractor.extract_text, raw_bytes, content_type=ct
            )
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
