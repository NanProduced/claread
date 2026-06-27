"""Deterministic text artifact extraction provider (D6-I3M).

Wraps an injectable :class:`StorageObjectReader` to download object bytes and
decode them as UTF-8 text. Supports ``text/plain``, ``text/markdown``,
``text/x-markdown``, and ``application/octet-stream`` (only when the source
filename ends with ``.txt`` or ``.md``).

This provider performs **no** OCR, PDF parsing, or real network I/O — all
storage access goes through the injected reader. In tests, a
``FakeStorageObjectReader`` is used; in production, an OSS-backed reader will
be wired later.

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
    """Stub for future Aliyun OSS integration.

    Not wired yet — calling :meth:`read_object` raises ``NotImplementedError``
    so production code fails loudly if it accidentally tries to use this reader
    before the real OSS SDK adapter is implemented. No credentials are read, no
    network calls are made.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Accept (and ignore) arbitrary config so future wiring can pass
        # credentials / endpoint / region without changing the call site.
        pass

    async def read_object(
        self,
        *,
        bucket: str,
        endpoint: str,
        object_key: str,
    ) -> StorageObjectReadResult:
        raise NotImplementedError(
            "AliyunOssObjectReader is not implemented yet; inject a "
            "FakeStorageObjectReader for tests or a real OSS adapter when ready"
        )


class TextArtifactExtractionProvider:
    """Deterministic text extraction provider.

    Downloads object bytes via an injected :class:`StorageObjectReader`,
    validates content_type / sha256 / byte_size, and decodes as UTF-8
    (stripping BOM if present). Returns an :class:`ArtifactExtractionResult`
    suitable for the :class:`ArtifactExtractionWorkerService` to persist into
    ``original_inputs.source_text``.
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
