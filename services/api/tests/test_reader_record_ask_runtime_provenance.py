"""Runtime provenance tests for Reading Record Ask structured answer blocks.

Covers grounding validation of article / general / mixed answer blocks,
evidence-handle binding across envelopes, knowledge_mode derivation,
clarification output, fail-closed evidence kinds, finalizer projection,
and the runtime's retry-budget behavior.
"""

from __future__ import annotations

import json
import re
import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from pydantic_ai.exceptions import ModelRetry, UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.services.reader_record_ask import runtime as runtime_module
from app.services.reader_record_ask.agent import (
    DEFAULT_OUTPUT_RETRIES,
)
from app.services.reader_record_ask.context_envelope import (
    ReadingRecordAskContextEnvelope,
    VerifiedEnvelopeInput,
    build_context_envelope,
)
from app.services.reader_record_ask.document_access import (
    InMemoryDocumentAccess,
    ReadingUnitView,
    build_document_scope,
)
from app.services.reader_record_ask.evidence import (
    build_server_evidence_observation,
)
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.fence import StaticGenerationFence
from app.services.reader_record_ask.grounding_validator import (
    AgentAnswerBlockOutput,
    AgentAnswerDraftOutput,
    build_evidence_validation_context,
    grounding_validator,
)
from app.services.reader_record_ask.runtime import (
    _to_finalizer_draft,
    run_reading_record_ask,
)
from app.services.reader_record_ask.runtime_deps import ReaderRecordAskDeps


def _envelope(*, generation: int = 1) -> ReadingRecordAskContextEnvelope:
    return build_context_envelope(
        VerifiedEnvelopeInput(
            user_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
            reading_record_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
            base_id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
            record_generation=generation,
            stable_document_id=None,
            base_content_sha256=None,
            product_state="readable_enhancing",
            readiness_state="article_ready",
            initial_anchor=None,
            visible_range=None,
        )
    )


def _document_access() -> InMemoryDocumentAccess:
    text = "文章确认了人物身份，后续背景介绍可以使用通用知识。"
    return InMemoryDocumentAccess(
        snapshot=build_document_scope(
            reading_record_id=uuid.UUID(
                "22222222-2222-2222-2222-222222222222"
            ),
            base_id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
            record_generation=1,
            units=[
                ReadingUnitView(
                    unit_id="unit-1",
                    order_index=0,
                    text=text,
                    text_hash="aaaaaaaa",
                    base_start_utf16=0,
                    base_end_utf16=len(text),
                )
            ],
            segments=[],
            stable_document_id=None,
            base_content_sha256=None,
        )
    )


def _registry_with_article(
    envelope: ReadingRecordAskContextEnvelope,
) -> tuple[EvidenceRegistry, str]:
    registry = EvidenceRegistry(envelope.envelope_fingerprint)
    handle = registry.register(
        build_server_evidence_observation(
            kind="article_seed",
            envelope_fingerprint=envelope.envelope_fingerprint,
            source_tool="baseline_context",
            snippet="文章确认的人物身份。",
            unit_id="unit-1",
        )
    )
    return registry, handle.handle_id


def _ctx(
    *,
    registry: EvidenceRegistry,
    confirmed_article_scopes: frozenset[str] = frozenset(
        {"evidence_bounded"}
    ),
) -> SimpleNamespace:
    envelope = _envelope()
    deps = ReaderRecordAskDeps(
        envelope=envelope,
        document_access=None,  # type: ignore[arg-type]
        fence=StaticGenerationFence(live_generation=1),
        evidence_registry=registry,
        confirmed_article_scopes=confirmed_article_scopes,  # type: ignore[arg-type]
    )
    return SimpleNamespace(deps=deps, partial_output=False)


@pytest.mark.asyncio
async def test_ordinary_general_only_answer_derives_general_knowledge() -> None:
    envelope = _envelope()
    draft = AgentAnswerDraftOutput(
        response_kind="grounded_answer",
        answer_blocks=[
            AgentAnswerBlockOutput(
                text="这是明确标注的通用知识补充。",
                basis="general",
                article_scope=None,
                evidence_handles=[],
            )
        ],
    )

    validated = await grounding_validator(
        _ctx(
            registry=EvidenceRegistry(envelope.envelope_fingerprint),
        ),
        draft,
    )

    assert validated.validated_answer_blocks.knowledge_mode == "general_knowledge"


@pytest.mark.asyncio
async def test_ordinary_article_answer_uses_current_envelope_evidence() -> None:
    envelope = _envelope()
    registry, handle_id = _registry_with_article(envelope)
    draft = AgentAnswerDraftOutput(
        response_kind="grounded_answer",
        answer_blocks=[
            AgentAnswerBlockOutput(
                text="文章确认了人物身份。",
                basis="article",
                article_scope="evidence_bounded",
                evidence_handles=[handle_id],
            )
        ],
    )

    validated = await grounding_validator(
        _ctx(registry=registry),
        draft,
    )

    assert validated.validated_answer_blocks.knowledge_mode == "article_grounded"
    assert (
        validated.validated_answer_blocks.blocks[0].evidence_handles
        == (handle_id,)
    )


