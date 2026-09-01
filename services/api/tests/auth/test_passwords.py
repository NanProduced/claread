"""AUTH-F2A: password normalization and Argon2id hashing primitive tests.

Behavior locked by this suite:
- NFC normalization, no trimming, no case changes;
- length 12-128 code points measured after NFC, no composition rules;
- Argon2id via argon2-cffi with random salt and needs_rehash on parameter
  drift;
- wrong passwords and corrupted hashes fail safely without leaking secrets;
- AUTH-F2A-R1: verify_password enforces the same 12-128 length policy as
  normalize_password even against a matching hash minted by an independent
  hasher, and JSON-escaped unpaired surrogates (not UTF-8 encodable) map to
  the stable domain error instead of leaking UnicodeEncodeError.
"""

from __future__ import annotations

import json
import unicodedata

import pytest
from argon2 import PasswordHasher
from argon2.low_level import Type, hash_secret

from app.services.auth.passwords import (
    _PASSWORD_HASHER,
    InvalidPasswordError,
    hash_password,
    normalize_password,
    verify_password,
)

pytestmark = [pytest.mark.chain_auth, pytest.mark.seam_pure_unit]

# Deliberately weaker than production parameters, used only to mint legacy
# hashes that must be flagged by needs_rehash.
_WEAK_HASHER = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)

# Independent hasher with its own defaults and no length policy, used only to
# mint matching hashes for out-of-policy passwords.
_INDEPENDENT_HASHER = PasswordHasher()


def _unpaired_surrogate_password() -> str:
    """12 unpaired surrogates via JSON escapes, as a JSON API would deliver."""
    return json.loads('"' + "\\ud800" * 12 + '"')


class TestNormalizePassword:
    def test_min_length_twelve_is_accepted(self) -> None:
        assert normalize_password("a" * 12) == "a" * 12

    def test_max_length_128_is_accepted(self) -> None:
        assert normalize_password("a" * 128) == "a" * 128

    def test_eleven_characters_rejected(self) -> None:
        with pytest.raises(InvalidPasswordError):
            normalize_password("a" * 11)

    def test_129_characters_rejected(self) -> None:
        with pytest.raises(InvalidPasswordError):
            normalize_password("a" * 129)

    def test_length_is_measured_after_nfc_normalization(self) -> None:
        # 12 raw code points whose NFC form collapses to 11 must be rejected.
        raw = "e\u0301" + "a" * 10
        assert len(raw) == 12
        assert len(unicodedata.normalize("NFC", raw)) == 11
        with pytest.raises(InvalidPasswordError):
            normalize_password(raw)

    def test_nfc_equivalent_forms_normalize_identically(self) -> None:
        composed = "é" + "a" * 11
        decomposed = "e\u0301" + "a" * 11
        assert normalize_password(composed) == normalize_password(decomposed) == composed

    def test_leading_and_trailing_spaces_are_preserved(self) -> None:
        raw = " " + "a" * 10 + " "
        assert normalize_password(raw) == raw

    def test_case_is_not_changed(self) -> None:
        raw = "AbCdEfGhIjKl"
        assert normalize_password(raw) == raw

    def test_unicode_password_accepted(self) -> None:
        raw = "密码" * 6  # 12 code points
        assert normalize_password(raw) == raw

    def test_error_message_is_stable_and_hides_password(self) -> None:
        with pytest.raises(InvalidPasswordError) as first:
            normalize_password("a" * 11)
        with pytest.raises(InvalidPasswordError) as second:
            normalize_password("b" * 129)
        assert str(first.value) == str(second.value)
        assert "a" * 11 not in str(first.value)
        assert "b" * 129 not in str(second.value)


class TestHashPassword:
    def test_hash_is_argon2id_encoded(self) -> None:
        assert hash_password("a" * 12).startswith("$argon2id$")

    def test_salt_is_random_per_hash(self) -> None:
        password = "correct horse battery"
        first = hash_password(password)
        second = hash_password(password)
        assert first != second
        assert verify_password(password, first).valid is True
        assert verify_password(password, second).valid is True

    def test_hash_does_not_embed_the_password(self) -> None:
        password = "secret-password-12"
        assert password not in hash_password(password)


