"""AUTH-CLOSEOUT-B phone auth exit guard.

Locks the API-side phone auth exit (RED on clean main, GREEN after the
exit commit):

1. The three phone-auth routes are gone (POST -> 404).
2. ``app/services/auth/phone.py`` is physically deleted with no importers.
3. Phone schemas and phone service exports are absent from the auth surface.
4. ``PHONE_*`` / ``ALIYUN_DYPNSAPI_*`` config is gone from settings and
   ``.env.example``.
5. The Dypnsapi dependency is gone from ``pyproject.toml`` and ``uv.lock``.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

API_ROOT = Path(__file__).resolve().parents[2]

PHONE_PATHS = (
    "/auth/phone/request-code",
    "/auth/phone/verify-code",
    "/auth/phone/bind",
)

# Phone-auth identifiers that must never reappear in production code.
PHONE_AUTH_IDENTIFIERS = (
    "phone_auth_provider",
    "phone_mock_verification_code",
    "aliyun_dypnsapi_access_key_id",
    "aliyun_dypnsapi_access_key_secret",
    "aliyun_dypnsapi_endpoint",
    "aliyun_dypnsapi_region_id",
    "aliyun_dypnsapi_sign_name",
    "aliyun_dypnsapi_login_template_code",
    "aliyun_dypnsapi_code_ttl_minutes",
    "aliyun_dypnsapi_code_length",
    "aliyun_dypnsapi_send_interval_seconds",
    "PhoneAuthError",
    "PhoneCodeRequest",
    "PhoneVerifyRequest",
    "PhoneBindRequest",
    "PhoneCodeResponse",
    "PhoneCodeResult",
    "PhoneCodeProvider",
    "MockPhoneCodeProvider",
    "AliyunDypnsapiPhoneCodeProvider",
    "request_phone_code",
    "verify_phone_code",
    "get_or_create_user_by_phone",
    "bind_phone_to_user",
    "normalize_phone",
)

PHONE_SCHEMA_NAMES = (
    "PhoneCodeRequest",
    "PhoneVerifyRequest",
    "PhoneBindRequest",
    "PhoneCodeResponse",
)

PHONE_SERVICE_EXPORTS = (
    "PhoneAuthError",
    "request_phone_code",
    "verify_phone_code",
    "get_or_create_user_by_phone",
    "bind_phone_to_user",
)

BANNED_MODULES = ("app.services.auth.phone",)


def _python_files_under(path: Path) -> Iterator[Path]:
    for candidate in sorted(path.rglob("*.py")):
        if "__pycache__" in candidate.parts:
            continue
        yield candidate


def test_phone_auth_routes_are_absent() -> None:
    client = TestClient(app)
    for path in PHONE_PATHS:
        response = client.post(path, json={})
        assert response.status_code == 404, f"{path} must be removed (got {response.status_code})"


def test_phone_service_module_is_deleted() -> None:
    assert not (API_ROOT / "app" / "services" / "auth" / "phone.py").exists()


def test_phone_module_has_no_importers() -> None:
    importers: list[str] = []
    for path in _python_files_under(API_ROOT / "app"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in BANNED_MODULES:
                importers.append(f"{path.relative_to(API_ROOT)}: {node.module}")
    assert importers == [], "phone auth module still has importers:\n" + "\n".join(importers)


def test_phone_auth_identifiers_absent_from_production_code() -> None:
    offenders: list[str] = []
    for path in _python_files_under(API_ROOT / "app"):
        text = path.read_text(encoding="utf-8")
        for ident in PHONE_AUTH_IDENTIFIERS:
            if re.search(rf"\b{re.escape(ident)}\b", text):
                offenders.append(f"{path.relative_to(API_ROOT)}: {ident}")
    assert offenders == [], "phone auth identifiers reappeared:\n" + "\n".join(offenders)


def test_phone_schemas_and_exports_are_absent() -> None:
    schema_source = (API_ROOT / "app" / "schemas" / "auth.py").read_text(encoding="utf-8")
    for name in PHONE_SCHEMA_NAMES:
        assert name not in schema_source, f"{name} must be removed from app/schemas/auth.py"

    services_init = (API_ROOT / "app" / "services" / "auth" / "__init__.py").read_text(
        encoding="utf-8"
    )
    for name in PHONE_SERVICE_EXPORTS:
        assert (
            name not in services_init
        ), f"{name} must be removed from app/services/auth/__init__.py"


def test_phone_config_absent_from_settings_and_env_example() -> None:
    settings_source = (API_ROOT / "app" / "config" / "settings.py").read_text(encoding="utf-8")
    env_example = (API_ROOT / ".env.example").read_text(encoding="utf-8")
    for token in ("PHONE_AUTH_PROVIDER", "PHONE_MOCK_VERIFICATION_CODE", "ALIYUN_DYPNSAPI"):
        assert token not in settings_source
        assert token not in env_example
    assert "phone_auth_provider" not in settings_source


def test_dypnsapi_dependency_absent() -> None:
    pyproject = (API_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lock = (API_ROOT / "uv.lock").read_text(encoding="utf-8")
    assert "dypnsapi" not in pyproject.lower()
    assert "dypnsapi" not in lock.lower()