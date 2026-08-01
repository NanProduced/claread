"""G4 deterministic Reader translation Prompt Profile contracts."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.contracts.annotation import compute_text_range_hash
from app.schemas.reader_orchestration import (
    TranslationGenerationGroup,
    TranslationLayerGenerationOutput,
)
from app.services.reader_orchestration.automatic_layer_policy import (
    resolve_automatic_layer_policy,
)
from app.services.reader_orchestration.reading_strategy import (
    resolve_reader_variant_strategy,
)
from app.services.reader_orchestration.semantic_classifier import (
    SEMANTIC_CONTRACT_V1,
)
from app.services.reader_orchestration.translation_prompt_profile import (
    TRANSLATION_PROFILE_CITATION_REFERENCE,
    TRANSLATION_PROFILE_EXPLICIT_SECTION,
    TRANSLATION_PROFILE_HEADING,
    TRANSLATION_PROFILE_PROSE,
    TRANSLATION_PROFILE_QUOTATION,
    TRANSLATION_PROFILE_SOURCE_CALLOUT,
    TRANSLATION_PROMPT_PROFILE_CONTRACT_VERSION,
    TRANSLATION_PROMPT_PROFILE_VERSION,
    build_translation_prompt_profile_contract,
    compose_translation_prompt_profile_fingerprint_token,
    get_translation_prompt_profile,
    resolve_translation_prompt_profile,
    resolve_translation_prompt_profile_for_unit,
    translation_prompt_profile_input_fields,
)
from app.services.reader_orchestration.translation_worker import (
    TranslationAnchorSegmentTarget,
    TranslationBatchJobContext,
    TranslationBatchUnitContext,
    TranslationExecutionError,
    TranslationExecutionResult,
    TranslationJobContext,
    _build_translation_batch_prompt,
    _build_translation_prompt,
    _validate_translation_prompt_profile_contract,
)


def _semantic_metadata(
    role: str | None,
    *,
    contract: str | None = SEMANTIC_CONTRACT_V1,
) -> dict[str, object]:
    return {
        "semantic": {
            "contract_version": contract,
            "content_role": role,
        }
    }


@pytest.mark.parametrize(
    ("block_type", "role", "expected"),
    [
        ("paragraph", "prose", TRANSLATION_PROFILE_PROSE),
        ("list_item", "prose", TRANSLATION_PROFILE_PROSE),
        ("heading", None, TRANSLATION_PROFILE_HEADING),
        ("blockquote", "quotation", TRANSLATION_PROFILE_QUOTATION),
        ("paragraph", "citation_reference", TRANSLATION_PROFILE_CITATION_REFERENCE),
        ("blockquote", "source_callout", TRANSLATION_PROFILE_SOURCE_CALLOUT),
    ],
)
def test_profile_resolver_maps_role_without_using_policy(
    block_type: str,
    role: str | None,
    expected: str,
) -> None:
    profile = resolve_translation_prompt_profile(
        contract_version=SEMANTIC_CONTRACT_V1,
        block_type=block_type,
        content_role=role,
    )

    assert profile.profile_id == expected
    assert profile.version == TRANSLATION_PROMPT_PROFILE_VERSION
    assert profile.key == f"{TRANSLATION_PROMPT_PROFILE_VERSION}:{expected}"


def test_profile_and_automatic_policy_are_independent() -> None:
    cases = [
        ("heading", None, TRANSLATION_PROFILE_HEADING, "t_only"),
        ("blockquote", "quotation", TRANSLATION_PROFILE_QUOTATION, "t_only"),
        (
            "blockquote",
            "source_callout",
            TRANSLATION_PROFILE_SOURCE_CALLOUT,
            "t_only",
        ),
        (
            "paragraph",
            "citation_reference",
            TRANSLATION_PROFILE_CITATION_REFERENCE,
            "t_only",
        ),
        ("paragraph", "prose", TRANSLATION_PROFILE_PROSE, "all_on"),
    ]
    for block_type, role, expected_profile, expected_policy in cases:
        payload = _semantic_metadata(role)
        resolved_policy = resolve_automatic_layer_policy(
            contract_version=SEMANTIC_CONTRACT_V1,
            block_type=block_type,
            payload_json=payload,
        )
        profile = resolve_translation_prompt_profile_for_unit(
            payload,
            block_type=block_type,
        )

        assert profile.profile_id == expected_profile
        assert (
            "t_only"
            if resolved_policy.policy.as_dict()
            == {
                "translation": True,
                "vocabulary": False,
                "grammar_note": False,
                "sentence_analysis": False,
            }
            else "all_on"
        ) == expected_policy


def test_legacy_and_unknown_metadata_fail_open_to_prose() -> None:
    assert (
        resolve_translation_prompt_profile_for_unit(
            {},
            block_type="code_block",
        ).profile_id
        == TRANSLATION_PROFILE_PROSE
    )
    assert (
        resolve_translation_prompt_profile(
            contract_version="semantic_contract_v99",
            block_type="heading",
            content_role="future_role",
        ).profile_id
        == TRANSLATION_PROFILE_PROSE
    )


def test_explicit_section_profile_is_independent_of_automatic_policy() -> None:
    profile = resolve_translation_prompt_profile(
        contract_version=SEMANTIC_CONTRACT_V1,
        block_type="code_block",
        content_role=None,
        explicit_section=True,
    )

    assert profile.profile_id == TRANSLATION_PROFILE_EXPLICIT_SECTION


def test_profile_contract_freezes_order_roles_and_prompt_content_hashes() -> None:
    contract = build_translation_prompt_profile_contract(
        [
            {
                "unit_id": "u2",
                "order_index": 2,
                "unit_type": "blockquote",
                "metadata_json": _semantic_metadata("source_callout"),
            },
            {
                "unit_id": "u1",
                "order_index": 1,
                "unit_type": "heading",
                "metadata_json": _semantic_metadata(None),
            },
        ]
    )

    assert contract["contract_version"] == TRANSLATION_PROMPT_PROFILE_CONTRACT_VERSION
    assert contract["profile_version"] == TRANSLATION_PROMPT_PROFILE_VERSION
    assert [entry["unit_id"] for entry in contract["manifest"]] == ["u1", "u2"]
    assert [entry["profile_id"] for entry in contract["manifest"]] == [
        TRANSLATION_PROFILE_HEADING,
        TRANSLATION_PROFILE_SOURCE_CALLOUT,
    ]
    assert all(entry["profile_content_hash"] for entry in contract["manifest"])
    fields = translation_prompt_profile_input_fields(contract)
    assert fields["translation_prompt_profile_manifest_hash"] == contract[
        "manifest_hash"
    ]
    assert compose_translation_prompt_profile_fingerprint_token(contract).endswith(
        f":{contract['manifest_hash']}"
    )


def test_profile_contract_changes_when_prompt_text_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    original = get_translation_prompt_profile(TRANSLATION_PROFILE_PROSE)
    baseline = build_translation_prompt_profile_contract(
        [
            {
                "unit_id": "u1",
                "order_index": 1,
                "unit_type": "paragraph",
                "metadata_json": _semantic_metadata("prose"),
            }
        ]
    )
    monkeypatch.setitem(
        __import__(
            "app.services.reader_orchestration.translation_prompt_profile",
            fromlist=["_PROFILE_PROMPT_LINES"],
        )._PROFILE_PROMPT_LINES,
        TRANSLATION_PROFILE_PROSE,
        original.prompt_lines + ("drift",),
    )
    drifted = build_translation_prompt_profile_contract(
        [
            {
                "unit_id": "u1",
                "order_index": 1,
                "unit_type": "paragraph",
                "metadata_json": _semantic_metadata("prose"),
            }
        ]
    )
    assert drifted["manifest_hash"] != baseline["manifest_hash"]


def test_worker_profile_contract_validation_fails_before_execution_on_drift() -> None:
    units = [
        {
            "unit_id": "u1",
            "order_index": 1,
            "unit_type": "heading",
            "metadata_json": _semantic_metadata(None),
        }
    ]
    contract = build_translation_prompt_profile_contract(units)
    input_json = translation_prompt_profile_input_fields(contract)
    operation_fingerprint = (
        "translation_unit:strategy:semantic:"
        + compose_translation_prompt_profile_fingerprint_token(contract)
    )

    assert _validate_translation_prompt_profile_contract(
        input_json,
        current_units=units,
        operation_fingerprint=operation_fingerprint,
        explicit_section=False,
    ) == contract

    input_json["translation_prompt_profile_manifest"] = []
    with pytest.raises(TranslationExecutionError) as exc_info:
        _validate_translation_prompt_profile_contract(
            input_json,
            current_units=units,
            operation_fingerprint=operation_fingerprint,
            explicit_section=False,
        )
    assert exc_info.value.failure_code == "translation_prompt_profile_contract_mismatch"
    assert exc_info.value.retryable is False


def test_legacy_profile_contract_fallback_does_not_apply_to_explicit_section() -> None:
    units = [
        {
            "unit_id": "u1",
            "order_index": 1,
            "unit_type": "paragraph",
            "metadata_json": _semantic_metadata("prose"),
        }
    ]

    legacy_contract = _validate_translation_prompt_profile_contract(
        {},
        current_units=units,
        operation_fingerprint="translation_unit:legacy",
        explicit_section=False,
    )
    assert legacy_contract["manifest"] == []
    assert legacy_contract["manifest_hash"] == ""

    with pytest.raises(TranslationExecutionError) as exc_info:
        _validate_translation_prompt_profile_contract(
            {},
            current_units=units,
            operation_fingerprint="translation_section:legacy",
            explicit_section=True,
        )
    assert exc_info.value.failure_code == "translation_prompt_profile_contract_mismatch"


def _segment() -> TranslationAnchorSegmentTarget:
    text = "Alpha."
    return TranslationAnchorSegmentTarget(
        anchor_segment_id="a1",
        sentence_id="s1",
        order_index=1,
        segment_type="sentence",
        boundary_quality="normal",
        unit_start_utf16=0,
        unit_end_utf16=len(text),
        text_hash=compute_text_range_hash(text),
        source_text=text,
    )


def _context(*, profile_id: str = TRANSLATION_PROFILE_PROSE) -> TranslationJobContext:
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers["translation"]
    return TranslationJobContext(
        job_id=UUID("11111111-1111-1111-1111-111111111111"),
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
        reading_record_id=UUID("33333333-3333-3333-3333-333333333333"),
        user_id=UUID("44444444-4444-4444-4444-444444444444"),
        base_id=UUID("55555555-5555-5555-5555-555555555555"),
        unit_id="u1",
        order_index=1,
        expected_generation=1,
        operation_fingerprint="test",
        source_language="en",
        target_language="zh-CN",
        source_text="Alpha.",
        text_hash=compute_text_range_hash("Alpha."),
        anchor_segments=(_segment(),),
        reading_goal=strategy.reading_goal,
        reading_variant=strategy.reading_variant,
        strategy_version=strategy.strategy_version,
        strategy_hash=strategy.strategy_hash,
        layer_policy_hash=layer.policy_hash,
        translation_prompt_lines=layer.prompt_lines,
        translation_prompt_profile_id=profile_id,
    )


def test_per_unit_prompt_golden_contains_versioned_profile_contract() -> None:
    prompt = _build_translation_prompt(
        _context(profile_id=TRANSLATION_PROFILE_CITATION_REFERENCE)
    )
    profile = get_translation_prompt_profile(TRANSLATION_PROFILE_CITATION_REFERENCE)

    assert "<translation_prompt_profile>" in prompt
    assert f"profile_version: {TRANSLATION_PROMPT_PROFILE_VERSION}" in prompt
    assert "profile_id: citation_reference" in prompt
    for line in profile.prompt_lines:
        assert line in prompt


def _batch_context() -> TranslationBatchJobContext:
    strategy = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    layer = strategy.layers["translation"]
    first = TranslationBatchUnitContext(
        unit_id="u1",
        order_index=1,
        source_text="A heading.",
        text_hash=compute_text_range_hash("A heading."),
        anchor_segments=(_segment(),),
        translation_prompt_profile_id=TRANSLATION_PROFILE_HEADING,
    )
    second = TranslationBatchUnitContext(
        unit_id="u2",
        order_index=2,
        source_text="A callout.",
        text_hash=compute_text_range_hash("A callout."),
        anchor_segments=(
            TranslationAnchorSegmentTarget(
                anchor_segment_id="a2",
                sentence_id="s2",
                order_index=2,
                segment_type="sentence",
                boundary_quality="normal",
                unit_start_utf16=0,
                unit_end_utf16=len("A callout."),
                text_hash=compute_text_range_hash("A callout."),
                source_text="A callout.",
            ),
        ),
        translation_prompt_profile_id=TRANSLATION_PROFILE_SOURCE_CALLOUT,
    )
    return TranslationBatchJobContext(
        job_id=UUID("11111111-1111-1111-1111-111111111111"),
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
        reading_record_id=UUID("33333333-3333-3333-3333-333333333333"),
        user_id=UUID("44444444-4444-4444-4444-444444444444"),
        base_id=UUID("55555555-5555-5555-5555-555555555555"),
        expected_generation=1,
        operation_fingerprint="test",
        source_language="en",
        target_language="zh-CN",
        target_unit_ids=("u1", "u2"),
        units=(first, second),
        reading_goal=strategy.reading_goal,
        reading_variant=strategy.reading_variant,
        strategy_version=strategy.strategy_version,
        strategy_hash=strategy.strategy_hash,
        layer_policy_hash=layer.policy_hash,
        translation_prompt_lines=layer.prompt_lines,
    )


def test_batch_prompt_declares_mixed_profiles_per_unit() -> None:
    prompt = _build_translation_batch_prompt(_batch_context())

    assert "profile_mode: per_unit" in prompt
    assert '<unit_profile unit_id="u1">' in prompt
    assert "profile_id: heading" in prompt
    assert '<unit_profile unit_id="u2">' in prompt
    assert "profile_id: source_callout" in prompt
    assert "Do not apply one unit's profile to another unit." in prompt


class _FakeTranslationExecutor:
    """Golden-prompt executor: records prompts and never calls a provider."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def translate(self, context: TranslationJobContext) -> TranslationExecutionResult:
        self.prompts.append(_build_translation_prompt(context))
        return TranslationExecutionResult(
            output=TranslationLayerGenerationOutput(
                groups=[
                    TranslationGenerationGroup(
                        anchor_segment_ids=["a1"],
                        translated_text="阿尔法。",
                    )
                ]
            ),
            prompt_version=TRANSLATION_PROMPT_PROFILE_VERSION,
        )


@pytest.mark.anyio
async def test_fake_executor_receives_role_profile_prompt_without_real_llm() -> None:
    executor = _FakeTranslationExecutor()
    await executor.translate(_context(profile_id=TRANSLATION_PROFILE_SOURCE_CALLOUT))

    assert len(executor.prompts) == 1
    assert "profile_id: source_callout" in executor.prompts[0]
