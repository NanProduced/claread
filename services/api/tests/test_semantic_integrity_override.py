"""Strict policy parsing and structural integrity override contract tests.

Covers the frozen contracts:
- ``AutomaticLayerPolicy.from_mapping`` strict parse (exact four keys,
  native bools only);
- recorded override wins before semantic / legacy paths;
- malformed overrides fall back to the existing paths;
- override writer and strict reader share one interface (round-trip).
"""

from __future__ import annotations

from app.services.reader_orchestration.automatic_layer_policy import (
    SEMANTIC_INTEGRITY_OVERRIDE_KEY,
    STRUCTURAL_INTEGRITY_OVERRIDE_VERSION,
    AutomaticLayerPolicy,
    build_semantic_integrity_override,
    policy_from_unit_metadata,
)

ALL_ON_DICT = {
    "translation": True,
    "vocabulary": True,
    "grammar_note": True,
    "sentence_analysis": True,
}
ALL_OFF_DICT = {key: False for key in ALL_ON_DICT}


class TestStrictFromMapping:
    def test_exact_four_native_bools_round_trip(self):
        policy = AutomaticLayerPolicy.from_mapping(ALL_ON_DICT)
        assert policy == AutomaticLayerPolicy.all_on()
        assert AutomaticLayerPolicy.from_mapping(policy.as_dict()) == policy

    def test_missing_key_rejected(self):
        data = dict(ALL_ON_DICT)
        del data["vocabulary"]
        assert AutomaticLayerPolicy.from_mapping(data) is None

    def test_extra_key_rejected(self):
        assert (
            AutomaticLayerPolicy.from_mapping({**ALL_ON_DICT, "extra": True}) is None
        )

    def test_string_values_rejected(self):
        assert (
            AutomaticLayerPolicy.from_mapping({**ALL_ON_DICT, "vocabulary": "false"})
            is None
        )
        assert (
            AutomaticLayerPolicy.from_mapping({**ALL_ON_DICT, "vocabulary": "true"})
            is None
        )

    def test_integer_values_rejected(self):
        assert (
            AutomaticLayerPolicy.from_mapping({**ALL_ON_DICT, "vocabulary": 0})
            is None
        )
        assert (
            AutomaticLayerPolicy.from_mapping({**ALL_ON_DICT, "vocabulary": 1})
            is None
        )

    def test_wrong_containers_rejected(self):
        assert AutomaticLayerPolicy.from_mapping(None) is None
        assert AutomaticLayerPolicy.from_mapping([True, True, True, True]) is None  # type: ignore[arg-type]


def _semantic_meta() -> dict:
    return {
        "semantic": {
            "contract_version": "semantic_contract_v1",
            "content_role": "prose",
            "resolver_version": "automatic_layer_policy_v1",
            "automatic_layer_policy": dict(ALL_ON_DICT),
        }
    }


class TestOverrideReadPriority:
    def test_well_formed_override_wins_over_recorded_all_on(self):
        meta = _semantic_meta()
        meta[SEMANTIC_INTEGRITY_OVERRIDE_KEY] = build_semantic_integrity_override(
            reason_code="annotation_range_mismatch",
        )
        resolved = policy_from_unit_metadata(meta)
        assert resolved.policy == AutomaticLayerPolicy.all_off()
        assert resolved.is_legacy is False
        # Identity fields still come from the semantic block for fences.
        assert resolved.contract_version == "semantic_contract_v1"
        assert resolved.resolver_version == "automatic_layer_policy_v1"

    def test_writer_reader_share_one_interface(self):
        override = build_semantic_integrity_override(reason_code="annotation_multi_unit_overlap")
        assert override["override_version"] == STRUCTURAL_INTEGRITY_OVERRIDE_VERSION
        assert override["reason_code"] == "annotation_multi_unit_overlap"
        assert AutomaticLayerPolicy.from_mapping(override["policy"]) == (
            AutomaticLayerPolicy.all_off()
        )

    def test_malformed_override_falls_back_to_semantic_path(self):
        for broken in (
            {"override_version": "other_version", "policy": dict(ALL_OFF_DICT), "reason_code": "x"},
            {"override_version": STRUCTURAL_INTEGRITY_OVERRIDE_VERSION, "reason_code": "x"},
            {
                "override_version": STRUCTURAL_INTEGRITY_OVERRIDE_VERSION,
                "policy": {**ALL_OFF_DICT, "translation": "false"},
                "reason_code": "x",
            },
            {
                "override_version": STRUCTURAL_INTEGRITY_OVERRIDE_VERSION,
                "policy": dict(ALL_OFF_DICT),
            },
        ):
            meta = _semantic_meta()
            meta[SEMANTIC_INTEGRITY_OVERRIDE_KEY] = broken
            resolved = policy_from_unit_metadata(meta)
            # Falls back to the recorded semantic all-on, never all-off.
            assert resolved.policy == AutomaticLayerPolicy.all_on()

    def test_override_without_semantic_still_reads_recorded_policy(self):
        meta = {
            SEMANTIC_INTEGRITY_OVERRIDE_KEY: build_semantic_integrity_override(
                reason_code="annotation_range_out_of_bounds",
            )
        }
        resolved = policy_from_unit_metadata(meta)
        assert resolved.policy == AutomaticLayerPolicy.all_off()
        assert resolved.is_legacy is False

    def test_legacy_unit_without_override_keeps_fail_open(self):
        resolved = policy_from_unit_metadata({})
        assert resolved.policy == AutomaticLayerPolicy.all_on()
        assert resolved.is_legacy is True
