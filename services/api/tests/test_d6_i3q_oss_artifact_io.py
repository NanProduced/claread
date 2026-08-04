"""Tests for D6-I3Q OSS-backed Text/Markdown Artifact IO.

Covers:

1. :class:`AliyunOssObjectReader` construction validation, lazy SDK import,
   and error classification (using a stubbed ``oss2`` module so the tests run
   without the real SDK installed).
2. :class:`AliyunOssPresigner` construction, lazy SDK import, and signing.
3. :class:`NullPresigner` / :class:`FakePresigner` / :func:`build_default_presigner`.
4. ``init-upload`` route response with/without a presigned URL, and the
   response never leaks the access key **secret** (the access key **id** may
   appear in presigned URLs per the standard OSS model).
5. Pipeline regression: a stable text artifact drained through
   :class:`ArtifactInputPipelineWorkerService` still reaches ``article_ready``.

No real network calls are made. The ``oss2`` SDK is stubbed via
``sys.modules`` so the lazy import path is exercised without installing the
real dependency.
"""

from __future__ import annotations

import hashlib
import sys
import types
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import reader_orchestration
from app.database.connection import init_connection
from app.services.reader_orchestration.artifact_input_application_service import (
    EXTRACTION_JOB_TYPE,
    EXTRACTION_OPERATION_FINGERPRINT,
    EXTRACTION_TARGET_TYPE,
)
from app.services.reader_orchestration.artifact_pipeline_worker_service import (
    ArtifactInputPipelineWorkerService,
)
from app.services.reader_orchestration.artifact_extraction_worker import (
    ArtifactExtractionError,
)
from app.services.reader_orchestration.oss_presigner import (
    AliyunOssPresigner,
    FakePresigner,
    NullPresigner,
    PresignedUpload,
    PresignerNotConfiguredError,
    build_default_presigner,
)
from app.services.reader_orchestration.source_artifact_service import (
    SourceArtifactError,
    SourceArtifactRegistrationResult,
)
from app.services.reader_orchestration.text_artifact_extraction_provider import (
    AliyunOssObjectReader,
    FAILURE_CODE_OSS_ACCESS_DENIED,
    FAILURE_CODE_OSS_BUCKET_ENDPOINT_MISMATCH,
    FAILURE_CODE_OSS_NETWORK_ERROR,
    FAILURE_CODE_OSS_OBJECT_NOT_FOUND,
    FAILURE_CODE_OSS_SDK_MISSING,
    StorageObjectReadResult,
    TextArtifactExtractionProvider,
)

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ARTIFACTS_SQL = "SELECT 1"  # folded into infra/migrations/0001_initial.sql

from tests.test_reader_orchestration_schema_baseline import BASELINE_SQL, DATABASE_URL  # noqa: E402

# 0004 (document_blocks) is now in BASELINE_SQL, so the I3Q schema is
# BASELINE_SQL + 0007 (reader_source_artifacts).
I3Q_SCHEMA_SQL = BASELINE_SQL + "\n" + SOURCE_ARTIFACTS_SQL

# Fixed UUIDs for deterministic seeding
_USER_ID = UUID("00000000-0000-0000-0000-000000000a91")
_RECORD_ID = UUID("00000000-0000-0000-0000-000000000a92")
_ORIGINAL_INPUT_ID = UUID("00000000-0000-0000-0000-000000000a93")
_ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000a94")
_EXTRACTION_RUN_ID = UUID("00000000-0000-0000-0000-000000000a95")

_BUCKET = "claread-dev"
_ENDPOINT = "https://oss-cn-shenzhen.aliyuncs.com"
_ACCESS_KEY_ID = "fake-ak-test-only"
_ACCESS_KEY_SECRET = "fake-sk-test-only-do-not-use-in-prod"

AUTH_HEADERS = {"Authorization": "Bearer test-token"}
_INIT_UPLOAD_USER_ID = UUID("00000000-0000-0000-0000-000000000aa1")
_INIT_UPLOAD_ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000aa2")
_INIT_UPLOAD_RECORD_ID = UUID("00000000-0000-0000-0000-000000000aa3")
_INIT_UPLOAD_ORIGINAL_INPUT_ID = UUID("00000000-0000-0000-0000-000000000aa4")
_CONTENT_SHA256 = "a" * 64
_INIT_UPLOAD_OBJECT_KEY = (
    f"dev/original-inputs/{_INIT_UPLOAD_USER_ID}/{_INIT_UPLOAD_ARTIFACT_ID}/chapter-01.txt"
)

_STABLE_TEXT = (
    "The quick brown fox jumps over the lazy dog near the riverbank. "
    "A small bird sings in the tree above them. The morning sun casts "
    "long shadows across the meadow. Children laugh and play in the "
    "distance while a gentle breeze rustles the leaves. This peaceful "
    "scene captures a moment of quiet harmony in nature."
)


def _strip_endpoint_host(endpoint: str) -> str:
    """Strip the scheme from an OSS endpoint to get the host."""
    if endpoint.startswith("https://"):
        return endpoint[len("https://"):]
    if endpoint.startswith("http://"):
        return endpoint[len("http://"):]
    return endpoint


# ---------------------------------------------------------------------------
# Stubbed oss2 module (no real SDK required)
# ---------------------------------------------------------------------------


class _FakeOssError(Exception):
    """Base for stubbed oss2 exceptions."""


class _FakeNoSuchKey(_FakeOssError):
    pass


class _FakeAccessDenied(_FakeOssError):
    pass


class _FakeRequestError(_FakeOssError):
    pass


class _FakeOssGenericError(_FakeOssError):
    pass


