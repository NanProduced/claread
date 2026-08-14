"""Tests for allowlist validation (§4.2(d) ten-step algorithm).

 stub: 待 完成后移除（schema/mapping 走 conftest 注入的 _stub）
"""

from __future__ import annotations

import hashlib
import json

from app.services.reader_record_ask.thread_memory.allowlist import (
    _ALLOWLIST_VIOLATION_REJECT_RATIO,
    build_allowlist,
    compute_watermark,
    validate_snapshot,
)
from app.services.reader_record_ask.thread_memory.schema import (
    Episode,
    SourceBinding,
    StructuredFact,
    ThreadMemorySnapshot,
)


def _binding(
    binding_id: str,
    *,
    source_type: str = "article",
    source_id: str = "doc_1",
    record_generation: int = 1,
    base_id: str = "b1",
    reading_record_id: str = "r1",
    status: str = "valid",
) -> SourceBinding:
    return SourceBinding(
        binding_id=binding_id,
        source_type=source_type,
        source_id=source_id,
        fence_type="stable_document" if source_type == "article" else "reading_record",
        fence_values={
            "reading_record_id": reading_record_id,
            "stable_document_id": source_id if source_type == "article" else None,
            "base_id": base_id,
            "record_generation": record_generation,
        },
        validity_check={"status": status, "last_validated_turn": 0},
    )


def _fact(
    fact_id: str,
    *,
    text: str = "x",
    source_type: str = "user_question",
    source_ids: list[str] | None = None,
    confidence: str = "medium",
    turn_origin: int = 1,
    protected: bool = False,
) -> StructuredFact:
    return StructuredFact(
        fact_id=fact_id,
        text=text,
        source_type=source_type,
        source_ids=source_ids if source_ids is not None else [fact_id],
        confidence=confidence,
        turn_origin=turn_origin,
        protected=protected,
    )


def _snapshot(
    episodes: list[Episode],
    *,
    watermark: str = "w",
    thread_id: str = "t1",
) -> ThreadMemorySnapshot:
    return ThreadMemorySnapshot(
        version="thread_memory_v1",
        watermark=watermark,
        thread_id=thread_id,
        created_at="2026-07-30T00:00:00Z",
        last_compacted_at="2026-07-30T00:00:00Z",
        last_compaction_stats=None,
        episodes=episodes,
    )


def _episode(
    facts: list[StructuredFact],
    bindings: list[SourceBinding] | None = None,
    *,
    episode_id: str = "ep_1_1",
) -> Episode:
    return Episode(
        episode_id=episode_id,
        turn_range={"start": 1, "end": 1},
        structured_facts=facts,
        source_bindings=bindings or [],
        excluded_content_markers=["reasoning", "raw_tool_payload"],
        compaction_model="none",
        compaction_method="emergency_deterministic",
        compaction_timestamp="2026-07-30T00:00:00Z",
        compaction_input_watermark="",
    )


# ---------------------------------------------------------------------------
# build_allowlist
# ---------------------------------------------------------------------------


def test_build_allowlist_unions_three_sets():
    thread_messages = [
        {"id": "m1", "role": "user"},
        {
            "id": "m2",
            "role": "assistant",
            "citations": [{"citation_id": "c1"}, {"citation_id": "c2"}],
        },
        {"id": "m3", "role": "user"},
    ]
    ok_turn_runs = [
        {
            "citation_bindings": [
                {
                    "citation_id": "b1",
                    "source_kind": "article",
                    "rag_citation": {
                        "stable_document_id": "doc_1",
                        "base_id": "b_1",
                        "record_generation": 1,
                        "reading_record_id": "r_1",
                    },
                }
            ]
        }
    ]
    allow = build_allowlist(thread_messages, ok_turn_runs)
    assert allow == {"m1", "m2", "m3", "c1", "c2", "b1"}


def test_build_allowlist_handles_empty_inputs():
    allow = build_allowlist([], [])
    assert allow == set()


def test_build_allowlist_skips_non_dict_entries():
    thread_messages = [
        "not a dict",
        {"id": "m1", "role": "user"},
        None,
    ]
    allow = build_allowlist(thread_messages, [])
    assert allow == {"m1"}