class TestVerifyPassword:
    def test_correct_password_with_current_params(self) -> None:
        password = "correct horse battery"
        result = verify_password(password, hash_password(password))
        assert result.valid is True
        assert result.needs_rehash is False

    def test_wrong_password_fails_safely(self) -> None:
        digest = hash_password("correct horse battery")
        result = verify_password("wrong horse battery", digest)
        assert result.valid is False
        assert result.needs_rehash is False

    def test_nfc_decomposed_input_verifies_composed_hash(self) -> None:
        composed = "é" + "a" * 11
        decomposed = "e\u0301" + "a" * 11
        assert verify_password(decomposed, hash_password(composed)).valid is True

    def test_garbage_hash_fails_safely(self) -> None:
        result = verify_password("a" * 12, "not-a-valid-hash")
        assert result.valid is False
        assert result.needs_rehash is False

    def test_truncated_hash_fails_safely(self) -> None:
        digest = hash_password("a" * 12)
        result = verify_password("a" * 12, digest[:-8])
        assert result.valid is False
        assert result.needs_rehash is False

    def test_empty_hash_fails_safely(self) -> None:
        result = verify_password("a" * 12, "")
        assert result.valid is False
        assert result.needs_rehash is False

    def test_non_string_hash_fails_safely(self) -> None:
        result = verify_password("a" * 12, None)  # type: ignore[arg-type]
        assert result.valid is False
        assert result.needs_rehash is False

    def test_non_argon2id_hash_type_rejected(self) -> None:
        # argon2-cffi's PasswordHasher.verify derives the hash type from the
        # encoded string itself, so a valid argon2i hash of the correct
        # password must still be rejected explicitly.
        argon2i_digest = hash_secret(
            secret=("a" * 12).encode("utf-8"),
            salt=b"0123456789abcdef",
            time_cost=2,
            memory_cost=19456,
            parallelism=1,
            hash_len=32,
            type=Type.I,
        ).decode("ascii")
        result = verify_password("a" * 12, argon2i_digest)
        assert result.valid is False
        assert result.needs_rehash is False


class TestNeedsRehash:
    def test_weaker_parameters_flag_rehash(self) -> None:
        password = "a" * 12
        legacy_digest = _WEAK_HASHER.hash(password)
        result = verify_password(password, legacy_digest)
        assert result.valid is True
        assert result.needs_rehash is True

    def test_rehash_cycle_clears_rehash_flag(self) -> None:
        password = "a" * 12
        legacy_digest = _WEAK_HASHER.hash(password)
        assert verify_password(password, legacy_digest).needs_rehash is True
        upgraded_digest = hash_password(password)
        result = verify_password(password, upgraded_digest)
        assert result.valid is True
        assert result.needs_rehash is False


class TestArgon2idParameters:
    def test_parameters_meet_owasp_minimum_floor(self) -> None:
        # OWASP Password Storage Cheat Sheet: Argon2id with at least
        # 19 MiB memory, 2 iterations and 1 degree of parallelism.
        assert _PASSWORD_HASHER.memory_cost >= 19456
        assert _PASSWORD_HASHER.time_cost >= 2
        assert _PASSWORD_HASHER.parallelism >= 1


class TestVerifyPasswordLengthPolicy:
    """AUTH-F2A-R1: verify_password must enforce the 12-128 policy itself."""

    def test_eleven_codepoint_password_rejected_against_matching_hash(self) -> None:
        # An independent hasher (own defaults, no length policy) can mint a
        # matching hash for an out-of-policy password; verify_password must
        # still reject it instead of trusting the hash.
        password = "a" * 11
        digest = _INDEPENDENT_HASHER.hash(password)
        result = verify_password(password, digest)
        assert result.valid is False
        assert result.needs_rehash is False

    def test_129_codepoint_password_rejected_against_matching_hash(self) -> None:
        password = "a" * 129
        digest = _INDEPENDENT_HASHER.hash(password)
        result = verify_password(password, digest)
        assert result.valid is False
        assert result.needs_rehash is False


class TestUnpairedSurrogateRejection:
    """AUTH-F2A-R1: unpaired surrogates are not UTF-8 encodable."""

    def test_normalize_password_raises_stable_error(self) -> None:
        with pytest.raises(InvalidPasswordError):
            normalize_password(_unpaired_surrogate_password())

    def test_hash_password_raises_stable_error(self) -> None:
        with pytest.raises(InvalidPasswordError):
            hash_password(_unpaired_surrogate_password())

    def test_verify_password_fails_safely_without_raising(self) -> None:
        digest = hash_password("a" * 12)
        result = verify_password(_unpaired_surrogate_password(), digest)
        assert result.valid is False
        assert result.needs_rehash is False

    def test_error_hides_password_surrogate_and_dependency_text(self) -> None:
        surrogate = _unpaired_surrogate_password()
        with pytest.raises(InvalidPasswordError) as excinfo:
            normalize_password(surrogate)
        message = str(excinfo.value)
        assert surrogate not in message
        assert "\\ud800" not in message
        assert "\ud800" not in message
        # Dependency exception text from UnicodeEncodeError must not leak.
        assert "surrogates not allowed" not in message
        assert "codec" not in message
