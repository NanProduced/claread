"""Run manifest V2 audit contract tests.

Spec: Actual Fixture Capture 与 Manifest Version 闭合.

This file covers two of the 9 required audit-contract scenarios:

* Scenario 4 — new manifest identity map missing/empty/partial/extra
  MUST be rejected at parse time (``corrupt_manifest``). V2 manifests
  have NO legacy bypass — selection is by EXPLICIT
  ``audit_contract_version``, NOT by empty-dict guessing.
* Scenario 7 — ``retry_policy`` / ``retry_headroom`` JSON round-trip
  is strictly typed. V2 Rules 18e/18f/18g/18h are enforced at parse
  time.

The other audit-contract scenarios are covered elsewhere:

* Scenario 1 (preflight == actual pass), 2 (monkeypatch mismatch
  blocked), 3 (runtime exception → null + incomplete) — covered in
  ``services/api/tests/test_reader_record_ask_real_llm_eval.py``
  because they exercise ``_run_one_case`` and
  ``_compute_preflight_runtime_fixture_fingerprint``.
* Scenario 5 (synthetic real_phase1 missing expected → builder=0) —
  covered in
  ``evals/tests/test_reader_record_ask_runtime_fixture_identity.py``
  by ``test_synthetic_real_phase1_missing_expected_returns_mismatch``.
* Scenario 6 (all 10 real_phase1 cases have expected identity) —
  blocked: 6 BBC cases require operator DB access to populate
  ``expected_runtime_fixture_fingerprint``. The 4 synthetic cases are
  already populated; documented as a known limitation in the report.
* Scenario 8 (old manifest only via explicit version → compat) —
  covered in
  ``evals/tests/test_reader_record_ask_runtime_fixture_identity.py``
  by ``test_pre_v2_manifest_backwards_compat`` and
  ``test_v1_explicit_version_skips_three_layer_check``.
* Scenario 9 (full test suite pass) — verification step (ruff + git
  diff --check + pytest).

No real LLM / provider calls. All tests are deterministic.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest

from claread_eval.reader_record_ask.run_manifest import (
    AUDIT_CONTRACT_VERSION_V1,
    AUDIT_CONTRACT_VERSION_V2,
    MANIFEST_SCHEMA_VERSION,
    ReaderRecordAskRunManifest,
    RunManifestError,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_FP_A = "a" * 64
_VALID_FP_B = "b" * 64
_VALID_FP_C = "c" * 64


# ---------------------------------------------------------------------------
# Helpers — build a minimal valid V2 manifest dict
# ---------------------------------------------------------------------------


def _base_valid_v2_manifest_dict() -> dict[str, Any]:
    """Build a minimal VALID V2 manifest dict.

    All V2 Rules (18a-18h) pass:
    - ``audit_contract_version == "r4-a4-2r3"``
    - ``runtime_fixture_identities`` covers all planned cases
    - keys exactly equal ``planned_run_indices.keys()``
    - each value is a strict 64-char lowercase hex SHA-256
    - ``planned_logical_runs == planned_count``
    - ``retry_policy`` has ``tool_max_retries`` / ``output_max_retries``
      as non-negative ints
    - ``retry_headroom`` is a non-negative int (NOT null)
    - ``request_cap`` is a non-negative int (NOT null)
    - ``retry_headroom == request_cap - planned_logical_runs``
    """
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "run_id": "phase1-v2-test",
        "phase": 1,
        "dataset_id": "test-dataset",
        "dataset_schema_version": "test-schema-v1",
        "dataset_content_sha256": _VALID_FP_A,
        "status": "completed",
        "planned_run_indices": {
            "case-a": [0, 1, 2],
            "case-b": [0, 1, 2],
        },
        "completed_run_indices": {
            "case-a": [0, 1, 2],
            "case-b": [0, 1, 2],
        },
        "remaining_run_indices": {},
        "executed_requests": 6,
        "executed_tokens": 1000,
        "stop_reason": None,
        "runtime_fixture_identities": {
            "case-a": _VALID_FP_A,
            "case-b": _VALID_FP_B,
        },
        "planned_logical_runs": 6,
        "request_cap": 40,
        "token_cap": 200_000,
        "retry_policy": {
            "tool_max_retries": 1,
            "output_max_retries": 2,
        },
        "retry_headroom": 34,  # 40 - 6
        "audit_contract_version": AUDIT_CONTRACT_VERSION_V2,
    }


def _serialize_and_parse(manifest_dict: dict[str, Any]) -> ReaderRecordAskRunManifest:
    """Serialize the dict to JSON and parse via ``from_json``.

    This exercises the full parse-time validation pipeline
    (``_parse_and_validate_manifest_dict`` → ``from_json``).
    """
    return ReaderRecordAskRunManifest.from_json(
        json.dumps(manifest_dict, ensure_ascii=False)
    )


# ---------------------------------------------------------------------------
# Scenario 4: V2 manifest identity map missing/empty/partial/extra
# ---------------------------------------------------------------------------


class TestScenario4V2IdentityMapStrictness:
    """Scenario 4: V2 manifests MUST carry a complete
    identity map. Missing / empty / partial / extra identity maps are
    rejected at parse time (``corrupt_manifest``). There is NO legacy
    bypass for V2 — selection is by EXPLICIT
    ``audit_contract_version``, NOT by empty-dict guessing.
    """

    def test_valid_v2_manifest_parses(self) -> None:
        """Sanity check: the base valid V2 manifest dict parses
        successfully. This proves the helper is a valid starting point
        for the negative tests below."""
        manifest = _serialize_and_parse(_base_valid_v2_manifest_dict())
        assert manifest.audit_contract_version == AUDIT_CONTRACT_VERSION_V2
        assert manifest.runtime_fixture_identities == {
            "case-a": _VALID_FP_A,
            "case-b": _VALID_FP_B,
        }

    def test_v2_missing_runtime_fixture_identities_field(self) -> None:
        """V2 manifest with NO ``runtime_fixture_identities`` field →
        ``corrupt_manifest`` (Rule 18a). The field is REQUIRED for V2.
        """
        d = _base_valid_v2_manifest_dict()
        del d["runtime_fixture_identities"]
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)

    def test_v2_empty_runtime_fixture_identities(self) -> None:
        """V2 manifest with ``runtime_fixture_identities={}`` →
        ``corrupt_manifest`` (Rule 18a). V2 requires coverage of every
        planned case — an empty map is NOT legacy (legacy is selected
        by EXPLICIT version, NOT by empty-dict guessing)."""
        d = _base_valid_v2_manifest_dict()
        d["runtime_fixture_identities"] = {}
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)

    def test_v2_partial_identity_map_missing_one_case(self) -> None:
        """V2 manifest with identity map missing one planned case →
        ``corrupt_manifest`` (Rule 18b: keys MUST exactly equal
        ``planned_run_indices.keys()``)."""
        d = _base_valid_v2_manifest_dict()
        # Remove one case from the identity map — keys no longer match.
        del d["runtime_fixture_identities"]["case-b"]
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)

    def test_v2_partial_identity_map_empty_value(self) -> None:
        """V2 manifest with one identity value being empty string →
        ``corrupt_manifest`` (Rule 18c: each value MUST be strict
        64-char lowercase hex)."""
        d = _base_valid_v2_manifest_dict()
        d["runtime_fixture_identities"]["case-b"] = ""
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)

    def test_v2_extra_identity_map_extra_case(self) -> None:
        """V2 manifest with identity map containing an EXTRA case not
        in ``planned_run_indices`` → ``corrupt_manifest`` (Rule 18b:
        keys MUST exactly equal ``planned_run_indices.keys()`` — no
        missing AND no extra)."""
        d = _base_valid_v2_manifest_dict()
        d["runtime_fixture_identities"]["case-foreign"] = _VALID_FP_C
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)

    def test_v2_identity_value_uppercase_hex_rejected(self) -> None:
        """V2 manifest with an identity value containing uppercase hex
        → ``corrupt_manifest`` (Rule 18c: strict LOWERCASE hex)."""
        d = _base_valid_v2_manifest_dict()
        d["runtime_fixture_identities"]["case-b"] = "B" * 64
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)

    def test_v2_identity_value_wrong_length_rejected(self) -> None:
        """V2 manifest with an identity value of wrong length →
        ``corrupt_manifest`` (Rule 18c: strict 64-char)."""
        d = _base_valid_v2_manifest_dict()
        d["runtime_fixture_identities"]["case-b"] = "b" * 63
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)

    def test_v2_identity_value_non_hex_rejected(self) -> None:
        """V2 manifest with an identity value containing non-hex chars
        → ``corrupt_manifest`` (Rule 18c)."""
        d = _base_valid_v2_manifest_dict()
        d["runtime_fixture_identities"]["case-b"] = "z" * 64
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)

    def test_v2_planned_logical_runs_mismatch_rejected(self) -> None:
        """V2 manifest with ``planned_logical_runs`` != sum of
        ``planned_run_indices`` list lengths → ``corrupt_manifest``
        (Rule 18d)."""
        d = _base_valid_v2_manifest_dict()
        # Set planned_logical_runs to a wrong value (5 instead of 6).
        d["planned_logical_runs"] = 5
        # Rule 18h will also fire because retry_headroom (34) !=
        # request_cap (40) - planned_logical_runs (5) = 35. Either
        # way, this is corrupt.
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)

    def test_v1_explicit_version_with_empty_identity_map_passes(self) -> None:
        """A V1 manifest (explicit
        ``audit_contract_version="r4-a4-2r2"``) with an empty identity
        map parses successfully — V1 is selected by EXPLICIT version,
        NOT by empty-dict guessing.

        This proves V1 backwards-compat is preserved: V1 manifests may
        carry an empty/partial identity map. The three-layer check in
        the aggregate is skipped for V1 (verified in
        ``test_reader_record_ask_runtime_fixture_identity.py``).
        """
        d = _base_valid_v2_manifest_dict()
        d["audit_contract_version"] = AUDIT_CONTRACT_VERSION_V1
        # V1 does not enforce V2 strict rules — empty identity map OK.
        d["runtime_fixture_identities"] = {}
        # V1 also does not enforce V2 retry_policy/retry_headroom
        # rules, but the dataclass field types still must be valid.
        # Leave retry_policy as dict (V1 accepts dict OR "default"
        # string).
        manifest = _serialize_and_parse(d)
        assert manifest.audit_contract_version == AUDIT_CONTRACT_VERSION_V1
        assert manifest.runtime_fixture_identities == {}

    def test_v1_none_version_with_empty_identity_map_passes(self) -> None:
        """A manifest with
        ``audit_contract_version=None`` (no field in JSON) and an empty
        identity map parses successfully — V1 default.

        This proves the absence of ``audit_contract_version`` is
        treated as V1 (legacy), and V1 skips the V2 strict contract.
        """
        d = _base_valid_v2_manifest_dict()
        del d["audit_contract_version"]
        d["runtime_fixture_identities"] = {}
        manifest = _serialize_and_parse(d)
        assert manifest.audit_contract_version is None
        assert manifest.runtime_fixture_identities == {}

    def test_v2_unknown_audit_contract_version_rejected(self) -> None:
        """An unknown ``audit_contract_version`` (not
        V1, not V2, not None) → ``corrupt_manifest`` (Rule 17).

        Forward / backward incompatibility is fail-closed: a future
        V3 string is NOT silently treated as V1 or V2.
        """
        d = _base_valid_v2_manifest_dict()
        d["audit_contract_version"] = "r4-a4-2r4"  # unknown future version
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)


# ---------------------------------------------------------------------------
# Scenario 7: retry_policy / retry_headroom JSON round-trip strict
# ---------------------------------------------------------------------------


class TestScenario7RetryPolicyRoundTrip:
    """Scenario 7: ``retry_policy`` (typed dict) and
    ``retry_headroom`` (non-negative int) round-trip through
    JSON serialization strictly. V2 Rules 18e/18f/18g/18h enforced at
    parse time.
    """

    def test_v2_retry_policy_round_trip_strict(self) -> None:
        """A valid V2 ``retry_policy`` dict round-trips through
        ``to_json`` / ``from_json`` exactly. The typed contract is
        preserved: ``tool_max_retries`` and ``output_max_retries``
        are non-negative ints."""
        manifest = _serialize_and_parse(_base_valid_v2_manifest_dict())
        assert manifest.retry_policy == {
            "tool_max_retries": 1,
            "output_max_retries": 2,
        }
        assert isinstance(manifest.retry_policy["tool_max_retries"], int)
        assert isinstance(manifest.retry_policy["output_max_retries"], int)
        # Round-trip again via to_json → from_json.
        reparsed = ReaderRecordAskRunManifest.from_json(manifest.to_json())
        assert reparsed.retry_policy == manifest.retry_policy
        assert reparsed.retry_headroom == manifest.retry_headroom
        assert reparsed.request_cap == manifest.request_cap
        assert reparsed.planned_logical_runs == manifest.planned_logical_runs

    def test_v2_retry_headroom_round_trip_strict(self) -> None:
        """``retry_headroom`` is a non-negative int (NOT null) for V2
        manifests. Round-trips exactly."""
        manifest = _serialize_and_parse(_base_valid_v2_manifest_dict())
        assert manifest.retry_headroom == 34
        assert isinstance(manifest.retry_headroom, int)
        reparsed = ReaderRecordAskRunManifest.from_json(manifest.to_json())
        assert reparsed.retry_headroom == 34

    def test_v2_request_cap_round_trip_strict(self) -> None:
        """``request_cap`` is a non-negative int (NOT null) for V2
        manifests. Round-trips exactly."""
        manifest = _serialize_and_parse(_base_valid_v2_manifest_dict())
        assert manifest.request_cap == 40
        assert isinstance(manifest.request_cap, int)
        reparsed = ReaderRecordAskRunManifest.from_json(manifest.to_json())
        assert reparsed.request_cap == 40

    def test_v2_retry_headroom_zero_when_cap_equals_planned(self) -> None:
        """V2 Rule 18h: when ``request_cap == planned_logical_runs``,
        ``retry_headroom`` MUST be 0 (exactly enough for one attempt
        per planned run, no retries allowed)."""
        d = _base_valid_v2_manifest_dict()
        d["planned_logical_runs"] = 30
        d["request_cap"] = 30
        d["retry_headroom"] = 0
        # Reconcile planned_run_indices so sum of lengths == 30.
        d["planned_run_indices"] = {f"case-{i:02d}": [0, 1, 2] for i in range(10)}
        d["completed_run_indices"] = deepcopy(d["planned_run_indices"])
        # Reconcile identity map keys.
        d["runtime_fixture_identities"] = {
            cid: _VALID_FP_A for cid in d["planned_run_indices"]
        }
        d["executed_requests"] = 30
        manifest = _serialize_and_parse(d)
        assert manifest.retry_headroom == 0
        assert manifest.request_cap == 30
        assert manifest.planned_logical_runs == 30

    def test_v2_retry_policy_missing_tool_max_retries_rejected(self) -> None:
        """V2 Rule 18e: ``retry_policy`` MUST contain
        ``tool_max_retries``. Missing key → ``corrupt_manifest``."""
        d = _base_valid_v2_manifest_dict()
        del d["retry_policy"]["tool_max_retries"]
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)

    def test_v2_retry_policy_missing_output_max_retries_rejected(self) -> None:
        """V2 Rule 18e: ``retry_policy`` MUST contain
        ``output_max_retries``. Missing key → ``corrupt_manifest``."""
        d = _base_valid_v2_manifest_dict()
        del d["retry_policy"]["output_max_retries"]
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)

    def test_v2_retry_policy_negative_tool_retries_rejected(self) -> None:
        """V2 Rule 18e: ``tool_max_retries`` MUST be non-negative.
        Negative → ``corrupt_manifest``."""
        d = _base_valid_v2_manifest_dict()
        d["retry_policy"]["tool_max_retries"] = -1
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)

    def test_v2_retry_policy_negative_output_retries_rejected(self) -> None:
        """V2 Rule 18e: ``output_max_retries`` MUST be non-negative.
        Negative → ``corrupt_manifest``."""
        d = _base_valid_v2_manifest_dict()
        d["retry_policy"]["output_max_retries"] = -1
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)

    def test_v2_retry_policy_bool_value_rejected(self) -> None:
        """V2 Rule 18e: ``retry_policy`` values MUST be ints — bool
        is rejected (bool is a subclass of int in Python, but V2
        strictly rejects it to prevent ``True``/``False`` sneaking
        through as 1/0)."""
        d = _base_valid_v2_manifest_dict()
        d["retry_policy"]["tool_max_retries"] = True
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)

    def test_v2_retry_policy_string_value_rejected(self) -> None:
        """V2 Rule 18e: ``retry_policy`` values MUST be ints — string
        rejected."""
        d = _base_valid_v2_manifest_dict()
        d["retry_policy"]["tool_max_retries"] = "1"
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)

    def test_v2_retry_policy_not_a_dict_rejected(self) -> None:
        """V2 Rule 18e: ``retry_policy`` MUST be a dict — list / str /
        None rejected."""
        d = _base_valid_v2_manifest_dict()
        d["retry_policy"] = ["tool_max_retries", 1]
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)

    def test_v2_retry_headroom_null_rejected(self) -> None:
        """V2 Rule 18f: ``retry_headroom`` MUST be a non-negative int
        (NOT null). Null → ``corrupt_manifest``."""
        d = _base_valid_v2_manifest_dict()
        d["retry_headroom"] = None
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)

    def test_v2_retry_headroom_negative_rejected(self) -> None:
        """V2 Rule 18f: ``retry_headroom`` MUST be non-negative.
        Negative → ``corrupt_manifest``."""
        d = _base_valid_v2_manifest_dict()
        d["retry_headroom"] = -1
        # Also violates Rule 18h (≠ request_cap - planned_logical_runs).
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)

    def test_v2_retry_headroom_bool_rejected(self) -> None:
        """V2 Rule 18f: ``retry_headroom`` MUST be an int — bool
        rejected."""
        d = _base_valid_v2_manifest_dict()
        # Set retry_headroom to a bool that would otherwise be valid
        # as an int (False == 0). Rule 18f rejects bool.
        d["retry_headroom"] = False
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)

    def test_v2_request_cap_null_rejected(self) -> None:
        """V2 Rule 18g: ``request_cap`` MUST be a non-negative int
        (NOT null). Null → ``corrupt_manifest``."""
        d = _base_valid_v2_manifest_dict()
        d["request_cap"] = None
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)

    def test_v2_request_cap_negative_rejected(self) -> None:
        """V2 Rule 18g: ``request_cap`` MUST be non-negative.
        Negative → ``corrupt_manifest``."""
        d = _base_valid_v2_manifest_dict()
        d["request_cap"] = -1
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)

    def test_v2_retry_headroom_arithmetic_drift_rejected(self) -> None:
        """V2 Rule 18h: ``retry_headroom`` MUST equal
        ``request_cap - planned_logical_runs``. Drift →
        ``corrupt_manifest``.

        This catches a harness bug where the persisted headroom does
        not match the actual cap - planned arithmetic.
        """
        d = _base_valid_v2_manifest_dict()
        # request_cap=40, planned_logical_runs=6, retry_headroom should
        # be 34. Set it to 35 to introduce drift.
        d["retry_headroom"] = 35
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)

    def test_v1_legacy_retry_policy_string_default_migrates_to_empty_dict(
        self,
    ) -> None:
        """V1 manifests may carry the legacy string
        ``"default"`` for ``retry_policy``. ``from_json`` migrates it
        to ``{}`` so the dataclass type stays ``dict[str, Any]``.

        This preserves backwards-compat with pre-V2 audit-contract manifests.
        """
        d = _base_valid_v2_manifest_dict()
        d["audit_contract_version"] = AUDIT_CONTRACT_VERSION_V1
        d["runtime_fixture_identities"] = {}  # V1 allows empty
        d["retry_policy"] = "default"
        manifest = _serialize_and_parse(d)
        assert manifest.retry_policy == {}

    def test_v1_legacy_retry_policy_unknown_string_rejected(self) -> None:
        """V1 manifests with a string ``retry_policy``
        other than ``"default"`` → ``corrupt_manifest`` (Rule 15).

        Only the literal ``"default"`` is the legacy V1 value; any
        other string is corrupt.
        """
        d = _base_valid_v2_manifest_dict()
        d["audit_contract_version"] = AUDIT_CONTRACT_VERSION_V1
        d["runtime_fixture_identities"] = {}
        d["retry_policy"] = "unknown"
        with pytest.raises(RunManifestError, match="corrupt_manifest"):
            _serialize_and_parse(d)

    def test_v2_retry_policy_extra_keys_allowed(self) -> None:
        """V2 Rule 18e does NOT reject extra keys in
        ``retry_policy`` — only the required keys
        (``tool_max_retries`` / ``output_max_retries``) are validated.
        Extra keys are preserved (forward-compat for future fields
        like ``max_consecutive_retries``).

        The round-trip preserves the extra key.
        """
        d = _base_valid_v2_manifest_dict()
        d["retry_policy"]["future_field"] = 5
        manifest = _serialize_and_parse(d)
        assert manifest.retry_policy["future_field"] == 5
        assert manifest.retry_policy["tool_max_retries"] == 1
        assert manifest.retry_policy["output_max_retries"] == 2


# ---------------------------------------------------------------------------
# Budget manifest JSON example (used in the report)
# ---------------------------------------------------------------------------


class TestBudgetManifestJsonExample:
    """Produce a real V2 budget manifest JSON example
    that demonstrates the typed retry_policy / non-null retry_headroom
    contract. This test exists primarily to surface the actual JSON
    structure for the report (``json.dumps`` output is deterministic
    via ``sort_keys=True``).
    """

    def test_budget_manifest_json_has_typed_retry_policy_and_headroom(self) -> None:
        """The serialized V2 manifest JSON contains the typed
        ``retry_policy`` dict and the non-null ``retry_headroom`` int.
        Both are persisted with their strict types (no None, no
        string).
        """
        manifest = _serialize_and_parse(_base_valid_v2_manifest_dict())
        serialized = json.loads(manifest.to_json())
        assert serialized["retry_policy"] == {
            "tool_max_retries": 1,
            "output_max_retries": 2,
        }
        assert serialized["retry_headroom"] == 34
        assert serialized["request_cap"] == 40
        assert serialized["planned_logical_runs"] == 6
        assert serialized["audit_contract_version"] == AUDIT_CONTRACT_VERSION_V2
        # The actual JSON example (for the report).
        # Format: indent=2, sort_keys=True, ensure_ascii=False.
        example_json = manifest.to_json()
        # Sanity: the JSON is parseable and round-trips.
        reparsed = ReaderRecordAskRunManifest.from_json(example_json)
        assert reparsed.retry_policy == manifest.retry_policy
        assert reparsed.retry_headroom == manifest.retry_headroom
