"""手机号认证 provider 测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import pytest

from app.config.settings import Settings
from app.services.auth.phone import (
    AliyunDypnsapiPhoneCodeProvider,
    MockPhoneCodeProvider,
    PhoneAuthError,
    bind_phone_to_user,
    get_or_create_user_by_phone,
    normalize_phone,
)


def test_normalize_phone_accepts_mainland_variants() -> None:
    assert normalize_phone("13800138000") == "+8613800138000"
    assert normalize_phone("86 13800138000") == "+8613800138000"
    assert normalize_phone("+86-13800138000") == "+8613800138000"


def test_normalize_phone_rejects_invalid_number() -> None:
    with pytest.raises(PhoneAuthError):
        normalize_phone("12800138000")


async def test_mock_provider_accepts_configured_code() -> None:
    provider = MockPhoneCodeProvider()

    result = await provider.verify_code("+8613800138000", "888888")

    assert result.normalized_phone == "+8613800138000"


async def test_mock_provider_rejects_wrong_code() -> None:
    provider = MockPhoneCodeProvider()

    with pytest.raises(PhoneAuthError):
        await provider.verify_code("+8613800138000", "000000")


async def test_get_or_create_user_by_phone_does_not_pass_unionid() -> None:
    user_id = UUID("11111111-2222-4333-8444-555555555555")

    with patch("app.services.auth.phone.get_or_create_user_by_identity") as mock_get:
        mock_get.return_value = SimpleNamespace(user_id=user_id)

        result = await get_or_create_user_by_phone("+8613800138000")

    assert result == user_id
    assert mock_get.call_args.kwargs["provider"] == "phone"
    assert mock_get.call_args.kwargs["provider_user_id"] == "+8613800138000"
    assert "unionid" not in mock_get.call_args.kwargs


async def test_bind_phone_to_user_does_not_pass_unionid() -> None:
    user_id = UUID("22222222-3333-4444-8555-666666666666")

    with patch("app.services.auth.phone.bind_identity_to_user") as mock_bind:
        mock_bind.return_value = "created"

        result = await bind_phone_to_user(
            user_id=user_id,
            normalized_phone="+8613800138000",
        )

    assert result == "created"
    assert mock_bind.call_args.kwargs["provider"] == "phone"
    assert mock_bind.call_args.kwargs["provider_user_id"] == "+8613800138000"
    assert "unionid" not in mock_bind.call_args.kwargs


async def test_aliyun_provider_sends_code_with_success_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = object()
    response = SimpleNamespace(body=SimpleNamespace(code="OK", success=True, message="成功"))
    calls: list[object] = []

    class FakeClient:
        def send_sms_verify_code(self, send_request: object) -> object:
            calls.append(send_request)
            return response

    provider = AliyunDypnsapiPhoneCodeProvider(Settings())
    monkeypatch.setattr(provider, "_create_client", lambda: FakeClient())
    monkeypatch.setattr(provider, "_build_send_request", lambda normalized_phone: request)

    result = await provider.send_code("+8613800138000")

    assert result.normalized_phone == "+8613800138000"
    assert calls == [request]


async def test_aliyun_provider_accepts_pass_verify_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = object()
    response = SimpleNamespace(
        body=SimpleNamespace(
            code="OK",
            success=True,
            model=SimpleNamespace(verify_result="PASS"),
        )
    )

    class FakeClient:
        def check_sms_verify_code(self, check_request: object) -> object:
            assert check_request is request
            return response

    provider = AliyunDypnsapiPhoneCodeProvider(Settings())
    monkeypatch.setattr(provider, "_create_client", lambda: FakeClient())
    monkeypatch.setattr(provider, "_build_check_request", lambda normalized_phone, code: request)

    result = await provider.verify_code("+8613800138000", "123456")

    assert result.normalized_phone == "+8613800138000"


async def test_aliyun_provider_rejects_unknown_verify_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(
        body=SimpleNamespace(
            code="OK",
            success=True,
            model=SimpleNamespace(verify_result="UNKNOWN"),
        )
    )

    class FakeClient:
        def check_sms_verify_code(self, check_request: object) -> object:
            return response

    provider = AliyunDypnsapiPhoneCodeProvider(Settings())
    monkeypatch.setattr(provider, "_create_client", lambda: FakeClient())
    monkeypatch.setattr(provider, "_build_check_request", lambda normalized_phone, code: object())

    with pytest.raises(PhoneAuthError):
        await provider.verify_code("+8613800138000", "123456")
