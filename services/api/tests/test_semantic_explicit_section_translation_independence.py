"""Prove USER_EXPLICIT section translation is independent of automatic policy.

Option B (repair brief): the existing ``section_v1`` bootstrap path never
calls ``filter_units_for_automatic_layer`` / automatic target loading.
Automatic=false units (code/table/citation) remain requestable via
explicit section admission (budget/authorization/overlap still apply).
"""

from __future__ import annotations

import inspect

from app.services.reader_orchestration import section_translation_bootstrap as stb
from app.services.reader_orchestration.automatic_layer_policy import (
    AutomaticLayerPolicy,
    filter_units_for_automatic_layer,
    validate_automatic_job_semantic_fence,
)
from app.services.reader_orchestration.section_lane import (
    SECTION_REQUEST_ORIGIN,
    TRANSLATION_SECTION_OPERATION_FINGERPRINT,
)
from app.services.reader_orchestration.section_request_planner import (
    ExplicitSectionIntent,
    SectionRequestTrigger,
    plan_explicit_section_request,
)
from app.services.reader_orchestration.semantic_classifier import SEMANTIC_CONTRACT_V1


def test_section_bootstrap_module_does_not_import_automatic_filter() -> None:
    src = inspect.getsource(stb)
    assert "filter_units_for_automatic_layer" not in src
    assert "load_automatic_layer_targets" not in src
    assert "request_origin" in src and "section_v1" in src


def test_section_v1_input_skips_automatic_disallow() -> None:
    """Worker fence must not block trusted section jobs for automatic=false units."""
    from app.services.reader_orchestration.section_identity import (
        SectionIdentity,
        encode_section_target_key,
    )

    identity = SectionIdentity(
        record_id="r",
        base_id="b",
        generation=1,
        start_unit_id="u-code",
        end_unit_id="u-code",
    )
    target_key = encode_section_target_key(identity)
    validate_automatic_job_semantic_fence(
        job_input={
            "request_origin": SECTION_REQUEST_ORIGIN,
            "section_identity": {
                "record_id": "r",
                "base_id": "b",
                "generation": 1,
                "start_unit_id": "u-code",
                "end_unit_id": "u-code",
            },
            "target_unit_ids": ["u-code"],
            "semantic_policy_mode": "enforce",
            "semantic_contract_version": SEMANTIC_CONTRACT_V1,
            "automatic_layer_policy_resolver_version": "automatic_layer_policy_v1",
            "automatic_layer_name": "translation",
        },
        layer="translation",
        unit_metadata_list=[
            {
                "semantic": {
                    "contract_version": SEMANTIC_CONTRACT_V1,
                    "resolver_version": "automatic_layer_policy_v1",
                    "automatic_layer_policy": AutomaticLayerPolicy.all_off().as_dict(),
                }
            }
        ],
        operation_fingerprint=f"{TRANSLATION_SECTION_OPERATION_FINGERPRINT}:hash",
        trusted_record_id="r",
        trusted_base_id="b",
        trusted_generation=1,
        trusted_target_key=target_key,
        trusted_loaded_unit_ids=["u-code"],
        trusted_base_ordered_units=[{"unit_id": "u-code", "order_index": 0}],
        trusted_anchor_to_unit={},
    )


def test_automatic_filter_would_drop_code_but_section_path_independent() -> None:
    units = [
        {
            "unit_id": "u-code",
            "order_index": 1,
            "metadata_json": {
                "semantic": {
                    "contract_version": SEMANTIC_CONTRACT_V1,
                    "resolver_version": "automatic_layer_policy_v1",
                    "automatic_layer_policy": AutomaticLayerPolicy.all_off().as_dict(),
                }
            },
        }
    ]
    # Automatic bootstrap would drop code for translation.
    assert (
        filter_units_for_automatic_layer(units, "translation", mode="enforce") == []
    )
    # Explicit planner types still accept translation family + range intents
    # (admission itself is covered by section planner tests; here we only
    # prove the independence surface).
    intent = ExplicitSectionIntent(
        trigger=SectionRequestTrigger.USER_EXPLICIT,
        layer_family="translation",
        record_id="r1",
        base_id="b1",
        generation=1,
        start_unit_id="u-code",
        end_unit_id="u-code",
    )
    assert intent.trigger is SectionRequestTrigger.USER_EXPLICIT
    assert intent.layer_family == "translation"
    # plan_explicit_section_request is pure and does not read automatic policy.
    assert "automatic_layer" not in inspect.getsource(plan_explicit_section_request)