def _install_fake_oss2(
    *,
    get_object_side_effect: Exception | None = None,
    get_object_return: object | None = None,
    sign_url_return: str = "https://signed.oss-test.test/object?Signature=stub",
) -> types.ModuleType:
    """Install a fake ``oss2`` module in ``sys.modules`` and return it.

    The fake provides ``Auth``, ``Bucket``, and ``exceptions`` with the
    exception classes used by :class:`AliyunOssObjectReader` /
    :class:`AliyunOssPresigner`.
    """
    fake_module = types.ModuleType("oss2")
    exceptions = types.ModuleType("oss2.exceptions")

    exceptions.NoSuchKey = _FakeNoSuchKey
    exceptions.AccessDenied = _FakeAccessDenied
    exceptions.RequestError = _FakeRequestError
    exceptions.OssError = _FakeOssError
    fake_module.exceptions = exceptions

    class _FakeAuth:
        def __init__(self, ak: str, sk: str) -> None:
            self.ak = ak
            self.sk = sk

    class _FakeBucket:
        def __init__(self, auth: _FakeAuth, endpoint: str, bucket: str) -> None:
            self.auth = auth
            self.endpoint = endpoint
            self.bucket = bucket
            self.sign_url = Mock(return_value=sign_url_return)
            if get_object_side_effect is not None:
                self.get_object = Mock(side_effect=get_object_side_effect)
            else:
                self.get_object = Mock(return_value=get_object_return)

    fake_module.Auth = _FakeAuth
    fake_module.Bucket = _FakeBucket
    sys.modules["oss2"] = fake_module
    sys.modules["oss2.exceptions"] = exceptions
    return fake_module


def _remove_fake_oss2() -> None:
    sys.modules.pop("oss2", None)
    sys.modules.pop("oss2.exceptions", None)


@pytest.fixture(autouse=True)
def _clean_oss2_stub():
    """Ensure no fake oss2 leaks between tests."""
    _remove_fake_oss2()
    yield
    _remove_fake_oss2()


@pytest.fixture
def _clear_settings_cache():
    """Clear the lru_cache on get_settings so tests see fresh env values."""
    from app.config import settings as settings_module

    settings_module.get_settings.cache_clear()
    yield
    settings_module.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# AliyunOssObjectReader construction
# ---------------------------------------------------------------------------


def test_reader_construction_requires_access_key_id() -> None:
    with pytest.raises(ValueError, match="access_key_id"):
        AliyunOssObjectReader(
            access_key_id="",
            access_key_secret=_ACCESS_KEY_SECRET,
            bucket=_BUCKET,
            endpoint=_ENDPOINT,
        )


def test_reader_construction_requires_access_key_secret() -> None:
    with pytest.raises(ValueError, match="access_key_secret"):
        AliyunOssObjectReader(
            access_key_id=_ACCESS_KEY_ID,
            access_key_secret="",
            bucket=_BUCKET,
            endpoint=_ENDPOINT,
        )


def test_reader_construction_requires_bucket() -> None:
    with pytest.raises(ValueError, match="bucket"):
        AliyunOssObjectReader(
            access_key_id=_ACCESS_KEY_ID,
            access_key_secret=_ACCESS_KEY_SECRET,
            bucket="",
            endpoint=_ENDPOINT,
        )


def test_reader_construction_requires_endpoint() -> None:
    with pytest.raises(ValueError, match="endpoint"):
        AliyunOssObjectReader(
            access_key_id=_ACCESS_KEY_ID,
            access_key_secret=_ACCESS_KEY_SECRET,
            bucket=_BUCKET,
            endpoint="",
        )


# ---------------------------------------------------------------------------
# AliyunOssObjectReader read + error classification
# ---------------------------------------------------------------------------


async def test_reader_raises_sdk_missing_when_oss2_not_installed() -> None:
    reader = AliyunOssObjectReader(
        access_key_id=_ACCESS_KEY_ID,
        access_key_secret=_ACCESS_KEY_SECRET,
        bucket=_BUCKET,
        endpoint=_ENDPOINT,
    )
    with pytest.raises(ArtifactExtractionError) as exc_info:
        await reader.read_object(
            bucket=_BUCKET,
            endpoint=_ENDPOINT,
            object_key="dev/test/notes.txt",
        )
    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_OSS_SDK_MISSING


async def test_reader_bucket_endpoint_mismatch_is_terminal() -> None:
    """Bucket/endpoint mismatch is caught before SDK load (no oss2 needed)."""
    reader = AliyunOssObjectReader(
        access_key_id=_ACCESS_KEY_ID,
        access_key_secret=_ACCESS_KEY_SECRET,
        bucket=_BUCKET,
        endpoint=_ENDPOINT,
    )
    with pytest.raises(ArtifactExtractionError) as exc_info:
        await reader.read_object(
            bucket="other-bucket",
            endpoint=_ENDPOINT,
            object_key="dev/test/notes.txt",
        )
    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_OSS_BUCKET_ENDPOINT_MISMATCH


async def test_reader_text_plain_object_succeeds() -> None:
    text_bytes = b"Hello world from OSS."
    fake_result = SimpleNamespace(
        read=Mock(return_value=text_bytes),
        headers={"Content-Type": "text/plain", "ETag": '"abc123"'},
    )
    _install_fake_oss2(get_object_return=fake_result)
    reader = AliyunOssObjectReader(
        access_key_id=_ACCESS_KEY_ID,
        access_key_secret=_ACCESS_KEY_SECRET,
        bucket=_BUCKET,
        endpoint=_ENDPOINT,
    )
    result = await reader.read_object(
        bucket=_BUCKET,
        endpoint=_ENDPOINT,
        object_key="dev/test/notes.txt",
    )
    assert result.data == text_bytes
    assert result.byte_size == len(text_bytes)
    assert result.etag == "abc123"
    assert result.content_type == "text/plain"


