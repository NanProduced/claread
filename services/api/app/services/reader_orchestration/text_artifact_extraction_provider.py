"""Deterministic text artifact extraction provider (D6-I3M + D6-I3Q).

Wraps an injectable :class:`StorageObjectReader` to download object bytes and
decode them as UTF-8 text. Supports ``text/plain``, ``text/markdown``,
``text/x-markdown``, and ``application/octet-stream`` (only when the source
filename ends with ``.txt`` or ``.md``).

This provider performs **no** OCR, PDF parsing — all storage access goes
through the injected reader. In tests, a ``FakeStorageObjectReader`` is used;
in production, :class:`AliyunOssObjectReader` (D6-I3Q) is wired via
:class:`ArtifactInputPipelineWorkerService`.

Validation rules (all fail-terminal, non-retryable):

- **Content type**: must be one of the supported types above.
- **byte_size**: if the artifact metadata carries ``byte_size``, the downloaded
  bytes must match exactly.
- **content_sha256**: if the artifact metadata carries ``content_sha256``, the
  downloaded bytes must hash to the same value.
- **Decoding**: bytes must be valid UTF-8 (with or without BOM). Binary content
  that cannot be decoded is rejected.
- **Empty text**: whitespace-only text is rejected.

The returned :class:`ArtifactExtractionResult` carries quality metadata
(content_type, source_filename, byte_size, content_sha256_verified, encoding)
and non-fatal warnings (BOM stripped, octet-stream-by-extension).
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

from .artifact_extraction_worker import (
    ArtifactExtractionError,
    ArtifactExtractionJobContext,
    ArtifactExtractionResult,
)

EXTRACTOR_NAME = "deterministic_text_artifact_extractor_v1"

SUPPORTED_CONTENT_TYPES: frozenset[str] = frozenset({
    "text/plain",
    "text/markdown",
    "text/x-markdown",
})

# application/octet-stream is allowed only when source_filename has one of
# these extensions (case-insensitive).
OCTET_STREAM_ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".txt", ".md"})

FAILURE_CODE_UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
FAILURE_CODE_SHA256_MISMATCH = "sha256_mismatch"
FAILURE_CODE_BYTE_SIZE_MISMATCH = "byte_size_mismatch"
FAILURE_CODE_DECODE_ERROR = "decode_error"
FAILURE_CODE_EMPTY_TEXT = "extraction_empty_text"
FAILURE_CODE_STORAGE_READ_ERROR = "storage_read_error"

# AliyunOssObjectReader failure codes (D6-I3Q)
FAILURE_CODE_OSS_SDK_MISSING = "oss_sdk_missing"
FAILURE_CODE_OSS_OBJECT_NOT_FOUND = "oss_object_not_found"
FAILURE_CODE_OSS_ACCESS_DENIED = "oss_access_denied"
FAILURE_CODE_OSS_BUCKET_ENDPOINT_MISMATCH = "oss_bucket_endpoint_mismatch"
FAILURE_CODE_OSS_NETWORK_ERROR = "oss_network_error"
FAILURE_CODE_OSS_ERROR = "oss_error"

_UTF8_BOM = b"\xef\xbb\xbf"


@dataclass(frozen=True, slots=True)
class StorageObjectReadResult:
    """Raw bytes read from storage, plus optional metadata from the response."""

    data: bytes
    byte_size: int | None = None
    etag: str | None = None
    content_type: str | None = None


class StorageObjectReader(Protocol):
    """Reads raw object bytes from a storage backend (OSS, S3, local, fake…)."""

    async def read_object(
        self,
        *,
        bucket: str,
        endpoint: str,
        object_key: str,
    ) -> StorageObjectReadResult: ...


class AliyunOssObjectReader:
    """Aliyun OSS object reader using ``oss2`` (lazy import, D6-I3Q).

    Fail-closed: if ``oss2`` is not installed or credentials are missing,
    :meth:`read_object` raises :class:`ArtifactExtractionError` with
    ``retryable=False`` (terminal). No credentials are ever returned in error
    messages or :class:`StorageObjectReadResult`.

    Error classification:

    - ``NoSuchKey`` (404) / ``AccessDenied`` (403) → terminal (non-retryable)
    - ``RequestError`` / ``TimeoutError`` / ``ConnectionError`` → retryable
    - Other ``oss2.exceptions.OssError`` → retryable (conservative default)

    The synchronous ``oss2`` call runs in a thread executor so the event loop
    is not blocked. Credentials are read once at construction time and never
    logged.
    """

    def __init__(
        self,
        *,
        access_key_id: str,
        access_key_secret: str,
        bucket: str,
        endpoint: str,
    ) -> None:
        if not access_key_id or not access_key_secret:
            raise ValueError(
                "AliyunOssObjectReader requires non-empty access_key_id and access_key_secret"
            )
        if not bucket or not endpoint:
            raise ValueError(
                "AliyunOssObjectReader requires non-empty bucket and endpoint"
            )
        self._access_key_id = access_key_id
        self._access_key_secret = access_key_secret
        self._bucket = bucket
        self._endpoint = endpoint
        # Lazy-initialized oss2.Bucket instance (None until first use).
        self._bucket_instance: Any = None

    def _ensure_sdk(self) -> None:
        """Lazy-import ``oss2`` and build the Auth + Bucket instances.

        Raises :class:`ArtifactExtractionError` (terminal) if the SDK is not
        installed. Credentials are never included in the error message.
        """
        if self._bucket_instance is not None:
            return
        try:
            import oss2  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ArtifactExtractionError(
                "oss2 SDK is not installed; install 'oss2' to enable "
                "AliyunOssObjectReader",
                retryable=False,
                failure_class="extraction",
                failure_code=FAILURE_CODE_OSS_SDK_MISSING,
            ) from exc
        auth = oss2.Auth(self._access_key_id, self._access_key_secret)
        self._bucket_instance = oss2.Bucket(auth, self._endpoint, self._bucket)

    async def read_object(
        self,
        *,
        bucket: str,
        endpoint: str,
        object_key: str,
    ) -> StorageObjectReadResult:
        # Validate request before loading SDK so bucket/endpoint mismatch is
        # caught even when oss2 is not installed.
        if bucket != self._bucket or endpoint != self._endpoint:
            raise ArtifactExtractionError(
                "AliyunOssObjectReader bucket/endpoint mismatch: reader is "
                "configured for a different bucket/endpoint than the one "
                "requested. Refusing to read from an unconfigured bucket.",
                retryable=False,
                failure_class="extraction",
                failure_code=FAILURE_CODE_OSS_BUCKET_ENDPOINT_MISMATCH,
            )

        try:
            self._ensure_sdk()
        except ArtifactExtractionError:
            raise

        # oss2 is synchronous — run in a thread to avoid blocking the event loop.
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._read_object_sync, object_key,
        )

    def _read_object_sync(self, object_key: str) -> StorageObjectReadResult:
        """Synchronous OSS read + error classification (runs in executor)."""
        import oss2  # type: ignore[import-untyped]  # local for type narrowing

        try:
            result = self._bucket_instance.get_object(object_key)
            data = result.read()
            content_type = result.headers.get("Content-Type")
            etag = result.headers.get("ETag")
            if etag:
                etag = etag.strip('"')
            return StorageObjectReadResult(
                data=data,
                byte_size=len(data),
                etag=etag,
                content_type=content_type,
            )
        except oss2.exceptions.NoSuchKey as exc:
            raise ArtifactExtractionError(
                f"OSS object not found: object_key={object_key!r}",
                retryable=False,
                failure_class="extraction",
                failure_code=FAILURE_CODE_OSS_OBJECT_NOT_FOUND,
            ) from exc
        except oss2.exceptions.AccessDenied as exc:
            raise ArtifactExtractionError(
                f"OSS access denied reading object: object_key={object_key!r}",
                retryable=False,
                failure_class="extraction",
                failure_code=FAILURE_CODE_OSS_ACCESS_DENIED,
            ) from exc
        except (
            oss2.exceptions.RequestError,
            TimeoutError,
            ConnectionError,
        ) as exc:
            raise ArtifactExtractionError(
                f"OSS network error reading object: object_key={object_key!r}",
                retryable=True,
                failure_class="extraction",
                failure_code=FAILURE_CODE_OSS_NETWORK_ERROR,
            ) from exc
        except oss2.exceptions.OssError as exc:
            # Conservative default: treat unknown OSS errors as retryable so
            # the worker can schedule retry_later. A reader that knows an
            # error is terminal should raise ArtifactExtractionError directly.
            raise ArtifactExtractionError(
                f"OSS error reading object: object_key={object_key!r}",
                retryable=True,
                failure_class="extraction",
                failure_code=FAILURE_CODE_OSS_ERROR,
            ) from exc


class TextArtifactExtractionProvider:
    """Deterministic text extraction provider.

    Downloads object bytes via an injected :class:`StorageObjectReader`,
    validates content_type / sha256 / byte_size, and decodes as UTF-8
    (stripping BOM if present). Returns an :class:`ArtifactExtractionResult`
    suitable for the :class:`ArtifactExtractionWorkerService` to persist as
    the confirmed-source document.
    """

    def __init__(self, reader: StorageObjectReader) -> None:
        self._reader = reader

    async def extract(
        self,
        context: ArtifactExtractionJobContext,
    ) -> ArtifactExtractionResult:
        content_type = context.content_type
        source_filename = context.source_filename or ""
        warnings: list[str] = []

        # 1. Content type gate
        if not _is_content_type_supported(content_type, source_filename, warnings):
            raise ArtifactExtractionError(
                f"unsupported content_type {content_type!r} for "
                f"source_filename {source_filename!r}",
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
            # Reader already classified the error (e.g. terminal 404/403) —
            # pass through with its original retryable flag.
            raise
        except Exception as exc:
            # Transient storage errors (timeout, connection reset, 5xx) default
            # to retryable so the worker can schedule retry_later. A reader
            # that knows an error is terminal should raise
            # ArtifactExtractionError(retryable=False) directly.
            raise ArtifactExtractionError(
                f"storage read failed for object_key={context.object_key!r}: {exc}",
                retryable=True,
                failure_class="extraction",
                failure_code=FAILURE_CODE_STORAGE_READ_ERROR,
            ) from exc

        raw_bytes = read_result.data

        # 3. byte_size validation (if artifact metadata carries it)
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

        # 4. content_sha256 validation (if artifact metadata carries it)
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

        # 5. UTF-8 / UTF-8 BOM decode
        text, encoding = _decode_utf8(raw_bytes)
        if text is None:
            raise ArtifactExtractionError(
                f"failed to decode {len(raw_bytes)} bytes as UTF-8 text",
                retryable=False,
                failure_class="extraction",
                failure_code=FAILURE_CODE_DECODE_ERROR,
            )

        # 6. Empty / whitespace check
        if not text.strip():
            raise ArtifactExtractionError(
                "extracted text is empty or whitespace-only",
                retryable=False,
                failure_class="extraction",
                failure_code=FAILURE_CODE_EMPTY_TEXT,
            )

        quality: dict[str, Any] = {
            "content_type": content_type,
            "source_filename": source_filename,
            "byte_size": len(raw_bytes),
            "content_sha256_verified": content_sha256_verified,
            "encoding": encoding,
        }

        return ArtifactExtractionResult(
            extracted_text=text,
            extractor_name=EXTRACTOR_NAME,
            quality=quality,
            warnings=warnings if warnings else None,
        )


def _is_content_type_supported(
    content_type: str | None,
    source_filename: str,
    warnings: list[str],
) -> bool:
    """Check whether the content type is supported for text extraction.

    ``application/octet-stream`` is allowed only when ``source_filename`` ends
    with ``.txt`` or ``.md`` (case-insensitive); a warning is appended.
    """
    if content_type is None:
        return False

    # Strip charset suffix like "text/plain; charset=utf-8"
    ct = content_type.strip().lower().split(";")[0].strip()

    if ct in SUPPORTED_CONTENT_TYPES:
        return True

    if ct == "application/octet-stream":
        lower_name = source_filename.lower()
        for ext in OCTET_STREAM_ALLOWED_EXTENSIONS:
            if lower_name.endswith(ext):
                warnings.append(
                    f"content_type is application/octet-stream but "
                    f"source_filename ends with {ext}; treating as text"
                )
                return True
        return False

    return False


def _decode_utf8(data: bytes) -> tuple[str | None, str]:
    """Decode bytes as UTF-8, stripping BOM if present.

    Returns ``(text, encoding_name)`` on success, or ``(None, "")`` on failure.
    The encoding name is ``"utf-8-bom"`` when a BOM was stripped, ``"utf-8"``
    otherwise.
    """
    if data.startswith(_UTF8_BOM):
        try:
            text = data[len(_UTF8_BOM):].decode("utf-8")
            return text, "utf-8-bom"
        except UnicodeDecodeError:
            return None, ""

    try:
        text = data.decode("utf-8")
        return text, "utf-8"
    except UnicodeDecodeError:
        return None, ""