@pytest.mark.asyncio
async def test_ordinary_mixed_answer_derives_mixed_mode() -> None:
    envelope = _envelope()
    registry, handle_id = _registry_with_article(envelope)
    draft = AgentAnswerDraftOutput(
        response_kind="grounded_answer",
        answer_blocks=[
            AgentAnswerBlockOutput(
                text="文章确认了人物身份。",
                basis="article",
                article_scope="evidence_bounded",
                evidence_handles=[handle_id],
            ),
            AgentAnswerBlockOutput(
                text="以下人物背景属于通用知识。",
                basis="general",
                article_scope=None,
                evidence_handles=[],
            ),
        ],
    )

    validated = await grounding_validator(
        _ctx(registry=registry),
        draft,
    )

    assert validated.validated_answer_blocks.knowledge_mode == "mixed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "draft",
    [
        AgentAnswerDraftOutput(
            response_kind="grounded_answer",
            answer_blocks=[
                AgentAnswerBlockOutput(
                    text="没有证据的文章结论。",
                    basis="article",
                    article_scope="evidence_bounded",
                    evidence_handles=[],
                )
            ],
        ),
        AgentAnswerDraftOutput(
            response_kind="grounded_answer",
            answer_blocks=[
                AgentAnswerBlockOutput(
                    text="伪造证据的文章结论。",
                    basis="article",
                    article_scope="evidence_bounded",
                    evidence_handles=["evh_00000000000000000000000000000000"],
                )
            ],
        ),
    ],
    ids=["missing_handle", "fabricated_handle"],
)
async def test_article_block_without_registered_evidence_retries(
    draft: AgentAnswerDraftOutput,
) -> None:
    envelope = _envelope()

    with pytest.raises(ModelRetry):
        await grounding_validator(
            _ctx(
                registry=EvidenceRegistry(envelope.envelope_fingerprint),
            ),
            draft,
        )

    assert draft.validated_answer_blocks is None
    assert draft.knowledge_mode is None


@pytest.mark.asyncio
async def test_article_block_rejects_handle_from_another_envelope() -> None:
    other_envelope = _envelope(generation=2)
    other_registry, other_handle_id = _registry_with_article(other_envelope)
    draft = AgentAnswerDraftOutput(
        response_kind="grounded_answer",
        answer_blocks=[
            AgentAnswerBlockOutput(
                text="来自其他 turn envelope 的文章结论。",
                basis="article",
                article_scope="evidence_bounded",
                evidence_handles=[other_handle_id],
            )
        ],
    )

    with pytest.raises(ModelRetry, match="foreign envelope"):
        await grounding_validator(
            _ctx(registry=other_registry),
            draft,
        )

    assert draft.validated_answer_blocks is None
    assert draft.knowledge_mode is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "block",
    [
        AgentAnswerBlockOutput(
            text="错误携带文章证据的通用知识。",
            basis="general",
            article_scope=None,
            evidence_handles=["evh_00000000000000000000000000000000"],
        ),
        AgentAnswerBlockOutput(
            text="错误声明文章范围的通用知识。",
            basis="general",
            article_scope="evidence_bounded",
            evidence_handles=[],
        ),
    ],
    ids=["evidence", "article_scope"],
)
async def test_general_block_cannot_claim_article_provenance(
    block: AgentAnswerBlockOutput,
) -> None:
    envelope = _envelope()

    with pytest.raises(ModelRetry):
        await grounding_validator(
            _ctx(
                registry=EvidenceRegistry(envelope.envelope_fingerprint),
            ),
            AgentAnswerDraftOutput(
                response_kind="grounded_answer",
                answer_blocks=[block],
            ),
        )


@pytest.mark.asyncio
async def test_partial_coverage_cannot_claim_full_article() -> None:
    envelope = _envelope()
    registry, handle_id = _registry_with_article(envelope)

    with pytest.raises(ModelRetry, match="not confirmed by current coverage"):
        await grounding_validator(
            _ctx(
                registry=registry,
                confirmed_article_scopes=frozenset({"evidence_bounded"}),
            ),
            AgentAnswerDraftOutput(
                response_kind="grounded_answer",
                answer_blocks=[
                    AgentAnswerBlockOutput(
                        text="未经完整覆盖确认的全文结论。",
                        basis="article",
                        article_scope="full_article",
                        evidence_handles=[handle_id],
                    )
                ],
            ),
        )


