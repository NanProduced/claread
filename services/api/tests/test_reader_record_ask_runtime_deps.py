"""R4-A4-1B write-once invariant tests for ReaderRecordAskDeps.

``ReaderRecordAskDeps`` is a mutable ``@dataclass(slots=True)``; write-once
for ``answer_correctness_policy`` is a convention, not type-enforced. These
tests statically verify that only ``runtime.py`` assigns to that field
anywhere in ``services/api/app/`` and that the default is ``None``.

Authoritative contract: design report §21 (TMP-reader-record-ask-r4-a4-1-
correctness-policy-design-2026-07-19.md).
"""

from __future__ import annotations

import uuid

from app.services.reader_record_ask.context_envelope import (
    VerifiedEnvelopeInput,
    build_context_envelope,
)
from app.services.reader_record_ask.evidence_registry import EvidenceRegistry
from app.services.reader_record_ask.fence import StaticGenerationFence
from app.services.reader_record_ask.runtime_deps import ReaderRecordAskDeps

_USER = uuid.UUID("00000000-0000-0000-0000-000000000001")
_RECORD = uuid.UUID("00000000-0000-0000-0000-000000000002")
_BASE = uuid.UUID("00000000-0000-0000-0000-000000000003")


def _envelope() -> object:
    return build_context_envelope(
        VerifiedEnvelopeInput(
            user_id=_USER,
            reading_record_id=_RECORD,
            base_id=_BASE,
            record_generation=1,
            stable_document_id=None,
            base_content_sha256=None,
            product_state="readable_enhancing",
            readiness_state="article_ready",
            initial_anchor=None,
            visible_range=None,
        )
    )


def test_deps_field_default_is_none() -> None:
    """``ReaderRecordAskDeps.answer_correctness_policy`` defaults to ``None``.

    This guarantees the fail-closed path (baseline not injected → runtime
    returns early) and existing test fixtures that do not pass the policy
    leave the field at ``None``, so the grounding validator skips the
    policy check.
    """
    envelope = _envelope()  # type: ignore[assignment]
    registry = EvidenceRegistry(envelope.envelope_fingerprint)
    deps = ReaderRecordAskDeps(
        envelope=envelope,
        document_access=None,  # type: ignore[arg-type]
        fence=StaticGenerationFence(live_generation=1),
        evidence_registry=registry,
    )
    assert deps.answer_correctness_policy is None


def test_deps_field_accepts_policy_via_constructor() -> None:
    """The field can be set via the dataclass constructor (keyword-only).

    This is the test-fixture path: tests construct ``ReaderRecordAskDeps``
    directly with a policy to exercise the validator composition.
    """
    from app.services.reader_record_ask.answer_correctness_policy import (
        build_answer_correctness_policy,
    )

    envelope = _envelope()  # type: ignore[assignment]
    registry = EvidenceRegistry(envelope.envelope_fingerprint)
    policy = build_answer_correctness_policy(
        user_message="这篇文章主要说了什么",
        model_visible_chunk_texts=("文章发表于 2023 年 5 月。",),
        baseline_is_complete=True,
    )
    deps = ReaderRecordAskDeps(
        envelope=envelope,
        document_access=None,  # type: ignore[arg-type]
        fence=StaticGenerationFence(live_generation=1),
        evidence_registry=registry,
        answer_correctness_policy=policy,
    )
    assert deps.answer_correctness_policy is policy
