"""Aliyun OSS presigner for upload instructions.

Provides a minimal :class:`Presigner` protocol that produces presigned PUT
URLs for OSS object uploads. The default :class:`NullPresigner` returns
``None`` so callers fail closed when no credentials are configured — the
client then falls back to the ``oss_put_object_pending_credentials`` semantic
and must supply its own credentials.

:class:`AliyunOssPresigner` lazily imports ``oss2`` and fails with a clear
error if the SDK or credentials are missing.

Security contract
-----------------
The AccessKey **secret** never leaves the server process — it is used only to
compute the URL signature. The AccessKey **id** is not a secret and MAY appear
in the presigned URL query string (e.g. ``OSSAccessKeyId=...``); this is the
standard Aliyun OSS presigned-URL model. Callers/tests that check for
credential leakage must assert that the **secret** is absent, not the id.

This module is imported at module load by routes/services that need a
presigner, but no ``oss2`` import happens until a concrete
:class:`AliyunOssPresigner` is asked to sign.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app.config.settings import get_settings


@dataclass(frozen=True, slots=True)
class PresignedUpload:
    """Presigned upload instruction returned by a :class:`Presigner`.

    ``url`` carries the signature in the query string and may include the
    AccessKey id (``OSSAccessKeyId=...``) per the standard OSS presigned-URL
    model — the id is not a secret. ``headers`` carries only request headers
    the client must send (e.g. ``Content-Type``, ``x-oss-content-sha256``),
    never the AccessKey secret. ``expires_at`` is the absolute expiry
    timestamp.
    """

    url: str
    method: str  # "PUT"
    headers: dict[str, str]
    expires_at: datetime


class Presigner(Protocol):
    """Produces presigned upload URLs for OSS objects.

    Implementations must NOT return secret material (the AccessKey secret).
    The AccessKey id is not a secret and may appear in the presigned URL
    query string. If the presigner cannot produce a URL (e.g. credentials
    missing, bucket mismatch), it returns ``None`` so the caller can fall
    back to the pending-credentials path.
    """

    def presign_put_object(
        self,
        *,
        bucket: str,
        endpoint: str,
        object_key: str,
        content_type: str | None = None,
        content_sha256: str | None = None,
        expires_in: timedelta,
    ) -> PresignedUpload | None: ...

    def presign_get_object(
        self,
        *,
        bucket: str,
        endpoint: str,
        object_key: str,
        expires_in: timedelta,
    ) -> PresignedUpload | None:
        """Owner-scoped short-lived read-only URL (R8 source preview).

        Fail-closed: returns ``None`` when no credentials / SDK are
        configured. The URL is a GET with a short ``expires_in``; the
        AccessKey secret never leaves the server.
        """


class PresignerNotConfiguredError(RuntimeError):
    """Raised when a presigner is asked to sign but has no credentials/SDK."""


class NullPresigner:
    """Default presigner that always returns ``None`` (fail closed).

    Used when ``ALIYUN_OSS_PRESIGN_ENABLED=False`` or credentials are missing.
    The caller falls back to ``oss_put_object_pending_credentials``.
    """

    def presign_put_object(
        self,
        *,
        bucket: str,
        endpoint: str,
        object_key: str,
        content_type: str | None = None,
        content_sha256: str | None = None,
        expires_in: timedelta,
    ) -> PresignedUpload | None:
        return None

    def presign_get_object(
        self,
        *,
        bucket: str,
        endpoint: str,
        object_key: str,
        expires_in: timedelta,
    ) -> PresignedUpload | None:
        return None


class FakePresigner:
    """Deterministic presigner for tests — never makes network calls.

    Produces a URL of the form
    ``https://{bucket}.{endpoint_host}/{object_key}?Expires=...&Signature=fake``.
    No real signing is performed; the URL is recognisable so tests can assert
    it without depending on ``oss2``.
    """

    def __init__(
        self,
        *,
        url_prefix: str = "https://fake-oss-signer.test",
    ) -> None:
        self._url_prefix = url_prefix.rstrip("/")

    def presign_put_object(
        self,
        *,
        bucket: str,
        endpoint: str,
        object_key: str,
        content_type: str | None = None,
        content_sha256: str | None = None,
        expires_in: timedelta,
    ) -> PresignedUpload | None:
        endpoint_host = _strip_scheme(endpoint)
        expires_at = datetime.now(UTC) + expires_in
        url = (
            f"{self._url_prefix}/{bucket}.{endpoint_host}/{object_key}"
            f"?Expires={int(expires_at.timestamp())}&Signature=fake"
        )
        headers: dict[str, str] = {}
        if content_type is not None:
            headers["Content-Type"] = content_type
        if content_sha256 is not None:
            headers["x-oss-content-sha256"] = content_sha256
        return PresignedUpload(
            url=url,
            method="PUT",
            headers=headers,
            expires_at=expires_at,
        )

    def presign_get_object(
        self,
        *,
        bucket: str,
        endpoint: str,
        object_key: str,
        expires_in: timedelta,
    ) -> PresignedUpload | None:
        endpoint_host = _strip_scheme(endpoint)
        expires_at = datetime.now(UTC) + expires_in
        url = (
            f"{self._url_prefix}/{bucket}.{endpoint_host}/{object_key}"
            f"?Expires={int(expires_at.timestamp())}&Signature=fake"
        )
        return PresignedUpload(
            url=url,
            method="GET",
            headers={},
            expires_at=expires_at,
        )


class AliyunOssPresigner:
    """Aliyun OSS presigner using ``oss2`` (lazy import).

    Fail-closed: if ``oss2`` is not installed, :meth:`presign_put_object`
    raises :class:`PresignerNotConfiguredError`. If credentials are missing
    at construction time, :class:`PresignerNotConfiguredError` is raised.

    The AccessKey **secret** is never returned to the caller — only a
    presigned URL whose signature is computed with the secret. The AccessKey
    **id** is not a secret and appears in the URL query string as
    ``OSSAccessKeyId=...`` per the standard OSS presigned-URL model.
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
            raise PresignerNotConfiguredError(
                "AliyunOssPresigner requires non-empty access_key_id and access_key_secret"
            )
        if not bucket or not endpoint:
            raise PresignerNotConfiguredError(
                "AliyunOssPresigner requires non-empty bucket and endpoint"
            )
        self._access_key_id = access_key_id
        self._access_key_secret = access_key_secret
        self._bucket = bucket
        self._endpoint = endpoint
        self._auth: Any = None
        self._bucket_instance: Any = None

    def _ensure_sdk(self) -> None:
        if self._bucket_instance is not None:
            return
        try:
            import oss2  # type: ignore[import-untyped]
        except ImportError as exc:
            raise PresignerNotConfiguredError(
                "oss2 SDK is not installed; install 'oss2' to enable AliyunOssPresigner"
            ) from exc
        self._auth = oss2.Auth(self._access_key_id, self._access_key_secret)
        self._bucket_instance = oss2.Bucket(self._auth, self._endpoint, self._bucket)

    def presign_put_object(
        self,
        *,
        bucket: str,
        endpoint: str,
        object_key: str,
        content_type: str | None = None,
        content_sha256: str | None = None,
        expires_in: timedelta,
    ) -> PresignedUpload | None:
        self._ensure_sdk()
        if bucket != self._bucket or endpoint != self._endpoint:
            # Only sign for the configured bucket/endpoint. A multi-bucket
            # presigner would need separate Bucket instances.
            return None
        expires_seconds = int(expires_in.total_seconds())
        headers: dict[str, str] = {}
        if content_type is not None:
            headers["Content-Type"] = content_type
        if content_sha256 is not None:
            headers["x-oss-content-sha256"] = content_sha256
        url = self._bucket_instance.sign_url(
            "PUT",
            object_key,
            expires_seconds,
            headers=headers or None,
        )
        return PresignedUpload(
            url=url,
            method="PUT",
            headers=headers,
            expires_at=datetime.now(UTC) + expires_in,
        )

    def presign_get_object(
        self,
        *,
        bucket: str,
        endpoint: str,
        object_key: str,
        expires_in: timedelta,
    ) -> PresignedUpload | None:
        """Short-lived read-only GET URL (R8 source preview)."""
        self._ensure_sdk()
        if bucket != self._bucket or endpoint != self._endpoint:
            return None
        expires_seconds = int(expires_in.total_seconds())
        url = self._bucket_instance.sign_url(
            "GET",
            object_key,
            expires_seconds,
            headers=None,
        )
        return PresignedUpload(
            url=url,
            method="GET",
            headers={},
            expires_at=datetime.now(UTC) + expires_in,
        )


