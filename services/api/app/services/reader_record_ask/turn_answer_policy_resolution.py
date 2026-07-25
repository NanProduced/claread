"""Pure host resolver from request constraints to a canonical TurnAnswerPolicy.

This module is the only place where a client's requested answer constraints
meet the server's real answer capabilities.  It never reads the user message,
never routes on keywords, never touches routes, services, streams, LLMs,
persistence, or UI.  It produces the canonical
``app.services.reader_record_ask.turn_answer_policy.TurnAnswerPolicy`` and an
immutable, JSON-native snapshot that can later be rehydrated into the exact
same policy for retry without re-reading current capabilities.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from app.services.reader_record_ask.turn_answer_policy import (
    RequestedCitationScope,
    TurnAnswerPolicy,
    WebCapability,
)

TURN_ANSWER_POLICY_SNAPSHOT_VERSION: Final = 1

_CONSTRAINT_FIELDS = frozenset({"article_only", "requested_citation_scope"})
_SNAPSHOT_FIELDS = (
    "policy_version",
    "article_only",
    "requested_citation_scope",
    "web_capability",
)
_REQUESTED_CITATION_SCOPES = frozenset({"none", "article", "web"})
_WEB_CAPABILITIES = frozenset({"unavailable", "available"})


@dataclass(frozen=True, slots=True)
class RequestedAnswerConstraints:
    """Client-requestable answer constraints for one turn.

    ``citation_required`` is deliberately absent: it is host-derived from
    ``requested_citation_scope`` so a client cannot contradict itself or
    bypass host rules.  Web capability, provider, model, and tool state are
    server-owned and must never appear here.
    """

    article_only: bool = False
    requested_citation_scope: RequestedCitationScope = "none"

    def __post_init__(self) -> None:
        if type(self.article_only) is not bool:
            raise ValueError("article_only must be a bool")
        if self.requested_citation_scope not in _REQUESTED_CITATION_SCOPES:
            raise ValueError("requested_citation_scope must be none, article, or web")


@dataclass(frozen=True, slots=True)
class ServerAnswerCapabilities:
    """Server-truth answer capabilities for one turn.

    Constructed by the service/preflight layer from real server state; never
    accepted from the client payload.
    """

    web_capability: WebCapability = "unavailable"

    def __post_init__(self) -> None:
        if self.web_capability not in _WEB_CAPABILITIES:
            raise ValueError("web_capability must be unavailable or available")


def parse_requested_answer_constraints(
    payload: Mapping[str, object] | None,
) -> RequestedAnswerConstraints:
    """Parse a raw client constraints payload, failing closed.

    A missing or empty payload yields the ordinary defaults.  Any key other
    than ``article_only`` / ``requested_citation_scope`` — in particular the
    server-owned ``citation_required`` and ``web_capability`` — is rejected.
    """

    if payload is None:
        return RequestedAnswerConstraints()
    if not isinstance(payload, Mapping):
        raise ValueError("requested answer constraints must be a mapping or absent")
    unknown = set(payload) - _CONSTRAINT_FIELDS
    if unknown:
        names = ", ".join(sorted(str(key) for key in unknown))
        raise ValueError(f"requested answer constraints reject non-client fields: {names}")
    return RequestedAnswerConstraints(
        article_only=payload.get("article_only", False),  # type: ignore[arg-type]
        requested_citation_scope=payload.get(  # type: ignore[arg-type]
            "requested_citation_scope", "none"
        ),
    )


def resolve_turn_answer_policy(
    constraints: RequestedAnswerConstraints | None,
    capabilities: ServerAnswerCapabilities,
) -> TurnAnswerPolicy:
    """Resolve the canonical turn policy from constraints and capabilities.

    ``citation_required`` is derived here and only here:
    ``requested_citation_scope != "none"``.  A requested Web citation still
    yields a canonical policy; the policy's own ``host_drafting_decision``
    decides ``web_unavailable`` / ``web_not_supported_in_v1``.  Illegal
    combinations (e.g. article_only with Web scope) fail closed via the
    canonical policy's invariants.
    """

    effective = constraints if constraints is not None else RequestedAnswerConstraints()
    return TurnAnswerPolicy(
        article_only=effective.article_only,
        citation_required=effective.requested_citation_scope != "none",
        requested_citation_scope=effective.requested_citation_scope,
        web_capability=capabilities.web_capability,
    )


@dataclass(frozen=True, slots=True)
class TurnAnswerPolicySnapshot:
    """Immutable, JSON-native persistence snapshot of one turn's policy.

    Carries only ``policy_version`` and the four policy inputs.  It never
    contains the user question, answer text, evidence handles, record/user
    identity, or provider payloads.  ``citation_required`` is not stored; it
    is re-derived from ``requested_citation_scope`` on rehydration.
    """

    policy_version: int
    article_only: bool
    requested_citation_scope: RequestedCitationScope
    web_capability: WebCapability

    def __post_init__(self) -> None:
        if type(self.policy_version) is not int:
            raise ValueError("policy_version must be an int")
        if self.policy_version != TURN_ANSWER_POLICY_SNAPSHOT_VERSION:
            raise ValueError("unknown turn answer policy snapshot version")
        if type(self.article_only) is not bool:
            raise ValueError("snapshot article_only must be a bool")
        if self.requested_citation_scope not in _REQUESTED_CITATION_SCOPES:
            raise ValueError("snapshot requested_citation_scope is illegal")
        if self.web_capability not in _WEB_CAPABILITIES:
            raise ValueError("snapshot web_capability is illegal")

    @classmethod
    def from_policy(cls, policy: TurnAnswerPolicy) -> TurnAnswerPolicySnapshot:
        return cls(
            policy_version=TURN_ANSWER_POLICY_SNAPSHOT_VERSION,
            article_only=policy.article_only,
            requested_citation_scope=policy.requested_citation_scope,
            web_capability=policy.web_capability,
        )

    def to_policy(self) -> TurnAnswerPolicy:
        """Rebuild the canonical policy from the snapshot alone.

        Never consults current server capabilities; retry replays exactly
        what was persisted for the turn.
        """

        return TurnAnswerPolicy(
            article_only=self.article_only,
            citation_required=self.requested_citation_scope != "none",
            requested_citation_scope=self.requested_citation_scope,
            web_capability=self.web_capability,
        )

    def to_json_dict(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "article_only": self.article_only,
            "requested_citation_scope": self.requested_citation_scope,
            "web_capability": self.web_capability,
        }

    def to_json(self) -> str:
        """Serialize with a fixed key order and compact separators."""

        return json.dumps(self.to_json_dict(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json_dict(cls, payload: Mapping[str, object]) -> TurnAnswerPolicySnapshot:
        """Fail-closed parse: exact field set, strict types, legal enums."""

        if not isinstance(payload, Mapping):
            raise ValueError("turn answer policy snapshot must be a mapping")
        keys = set(payload)
        expected = set(_SNAPSHOT_FIELDS)
        if keys != expected:
            missing = sorted(expected - keys)
            extra = sorted(keys - expected)
            raise ValueError(
                "turn answer policy snapshot fields mismatch "
                f"(missing={missing}, extra={extra})"
            )
        return cls(
            policy_version=payload["policy_version"],  # type: ignore[arg-type]
            article_only=payload["article_only"],  # type: ignore[arg-type]
            requested_citation_scope=payload["requested_citation_scope"],  # type: ignore[arg-type]
            web_capability=payload["web_capability"],  # type: ignore[arg-type]
        )

    @classmethod
    def from_json(cls, text: str) -> TurnAnswerPolicySnapshot:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("turn answer policy snapshot is not valid JSON") from exc
        return cls.from_json_dict(payload)
