"""Versioned automatic layer policy + bootstrap filter + fence tests (repair)."""

from __future__ import annotations

import pytest

from app.services.reader_orchestration.automatic_layer_policy import (
    AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
    SEMANTIC_FENCE_INCONSISTENT_CODE,
    SEMANTIC_FENCE_KEY_CONTRACT,
    SEMANTIC_FENCE_KEY_RESOLVER,
    AutomaticLayerPolicy,
    AutomaticLayerTargetUnit,
    SemanticFenceConstructionError,
    SemanticFenceError,
    compose_semantic_fingerprint_token,
    filter_units_for_automatic_layer,
    generation_semantic_fence_from_targets,
    get_automatic_layer_policy_mode,
    is_trusted_explicit_section_translation_job,
    materialize_target_units,
    resolve_automatic_layer_policy,
    resolve_policy_for_stable_block,
    unit_allows_any_grammar,
    unit_allows_automatic_layer,
    validate_automatic_job_semantic_fence,
)
from app.services.reader_orchestration.semantic_classifier import SEMANTIC_CONTRACT_V1


def _payload(role: str | None, *, shadow: bool = False) -> dict:
    body: dict = {
        "semantic": {
            "contract_version": SEMANTIC_CONTRACT_V1,
            "content_role": role,
            "classification": {
                "source": "deterministic",
                "confidence": 1.0,
                "rules_version": "semrules_v1",
                "signals": [],
            },
        }
    }
    if shadow:
        body["semantic"]["classification"]["shadow_only"] = True
    return body


def _assert_t_only(policy: AutomaticLayerPolicy) -> None:
    assert policy.as_dict() == {
        "translation": True,
        "vocabulary": False,
        "grammar_note": False,
        "sentence_analysis": False,
    }


def _assert_all_on(policy: AutomaticLayerPolicy) -> None:
    assert policy == AutomaticLayerPolicy.all_on()


def _assert_all_off(policy: AutomaticLayerPolicy) -> None:
    assert policy == AutomaticLayerPolicy.all_off()


@pytest.mark.parametrize(
    ("block_type", "role", "shadow", "expect"),
    [
        ("paragraph", "prose", False, "all_on"),
        ("list_item", "prose", False, "all_on"),
        ("heading", None, False, "t_only"),
        ("paragraph", "citation_reference", False, "t_only"),
        ("blockquote", "quotation", False, "t_only"),
        ("blockquote", "source_callout", False, "t_only"),
        ("blockquote", "quotation", True, "t_only"),  # shadow must NOT re-open V/G/S
        ("code_block", None, False, "all_off"),
        ("table_cell", None, False, "all_off"),
        ("paragraph", "link_only", False, "all_off"),
        ("paragraph", "prompt_question", True, "all_on"),  # shadow fail-open
    ],
)
def test_product_matrix_all_roles(block_type, role, shadow, expect) -> None:
    resolved = resolve_policy_for_stable_block(
        block_type=block_type,
        payload_json=_payload(role, shadow=shadow),
    )
    if expect == "all_on":
        _assert_all_on(resolved.policy)
    elif expect == "t_only":
        _assert_t_only(resolved.policy)
    else:
        _assert_all_off(resolved.policy)


def test_legacy_fail_open_all_on() -> None:
    resolved = resolve_automatic_layer_policy(
        contract_version=None,
        block_type="code_block",
        payload_json={},
    )
    assert resolved.is_legacy
    _assert_all_on(resolved.policy)


def test_heading_vocabulary_off() -> None:
    resolved = resolve_policy_for_stable_block(
        block_type="heading",
        payload_json=_payload(None),
    )
    assert resolved.policy.translation is True
    assert resolved.policy.vocabulary is False


def test_blockquote_structure_t_only_even_without_role() -> None:
    resolved = resolve_policy_for_stable_block(
        block_type="blockquote",
        payload_json={
            "semantic": {
                "contract_version": SEMANTIC_CONTRACT_V1,
                "content_role": None,
                "classification": {
                    "source": "deterministic",
                    "confidence": 1.0,
                    "rules_version": "semrules_v1",
                    "signals": [],
                },
            }
        },
    )
    _assert_t_only(resolved.policy)


