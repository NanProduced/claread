"""Cover image storage backends for Daily Reader (B-1).

Interface + two implementations (ponytail: no plugin system):

- ``LocalCoverStorage`` — dev: writes to ``services/api/static/covers/``
  and serves via the API server's static mount.
- ``OssCoverStorage`` — prod: object storage via the repo's existing
  optional ``[oss]`` extra (Aliyun OSS / ``oss2``). The task brief named
  "COS"; the repo's dependency convention is the Aliyun OSS extra with a
  fail-closed lazy-import pattern, so prod object storage reuses it. The
  ``CoverStorage`` interface is SDK-agnostic if another provider is ever
  required.

``get_cover_storage()`` switches by ``COVER_STORAGE_BACKEND`` (local|oss)
and falls back to local with a warning when OSS is unconfigured or the
``oss2`` SDK is missing — the pipeline never breaks on storage config.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

_COVER_DIR: Path | None = None
OSS_KEY_PREFIX = "daily-covers"


def _get_cover_dir() -> Path:
    global _COVER_DIR
    if _COVER_DIR is not None:
        return _COVER_DIR
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    _COVER_DIR = project_root / "static" / "covers"
    _COVER_DIR.mkdir(parents=True, exist_ok=True)
    return _COVER_DIR


@runtime_checkable
class CoverStorage(Protocol):
    backend: str

    async def store(self, data: bytes, *, filename: str, content_type: str) -> str:
        """Persist image bytes; return a publicly accessible URL."""
        ...


class LocalCoverStorage:
    backend = "local"

    def __init__(self, cover_dir: Path | None = None) -> None:
        self._cover_dir = cover_dir

    async def store(self, data: bytes, *, filename: str, content_type: str) -> str:
        cover_dir = self._cover_dir or _get_cover_dir()
        cover_dir.mkdir(parents=True, exist_ok=True)
        filepath = cover_dir / filename
        filepath.write_bytes(data)

        settings = get_settings()
        base_url = (
            settings.server_base_url.rstrip("/")
            if settings.server_base_url
            else "http://127.0.0.1:8000"
        )
        local_url = f"{base_url}/static/covers/{filename}"
        logger.info(
            "Cover image stored locally: %s (%d bytes)", local_url, len(data)
        )
        return local_url


class OssCoverStorage:
    """Object-storage backend using ``oss2`` (lazy import, fail-closed)."""

    backend = "oss"

    def __init__(
        self,
        *,
        access_key_id: str,
        access_key_secret: str,
        bucket: str,
        endpoint: str,
        public_url_base: str = "",
    ) -> None:
        if not access_key_id or not access_key_secret:
            raise ValueError("OssCoverStorage requires non-empty OSS credentials")
        if not bucket or not endpoint:
            raise ValueError("OssCoverStorage requires non-empty bucket and endpoint")
        self._access_key_id = access_key_id
        self._access_key_secret = access_key_secret
        self._bucket_name = bucket
        self._endpoint = endpoint
        endpoint_host = endpoint.removeprefix("https://").removeprefix("http://").rstrip("/")
        self._public_url_base = (
            public_url_base.rstrip("/") if public_url_base else f"https://{bucket}.{endpoint_host}"
        )
        self._bucket_instance = None

    def ensure_available(self) -> None:
        """Probe SDK availability + build the bucket handle (fail-closed)."""
        if self._bucket_instance is not None:
            return
        try:
            import oss2  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "oss2 SDK is not installed; install the '[oss]' extra to enable "
                "OSS cover storage"
            ) from exc
        auth = oss2.Auth(self._access_key_id, self._access_key_secret)
        self._bucket_instance = oss2.Bucket(auth, self._endpoint, self._bucket_name)

    async def store(self, data: bytes, *, filename: str, content_type: str) -> str:
        self.ensure_available()
        key = f"{OSS_KEY_PREFIX}/{filename}"
        # oss2 is a sync SDK; the payload is a single small image and this
        # runs inside a low-concurrency pipeline — direct call is fine.
        # ponytail: asyncio.to_thread if cover throughput ever matters.
        self._bucket_instance.put_object(
            key, data, headers={"Content-Type": content_type or "image/jpeg"}
        )
        url = f"{self._public_url_base}/{key}"
        logger.info("Cover image stored to OSS: %s (%d bytes)", url, len(data))
        return url


def get_cover_storage() -> CoverStorage:
    """Pick the backend from settings; degrade to local with a warning."""
    settings = get_settings()
    backend = (settings.cover_storage_backend or "local").strip().lower()
    if backend == "oss":
        access_key_id, access_key_secret = settings.resolve_aliyun_oss_credentials()
        try:
            storage = OssCoverStorage(
                access_key_id=access_key_id,
                access_key_secret=access_key_secret,
                bucket=settings.aliyun_oss_bucket,
                endpoint=settings.aliyun_oss_endpoint,
                public_url_base=settings.cover_oss_public_url_base,
            )
            storage.ensure_available()
            return storage
        except Exception as exc:
            logger.warning(
                "cover_storage_backend=oss unavailable (%s); "
                "falling back to local static cover storage",
                exc,
            )
    return LocalCoverStorage()