def test_model_output_schema_has_no_knowledge_mode_or_legacy_flat_escape() -> None:
    schema = AgentAnswerDraftOutput.model_json_schema()
    assert "knowledge_mode" not in str(schema)
    assert "answer_text" not in schema["properties"]
    assert "cited_evidence_handles" not in schema["properties"]

    with pytest.raises(ValidationError):
        AgentAnswerDraftOutput.model_validate(
            {
                "response_kind": "grounded_answer",
                "answer_text": "旧 flat 无 handle 输出。",
                "cited_evidence_handles": [],
            }
        )
    with pytest.raises(ValidationError):
        AgentAnswerDraftOutput.model_validate(
            {
                "response_kind": "grounded_answer",
                "knowledge_mode": "article_grounded",
                "answer_blocks": [
                    {
                        "text": "试图伪造 knowledge_mode。",
                        "basis": "general",
                        "article_scope": None,
                        "evidence_handles": [],
                    }
                ],
            }
        )


def test_clarification_schema_requires_text_and_forbids_answer_blocks() -> None:
    clarification = AgentAnswerDraftOutput.model_validate(
        {
            "response_kind": "clarification",
            "clarification_text": "请明确你想了解文章的哪一部分。",
            "answer_blocks": [],
        }
    )
    assert clarification.clarification_text == "请明确你想了解文章的哪一部分。"
    assert clarification.answer_blocks == []
    assert clarification.cited_evidence_handles == []
    assert clarification.validated_answer_blocks is None

    invalid_payloads = [
        {
            "response_kind": "clarification",
            "clarification_text": "请明确问题。",
            "answer_blocks": [
                {
                    "text": "clarification 不得携带答案 block。",
                    "basis": "general",
                    "article_scope": None,
                    "evidence_handles": [],
                }
            ],
        },
        {
            "response_kind": "clarification",
            "clarification_text": None,
            "answer_blocks": [],
        },
        {
            "response_kind": "grounded_answer",
            "clarification_text": "grounded answer 不得伪装成澄清。",
            "answer_blocks": [
                {
                    "text": "有效答案。",
                    "basis": "general",
                    "article_scope": None,
                    "evidence_handles": [],
                }
            ],
        },
        {
            "response_kind": "grounded_answer",
            "clarification_text": None,
            "answer_blocks": [],
        },
    ]
    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            AgentAnswerDraftOutput.model_validate(payload)


@pytest.mark.asyncio
async def test_clarification_validator_has_no_blocks_or_knowledge_mode() -> None:
    envelope = _envelope()
    draft = AgentAnswerDraftOutput(
        response_kind="clarification",
        clarification_text="你希望了解人物身份，还是人物背景？",
        answer_blocks=[],
    )

    validated = await grounding_validator(
        _ctx(
            registry=EvidenceRegistry(envelope.envelope_fingerprint),
        ),
        draft,
    )

    assert validated is draft
    assert validated.validated_answer_blocks is None
    assert validated.cited_evidence_handles == []


@pytest.mark.asyncio
async def test_unknown_evidence_kind_is_rejected_fail_closed() -> None:
    envelope = _envelope()
    valid_observation = build_server_evidence_observation(
        kind="article_seed",
        envelope_fingerprint=envelope.envelope_fingerprint,
        source_tool="baseline_context",
        snippet="合法文章证据。",
        unit_id="unit-1",
    )
    future_handle = valid_observation.handle.model_copy(
        update={"kind": "web_hit"}
    )
    future_observation = valid_observation.model_copy(
        update={"handle": future_handle}
    )
    unknown_registry = SimpleNamespace(
        list_observations=lambda: (future_observation,)
    )
    ctx = _ctx(
        registry=unknown_registry,  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="unsupported evidence kind"):
        build_evidence_validation_context(ctx.deps)

    with pytest.raises(ModelRetry, match="unsupported evidence kind"):
        await grounding_validator(
            ctx,
            AgentAnswerDraftOutput(
                response_kind="grounded_answer",
                answer_blocks=[
                    AgentAnswerBlockOutput(
                        text="不得把未来 Web evidence 当成 article。",
                        basis="article",
                        article_scope="evidence_bounded",
                        evidence_handles=[future_handle.handle_id],
                    )
                ],
            ),
        )