def test_filter_modes_off_shadow_enforce() -> None:
    units = [
        {
            "unit_id": "prose",
            "order_index": 1,
            "metadata_json": {
                "semantic": {
                    "contract_version": SEMANTIC_CONTRACT_V1,
                    "content_role": "prose",
                    "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                    "automatic_layer_policy": AutomaticLayerPolicy.all_on().as_dict(),
                }
            },
        },
        {
            "unit_id": "code",
            "order_index": 2,
            "metadata_json": {
                "semantic": {
                    "contract_version": SEMANTIC_CONTRACT_V1,
                    "content_role": None,
                    "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                    "automatic_layer_policy": AutomaticLayerPolicy.all_off().as_dict(),
                }
            },
        },
    ]
    off = filter_units_for_automatic_layer(units, "vocabulary", mode="off")
    assert {u["unit_id"] for u in off} == {"prose", "code"}

    shadow = filter_units_for_automatic_layer(units, "vocabulary", mode="shadow")
    assert {u["unit_id"] for u in shadow} == {"prose", "code"}

    enforce = filter_units_for_automatic_layer(units, "vocabulary", mode="enforce")
    assert {u["unit_id"] for u in enforce} == {"prose"}


def test_default_mode_is_enforce() -> None:
    assert get_automatic_layer_policy_mode() in {"off", "shadow", "enforce"}


def test_filter_citation_and_heading_no_vocab() -> None:
    units = [
        {
            "unit_id": "heading",
            "order_index": 1,
            "metadata_json": {
                "semantic": {
                    "contract_version": SEMANTIC_CONTRACT_V1,
                    "content_role": None,
                    "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                    "automatic_layer_policy": {
                        "translation": True,
                        "vocabulary": False,
                        "grammar_note": False,
                        "sentence_analysis": False,
                    },
                }
            },
        },
        {
            "unit_id": "cite",
            "order_index": 2,
            "metadata_json": {
                "semantic": {
                    "contract_version": SEMANTIC_CONTRACT_V1,
                    "content_role": "citation_reference",
                    "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                    "automatic_layer_policy": {
                        "translation": True,
                        "vocabulary": False,
                        "grammar_note": False,
                        "sentence_analysis": False,
                    },
                }
            },
        },
    ]
    assert filter_units_for_automatic_layer(units, "vocabulary", mode="enforce") == []
    t = filter_units_for_automatic_layer(units, "translation", mode="enforce")
    assert {u["unit_id"] for u in t} == {"heading", "cite"}


def test_fence_layer_disallowed() -> None:
    job_input = {
        "semantic_contract_version": SEMANTIC_CONTRACT_V1,
        "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
        "automatic_layer_name": "vocabulary",
    }
    unit_meta = {
        "semantic": {
            "contract_version": SEMANTIC_CONTRACT_V1,
            "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
            "automatic_layer_policy": {
                "translation": True,
                "vocabulary": False,
                "grammar_note": False,
                "sentence_analysis": False,
            },
        }
    }
    with pytest.raises(SemanticFenceError) as ei:
        validate_automatic_job_semantic_fence(
            job_input=job_input,
            layer="vocabulary",
            unit_metadata_list=[unit_meta],
        )
    assert ei.value.code == "semantic_automatic_layer_disallowed"


def test_fence_version_mismatch() -> None:
    with pytest.raises(SemanticFenceError) as ei:
        validate_automatic_job_semantic_fence(
            job_input={
                "semantic_contract_version": SEMANTIC_CONTRACT_V1,
                "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                "automatic_layer_name": "translation",
            },
            layer="translation",
            unit_metadata_list=[
                {
                    "semantic": {
                        "contract_version": SEMANTIC_CONTRACT_V1,
                        "resolver_version": "automatic_layer_policy_v99",
                        "automatic_layer_policy": AutomaticLayerPolicy.all_on().as_dict(),
                    }
                }
            ],
        )
    assert ei.value.code == "semantic_policy_version_mismatch"


def test_section_v1_skips_fence() -> None:
    # Trusted USER_EXPLICIT: only skips allows(false); still needs full target bind.
    from app.services.reader_orchestration.section_identity import (
        SectionIdentity,
        encode_section_target_key,
    )
    from app.services.reader_orchestration.section_lane import (
        SECTION_REQUEST_ORIGIN,
        TRANSLATION_SECTION_OPERATION_FINGERPRINT,
    )

    identity = SectionIdentity(
        record_id="r",
        base_id="b",
        generation=1,
        start_unit_id="u1",
        end_unit_id="u1",
    )
    target_key = encode_section_target_key(identity)
    universe = [{"unit_id": "u1", "order_index": 0}]
    validate_automatic_job_semantic_fence(
        job_input={
            "request_origin": SECTION_REQUEST_ORIGIN,
            "section_identity": {
                "record_id": "r",
                "base_id": "b",
                "generation": 1,
                "start_unit_id": "u1",
                "end_unit_id": "u1",
            },
            "target_unit_ids": ["u1"],
            "semantic_contract_version": SEMANTIC_CONTRACT_V1,
            "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
            "automatic_layer_name": "translation",
            "semantic_policy_mode": "enforce",
        },
        layer="translation",
        unit_metadata_list=[
            {
                "semantic": {
                    "contract_version": SEMANTIC_CONTRACT_V1,
                    "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                    "automatic_layer_policy": AutomaticLayerPolicy.all_off().as_dict(),
                }
            }
        ],
        operation_fingerprint=f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:hash",
        trusted_record_id="r",
        trusted_base_id="b",
        trusted_generation=1,
        trusted_target_key=target_key,
        trusted_loaded_unit_ids=["u1"],
        trusted_base_ordered_units=universe,
        trusted_anchor_to_unit={},
    )