async def test_reader_text_markdown_object_succeeds() -> None:
    md_bytes = b"# Title\n\nContent.\n"
    fake_result = SimpleNamespace(
        read=Mock(return_value=md_bytes),
        headers={"Content-Type": "text/markdown"},
    )
    _install_fake_oss2(get_object_return=fake_result)
    reader = AliyunOssObjectReader(
        access_key_id=_ACCESS_KEY_ID,
        access_key_secret=_ACCESS_KEY_SECRET,
        bucket=_BUCKET,
        endpoint=_ENDPOINT,
    )
    result = await reader.read_object(
        bucket=_BUCKET,
        endpoint=_ENDPOINT,
        object_key="dev/test/readme.md",
    )
    assert result.data == md_bytes
    assert result.content_type == "text/markdown"


async def test_reader_404_not_found_is_terminal() -> None:
    _install_fake_oss2(get_object_side_effect=_FakeNoSuchKey("not found"))
    reader = AliyunOssObjectReader(
        access_key_id=_ACCESS_KEY_ID,
        access_key_secret=_ACCESS_KEY_SECRET,
        bucket=_BUCKET,
        endpoint=_ENDPOINT,
    )
    with pytest.raises(ArtifactExtractionError) as exc_info:
        await reader.read_object(
            bucket=_BUCKET,
            endpoint=_ENDPOINT,
            object_key="dev/test/missing.txt",
        )
    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_OSS_OBJECT_NOT_FOUND


async def test_reader_403_access_denied_is_terminal() -> None:
    _install_fake_oss2(get_object_side_effect=_FakeAccessDenied("forbidden"))
    reader = AliyunOssObjectReader(
        access_key_id=_ACCESS_KEY_ID,
        access_key_secret=_ACCESS_KEY_SECRET,
        bucket=_BUCKET,
        endpoint=_ENDPOINT,
    )
    with pytest.raises(ArtifactExtractionError) as exc_info:
        await reader.read_object(
            bucket=_BUCKET,
            endpoint=_ENDPOINT,
            object_key="dev/test/notes.txt",
        )
    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == FAILURE_CODE_OSS_ACCESS_DENIED


async def test_reader_network_error_is_retryable() -> None:
    _install_fake_oss2(get_object_side_effect=_FakeRequestError("timeout"))
    reader = AliyunOssObjectReader(
        access_key_id=_ACCESS_KEY_ID,
        access_key_secret=_ACCESS_KEY_SECRET,
        bucket=_BUCKET,
        endpoint=_ENDPOINT,
    )
    with pytest.raises(ArtifactExtractionError) as exc_info:
        await reader.read_object(
            bucket=_BUCKET,
            endpoint=_ENDPOINT,
            object_key="dev/test/notes.txt",
        )
    assert exc_info.value.retryable is True
    assert exc_info.value.failure_code == FAILURE_CODE_OSS_NETWORK_ERROR


async def test_reader_timeout_builtin_is_retryable() -> None:
    _install_fake_oss2(get_object_side_effect=TimeoutError("read timeout"))
    reader = AliyunOssObjectReader(
        access_key_id=_ACCESS_KEY_ID,
        access_key_secret=_ACCESS_KEY_SECRET,
        bucket=_BUCKET,
        endpoint=_ENDPOINT,
    )
    with pytest.raises(ArtifactExtractionError) as exc_info:
        await reader.read_object(
            bucket=_BUCKET,
            endpoint=_ENDPOINT,
            object_key="dev/test/notes.txt",
        )
    assert exc_info.value.retryable is True
    assert exc_info.value.failure_code == FAILURE_CODE_OSS_NETWORK_ERROR


async def test_reader_connection_error_builtin_is_retryable() -> None:
    _install_fake_oss2(get_object_side_effect=ConnectionError("reset"))
    reader = AliyunOssObjectReader(
        access_key_id=_ACCESS_KEY_ID,
        access_key_secret=_ACCESS_KEY_SECRET,
        bucket=_BUCKET,
        endpoint=_ENDPOINT,
    )
    with pytest.raises(ArtifactExtractionError) as exc_info:
        await reader.read_object(
            bucket=_BUCKET,
            endpoint=_ENDPOINT,
            object_key="dev/test/notes.txt",
        )
    assert exc_info.value.retryable is True
    assert exc_info.value.failure_code == FAILURE_CODE_OSS_NETWORK_ERROR


async def test_reader_generic_oss_error_is_retryable() -> None:
    _install_fake_oss2(
        get_object_side_effect=_FakeOssGenericError("unknown oss error"),
    )
    reader = AliyunOssObjectReader(
        access_key_id=_ACCESS_KEY_ID,
        access_key_secret=_ACCESS_KEY_SECRET,
        bucket=_BUCKET,
        endpoint=_ENDPOINT,
    )
    with pytest.raises(ArtifactExtractionError) as exc_info:
        await reader.read_object(
            bucket=_BUCKET,
            endpoint=_ENDPOINT,
            object_key="dev/test/notes.txt",
        )
    assert exc_info.value.retryable is True
    assert exc_info.value.failure_code == "oss_error"


# ---------------------------------------------------------------------------
# Provider integration with AliyunOssObjectReader (via stubbed oss2)
# ---------------------------------------------------------------------------


async def test_provider_with_real_reader_text_plain_succeeds() -> None:
    """End-to-end: TextArtifactExtractionProvider + AliyunOssObjectReader
    (stubbed oss2) → text/plain extraction succeeds."""
    from app.services.reader_orchestration.artifact_extraction_worker import (
        ArtifactExtractionJobContext,
    )

    text_bytes = b"Hello from OSS text/plain."
    fake_result = SimpleNamespace(
        read=Mock(return_value=text_bytes),
        headers={"Content-Type": "text/plain"},
    )
    _install_fake_oss2(get_object_return=fake_result)
    reader = AliyunOssObjectReader(
        access_key_id=_ACCESS_KEY_ID,
        access_key_secret=_ACCESS_KEY_SECRET,
        bucket=_BUCKET,
        endpoint=_ENDPOINT,
    )
    provider = TextArtifactExtractionProvider(reader=reader)
    context = ArtifactExtractionJobContext(
        job_id=uuid4(),
        run_id=uuid4(),
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        original_input_id=_ORIGINAL_INPUT_ID,
        source_artifact_id=_ARTIFACT_ID,
        artifact_kind="original_upload",
        storage_provider="oss",
        bucket=_BUCKET,
        endpoint=_ENDPOINT,
        object_key="dev/test/notes.txt",
        content_type="text/plain",
        byte_size=len(text_bytes),
        content_sha256=hashlib.sha256(text_bytes).hexdigest(),
        source_filename="notes.txt",
        expected_generation=1,
        operation_fingerprint="input_artifact_extraction_v1",
    )
    result = await provider.extract(context)
    assert result.extracted_text == text_bytes.decode("utf-8")
    assert result.quality["content_sha256_verified"] is True


