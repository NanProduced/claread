"""Phone auth adapter.

The first production provider target is Alibaba Cloud Dypnsapi:
SendSmsVerifyCode / CheckSmsVerifyCode. Local development uses a mock code.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.config.settings import Settings, get_settings
from app.services.auth.identity import (
    IdentityBindResult,
    bind_identity_to_user,
    get_or_create_user_by_identity,
)

PHONE_PROVIDER = "phone"
PHONE_CLIENT_PLATFORM = "web"


class PhoneAuthError(Exception):
    """Phone auth provider or validation failed."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class PhoneCodeResult:
    normalized_phone: str
    message: str


class PhoneCodeProvider(Protocol):
    async def send_code(self, normalized_phone: str) -> PhoneCodeResult: ...

    async def verify_code(self, normalized_phone: str, code: str) -> PhoneCodeResult: ...


def normalize_phone(phone: str) -> str:
    """Normalize mainland China phone numbers to E.164-like +86 form."""
    cleaned = re.sub(r"[\s-]", "", phone)
    if cleaned.startswith("+86"):
        national = cleaned[3:]
    elif cleaned.startswith("86") and len(cleaned) == 13:
        national = cleaned[2:]
    else:
        national = cleaned

    if not re.fullmatch(r"1[3-9]\d{9}", national):
        raise PhoneAuthError("Invalid mainland China phone number")

    return f"+86{national}"


class MockPhoneCodeProvider:
    async def send_code(self, normalized_phone: str) -> PhoneCodeResult:
        return PhoneCodeResult(
            normalized_phone=normalized_phone,
            message="Mock verification code generated. Use 888888.",
        )

    async def verify_code(self, normalized_phone: str, code: str) -> PhoneCodeResult:
        settings = get_settings()
        expected_code = settings.phone_mock_verification_code
        if code != expected_code:
            raise PhoneAuthError("Invalid verification code")

        return PhoneCodeResult(
            normalized_phone=normalized_phone,
            message="Mock verification code accepted.",
        )


