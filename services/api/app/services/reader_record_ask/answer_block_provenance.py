"""Pure, server-owned block-level provenance validation for one Ask turn.

This module deliberately does not know about agents, prompts, finalization,
wire DTOs, persistence, Web rendering, or user intent.  The answer Agent
owns every semantic decision (whether to expand evidence, whether to search
the article, which basis a block uses).  The Host only verifies what is
mechanically provable: each block's basis obeys the evidence contract,
article scopes are backed by server-confirmed coverage, and every evidence
handle belongs to the current turn's envelope.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias

AnswerBlockBasis: TypeAlias = Literal["article", "general", "web"]
ArticleScope: TypeAlias = Literal[
    "selection_bounded",
    "evidence_bounded",
    "article_overview",
    "full_article",
]
KnowledgeMode: TypeAlias = Literal[
    "article_grounded",
    "general_knowledge",
    "web_grounded",
    "mixed",
]
EvidenceSourceKind: TypeAlias = Literal["article", "web"]

_ARTICLE_SCOPES = frozenset(
    {
        "selection_bounded",
        "evidence_bounded",
        "article_overview",
        "full_article",
    }
)
_ANSWER_BLOCK_BASES = frozenset({"article", "general", "web"})
_EVIDENCE_SOURCE_KINDS = frozenset({"article", "web"})


@dataclass(frozen=True, slots=True)
class AnswerBlockDraft:
    """One semantic answer block before finalization.

    ``evidence_handles`` are opaque, internal-only handles.  ``knowledge_mode``
    intentionally is not a draft field; it is derived only after validation.
    """

    text: str
    basis: AnswerBlockBasis
    article_scope: ArticleScope | None
    evidence_handles: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_handles, list | tuple):
            raise ValueError("evidence_handles must be an immutable-safe sequence")
        object.__setattr__(self, "evidence_handles", tuple(self.evidence_handles))
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("answer block text must be a non-empty str")
        if self.basis not in _ANSWER_BLOCK_BASES:
            raise ValueError("basis must be article, general, or web")
        if self.article_scope is not None and self.article_scope not in _ARTICLE_SCOPES:
            raise ValueError("article_scope is not a legal article coverage scope")
        if any(
            not isinstance(handle_id, str) or not handle_id
            for handle_id in self.evidence_handles
        ):
            raise ValueError("evidence_handles must contain non-empty opaque handle strings")


@dataclass(frozen=True, slots=True)
class ValidatedEvidence:
    """A host-confirmed internal evidence handle usable by the provenance seam."""

    handle_id: str
    source_kind: EvidenceSourceKind
    envelope_id: str
    publicly_mappable: bool

    def __post_init__(self) -> None:
        if not isinstance(self.handle_id, str) or not self.handle_id:
            raise ValueError("validated evidence requires a non-empty handle_id")
        if self.source_kind not in _EVIDENCE_SOURCE_KINDS:
            raise ValueError("validated evidence source_kind must be article or web")
        if not isinstance(self.envelope_id, str) or not self.envelope_id:
            raise ValueError("validated evidence requires a non-empty envelope_id")
        if type(self.publicly_mappable) is not bool:
            raise ValueError("validated evidence publicly_mappable must be a bool")


@dataclass(frozen=True, slots=True)
class EvidenceValidationContext:
    """Host-provided evidence and coverage facts for the current envelope."""

    envelope_id: str
    evidence: tuple[ValidatedEvidence, ...]
    confirmed_article_scopes: frozenset[ArticleScope]

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, list | tuple):
            raise ValueError("evidence validation context evidence must be immutable-safe")
        if not isinstance(
            self.confirmed_article_scopes,
            set | frozenset | list | tuple,
        ):
            raise ValueError(
                "evidence validation context confirmed_article_scopes must be immutable-safe"
            )
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(
            self,
            "confirmed_article_scopes",
            frozenset(self.confirmed_article_scopes),
        )
        if not isinstance(self.envelope_id, str) or not self.envelope_id:
            raise ValueError("evidence validation context requires a non-empty envelope_id")
        if any(
            item.envelope_id != self.envelope_id
            for item in self.evidence
        ):
            raise ValueError("evidence validation context contains a foreign envelope")
        if len({item.handle_id for item in self.evidence}) != len(self.evidence):
            raise ValueError("evidence validation context contains duplicate handle ids")
        if any(scope not in _ARTICLE_SCOPES for scope in self.confirmed_article_scopes):
            raise ValueError("confirmed_article_scopes contains an illegal article scope")

    def evidence_for(self, handle_id: str) -> ValidatedEvidence | None:
        """Resolve one current-turn internal handle without exposing a registry."""

        return next((item for item in self.evidence if item.handle_id == handle_id), None)


@dataclass(frozen=True, slots=True)
class ValidatedAnswerBlocks:
    """Validated blocks plus their server-derived knowledge mode."""

    blocks: tuple[AnswerBlockDraft, ...]
    knowledge_mode: KnowledgeMode


def validate_answer_blocks(
    *,
    blocks: Sequence[AnswerBlockDraft],
    evidence_context: EvidenceValidationContext,
) -> ValidatedAnswerBlocks:
    """Fail closed unless every block obeys the turn's provenance contract."""

    normalized_blocks = tuple(blocks)
    if not normalized_blocks:
        raise ValueError("answer requires at least one answer block")

    for block in normalized_blocks:
        if block.basis == "article":
            _validate_article_block(block, evidence_context)
        elif block.basis == "general":
            _validate_general_block(block)
        else:
            _validate_web_block_v1(block)

    return ValidatedAnswerBlocks(
        blocks=normalized_blocks,
        knowledge_mode=_derive_knowledge_mode(normalized_blocks),
    )


def _validate_article_block(
    block: AnswerBlockDraft,
    evidence_context: EvidenceValidationContext,
) -> None:
    if block.article_scope is None:
        raise ValueError("article block requires a non-null article_scope")
    if block.article_scope not in evidence_context.confirmed_article_scopes:
        raise ValueError(
            f"article scope {block.article_scope!r} is not confirmed by current coverage"
        )
    if not block.evidence_handles:
        raise ValueError("article block requires at least one article evidence handle")

    for handle_id in block.evidence_handles:
        evidence = evidence_context.evidence_for(handle_id)
        if evidence is None:
            raise ValueError("article block references an unknown evidence handle")
        if evidence.source_kind != "article":
            raise ValueError("article block cannot use web evidence")


def _validate_general_block(block: AnswerBlockDraft) -> None:
    if block.article_scope is not None:
        raise ValueError("general block requires article_scope=null")
    if block.evidence_handles:
        raise ValueError("general block cannot carry evidence handles")


def _validate_web_block_v1(block: AnswerBlockDraft) -> None:
    del block
    raise ValueError("web answer blocks are not supported in v1")


def _derive_knowledge_mode(blocks: tuple[AnswerBlockDraft, ...]) -> KnowledgeMode:
    bases = {block.basis for block in blocks}
    if bases == {"article"}:
        return "article_grounded"
    if bases == {"general"}:
        return "general_knowledge"
    if bases == {"web"}:
        return "web_grounded"
    return "mixed"
