"""AUTH-F2C: offline password safety evaluation behavior."""

from __future__ import annotations

import httpx
import pytest

from app.services.auth.password_safety import evaluate_password_safety
from app.services.auth.passwords import InvalidPasswordError

pytestmark = [pytest.mark.chain_auth, pytest.mark.seam_pure_unit]

_SAFE_PASSWORD = "CorrectHorseBatteryStaple!"
_SAFE_SHA1 = "03584FFAFB57D45D51931E72438C7130C3A7660B"


async def test_common_password_is_rejected_without_hibp_request() -> None:
    requests = 0

    def unexpected_request(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        raise AssertionError("common passwords must be rejected locally")

    result = await evaluate_password_safety(
        "PaSsWoRd1234",
        transport=httpx.MockTransport(unexpected_request),
    )

    assert result == "common"
    assert requests == 0


async def test_hibp_request_discloses_only_prefix_and_required_headers() -> None:
    requests: list[httpx.Request] = []

    def miss(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=f"{'A' * 35}:7\r\n")

    result = await evaluate_password_safety(
        _SAFE_PASSWORD,
        transport=httpx.MockTransport(miss),
    )

    assert result == "ok"
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert str(request.url) == "https://api.pwnedpasswords.com/range/03584"
    assert request.headers["user-agent"] == "Claread"
    assert request.headers["add-padding"] == "true"
    assert request.extensions["timeout"] == {
        "connect": 3.0,
        "read": 3.0,
        "write": 3.0,
        "pool": 3.0,
    }
    assert "authorization" not in request.headers
    assert "api-key" not in request.headers
    assert "x-api-key" not in request.headers
    disclosed = " ".join(
        [
            str(request.url),
            *request.headers.values(),
            request.content.decode(),
        ]
    )
    assert _SAFE_PASSWORD not in disclosed
    assert _SAFE_SHA1 not in disclosed
    assert _SAFE_SHA1[5:] not in disclosed


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (f"{_SAFE_SHA1[5:]}:42\r\n", "compromised"),
        (f"{_SAFE_SHA1[5:].lower()}:1\r\n", "compromised"),
        (f"{_SAFE_SHA1[5:]}:0\r\n", "ok"),
        (f"{'B' * 35}:9\r\n", "ok"),
    ],
)
async def test_hibp_suffix_is_compared_locally(
    body: str,
    expected: str,
) -> None:
    requests = 0

    def response(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, text=body, request=request)

    result = await evaluate_password_safety(
        _SAFE_PASSWORD,
        transport=httpx.MockTransport(response),
    )

    assert result == expected
    assert requests == 1


@pytest.mark.parametrize(
    "failure",
    ["timeout", "request_error", "http_error", "malformed"],
)
async def test_hibp_failures_fail_open_without_retry(failure: str) -> None:
    requests = 0

    def fail(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("simulated", request=request)
        if failure == "request_error":
            raise httpx.ConnectError("simulated", request=request)
        if failure == "http_error":
            return httpx.Response(503, request=request)
        return httpx.Response(200, text="malformed", request=request)

    result = await evaluate_password_safety(
        _SAFE_PASSWORD,
        transport=httpx.MockTransport(fail),
    )

    assert result == "hibp_unavailable"
    assert requests == 1


async def test_invalid_password_uses_existing_error_without_hibp_request() -> None:
    raw = "too-short"
    requests = 0

    def unexpected_request(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        raise AssertionError("invalid passwords must stop before HIBP")

    with pytest.raises(InvalidPasswordError) as exc_info:
        await evaluate_password_safety(
            raw,
            transport=httpx.MockTransport(unexpected_request),
        )

    assert requests == 0
    assert raw not in str(exc_info.value)
    assert raw not in repr(exc_info.value)


async def test_result_and_logs_hide_all_secret_material(
    caplog: pytest.LogCaptureFixture,
) -> None:
    breach_count = "987654321"

    def compromised(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=f"{_SAFE_SHA1[5:]}:{breach_count}\r\n",
            request=request,
        )

    with caplog.at_level("DEBUG"):
        result = await evaluate_password_safety(
            _SAFE_PASSWORD,
            transport=httpx.MockTransport(compromised),
        )

    public_surface = " ".join(
        [repr(result), *(record.getMessage() for record in caplog.records)]
    )
    assert result == "compromised"
    for secret in (_SAFE_PASSWORD, _SAFE_SHA1, _SAFE_SHA1[:5], breach_count):
        assert secret not in public_surface
