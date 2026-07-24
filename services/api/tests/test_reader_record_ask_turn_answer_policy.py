"""Pure-contract tests for the Ask turn answer provenance policy."""

from __future__ import annotations

import pytest

from app.services.reader_record_ask.turn_answer_policy import (
    AnswerBlockDraft,
    EvidenceValidationContext,
    TurnAnswerPolicy,
    ValidatedEvidence,
    validate_answer_blocks,
)


def _policy(
    *,
    article_only: bool = False,
    citation_required: bool = False,
    requested_citation_scope: str = "none",
    web_capability: str = "unavailable",
) -> TurnAnswerPolicy:
    return TurnAnswerPolicy(
        article_only=article_only,
        citation_required=citation_required,
        requested_citation_scope=requested_citation_scope,  # type: ignore[arg-type]
        web_capability=web_capability,  # type: ignore[arg-type]
    )


def _context(
    *evidence: ValidatedEvidence,
    article_scopes: frozenset[str] = frozenset({"evidence_bounded"}),
) -> EvidenceValidationContext:
    return EvidenceValidationContext(
        envelope_id="turn-current",
        evidence=tuple(evidence),
        confirmed_article_scopes=article_scopes,  # type: ignore[arg-type]
    )


def _article_evidence(
    *,
    handle_id: str = "evh_article",
    envelope_id: str = "turn-current",
    publicly_mappable: bool = True,
) -> ValidatedEvidence:
    return ValidatedEvidence(
        handle_id=handle_id,
        source_kind="article",
        envelope_id=envelope_id,
        publicly_mappable=publicly_mappable,
    )


def _article_block(
    *,
    handles: tuple[str, ...] = ("evh_article",),
    article_scope: str | None = "evidence_bounded",
) -> AnswerBlockDraft:
    return AnswerBlockDraft(
        text="文章观点。",
        basis="article",
        article_scope=article_scope,  # type: ignore[arg-type]
        evidence_handles=handles,
    )


def _general_block(*, handles: tuple[str, ...] = ()) -> AnswerBlockDraft:
    return AnswerBlockDraft(
        text="这是背景补充。",
        basis="general",
        article_scope=None,
        evidence_handles=handles,
    )


def _web_block() -> AnswerBlockDraft:
    return AnswerBlockDraft(
        text="实时网页结论。",
        basis="web",
        article_scope=None,
        evidence_handles=("evh_web",),
    )


def test_general_block_without_evidence_is_valid_and_derives_general_mode() -> None:
    result = validate_answer_blocks(
        policy=_policy(),
        blocks=(_general_block(),),
        evidence_context=_context(),
    )

    assert result.knowledge_mode == "general_knowledge"
    assert result.blocks == (_general_block(),)


def test_general_block_with_evidence_handle_is_rejected() -> None:
    with pytest.raises(ValueError, match="general"):
        validate_answer_blocks(
            policy=_policy(),
            blocks=(_general_block(handles=("evh_article",)),),
            evidence_context=_context(_article_evidence()),
        )


def test_general_block_requires_null_article_scope() -> None:
    draft = AnswerBlockDraft(
        text="这是背景补充。",
        basis="general",
        article_scope="evidence_bounded",
        evidence_handles=(),
    )

    with pytest.raises(ValueError, match="article_scope=null"):
        validate_answer_blocks(
            policy=_policy(),
            blocks=(draft,),
            evidence_context=_context(),
        )


def test_article_block_without_handle_is_rejected_without_downgrading_basis() -> None:
    draft = _article_block(handles=())

    with pytest.raises(ValueError, match="article"):
        validate_answer_blocks(
            policy=_policy(),
            blocks=(draft,),
            evidence_context=_context(_article_evidence()),
        )

    assert draft.basis == "article"


def test_article_block_with_current_article_evidence_is_valid() -> None:
    result = validate_answer_blocks(
        policy=_policy(),
        blocks=(_article_block(),),
        evidence_context=_context(_article_evidence()),
    )

    assert result.knowledge_mode == "article_grounded"