def test_build_allowlist_ignores_user_message_citations():
    """Only assistant messages contribute citations (caller is expected to
    pass canonical user+ok-assistant messages; user messages don't carry
    public citations)."""
    thread_messages = [
        {"id": "u1", "role": "user", "citations": [{"citation_id": "x"}]},
        {"id": "a1", "role": "assistant", "citations": [{"citation_id": "y"}]},
    ]
    allow = build_allowlist(thread_messages, [])
    assert "x" not in allow
    assert "y" in allow


# ---------------------------------------------------------------------------
# validate_snapshot
# ---------------------------------------------------------------------------


def test_validate_snapshot_strips_facts_with_allowlist_foreign_ids():
    # 5 facts total, 1 with a foreign ID → 20% stripped (boundary, not rejected).
    facts = [
        _fact("f1", source_ids=["m1"], confidence="medium"),
        _fact("f2", source_ids=["m2"], confidence="high"),
        _fact("f3", source_ids=["m3"], confidence="high"),
        _fact("f4", source_ids=["m4"], confidence="high"),
        _fact("f_bad", source_ids=["FABRICATED"], confidence="high"),
    ]
    snap = _snapshot([_episode(facts)])
    allow = {"m1", "m2", "m3", "m4"}
    validated, metrics = validate_snapshot(snap, {}, allow, fence_results={})
    assert metrics["total_facts"] == 5
    assert metrics["allowlist_violation"] == 1
    assert metrics["stripped_facts"] == 1
    assert metrics["rejected"] is False  # 20% exactly → strict > 20% → not rejected
    # f1-f4 kept, f_bad stripped
    kept_ids = {f.fact_id for ep in validated.episodes for f in ep.structured_facts}
    assert kept_ids == {"f1", "f2", "f3", "f4"}


def test_validate_snapshot_rejects_when_stripped_exceeds_20_percent():
    # 10 facts, 3 with foreign IDs → 30% stripped > 20% → reject.
    facts = [
        _fact(f"f{i}", source_ids=[f"m{i}"]) for i in range(7)
    ] + [
        _fact(f"bad{i}", source_ids=["FABRICATED"]) for i in range(3)
    ]
    snap = _snapshot([_episode(facts)])
    allow = {f"m{i}" for i in range(7)}
    validated, metrics = validate_snapshot(snap, {}, allow, fence_results={})
    assert metrics["stripped_facts"] == 3
    assert metrics["total_facts"] == 10
    assert metrics["rejected"] is True
    assert metrics["reject_reason"] == "allowlist_violation_exceeded_20pct"


def test_validate_snapshot_does_not_reject_at_exactly_20_percent():
    """Boundary: 20% exactly is NOT rejected (strict >)."""
    # 5 facts, 1 stripped → 20% → not rejected.
    facts = [
        _fact("f1", source_ids=["m1"]),
        _fact("f2", source_ids=["m2"]),
        _fact("f3", source_ids=["m3"]),
        _fact("f4", source_ids=["m4"]),
        _fact("bad", source_ids=["FABRICATED"]),
    ]
    snap = _snapshot([_episode(facts)])
    allow = {"m1", "m2", "m3", "m4"}
    _validated, metrics = validate_snapshot(snap, {}, allow, fence_results={})
    assert metrics["stripped_facts"] == 1
    assert metrics["total_facts"] == 5
    assert metrics["rejected"] is False


def test_validate_snapshot_strips_article_facts_without_binding():
    """Article facts must reference a Host binding (§4.2(d) step 6b)."""
    facts = [
        # Valid article fact: source_ids includes a binding_id.
        _fact(
            "f1",
            source_type="article",
            source_ids=["m1", "bind1"],
            confidence="high",
        ),
        # Invalid article fact: source_ids has no binding_id.
        _fact(
            "f2",
            source_type="article",
            source_ids=["m2"],
            confidence="high",
        ),
    ]
    bind1 = _binding("bind1")
    bindings = [bind1]
    snap = _snapshot([_episode(facts, bindings)])
    allow = {"m1", "m2", "bind1"}
    # Pass Host binding map so article provenance check works.
    host_map = {"bind1": bind1}
    validated, metrics = validate_snapshot(snap, host_map, allow, fence_results={})
    assert metrics["binding_violation"] == 1
    assert metrics["stripped_facts"] == 1
    kept_ids = {f.fact_id for ep in validated.episodes for f in ep.structured_facts}
    assert kept_ids == {"f1"}