def test_section_identity_does_not_bypass_vocabulary_or_contract() -> None:
    """Regression: section claim must not early-return for non-translation layers."""
    from app.services.reader_orchestration.section_identity import (
        SectionIdentity,
        encode_section_target_key,
    )
    from app.services.reader_orchestration.section_lane import (
        SECTION_REQUEST_ORIGIN,
        TRANSLATION_SECTION_OPERATION_FINGERPRINT,
    )

    identity = SectionIdentity(
        record_id="r", base_id="b", generation=1, start_unit_id="u1", end_unit_id="u1"
    )
    target_key = encode_section_target_key(identity)
    universe = [{"unit_id": "u1", "order_index": 0}]
    base_job = {
        "request_origin": SECTION_REQUEST_ORIGIN,
        "section_identity": {
            "record_id": "r",
            "base_id": "b",
            "generation": 1,
            "start_unit_id": "u1",
            "end_unit_id": "u1",
        },
        "target_unit_ids": ["u1"],
        "semantic_contract_version": SEMANTIC_CONTRACT_V1,
        "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
        "automatic_layer_name": "vocabulary",
        "semantic_policy_mode": "enforce",
    }
    meta = {
        "semantic": {
            "contract_version": SEMANTIC_CONTRACT_V1,
            "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
            "automatic_layer_policy": AutomaticLayerPolicy.all_off().as_dict(),
        }
    }
    # Vocabulary still enforces allows=false.
    with pytest.raises(Exception) as ei:
        validate_automatic_job_semantic_fence(
            job_input=base_job,
            layer="vocabulary",
            unit_metadata_list=[meta],
            operation_fingerprint=f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:hash",
            trusted_record_id="r",
            trusted_base_id="b",
            trusted_generation=1,
            trusted_target_key=target_key,
            trusted_loaded_unit_ids=["u1"],
            trusted_base_ordered_units=universe,
            trusted_anchor_to_unit={},
        )
    assert ei.value.code == "semantic_automatic_layer_disallowed"  # type: ignore[attr-defined]

    # Trusted translation still fails closed on contract mismatch.
    with pytest.raises(Exception) as ei2:
        validate_automatic_job_semantic_fence(
            job_input={
                **base_job,
                "automatic_layer_name": "translation",
                "automatic_layer_policy_resolver_version": "automatic_layer_policy_v99",
            },
            layer="translation",
            unit_metadata_list=[meta],
            operation_fingerprint=f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:hash",
            trusted_record_id="r",
            trusted_base_id="b",
            trusted_generation=1,
            trusted_target_key=target_key,
            trusted_loaded_unit_ids=["u1"],
            trusted_base_ordered_units=universe,
            trusted_anchor_to_unit={},
        )
    assert ei2.value.code == "semantic_policy_version_mismatch"  # type: ignore[attr-defined]


def test_fenced_job_requires_exact_layer_name() -> None:
    meta = {
        "semantic": {
            "contract_version": SEMANTIC_CONTRACT_V1,
            "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
            "automatic_layer_policy": AutomaticLayerPolicy.all_on().as_dict(),
        }
    }
    base = {
        "semantic_contract_version": SEMANTIC_CONTRACT_V1,
        "automatic_layer_policy_resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
        "semantic_policy_mode": "enforce",
    }
    # Missing layer name.
    with pytest.raises(Exception) as ei:
        validate_automatic_job_semantic_fence(
            job_input=base,
            layer="translation",
            unit_metadata_list=[meta],
        )
    assert ei.value.code == "semantic_policy_version_mismatch"  # type: ignore[attr-defined]

    # grammar_bundle alias no longer accepted for translation worker.
    with pytest.raises(Exception) as ei2:
        validate_automatic_job_semantic_fence(
            job_input={**base, "automatic_layer_name": "grammar_bundle"},
            layer="translation",
            unit_metadata_list=[meta],
        )
    assert ei2.value.code == "semantic_policy_version_mismatch"  # type: ignore[attr-defined]

    # layers_any does not bypass job layer identity.
    with pytest.raises(Exception) as ei3:
        validate_automatic_job_semantic_fence(
            job_input={**base, "automatic_layer_name": "translation"},
            layer="grammar_note",
            layers_any=("grammar_note", "sentence_analysis"),
            unit_metadata_list=[meta],
        )
    assert ei3.value.code == "semantic_policy_version_mismatch"  # type: ignore[attr-defined]

    # Exact match still works.
    validate_automatic_job_semantic_fence(
        job_input={**base, "automatic_layer_name": "grammar_note"},
        layer="grammar_note",
        layers_any=("grammar_note", "sentence_analysis"),
        unit_metadata_list=[meta],
    )