def test_article_block_cannot_use_web_evidence() -> None:
    with pytest.raises(ValueError, match="web evidence"):
        validate_answer_blocks(
            policy=_policy(),
            blocks=(_article_block(handles=("evh_web",)),),
            evidence_context=_context(
                ValidatedEvidence(
                    handle_id="evh_web",
                    source_kind="web",
                    envelope_id="turn-current",
                    publicly_mappable=True,
                )
            ),
        )


@pytest.mark.parametrize(
    ("article_only", "citation_required", "requested_citation_scope", "web_capability"),
    [
        (False, False, "article", "unavailable"),
        (False, False, "web", "unavailable"),
        (False, True, "none", "unavailable"),
        (True, True, "web", "available"),
    ],
)
def test_turn_answer_policy_rejects_impossible_combinations(
    article_only: bool,
    citation_required: bool,
    requested_citation_scope: str,
    web_capability: str,
) -> None:
    with pytest.raises(ValueError):
        _policy(
            article_only=article_only,
            citation_required=citation_required,
            requested_citation_scope=requested_citation_scope,
            web_capability=web_capability,
        )


@pytest.mark.parametrize("block", [_general_block(), _web_block()])
def test_article_only_rejects_non_article_blocks(block: AnswerBlockDraft) -> None:
    with pytest.raises(ValueError, match="article_only"):
        validate_answer_blocks(
            policy=_policy(article_only=True),
            blocks=(block,),
            evidence_context=_context(_article_evidence()),
        )


def test_article_citation_request_rejects_general_only_answer() -> None:
    with pytest.raises(ValueError, match="article citation"):
        validate_answer_blocks(
            policy=_policy(
                citation_required=True,
                requested_citation_scope="article",
            ),
            blocks=(_general_block(),),
            evidence_context=_context(_article_evidence()),
        )


def test_article_citation_request_requires_publicly_mappable_article_block() -> None:
    with pytest.raises(ValueError, match="publicly mappable"):
        validate_answer_blocks(
            policy=_policy(
                citation_required=True,
                requested_citation_scope="article",
            ),
            blocks=(_article_block(),),
            evidence_context=_context(_article_evidence(publicly_mappable=False)),
        )


def test_article_citation_request_accepts_publicly_mappable_article_block() -> None:
    result = validate_answer_blocks(
        policy=_policy(
            citation_required=True,
            requested_citation_scope="article",
        ),
        blocks=(_article_block(),),
        evidence_context=_context(_article_evidence(publicly_mappable=True)),
    )

    assert result.knowledge_mode == "article_grounded"


def test_web_scope_with_unavailable_capability_is_host_owned_before_drafting() -> None:
    policy = _policy(
        citation_required=True,
        requested_citation_scope="web",
        web_capability="unavailable",
    )

    assert policy.host_drafting_decision().kind == "web_unavailable"


def test_web_block_is_rejected_in_v1_even_when_capability_is_available() -> None:
    with pytest.raises(ValueError, match="v1"):
        validate_answer_blocks(
            policy=_policy(web_capability="available"),
            blocks=(_web_block(),),
            evidence_context=_context(),
        )


def test_article_scope_requires_host_confirmed_coverage() -> None:
    with pytest.raises(ValueError, match="full_article"):
        validate_answer_blocks(
            policy=_policy(),
            blocks=(_article_block(article_scope="full_article"),),
            evidence_context=_context(_article_evidence()),
        )


def test_full_article_scope_is_valid_when_host_explicitly_confirms_it() -> None:
    result = validate_answer_blocks(
        policy=_policy(),
        blocks=(_article_block(article_scope="full_article"),),
        evidence_context=_context(
            _article_evidence(),
            article_scopes=frozenset({"full_article"}),
        ),
    )

    assert result.knowledge_mode == "article_grounded"


@pytest.mark.parametrize(
    ("blocks", "expected_mode"),
    [
        ((_article_block(),), "article_grounded"),
        ((_general_block(),), "general_knowledge"),
        ((_article_block(), _general_block()), "mixed"),
    ],
)
def test_knowledge_mode_is_derived_from_validated_blocks(
    blocks: tuple[AnswerBlockDraft, ...],
    expected_mode: str,
) -> None:
    result = validate_answer_blocks(
        policy=_policy(),
        blocks=blocks,
        evidence_context=_context(_article_evidence()),
    )

    assert result.knowledge_mode == expected_mode