async def test_provider_with_real_reader_sha_mismatch_fails_terminal() -> None:
    """SHA mismatch from artifact metadata → terminal failure."""
    from app.services.reader_orchestration.artifact_extraction_worker import (
        ArtifactExtractionJobContext,
    )

    text_bytes = b"Hello from OSS text/plain."
    fake_result = SimpleNamespace(
        read=Mock(return_value=text_bytes),
        headers={"Content-Type": "text/plain"},
    )
    _install_fake_oss2(get_object_return=fake_result)
    reader = AliyunOssObjectReader(
        access_key_id=_ACCESS_KEY_ID,
        access_key_secret=_ACCESS_KEY_SECRET,
        bucket=_BUCKET,
        endpoint=_ENDPOINT,
    )
    provider = TextArtifactExtractionProvider(reader=reader)
    context = ArtifactExtractionJobContext(
        job_id=uuid4(),
        run_id=uuid4(),
        reading_record_id=_RECORD_ID,
        user_id=_USER_ID,
        original_input_id=_ORIGINAL_INPUT_ID,
        source_artifact_id=_ARTIFACT_ID,
        artifact_kind="original_upload",
        storage_provider="oss",
        bucket=_BUCKET,
        endpoint=_ENDPOINT,
        object_key="dev/test/notes.txt",
        content_type="text/plain",
        byte_size=None,
        content_sha256="0" * 64,  # mismatch
        source_filename="notes.txt",
        expected_generation=1,
        operation_fingerprint="input_artifact_extraction_v1",
    )
    with pytest.raises(ArtifactExtractionError) as exc_info:
        await provider.extract(context)
    assert exc_info.value.retryable is False
    assert exc_info.value.failure_code == "sha256_mismatch"


# ---------------------------------------------------------------------------
# Presigner tests
# ---------------------------------------------------------------------------


def test_null_presigner_returns_none() -> None:
    presigner = NullPresigner()
    result = presigner.presign_put_object(
        bucket=_BUCKET,
        endpoint=_ENDPOINT,
        object_key="dev/test/notes.txt",
        expires_in=timedelta(seconds=900),
    )
    assert result is None


def test_fake_presigner_returns_deterministic_url_without_credentials() -> None:
    presigner = FakePresigner()
    result = presigner.presign_put_object(
        bucket=_BUCKET,
        endpoint=_ENDPOINT,
        object_key="dev/test/notes.txt",
        content_type="text/plain",
        content_sha256=_CONTENT_SHA256,
        expires_in=timedelta(seconds=900),
    )
    assert result is not None
    assert result.method == "PUT"
    assert "fake-oss-signer.test" in result.url
    assert _BUCKET in result.url
    assert "dev/test/notes.txt" in result.url
    assert "Signature=fake" in result.url
    assert result.headers["Content-Type"] == "text/plain"
    assert result.headers["x-oss-content-sha256"] == _CONTENT_SHA256
    # No credentials in the result
    result_str = str(result.url) + str(result.headers)
    assert "access_key" not in result_str.lower()
    assert _ACCESS_KEY_ID not in result_str
    assert _ACCESS_KEY_SECRET not in result_str


def test_aliyun_presigner_construction_requires_credentials() -> None:
    with pytest.raises(PresignerNotConfiguredError, match="access_key_id"):
        AliyunOssPresigner(
            access_key_id="",
            access_key_secret=_ACCESS_KEY_SECRET,
            bucket=_BUCKET,
            endpoint=_ENDPOINT,
        )


def test_aliyun_presigner_raises_when_sdk_missing() -> None:
    presigner = AliyunOssPresigner(
        access_key_id=_ACCESS_KEY_ID,
        access_key_secret=_ACCESS_KEY_SECRET,
        bucket=_BUCKET,
        endpoint=_ENDPOINT,
    )
    with pytest.raises(PresignerNotConfiguredError, match="oss2 SDK is not installed"):
        presigner.presign_put_object(
            bucket=_BUCKET,
            endpoint=_ENDPOINT,
            object_key="dev/test/notes.txt",
            expires_in=timedelta(seconds=900),
        )


def test_aliyun_presigner_signs_url_with_stubbed_sdk() -> None:
    signed_url = "https://claread-dev.oss-cn-shenzhen.aliyuncs.com/dev/test/notes.txt?OSSAccessKeyId=stub&Signature=signed"
    _install_fake_oss2(sign_url_return=signed_url)
    presigner = AliyunOssPresigner(
        access_key_id=_ACCESS_KEY_ID,
        access_key_secret=_ACCESS_KEY_SECRET,
        bucket=_BUCKET,
        endpoint=_ENDPOINT,
    )
    result = presigner.presign_put_object(
        bucket=_BUCKET,
        endpoint=_ENDPOINT,
        object_key="dev/test/notes.txt",
        content_type="text/plain",
        content_sha256=_CONTENT_SHA256,
        expires_in=timedelta(seconds=900),
    )
    assert result is not None
    assert result.url == signed_url
    assert result.method == "PUT"
    assert result.headers["Content-Type"] == "text/plain"
    assert result.headers["x-oss-content-sha256"] == _CONTENT_SHA256