@pytest.mark.asyncio
async def test_finalizer_projection_uses_head_flat_constructor_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = _envelope()
    registry, handle_id = _registry_with_article(envelope)
    draft = await grounding_validator(
        _ctx(registry=registry),
        AgentAnswerDraftOutput(
            response_kind="grounded_answer",
            answer_blocks=[
                AgentAnswerBlockOutput(
                    text="文章确认了人物身份。",
                    basis="article",
                    article_scope="evidence_bounded",
                    evidence_handles=[handle_id],
                ),
                AgentAnswerBlockOutput(
                    text="人物背景属于通用知识。",
                    basis="general",
                    article_scope=None,
                    evidence_handles=[],
                ),
            ],
        ),
    )

    class HeadOnlyFinalizerDraft:
        def __init__(
            self,
            *,
            answer_text: str,
            cited_evidence_handles: list[str],
            response_kind: str,
        ) -> None:
            self.answer_text = answer_text
            self.cited_evidence_handles = cited_evidence_handles
            self.response_kind = response_kind

    monkeypatch.setattr(
        runtime_module,
        "FinalizerAgentAnswerDraft",
        HeadOnlyFinalizerDraft,
    )

    projected = _to_finalizer_draft(draft)
    assert projected.answer_text == (
        "文章确认了人物身份。\n\n人物背景属于通用知识。"
    )
    assert projected.cited_evidence_handles == [handle_id]
    assert projected.response_kind == "grounded_answer"

    clarification = AgentAnswerDraftOutput(
        response_kind="clarification",
        clarification_text="请明确你希望了解的范围。",
        answer_blocks=[],
    )
    projected_clarification = _to_finalizer_draft(clarification)
    assert projected_clarification.answer_text == "请明确你希望了解的范围。"
    assert projected_clarification.cited_evidence_handles == []
    assert projected_clarification.response_kind == "clarification"


@pytest.mark.asyncio
async def test_runtime_validates_mixed_blocks_before_finalizer_projection() -> None:
    async def model_fn(messages, info):
        del info
        prompt = "".join(
            str(getattr(part, "content", "") or "")
            for message in messages
            for part in getattr(message, "parts", ())
        )
        handle_match = re.search(r"evh_[0-9a-f]{32}", prompt)
        assert handle_match is not None
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="final_result",
                    args=json.dumps(
                        {
                            "response_kind": "grounded_answer",
                            "answer_blocks": [
                                {
                                    "text": "文章确认了人物身份。",
                                    "basis": "article",
                                    "article_scope": "evidence_bounded",
                                    "evidence_handles": [handle_match.group(0)],
                                },
                                {
                                    "text": "以下人物背景属于通用知识。",
                                    "basis": "general",
                                    "article_scope": None,
                                    "evidence_handles": [],
                                },
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    tool_call_id="final-1",
                )
            ]
        )

    result = await run_reading_record_ask(
        user_message="先确认文章人物，再介绍其背景。",
        envelope=_envelope(),
        document_access=_document_access(),
        model=FunctionModel(model_fn),
    )

    assert result.validated_answer_blocks is not None
    assert result.validated_answer_blocks.knowledge_mode == "mixed"
    assert result.final_text == (
        "文章确认了人物身份。\n\n以下人物背景属于通用知识。"
    )


@pytest.mark.asyncio
async def test_runtime_clarification_has_no_validated_blocks_or_evidence() -> None:
    async def model_fn(messages, info):
        del messages, info
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="final_result",
                    args=json.dumps(
                        {
                            "response_kind": "clarification",
                            "clarification_text": "你希望了解人物身份，还是人物背景？",
                            "answer_blocks": [],
                        },
                        ensure_ascii=False,
                    ),
                    tool_call_id="clarification-1",
                )
            ]
        )

    result = await run_reading_record_ask(
        user_message="介绍一下。",
        envelope=_envelope(),
        document_access=_document_access(),
        model=FunctionModel(model_fn),
    )

    assert result.final_text == "你希望了解人物身份，还是人物背景？"
    assert result.validated_answer_blocks is None
    assert result.agent_output is not None
    assert result.agent_output.validated_answer_blocks is None
    assert result.agent_output.knowledge_mode is None
    assert result.agent_output.answer_blocks == []
    assert result.agent_output.cited_evidence_handles == []
    assert result.agent_draft is not None
    assert result.agent_draft.cited_evidence_handles == []
    assert result.finalized is not None
    assert result.finalized.resolved_evidence == ()


@pytest.mark.asyncio
async def test_online_legacy_flat_no_handle_output_uses_retry_budget() -> None:
    model_calls = 0

    async def model_fn(messages, info):
        del messages, info
        nonlocal model_calls
        model_calls += 1
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="final_result",
                    args=json.dumps(
                        {
                            "response_kind": "grounded_answer",
                            "answer_text": "旧 flat 无 handle 回答。",
                            "cited_evidence_handles": [],
                        },
                        ensure_ascii=False,
                    ),
                    tool_call_id=f"legacy-{model_calls}",
                )
            ]
        )

    with pytest.raises(UnexpectedModelBehavior):
        await run_reading_record_ask(
            user_message="请回答。",
            envelope=_envelope(),
            document_access=_document_access(),
            model=FunctionModel(model_fn),
        )

    assert model_calls == DEFAULT_OUTPUT_RETRIES + 1