def test_knowledge_mode_cannot_be_supplied_on_the_draft() -> None:
    with pytest.raises(TypeError):
        AnswerBlockDraft(  # type: ignore[call-arg]
            text="文章观点。",
            basis="article",
            article_scope="evidence_bounded",
            evidence_handles=("evh_article",),
            knowledge_mode="general_knowledge",
        )


def test_foreign_envelope_evidence_is_not_valid_article_evidence() -> None:
    with pytest.raises(ValueError, match="envelope"):
        _context(_article_evidence(envelope_id="turn-foreign"))


def test_article_source_unavailable_is_a_host_owned_internal_outcome() -> None:
    policy = _policy(
        citation_required=True,
        requested_citation_scope="article",
    )

    assert policy.article_source_unavailable_outcome().kind == "article_source_unavailable"


def test_required_web_with_available_capability_is_host_blocked_before_drafting() -> None:
    policy = _policy(
        citation_required=True,
        requested_citation_scope="web",
        web_capability="available",
    )

    assert policy.host_drafting_decision().kind == "web_not_supported_in_v1"


@pytest.mark.parametrize(
    "blocks",
    [
        (_general_block(),),
        (_article_block(),),
        (_article_block(), _general_block()),
    ],
    ids=["general", "article", "mixed"],
)
def test_required_web_citation_cannot_be_satisfied_by_non_web_drafts(
    blocks: tuple[AnswerBlockDraft, ...],
) -> None:
    with pytest.raises(ValueError, match="host must handle"):
        validate_answer_blocks(
            policy=_policy(
                citation_required=True,
                requested_citation_scope="web",
                web_capability="available",
            ),
            blocks=blocks,
            evidence_context=_context(_article_evidence()),
        )


def test_validated_answer_blocks_do_not_retain_mutable_evidence_handle_list() -> None:
    handles = ["evh_article"]
    result = validate_answer_blocks(
        policy=_policy(),
        blocks=(
            AnswerBlockDraft(
                text="文章观点。",
                basis="article",
                article_scope="evidence_bounded",
                evidence_handles=handles,  # type: ignore[arg-type]
            ),
        ),
        evidence_context=_context(_article_evidence()),
    )

    handles.clear()

    assert result.blocks[0].evidence_handles == ("evh_article",)


def test_evidence_validation_context_does_not_retain_mutable_inputs() -> None:
    evidence = [_article_evidence()]
    article_scopes = {"evidence_bounded"}
    context = EvidenceValidationContext(
        envelope_id="turn-current",
        evidence=evidence,  # type: ignore[arg-type]
        confirmed_article_scopes=article_scopes,  # type: ignore[arg-type]
    )

    evidence.clear()
    article_scopes.clear()

    result = validate_answer_blocks(
        policy=_policy(),
        blocks=(_article_block(),),
        evidence_context=context,
    )

    assert result.blocks[0].evidence_handles == ("evh_article",)


@pytest.mark.parametrize("publicly_mappable", ["false", 1])
def test_validated_evidence_rejects_non_bool_publicly_mappable(
    publicly_mappable: object,
) -> None:
    with pytest.raises(ValueError, match="publicly_mappable"):
        ValidatedEvidence(
            handle_id="evh_article",
            source_kind="article",
            envelope_id="turn-current",
            publicly_mappable=publicly_mappable,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("article_only", "citation_required", "requested_citation_scope"),
    [
        (1, False, "none"),
        (False, 1, "article"),
        ("true", False, "none"),
        (False, "true", "article"),
    ],
    ids=[
        "article_only_integer",
        "citation_required_integer",
        "article_only_string",
        "citation_required_string",
    ],
)
def test_turn_answer_policy_rejects_non_bool_safety_flags(
    article_only: object,
    citation_required: object,
    requested_citation_scope: str,
) -> None:
    with pytest.raises(ValueError, match="article_only|citation_required"):
        _policy(
            article_only=article_only,  # type: ignore[arg-type]
            citation_required=citation_required,  # type: ignore[arg-type]
            requested_citation_scope=requested_citation_scope,
        )
