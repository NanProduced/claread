"""Pure, server-owned provenance policy for one Reading Record Ask turn.

This module deliberately does not know about agents, prompts, finalization,
wire DTOs, persistence, or Web rendering.  A future host adapter supplies
already-validated evidence and confirmed article coverage; this module then
decides whether a draft is structurally legal for the turn.
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
RequestedCitationScope: TypeAlias = Literal["none", "article", "web"]
WebCapability: TypeAlias = Literal["unavailable", "available"]
KnowledgeMode: TypeAlias = Literal[
    "article_grounded",
    "general_knowledge",
    "web_grounded",
    "mixed",
]
EvidenceSourceKind: TypeAlias = Literal["article", "web"]
HostDraftingDecisionKind: TypeAlias = Literal[
    "model_draft_allowed",
    "web_unavailable",
    "web_not_supported_in_v1",
]
HostOwnedOutcomeKind: TypeAlias = Literal["article_source_unavailable"]

_ARTICLE_SCOPES = frozenset(
    {
        "selection_bounded",
        "evidence_bounded",
        "article_overview",
        "full_article",
    }
)
_ANSWER_BLOCK_BASES = frozenset({"article", "general", "web"})
_REQUESTED_CITATION_SCOPES = frozenset({"none", "article", "web"})
_WEB_CAPABILITIES = frozenset({"unavailable", "available"})
_EVIDENCE_SOURCE_KINDS = frozenset({"article", "web"})


@dataclass(frozen=True, slots=True)
class HostDraftingDecision:
    """A host-only decision made before any model draft is accepted."""

    kind: HostDraftingDecisionKind


@dataclass(frozen=True, slots=True)
class HostOwnedOutcome:
    """A host-owned safe result marker, without public payload semantics."""

    kind: HostOwnedOutcomeKind


@dataclass(frozen=True, slots=True)
class TurnAnswerPolicy:
    """Immutable answer/provenance policy for one turn.

    The policy separates article-only knowledge constraints from citation
    presentation requirements.  It never authorizes Web use when the host has
    not enabled that capability.
    """

    article_only: bool
    citation_required: bool
    requested_citation_scope: RequestedCitationScope
    web_capability: WebCapability

    def __post_init__(self) -> None:
        if type(self.article_only) is not bool:
            raise ValueError("article_only must be a bool")
        if type(self.citation_required) is not bool:
            raise ValueError("citation_required must be a bool")
        if self.requested_citation_scope not in _REQUESTED_CITATION_SCOPES:
            raise ValueError("requested_citation_scope must be none, article, or web")
        if self.web_capability not in _WEB_CAPABILITIES:
            raise ValueError("web_capability must be unavailable or available")
        if not self.citation_required and self.requested_citation_scope != "none":
            raise ValueError(
                "citation_required=false requires requested_citation_scope=none"
            )
        if self.citation_required and self.requested_citation_scope == "none":
            raise ValueError(
                "citation_required=true requires requested_citation_scope=article or web"
            )
        if self.article_only and self.requested_citation_scope == "web":
            raise ValueError("article_only does not allow requested_citation_scope=web")

    def host_drafting_decision(self) -> HostDraftingDecision:
        """Return whether the host must stop before model drafting.

        A requested Web citation is not an invitation for the model to
        substitute general knowledge: unavailable Web stays unavailable, and
        v1 blocks even an available capability before model drafting.
        """

        if self.requested_citation_scope == "web":
            if self.web_capability == "unavailable":
                return HostDraftingDecision(kind="web_unavailable")
            return HostDraftingDecision(kind="web_not_supported_in_v1")
        return HostDraftingDecision(kind="model_draft_allowed")

    def article_source_unavailable_outcome(self) -> HostOwnedOutcome:
        """Describe the only policy state that may yield article-source absence.

        The caller owns the later completed-message projection.  No model text,
        DTO, persistence, or UI behavior is defined here.
        """

        if not (
            self.citation_required
            and self.requested_citation_scope == "article"
        ):
            raise ValueError(
                "article_source_unavailable is only legal for an article citation request"
            )
        return HostOwnedOutcome(kind="article_source_unavailable")


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
    """A host-confirmed internal evidence handle usable by the policy seam."""

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
    policy: TurnAnswerPolicy,
    blocks: Sequence[AnswerBlockDraft],
    evidence_context: EvidenceValidationContext,
) -> ValidatedAnswerBlocks:
    """Fail closed unless every block obeys the turn's provenance policy."""

    if policy.host_drafting_decision().kind != "model_draft_allowed":
        raise ValueError("host must handle web citation request before model drafting")

    normalized_blocks = tuple(blocks)
    if not normalized_blocks:
        raise ValueError("answer requires at least one answer block")

    has_publicly_mappable_article_block = False
    for block in normalized_blocks:
        if policy.article_only and block.basis != "article":
            raise ValueError("article_only allows only article blocks")

        if block.basis == "article":
            if _validate_article_block(block, evidence_context):
                has_publicly_mappable_article_block = True
        elif block.basis == "general":
            _validate_general_block(block)
        else:
            _validate_web_block_v1(block)

    if (
        policy.citation_required
        and policy.requested_citation_scope == "article"
        and not has_publicly_mappable_article_block
    ):
        raise ValueError(
            "article citation request requires a publicly mappable article block"
        )

    return ValidatedAnswerBlocks(
        blocks=normalized_blocks,
        knowledge_mode=_derive_knowledge_mode(normalized_blocks),
    )


def _validate_article_block(
    block: AnswerBlockDraft,
    evidence_context: EvidenceValidationContext,
) -> bool:
    if block.article_scope is None:
        raise ValueError("article block requires a non-null article_scope")
    if block.article_scope not in evidence_context.confirmed_article_scopes:
        raise ValueError(
            f"article scope {block.article_scope!r} is not confirmed by current coverage"
        )
    if not block.evidence_handles:
        raise ValueError("article block requires at least one article evidence handle")

    publicly_mappable = False
    for handle_id in block.evidence_handles:
        evidence = evidence_context.evidence_for(handle_id)
        if evidence is None:
            raise ValueError("article block references an unknown evidence handle")
        if evidence.source_kind != "article":
            raise ValueError("article block cannot use web evidence")
        publicly_mappable = publicly_mappable or evidence.publicly_mappable
    return publicly_mappable


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