def test_validate_snapshot_counts_fence_invalid_bindings():
    fence_results = {
        "bind1": {"status": "invalid", "invalidation_reason": "generation_changed"},
        "bind2": {"status": "valid"},
    }
    snap = _snapshot([_episode([], [])])
    _validated, metrics = validate_snapshot(snap, {}, {"m1"}, fence_results)
    assert metrics["fence_invalid_bindings"] == 1


def test_validate_snapshot_returns_immutable_copy():
    """validate_snapshot must not mutate the input snapshot (frozen model)."""
    facts = [_fact("f1", source_ids=["m1"])]
    snap = _snapshot([_episode(facts)])
    original_facts_count = len(snap.episodes[0].structured_facts)
    _validated, _metrics = validate_snapshot(snap, {}, {"m1"}, fence_results={})
    assert len(snap.episodes[0].structured_facts) == original_facts_count


# ---------------------------------------------------------------------------
# compute_watermark
# ---------------------------------------------------------------------------


def test_compute_watermark_is_deterministic():
    messages = [
        {"id": "m1", "role": "user", "content_md": "hello"},
        {"id": "m2", "role": "assistant", "current_turn_run_id": "r1",
         "answer_blocks": [{"text": "hi"}]},
    ]
    w1 = compute_watermark(messages)
    w2 = compute_watermark(messages)
    assert w1 == w2


def test_compute_watermark_matches_revision_digest_definition():
    """SHA256(json.dumps([(id, role,
    revision_digest)], sort_keys=True)).

    Replaces the old (id, role)-only hash. The revision digest is a
    per-message SHA-256 of safe-visible content — no raw text leaks.

     The assistant digest now uses a structured JSON
    serialization (``{"run_id": ..., "answer_texts": [...],
    "web_outcome": ...}``) instead of the old separator-based
    ``f"{run_id}|\\n".join(texts)`` which lost the run_id on
    single-element lists.
    """
    messages = [
        {"id": "m1", "role": "user", "content_md": "hello"},
        {"id": "m2", "role": "assistant",
         "canonical_turn_run_id": "r1",
         "answer_blocks": [{"text": "hi"}]},
    ]
    # Compute expected digests manually using the structured format.
    user_digest = hashlib.sha256(b"hello").hexdigest()
    # Structured JSON digest input (no separator-based join).
    assistant_digest_input = json.dumps(
        {"run_id": "r1", "answer_texts": ["hi"], "web_outcome": ""},
        sort_keys=True,
        ensure_ascii=False,
    )
    assistant_digest = hashlib.sha256(
        assistant_digest_input.encode("utf-8")
    ).hexdigest()
    expected = hashlib.sha256(
        json.dumps(
            [("m1", "user", user_digest),
             ("m2", "assistant", assistant_digest)],
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    assert compute_watermark(messages) == expected


def test_compute_watermark_changes_on_message_id_change():
    m1 = [{"id": "a", "role": "user", "content_md": "x"}]
    m2 = [{"id": "b", "role": "user", "content_md": "x"}]
    assert compute_watermark(m1) != compute_watermark(m2)


def test_compute_watermark_role_included():
    """Same id but different role must produce different watermark."""
    m1 = [{"id": "x", "role": "user", "content_md": "data"}]
    m2 = [{"id": "x", "role": "assistant", "current_turn_run_id": "r1",
           "answer_blocks": [{"text": "data"}]}]
    assert compute_watermark(m1) != compute_watermark(m2)


def test_compute_watermark_empty_input():
    """Empty canonical messages still produce a stable SHA-256 of '[]'."""
    expected = hashlib.sha256(
        json.dumps([], sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    assert compute_watermark([]) == expected


def test_reject_ratio_constant_is_twenty_percent():
    assert _ALLOWLIST_VIOLATION_REJECT_RATIO == 0.20


def test_validate_snapshot_handles_empty_snapshot():
    snap = _snapshot([])
    _validated, metrics = validate_snapshot(snap, {}, set(), fence_results={})
    assert metrics["total_facts"] == 0
    assert metrics["stripped_facts"] == 0
    assert metrics["rejected"] is False
