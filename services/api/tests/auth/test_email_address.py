"""AUTH-F2A: email address normalization primitive tests.

Behavior locked by this suite:
- syntax / Unicode / IDNA normalization delegated to email-validator with
  ``check_deliverability=False`` (no DNS, no network);
- ``+tag``, Gmail dots and local-part case are preserved (no provider-specific
  rewriting);
- invalid input maps to a stable domain error without leaking the input or
  dependency exception text.
"""

from __future__ import annotations

import socket

import pytest

from app.services.auth.email_address import (
    InvalidEmailAddressError,
    normalize_email_address,
)

pytestmark = [pytest.mark.chain_auth, pytest.mark.seam_pure_unit]


class TestNormalizeEmailAddress:
    def test_ascii_address_lowercases_domain_and_preserves_local_part(self) -> None:
        assert normalize_email_address("User@Example.COM") == "User@example.com"

    def test_plus_tag_is_preserved(self) -> None:
        assert normalize_email_address("user+tag@gmail.com") == "user+tag@gmail.com"

    def test_gmail_dots_are_preserved(self) -> None:
        assert normalize_email_address("first.last@gmail.com") == "first.last@gmail.com"

    def test_unicode_local_part_is_preserved(self) -> None:
        assert normalize_email_address("用户名@example.com") == "用户名@example.com"

    def test_idn_domain_is_normalized_to_lowercase_unicode(self) -> None:
        assert normalize_email_address("user@Bücher.example") == "user@bücher.example"

    def test_punycode_and_unicode_domains_normalize_identically(self) -> None:
        # Identity-string stability: both encodings of the same domain must
        # map to the same normalized address.
        assert normalize_email_address("user@xn--bcher-kva.example") == (
            normalize_email_address("user@bücher.example")
        )

    def test_idn_address_with_plus_tag(self) -> None:
        assert normalize_email_address("my+address@我的邮件.中国") == (
            "my+address@我的邮件.中国"
        )


class TestInvalidEmailAddress:
    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "no-at-sign",
            "@example.com",
            "user@",
            "user@@example.com",
            "user @example.com",
            "user@example",
            "user@[127.0.0.1]",
        ],
    )
    def test_invalid_input_raises_domain_error(self, raw: str) -> None:
        with pytest.raises(InvalidEmailAddressError):
            normalize_email_address(raw)

    def test_non_string_input_raises_domain_error(self) -> None:
        with pytest.raises(InvalidEmailAddressError):
            normalize_email_address(None)  # type: ignore[arg-type]

    def test_error_message_is_stable_across_inputs(self) -> None:
        with pytest.raises(InvalidEmailAddressError) as first:
            normalize_email_address("a@@example.com")
        with pytest.raises(InvalidEmailAddressError) as second:
            normalize_email_address("user@[127.0.0.1]")
        assert str(first.value) == str(second.value)

    def test_error_does_not_expose_input_or_dependency_text(self) -> None:
        marker = "secret-leaked-marker"
        with pytest.raises(InvalidEmailAddressError) as excinfo:
            normalize_email_address(f"{marker}@@example.com")
        message = str(excinfo.value)
        assert marker not in message
        assert "@" not in message
        assert "must have an @-sign" not in message


class TestZeroNetwork:
    def test_normalization_never_opens_a_socket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _no_socket(*args: object, **kwargs: object) -> socket.socket:
            raise AssertionError("normalize_email_address must not create sockets")

        monkeypatch.setattr(socket, "socket", _no_socket)
        assert normalize_email_address("user@example.com") == "user@example.com"