def test_section_anchor_wrong_unit_not_trusted() -> None:
    from app.services.reader_orchestration.section_identity import (
        SectionIdentity,
        encode_section_target_key,
    )
    from app.services.reader_orchestration.section_lane import (
        SECTION_REQUEST_ORIGIN,
        TRANSLATION_SECTION_OPERATION_FINGERPRINT,
    )

    identity = SectionIdentity(
        record_id="r",
        base_id="b",
        generation=1,
        start_unit_id="u1",
        end_unit_id="u1",
        start_anchor_segment_id="a1",
        end_anchor_segment_id="a2",
    )
    target_key = encode_section_target_key(identity)
    # Anchors exist but belong to a different unit.
    assert not is_trusted_explicit_section_translation_job(
        job_input={
            "request_origin": SECTION_REQUEST_ORIGIN,
            "section_identity": {
                "record_id": "r",
                "base_id": "b",
                "generation": 1,
                "start_unit_id": "u1",
                "end_unit_id": "u1",
                "start_anchor_segment_id": "a1",
                "end_anchor_segment_id": "a2",
            },
            "target_unit_ids": ["u1"],
        },
        operation_fingerprint=f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:h",
        trusted_record_id="r",
        trusted_base_id="b",
        trusted_generation=1,
        trusted_target_key=target_key,
        trusted_loaded_unit_ids=["u1"],
        trusted_base_ordered_units=[{"unit_id": "u1", "order_index": 0}],
        trusted_anchor_to_unit={"a1": "u2", "a2": "u2"},
    )


def test_section_missing_middle_unit_not_trusted() -> None:
    from app.services.reader_orchestration.section_identity import (
        SectionIdentity,
        encode_section_target_key,
    )
    from app.services.reader_orchestration.section_lane import (
        SECTION_REQUEST_ORIGIN,
        TRANSLATION_SECTION_OPERATION_FINGERPRINT,
    )

    identity = SectionIdentity(
        record_id="r", base_id="b", generation=1, start_unit_id="u1", end_unit_id="u3"
    )
    target_key = encode_section_target_key(identity)
    universe = [
        {"unit_id": "u1", "order_index": 0},
        {"unit_id": "u2", "order_index": 1},
        {"unit_id": "u3", "order_index": 2},
    ]
    # Omits middle unit u2.
    assert not is_trusted_explicit_section_translation_job(
        job_input={
            "request_origin": SECTION_REQUEST_ORIGIN,
            "section_identity": {
                "record_id": "r",
                "base_id": "b",
                "generation": 1,
                "start_unit_id": "u1",
                "end_unit_id": "u3",
            },
            "target_unit_ids": ["u1", "u3"],
        },
        operation_fingerprint=f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:h",
        trusted_record_id="r",
        trusted_base_id="b",
        trusted_generation=1,
        trusted_target_key=target_key,
        trusted_loaded_unit_ids=["u1", "u3"],
        trusted_base_ordered_units=universe,
        trusted_anchor_to_unit={},
    )


def test_legacy_job_skips_fence() -> None:
    validate_automatic_job_semantic_fence(
        job_input={"base_language": "en"},
        layer="translation",
        unit_metadata_list=[{}],
    )


def test_fingerprint_token() -> None:
    targets = materialize_target_units(
        [
            {
                "unit_id": "u1",
                "order_index": 1,
                "metadata_json": {
                    "semantic": {
                        "contract_version": SEMANTIC_CONTRACT_V1,
                        "content_role": "prose",
                        "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
                        "automatic_layer_policy": AutomaticLayerPolicy.all_on().as_dict(),
                    }
                },
            }
        ],
        "translation",
        mode="enforce",
    )
    fence = generation_semantic_fence_from_targets(targets)
    token = compose_semantic_fingerprint_token(fence)
    assert "semantic_contract_v1" in token