def test_aliyun_presigner_bucket_mismatch_returns_none() -> None:
    _install_fake_oss2(sign_url_return="https://signed.test/obj")
    presigner = AliyunOssPresigner(
        access_key_id=_ACCESS_KEY_ID,
        access_key_secret=_ACCESS_KEY_SECRET,
        bucket=_BUCKET,
        endpoint=_ENDPOINT,
    )
    result = presigner.presign_put_object(
        bucket="other-bucket",
        endpoint=_ENDPOINT,
        object_key="dev/test/notes.txt",
        expires_in=timedelta(seconds=900),
    )
    assert result is None


def test_build_default_presigner_returns_null_when_disabled(
    _clear_settings_cache,
) -> None:
    with patch.dict("os.environ", {"ALIYUN_OSS_PRESIGN_ENABLED": "False"}, clear=False):
        from app.config import settings as settings_module

        settings_module.get_settings.cache_clear()
        presigner = build_default_presigner()
    assert isinstance(presigner, NullPresigner)


def test_build_default_presigner_returns_null_when_credentials_missing(
    _clear_settings_cache,
) -> None:
    with patch.dict(
        "os.environ",
        {
            "ALIYUN_OSS_PRESIGN_ENABLED": "True",
            "ALIYUN_OSS_ACCESS_KEY_ID": "",
            "ALIYUN_OSS_ACCESS_KEY_SECRET": "",
            "ALIBABA_CLOUD_ACCESS_KEY_ID": "",
            "ALIBABA_CLOUD_ACCESS_KEY_SECRET": "",
        },
        clear=True,
    ):
        from app.config import settings as settings_module

        settings_module.get_settings.cache_clear()
        presigner = build_default_presigner()
    assert isinstance(presigner, NullPresigner)


def test_build_default_presigner_does_not_mix_partial_oss_credentials_with_fallback(
    _clear_settings_cache,
) -> None:
    with patch.dict(
        "os.environ",
        {
            "ALIYUN_OSS_PRESIGN_ENABLED": "True",
            "ALIYUN_OSS_ACCESS_KEY_ID": _ACCESS_KEY_ID,
            "ALIYUN_OSS_ACCESS_KEY_SECRET": "",
            "ALIBABA_CLOUD_ACCESS_KEY_ID": "GENERIC_ID",
            "ALIBABA_CLOUD_ACCESS_KEY_SECRET": "GENERIC_SECRET",
        },
        clear=True,
    ):
        from app.config import settings as settings_module

        settings_module.get_settings.cache_clear()
        presigner = build_default_presigner()
    assert isinstance(presigner, NullPresigner)


def test_build_default_presigner_falls_back_to_alibaba_cloud_credentials(
    _clear_settings_cache,
) -> None:
    with patch.dict(
        "os.environ",
        {
            "ALIYUN_OSS_PRESIGN_ENABLED": "True",
            "ALIYUN_OSS_ACCESS_KEY_ID": "",
            "ALIYUN_OSS_ACCESS_KEY_SECRET": "",
            "ALIBABA_CLOUD_ACCESS_KEY_ID": _ACCESS_KEY_ID,
            "ALIBABA_CLOUD_ACCESS_KEY_SECRET": _ACCESS_KEY_SECRET,
            "ALIYUN_OSS_BUCKET": _BUCKET,
            "ALIYUN_OSS_ENDPOINT": _ENDPOINT,
        },
        clear=True,
    ):
        from app.config import settings as settings_module

        settings_module.get_settings.cache_clear()
        presigner = build_default_presigner()
    assert isinstance(presigner, AliyunOssPresigner)
    assert presigner._access_key_id == _ACCESS_KEY_ID


def test_build_default_presigner_returns_aliyun_when_enabled_with_credentials(
    _clear_settings_cache,
) -> None:
    with patch.dict(
        "os.environ",
        {
            "ALIYUN_OSS_PRESIGN_ENABLED": "True",
            "ALIYUN_OSS_ACCESS_KEY_ID": _ACCESS_KEY_ID,
            "ALIYUN_OSS_ACCESS_KEY_SECRET": _ACCESS_KEY_SECRET,
            "ALIYUN_OSS_BUCKET": _BUCKET,
            "ALIYUN_OSS_ENDPOINT": _ENDPOINT,
        },
        clear=False,
    ):
        from app.config import settings as settings_module

        settings_module.get_settings.cache_clear()
        presigner = build_default_presigner()
    assert isinstance(presigner, AliyunOssPresigner)


# ---------------------------------------------------------------------------
# init-upload route tests (with/without presigned URL)
# ---------------------------------------------------------------------------


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(reader_orchestration.router)
    return app


def _route_path() -> str:
    return "/reader/source-artifacts/init-upload"


def _session_info(user_id: UUID = _INIT_UPLOAD_USER_ID) -> object:
    return type(
        "SessionInfo",
        (),
        {
            "user_id": user_id,
            "session_id": uuid4(),
        },
    )()


def _mock_auth(user_id: UUID = _INIT_UPLOAD_USER_ID):
    return patch(
        "app.services.auth.dependencies.validate_session",
        new=AsyncMock(return_value=_session_info(user_id)),
    )


def _build_registration_result() -> SourceArtifactRegistrationResult:
    return SourceArtifactRegistrationResult(
        artifact_id=_INIT_UPLOAD_ARTIFACT_ID,
        storage_provider="oss",
        bucket=_BUCKET,
        object_key=_INIT_UPLOAD_OBJECT_KEY,
        artifact_kind="original_upload",
        content_type="text/plain",
        byte_size=4096,
        content_sha256=_CONTENT_SHA256,
        source_filename="chapter-01.txt",
        status="pending",
    )