def build_default_presigner() -> Presigner:
    """Build a presigner from current settings.

    Returns :class:`AliyunOssPresigner` when ``ALIYUN_OSS_PRESIGN_ENABLED=True``
    AND credentials are configured. Otherwise returns :class:`NullPresigner`
    so the route falls back to the pending-credentials path.

    This factory never raises for missing credentials — it degrades to
    ``NullPresigner``. A missing ``oss2`` SDK is NOT detected here (the
    factory does not import ``oss2``); it is handled at signing time when
    the route calls ``presign_put_object()`` and catches the resulting
    :class:`PresignerNotConfiguredError`, falling back to pending-credentials.
    An explicit :class:`AliyunOssPresigner` constructed directly will raise
    on missing credentials (fail closed).
    """
    settings = get_settings()
    if not settings.aliyun_oss_presign_enabled:
        return NullPresigner()
    access_key_id, access_key_secret = settings.resolve_aliyun_oss_credentials()
    if not access_key_id or not access_key_secret:
        return NullPresigner()
    try:
        return AliyunOssPresigner(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            bucket=settings.aliyun_oss_bucket,
            endpoint=settings.aliyun_oss_endpoint,
        )
    except PresignerNotConfiguredError:
        return NullPresigner()


def _strip_scheme(endpoint: str) -> str:
    if endpoint.startswith("https://"):
        return endpoint[len("https://") :]
    if endpoint.startswith("http://"):
        return endpoint[len("http://") :]
    return endpoint