def test_unit_allows_helpers() -> None:
    code_meta = {
        "semantic": {
            "contract_version": SEMANTIC_CONTRACT_V1,
            "content_role": None,
            "resolver_version": AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
            "automatic_layer_policy": AutomaticLayerPolicy.all_off().as_dict(),
        }
    }
    assert unit_allows_automatic_layer(code_meta, "translation") is False
    assert unit_allows_any_grammar(code_meta) is False
    assert unit_allows_any_grammar({}) is True


# ---------------------------------------------------------------------------
# Shared semantic fence builder convergence tests
# ---------------------------------------------------------------------------


def _target(
    unit_id: str = "u1",
    *,
    contract_version: str | None = None,
    resolver_version: str | None = None,
) -> AutomaticLayerTargetUnit:
    return AutomaticLayerTargetUnit(
        unit_id=unit_id,
        order_index=0,
        metadata_json={},
        contract_version=contract_version,
        resolver_version=resolver_version,
    )


def test_shared_fence_builder_uniform_semantic() -> None:
    """Uniform semantic targets → exact contract + resolver."""
    targets = [
        _target("u1", contract_version=SEMANTIC_CONTRACT_V1,
                resolver_version=AUTOMATIC_LAYER_POLICY_RESOLVER_V1),
        _target("u2", contract_version=SEMANTIC_CONTRACT_V1,
                resolver_version=AUTOMATIC_LAYER_POLICY_RESOLVER_V1),
    ]
    fence = generation_semantic_fence_from_targets(targets)
    assert fence[SEMANTIC_FENCE_KEY_CONTRACT] == SEMANTIC_CONTRACT_V1
    assert fence[SEMANTIC_FENCE_KEY_RESOLVER] == AUTOMATIC_LAYER_POLICY_RESOLVER_V1


def test_shared_fence_builder_all_legacy() -> None:
    """All-legacy targets (no contract_version) → legacy fence."""
    targets = [
        _target("u1"),
        _target("u2"),
    ]
    fence = generation_semantic_fence_from_targets(targets)
    assert fence[SEMANTIC_FENCE_KEY_CONTRACT] is None
    assert fence[SEMANTIC_FENCE_KEY_RESOLVER] == "legacy_open"


def test_shared_fence_builder_empty_targets() -> None:
    """Empty targets → legacy fence (preserves no-op bootstrap compat)."""
    fence = generation_semantic_fence_from_targets([])
    assert fence[SEMANTIC_FENCE_KEY_CONTRACT] is None
    assert fence[SEMANTIC_FENCE_KEY_RESOLVER] == "legacy_open"


def test_shared_fence_builder_mixed_contract_raises() -> None:
    """Mixed contract versions → SemanticFenceConstructionError."""
    targets = [
        _target("u1", contract_version=SEMANTIC_CONTRACT_V1,
                resolver_version=AUTOMATIC_LAYER_POLICY_RESOLVER_V1),
        _target("u2", contract_version="semantic_contract_v999_bogus",
                resolver_version=AUTOMATIC_LAYER_POLICY_RESOLVER_V1),
    ]
    with pytest.raises(SemanticFenceConstructionError) as exc_info:
        generation_semantic_fence_from_targets(targets)
    assert exc_info.value.code == SEMANTIC_FENCE_INCONSISTENT_CODE


def test_shared_fence_builder_mixed_resolver_raises() -> None:
    """Mixed resolver versions → SemanticFenceConstructionError."""
    targets = [
        _target("u1", contract_version=SEMANTIC_CONTRACT_V1,
                resolver_version=AUTOMATIC_LAYER_POLICY_RESOLVER_V1),
        _target("u2", contract_version=SEMANTIC_CONTRACT_V1,
                resolver_version="automatic_layer_policy_v999_bogus"),
    ]
    with pytest.raises(SemanticFenceConstructionError) as exc_info:
        generation_semantic_fence_from_targets(targets)
    assert exc_info.value.code == SEMANTIC_FENCE_INCONSISTENT_CODE


def test_shared_fence_builder_legacy_plus_semantic_raises() -> None:
    """Legacy + semantic mix → SemanticFenceConstructionError."""
    targets = [
        _target("u1", contract_version=SEMANTIC_CONTRACT_V1,
                resolver_version=AUTOMATIC_LAYER_POLICY_RESOLVER_V1),
        _target("u2"),  # legacy, no contract
    ]
    with pytest.raises(SemanticFenceConstructionError) as exc_info:
        generation_semantic_fence_from_targets(targets)
    assert exc_info.value.code == SEMANTIC_FENCE_INCONSISTENT_CODE