class AliyunDypnsapiPhoneCodeProvider:
    """Alibaba Cloud Dypnsapi provider for server-side phone verification."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def send_code(self, normalized_phone: str) -> PhoneCodeResult:
        client = self._create_client()
        request = self._build_send_request(normalized_phone)
        response = await asyncio.to_thread(client.send_sms_verify_code, request)
        self._ensure_api_success(response)

        return PhoneCodeResult(
            normalized_phone=normalized_phone,
            message="Verification code sent.",
        )

    async def verify_code(self, normalized_phone: str, code: str) -> PhoneCodeResult:
        client = self._create_client()
        request = self._build_check_request(normalized_phone, code)
        response = await asyncio.to_thread(client.check_sms_verify_code, request)
        body = self._ensure_api_success(response)
        verify_result = _read_value(
            _read_value(body, "model", "Model"),
            "verify_result",
            "VerifyResult",
        )

        if verify_result != "PASS":
            raise PhoneAuthError("Invalid verification code")

        return PhoneCodeResult(
            normalized_phone=normalized_phone,
            message="Verification code accepted.",
        )

    def _create_client(self) -> Any:
        try:
            from alibabacloud_dypnsapi20170525.client import Client as DypnsapiClient
            from alibabacloud_tea_openapi import models as open_api_models
        except ImportError as exc:
            raise PhoneAuthError(
                "Aliyun Dypnsapi SDK is not installed",
                status_code=500,
            ) from exc

        access_key_id = self.settings.aliyun_dypnsapi_access_key_id or os.getenv(
            "ALIBABA_CLOUD_ACCESS_KEY_ID", ""
        )
        access_key_secret = self.settings.aliyun_dypnsapi_access_key_secret or os.getenv(
            "ALIBABA_CLOUD_ACCESS_KEY_SECRET", ""
        )

        if not access_key_id or not access_key_secret:
            raise PhoneAuthError(
                "Aliyun Dypnsapi access key is not configured",
                status_code=500,
            )

        config = open_api_models.Config(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            endpoint=self.settings.aliyun_dypnsapi_endpoint,
            region_id=self.settings.aliyun_dypnsapi_region_id,
        )
        return DypnsapiClient(config)

    def _build_send_request(self, normalized_phone: str) -> Any:
        from alibabacloud_dypnsapi20170525 import models as dypnsapi_models

        if not self.settings.aliyun_dypnsapi_sign_name:
            raise PhoneAuthError(
                "Aliyun Dypnsapi sign name is not configured",
                status_code=500,
            )

        template_param = json.dumps(
            {
                "code": "##code##",
                "min": str(self.settings.aliyun_dypnsapi_code_ttl_minutes),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

        return dypnsapi_models.SendSmsVerifyCodeRequest(
            country_code="86",
            phone_number=_to_mainland_national_number(normalized_phone),
            sign_name=self.settings.aliyun_dypnsapi_sign_name,
            template_code=self.settings.aliyun_dypnsapi_login_template_code,
            template_param=template_param,
            code_length=self.settings.aliyun_dypnsapi_code_length,
            code_type=1,
            duplicate_policy=1,
            interval=self.settings.aliyun_dypnsapi_send_interval_seconds,
            valid_time=self.settings.aliyun_dypnsapi_code_ttl_minutes * 60,
            return_verify_code=False,
        )

    def _build_check_request(self, normalized_phone: str, code: str) -> Any:
        from alibabacloud_dypnsapi20170525 import models as dypnsapi_models

        return dypnsapi_models.CheckSmsVerifyCodeRequest(
            country_code="86",
            phone_number=_to_mainland_national_number(normalized_phone),
            verify_code=code,
        )

    def _ensure_api_success(self, response: Any) -> Any:
        body = _read_value(response, "body", "Body")
        code = _read_value(body, "code", "Code")
        success = _read_value(body, "success", "Success")

        if code != "OK" or success is False:
            message = _read_value(body, "message", "Message") or "Aliyun Dypnsapi request failed"
            raise PhoneAuthError(
                str(message),
                status_code=_aliyun_error_status(code),
            )

        return body


def _to_mainland_national_number(normalized_phone: str) -> str:
    return normalized_phone[3:] if normalized_phone.startswith("+86") else normalized_phone


def _read_value(source: Any, *names: str) -> Any:
    if source is None:
        return None

    if isinstance(source, dict):
        for name in names:
            if name in source:
                return source[name]
        return None

    for name in names:
        if hasattr(source, name):
            return getattr(source, name)

    return None


def _aliyun_error_status(code: Any) -> int:
    if code in {"BUSINESS_LIMIT_CONTROL", "FREQUENCY_FAIL"}:
        return 429
    if code in {"MOBILE_NUMBER_ILLEGAL", "INVALID_PARAMETERS"}:
        return 400
    return 502


def get_phone_code_provider() -> PhoneCodeProvider:
    provider = get_settings().phone_auth_provider
    if provider == "mock":
        return MockPhoneCodeProvider()
    if provider == "aliyun_dypnsapi":
        return AliyunDypnsapiPhoneCodeProvider()

    raise PhoneAuthError(f"Unsupported phone auth provider: {provider}", status_code=500)


async def request_phone_code(phone: str) -> PhoneCodeResult:
    normalized_phone = normalize_phone(phone)
    return await get_phone_code_provider().send_code(normalized_phone)


async def verify_phone_code(phone: str, code: str) -> PhoneCodeResult:
    normalized_phone = normalize_phone(phone)
    return await get_phone_code_provider().verify_code(normalized_phone, code)


async def get_or_create_user_by_phone(normalized_phone: str) -> UUID:
    result = await get_or_create_user_by_identity(
        provider=PHONE_PROVIDER,
        provider_user_id=normalized_phone,
        auth_payload={"verified_by": get_settings().phone_auth_provider},
    )
    return result.user_id


async def bind_phone_to_user(*, user_id: UUID, normalized_phone: str) -> IdentityBindResult:
    return await bind_identity_to_user(
        user_id=user_id,
        provider=PHONE_PROVIDER,
        provider_user_id=normalized_phone,
        auth_payload={"verified_by": get_settings().phone_auth_provider},
    )