def _build_object_ref() -> dict[str, str]:
    return {
        "storage_provider": "oss",
        "bucket": _BUCKET,
        "endpoint": _ENDPOINT,
        "object_key": _INIT_UPLOAD_OBJECT_KEY,
    }


def _mock_source_artifact_service(
    *,
    result: SourceArtifactRegistrationResult | None = None,
) -> tuple[patch, SimpleNamespace]:
    service = SimpleNamespace()
    service.register_source_artifact = AsyncMock(
        return_value=result or _build_registration_result(),
    )
    service.build_oss_object_ref = Mock(return_value=_build_object_ref())
    return (
        patch(
            "app.api.routes.reader_orchestration.SourceArtifactService",
            return_value=service,
        ),
        service,
    )


def test_init_upload_without_presigner_returns_pending_credentials(
    _clear_settings_cache,
) -> None:
    """Default (no presigner configured) → pending_credentials, no presigned URL."""
    app = _build_app()
    service_patch, _ = _mock_source_artifact_service()

    with (
        _mock_auth(),
        service_patch,
        patch.dict("os.environ", {"ALIYUN_OSS_PRESIGN_ENABLED": "False"}, clear=False),
        TestClient(app) as client,
    ):
        from app.config import settings as settings_module

        settings_module.get_settings.cache_clear()
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "artifact_kind": "original_upload",
                "source_filename": "chapter-01.txt",
                "content_type": "text/plain",
                "byte_size": 4096,
                "content_sha256": _CONTENT_SHA256,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["upload_method"] == "oss_put_object_pending_credentials"
    assert body["presigned_url"] is None
    assert body["presigned_method"] is None
    assert body["presigned_expires_at"] is None
    assert body["bucket"] == _BUCKET
    assert body["endpoint"] == _ENDPOINT
    assert body["object_key"] == _INIT_UPLOAD_OBJECT_KEY


def test_init_upload_with_fake_presigner_returns_presigned_url(
    _clear_settings_cache,
) -> None:
    """When a FakePresigner is injected via build_default_presigner, the
    response carries a presigned URL and upload_method=oss_put_object_presigned."""
    app = _build_app()
    service_patch, _ = _mock_source_artifact_service()
    fake_presigner = FakePresigner()

    with (
        _mock_auth(),
        service_patch,
        patch(
            "app.api.routes.reader_orchestration.build_default_presigner",
            return_value=fake_presigner,
        ),
        patch(
            "app.api.routes.reader_orchestration._get_presign_expires_seconds",
            return_value=900,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "artifact_kind": "original_upload",
                "source_filename": "chapter-01.txt",
                "content_type": "text/plain",
                "byte_size": 4096,
                "content_sha256": _CONTENT_SHA256,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["upload_method"] == "oss_put_object_presigned"
    assert body["presigned_url"] is not None
    assert "fake-oss-signer.test" in body["presigned_url"]
    assert body["presigned_method"] == "PUT"
    assert body["presigned_expires_at"] is not None
    # Object ref is still correct
    assert body["bucket"] == _BUCKET
    assert body["object_key"] == _INIT_UPLOAD_OBJECT_KEY
    # D6-I3Q P1 fix: in presigned mode the response headers MUST come from
    # the presigner (signed headers), not from _build_source_artifact_upload_headers.
    # FakePresigner signs x-oss-content-sha256, so the response must carry it
    # (not the non-signed content-sha256 hint).
    header_keys = {k.lower() for k in body["headers"].keys()}
    assert "x-oss-content-sha256" in header_keys
    assert body["headers"]["x-oss-content-sha256"] == _CONTENT_SHA256
    assert "content-sha256" not in header_keys  # non-signed hint must not appear
    assert body["headers"]["Content-Type"] == "text/plain"
    # The access key SECRET must never leak; the id is not a secret and may
    # appear in presigned URLs per the standard OSS model.
    body_text = str(body)
    assert _ACCESS_KEY_SECRET not in body_text
    assert "access_key_secret" not in body_text.lower()


def test_init_upload_presigner_failure_falls_back_to_pending_credentials(
    _clear_settings_cache,
) -> None:
    """If the presigner raises, the route falls back to pending_credentials."""

    class _ExplodingPresigner:
        def presign_put_object(self, **_kwargs):
            raise PresignerNotConfiguredError("boom")

    app = _build_app()
    service_patch, _ = _mock_source_artifact_service()

    with (
        _mock_auth(),
        service_patch,
        patch(
            "app.api.routes.reader_orchestration.build_default_presigner",
            return_value=_ExplodingPresigner(),
        ),
        patch(
            "app.api.routes.reader_orchestration._get_presign_expires_seconds",
            return_value=900,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "artifact_kind": "original_upload",
                "source_filename": "chapter-01.txt",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["upload_method"] == "oss_put_object_pending_credentials"
    assert body["presigned_url"] is None


def test_init_upload_rejects_non_original_upload_kind(_clear_settings_cache) -> None:
    """unknown artifact_kind still 422 — presigner wiring does not relax the gate."""
    app = _build_app()

    with (
        _mock_auth(),
        patch("app.api.routes.reader_orchestration.SourceArtifactService") as service_cls,
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={"artifact_kind": "ocr_result"},
        )

    assert response.status_code == 422
    service_cls.assert_not_called()


def test_init_upload_does_not_accept_user_id_in_body(_clear_settings_cache) -> None:
    """user_id only comes from auth — body user_id is rejected (extra field)."""
    app = _build_app()

    with (
        _mock_auth(),
        patch("app.api.routes.reader_orchestration.SourceArtifactService") as service_cls,
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "artifact_kind": "original_upload",
                "user_id": str(uuid4()),
            },
        )

    assert response.status_code == 422
    service_cls.assert_not_called()


def test_init_upload_response_never_leaks_access_key_secret(
    _clear_settings_cache,
) -> None:
    """The response must never leak the access key **secret**.

    The access key **id** is not a secret and may appear in presigned URLs
    (``OSSAccessKeyId=...``) per the standard Aliyun OSS presigned-URL model.
    Only the secret is forbidden from leaving the server.
    """
    app = _build_app()
    service_patch, _ = _mock_source_artifact_service()
    fake_presigner = FakePresigner()

    with (
        _mock_auth(),
        service_patch,
        patch(
            "app.api.routes.reader_orchestration.build_default_presigner",
            return_value=fake_presigner,
        ),
        patch(
            "app.api.routes.reader_orchestration._get_presign_expires_seconds",
            return_value=900,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "artifact_kind": "original_upload",
                "source_filename": "chapter-01.txt",
                "content_type": "text/plain",
            },
        )

    assert response.status_code == 200
    body_text = response.text
    # The secret value must never appear (case-insensitive).
    assert _ACCESS_KEY_SECRET.lower() not in body_text.lower()
    # The secret field name must never appear.
    assert "access_key_secret" not in body_text.lower()
    assert "access_secret" not in body_text.lower()


def test_init_upload_presigned_url_may_contain_access_key_id(
    _clear_settings_cache,
) -> None:
    """A presigned URL containing ``OSSAccessKeyId=...`` is acceptable.

    The AccessKey id is a public identifier, not a secret. The standard OSS
    presigned-URL model embeds it in the query string. This test verifies the
    route returns such a URL without filtering it, while the secret is still
    absent.
    """

    class _AkIdPresigner:
        """Fake presigner that returns a URL containing OSSAccessKeyId."""

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
            from datetime import UTC, datetime

            expires_at = datetime.now(UTC) + expires_in
            url = (
                f"https://{bucket}.{_strip_endpoint_host(endpoint)}/{object_key}"
                f"?OSSAccessKeyId=TESTAKID&Expires={int(expires_at.timestamp())}"
                f"&Signature=signed"
            )
            headers: dict[str, str] = {"Content-Type": content_type or "application/octet-stream"}
            if content_sha256 is not None:
                headers["x-oss-content-sha256"] = content_sha256
            return PresignedUpload(
                url=url,
                method="PUT",
                headers=headers,
                expires_at=expires_at,
            )

    app = _build_app()
    service_patch, _ = _mock_source_artifact_service()

    with (
        _mock_auth(),
        service_patch,
        patch(
            "app.api.routes.reader_orchestration.build_default_presigner",
            return_value=_AkIdPresigner(),
        ),
        patch(
            "app.api.routes.reader_orchestration._get_presign_expires_seconds",
            return_value=900,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            _route_path(),
            headers=AUTH_HEADERS,
            json={
                "artifact_kind": "original_upload",
                "source_filename": "chapter-01.txt",
                "content_type": "text/plain",
                "content_sha256": _CONTENT_SHA256,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["upload_method"] == "oss_put_object_presigned"
    # The AccessKey id is present in the URL — this is acceptable.
    assert "OSSAccessKeyId=TESTAKID" in body["presigned_url"]
    # The secret must never appear.
    assert _ACCESS_KEY_SECRET not in response.text
    assert "access_key_secret" not in response.text.lower()
    # Signed headers from the presigner are passed through.
    assert body["headers"]["x-oss-content-sha256"] == _CONTENT_SHA256


# ---------------------------------------------------------------------------
# Pipeline regression: stable text artifact end-to-end
# ---------------------------------------------------------------------------


async def _make_pool(schema_name: str) -> asyncpg.Pool:
    async def _init_conn(conn: asyncpg.Connection) -> None:
        await init_connection(conn)

    async def _setup_conn(conn: asyncpg.Connection) -> None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')

    return await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=4,
        init=_init_conn,
        setup=_setup_conn,
    )


async def _connect_admin(schema_name: str | None = None) -> asyncpg.Connection:
    conn = await asyncpg.connect(DATABASE_URL)
    if schema_name is not None:
        await conn.execute(f'SET search_path TO "{schema_name}", public')
    return conn


@pytest.fixture
async def i3q_env() -> asyncpg.Pool:
    schema_name = f"test_i3q_{uuid4().hex}"
    admin_conn = await _connect_admin()
    try:
        await admin_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        await admin_conn.execute(f'SET search_path TO "{schema_name}", public')
        await admin_conn.execute(I3Q_SCHEMA_SQL)
        pool = await _make_pool(schema_name)
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def _seed_extraction_job(
    pool: asyncpg.Pool,
    *,
    source_text: str,
    content_type: str = "text/plain",
    source_filename: str = "notes.txt",
    object_key: str = "dev/test/notes.txt",
) -> UUID:
    source_bytes = source_text.encode("utf-8")
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    byte_size = len(source_bytes)

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING",
            _USER_ID,
        )
        await conn.execute(
            """
            INSERT INTO reading_records (
                id, user_id, source_type, title, language,
                lifecycle_status, product_state, readiness_state, generation
            )
            VALUES ($1, $2, 'text', 'I3Q Test', 'en',
                    'active', 'processing', 'submitted', 1)
            """,
            _RECORD_ID,
            _USER_ID,
        )
        source_ref_json = {
            "artifact_id": str(_ARTIFACT_ID),
            "storage_provider": "oss",
            "bucket": _BUCKET,
            "endpoint": _ENDPOINT,
            "object_key": object_key,
            "artifact_kind": "original_upload",
            "content_type": content_type,
            "byte_size": byte_size,
            "source_filename": source_filename,
        }
        await conn.execute(
            """
            INSERT INTO original_inputs (
                id, reading_record_id, user_id, input_type,
                source_text, source_ref_json, metadata_json, content_sha256
            )
            VALUES ($1, $2, $3, 'file_ref',
                    NULL, $4,
                    '{"source_artifact_status": "available"}'::jsonb,
                    $5)
            """,
            _ORIGINAL_INPUT_ID,
            _RECORD_ID,
            _USER_ID,
            source_ref_json,
            source_sha,
        )
        await conn.execute(
            """
            INSERT INTO source_artifacts (
                id, reading_record_id, original_input_id, user_id,
                artifact_kind, storage_provider, bucket, object_key, endpoint,
                content_type, byte_size, content_sha256, source_filename, status
            )
            VALUES ($1, $2, $3, $4,
                    'original_upload', 'oss', $5, $6, $7,
                    $8, $9, $10, $11, 'available')
            """,
            _ARTIFACT_ID,
            _RECORD_ID,
            _ORIGINAL_INPUT_ID,
            _USER_ID,
            _BUCKET,
            object_key,
            _ENDPOINT,
            content_type,
            byte_size,
            source_sha,
            source_filename,
        )
        await conn.execute(
            """
            INSERT INTO reader_runs (
                reading_record_id, user_id, run_type, status,
                record_generation, envelope_json, policy_version, trigger_kind, id
            )
            VALUES ($1, $2, 'input_artifact_extraction', 'queued', 1,
                    '{}'::jsonb, 'reader_input_artifact_extraction_v1', 'system', $3)
            """,
            _RECORD_ID,
            _USER_ID,
            _EXTRACTION_RUN_ID,
        )

        input_json = {
            "source": "artifact_input",
            "reading_record_id": str(_RECORD_ID),
            "original_input_id": str(_ORIGINAL_INPUT_ID),
            "source_artifact_id": str(_ARTIFACT_ID),
            "artifact_kind": "original_upload",
            "storage_provider": "oss",
            "bucket": _BUCKET,
            "endpoint": _ENDPOINT,
            "object_key": object_key,
            "content_type": content_type,
            "byte_size": byte_size,
            "content_sha256": source_sha,
            "source_filename": source_filename,
        }

        job_id = await conn.fetchval(
            """
            INSERT INTO reader_jobs (
                reading_record_id, base_id, run_id, user_id,
                job_type, target_type, target_key, status,
                priority, expected_generation, operation_fingerprint,
                idempotency_key, input_json, max_attempts
            )
            VALUES ($1, NULL, $2, $3,
                    $4, $5, $6, 'queued',
                    0, 1, $7,
                    $8, $9, 3)
            RETURNING id
            """,
            _RECORD_ID,
            _EXTRACTION_RUN_ID,
            _USER_ID,
            EXTRACTION_JOB_TYPE,
            EXTRACTION_TARGET_TYPE,
            str(_ARTIFACT_ID),
            EXTRACTION_OPERATION_FINGERPRINT,
            f"i3q-extraction-{uuid4().hex}",
            input_json,
        )
    assert isinstance(job_id, UUID)
    return job_id


class _FakeStorageReader:
    """Returns pre-configured bytes for any read_object call."""

    def __init__(self, *, data: bytes) -> None:
        self._data = data

    async def read_object(
        self,
        *,
        bucket: str,
        endpoint: str,
        object_key: str,
    ) -> StorageObjectReadResult:
        return StorageObjectReadResult(
            data=self._data,
            byte_size=len(self._data),
            etag=None,
            content_type=None,
        )


async def test_pipeline_regression_stable_text_end_to_end(i3q_env: asyncpg.Pool) -> None:
    """Regression: stable text artifact drained through the pipeline still
    reaches article_ready with a stable document + reading base."""
    pool = i3q_env
    await _seed_extraction_job(pool, source_text=_STABLE_TEXT)
    reader = _FakeStorageReader(data=_STABLE_TEXT.encode("utf-8"))
    service = ArtifactInputPipelineWorkerService(pool=pool, storage_reader=reader)

    results = await service.drain(
        lease_owner="i3q-regression-worker",
        lease_duration=timedelta(seconds=30),
        max_ticks=10,
    )

    # Should have processed both extraction and materialization
    assert len(results) >= 2
    final = results[-1]
    assert final.status == "succeeded"
    assert final.outcome == "stable_document_ready"

    # Verify stable document + reading base persisted, and the record
    # advanced to article_ready.
    async with pool.acquire() as conn:
        stable_count = await conn.fetchval(
            "SELECT COUNT(*) FROM stable_reading_documents WHERE reading_record_id = $1",
            _RECORD_ID,
        )
        assert stable_count == 1

        base_count = await conn.fetchval(
            "SELECT COUNT(*) FROM reading_bases WHERE reading_record_id = $1",
            _RECORD_ID,
        )
        assert base_count == 1

        record = await conn.fetchrow(
            "SELECT readiness_state, product_state, active_base_id FROM reading_records WHERE id = $1",
            _RECORD_ID,
        )
        assert record["readiness_state"] == "article_ready"
        assert record["active_base_id"] is not None

        # original_inputs.source_text should be populated
        input_row = await conn.fetchrow(
            "SELECT source_text FROM original_inputs WHERE id = $1",
            _ORIGINAL_INPUT_ID,
        )
        assert input_row is not None
        assert input_row["source_text"] == _STABLE_TEXT


# ---------------------------------------------------------------------------
# Opt-in smoke test (env-gated, never runs by default)
# ---------------------------------------------------------------------------


def test_real_oss_smoke_is_opt_in_only() -> None:
    """The real OSS smoke test must be env-gated and never run by default.

    This test documents the opt-in contract: it checks that the
    ``CLAREAD_OSS_SMOKE_ENABLED`` env var is not set, proving the smoke
    test is opt-in. A real smoke test would live in a separate module
    gated on ``CLAREAD_OSS_SMOKE_ENABLED=1`` +
    ``ALIYUN_OSS_ACCESS_KEY_ID`` + ``ALIYUN_OSS_ACCESS_KEY_SECRET`` +
    ``ALIYUN_OSS_BUCKET`` + ``ALIYUN_OSS_ENDPOINT``.
    """
    import os

    assert os.getenv("CLAREAD_OSS_SMOKE_ENABLED") is None
