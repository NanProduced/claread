"""Tests for emergency deterministic compaction (R0.1 §4.2(e) facts_det).

A1 stub: 待 A1 完成后移除（schema/mapping 走 conftest 注入的 _stub）
"""

from __future__ import annotations

import hashlib
import json

import pytest

from app.services.reader_record_ask.thread_memory.emergency import (
    emergency_compact,
    emergency_full_snapshot,
)
from app.services.reader_record_ask.thread_memory.schema import (
    Episode,
    ThreadMemorySnapshot,
)


def _user_msg(msg_id: str, text: str) -> dict:
    return {"id": msg_id, "role": "user", "content_md": text}


def _assistant_msg(
    msg_id: str,
    *,
    answer_blocks: list[dict] | None = None,
    web_outcome: str | None = None,
) -> dict:
    payload: dict = {"id": msg_id, "role": "assistant"}
    if answer_blocks is not None:
        payload["answer_blocks"] = answer_blocks
    if web_outcome is not None:
        payload["web_search_summary"] = {"outcome": web_outcome}
    return payload


# ---------------------------------------------------------------------------
# emergency_compact
# ---------------------------------------------------------------------------


def test_emergency_compact_extracts_user_question_and_assistant_answer():
    messages = [
        _user_msg("m1", "What is the main idea of paragraph 2?"),
        _assistant_msg(
            "m2",
            answer_blocks=[{"text": "It argues that reuse beats rewrite."}],
        ),
    ]
    episode = emergency_compact(messages, [], turn_range=(1, 1))

    assert isinstance(episode, Episode)
    assert episode.episode_id == "ep_1_1"
    # A1 schema: turn_range is a TurnRange Pydantic model (not a dict).
    assert episode.turn_range.start == 1
    assert episode.turn_range.end == 1
    assert episode.compaction_model == "none"
    assert episode.compaction_method == "emergency_deterministic"
    assert episode.compaction_input_watermark == ""
    # Excluded content markers cover the R0.1 §6 closed set.
    assert set(episode.excluded_content_markers) == {
        "reasoning",
        "raw_tool_payload",
        "failed_drafts",
        "secrets",
        "evh_handles",
    }

    facts_by_type = {f.source_type: f for f in episode.structured_facts}
    assert "user_question" in facts_by_type
    assert "assistant_answer" in facts_by_type
    assert facts_by_type["user_question"].text == (
        "What is the main idea of paragraph 2?"
    )
    assert facts_by_type["user_question"].source_ids == ["m1"]
    assert facts_by_type["user_question"].confidence == "medium"
    assert facts_by_type["user_question"].turn_origin == 1
    assert facts_by_type["assistant_answer"].text == (
        "It argues that reuse beats rewrite."
    )
    assert facts_by_type["assistant_answer"].source_ids == ["m2"]
    assert facts_by_type["assistant_answer"].confidence == "medium"
    assert facts_by_type["assistant_answer"].turn_origin == 1


def test_emergency_compact_extracts_web_outcome_as_prior_context():
    messages = [
        _user_msg("m1", "What is the latest news on X?"),
        _assistant_msg(
            "m2",
            answer_blocks=[{"text": "Per recent reports, Y."}],
            web_outcome="completed",
        ),
    ]
    episode = emergency_compact(messages, [], turn_range=(1, 1))
    web_facts = [f for f in episode.structured_facts if f.source_type == "web"]
    assert len(web_facts) == 1
    assert web_facts[0].text == "搜索结果:completed"
    assert web_facts[0].confidence == "prior_context"
    assert web_facts[0].turn_origin == 1


def test_emergency_compact_user_correction_keyword_detected():
    """Heuristic: user message containing correction keyword → protected fact."""
    messages = [
        _user_msg("m1", "不对，第二个段落应该是关于环境的"),
        _assistant_msg("m2", answer_blocks=[{"text": "Got it."}]),
    ]
    episode = emergency_compact(messages, [], turn_range=(1, 1))
    user_facts = [
        f for f in episode.structured_facts if f.source_type == "user_correction"
    ]
    assert len(user_facts) == 1
    assert user_facts[0].protected is True
    assert user_facts[0].confidence == "high"


def test_emergency_compact_user_correction_multiple_keywords():
    for keyword in ("纠正", "其实", "应该是", "说错了", "更正", "修正"):
        msg = _user_msg("m1", f"我{keyword}一下，原来的说法不准确")
        episode = emergency_compact([msg], [], turn_range=(1, 1))
        user_facts = [
            f
            for f in episode.structured_facts
            if f.source_type == "user_correction"
        ]
        assert len(user_facts) == 1, f"keyword {keyword!r} not detected"


def test_emergency_compact_episode_id_is_deterministic():
    messages = [_user_msg("m1", "hi"), _assistant_msg("m2", answer_blocks=[{"text": "x"}])]
    ep1 = emergency_compact(messages, [], turn_range=(1, 1))
    ep2 = emergency_compact(messages, [], turn_range=(1, 1))
    assert ep1.episode_id == ep2.episode_id == "ep_1_1"


def test_emergency_compact_turn_origin_advances_per_user_message():
    """Multiple turns: turn_origin increments per user message."""
    messages = [
        _user_msg("m1", "Q1"),
        _assistant_msg("m2", answer_blocks=[{"text": "A1"}]),
        _user_msg("m3", "Q2"),
        _assistant_msg("m4", answer_blocks=[{"text": "A2"}]),
    ]
    episode = emergency_compact(messages, [], turn_range=(1, 2))
    turn_origins = sorted({f.turn_origin for f in episode.structured_facts})
    assert turn_origins == [1, 2]


def test_emergency_compact_truncates_long_fact_text():
    long_text = "x" * 500
    msg = _user_msg("m1", long_text)
    episode = emergency_compact([msg], [], turn_range=(1, 1))
    user_fact = next(f for f in episode.structured_facts if f.source_type == "user_question")
    assert len(user_fact.text) <= 280


def test_emergency_compact_rejects_malformed_turn_range():
    with pytest.raises(ValueError):
        emergency_compact([], [], turn_range=(0, 1))
    with pytest.raises(ValueError):
        emergency_compact([], [], turn_range=(2, 1))


def test_emergency_compact_collects_bindings_via_derive_source_bindings():
    """Bindings are Host-derived (H6); emergency never fabricates them."""
    ok_turn_runs = [
        {
            "citation_bindings": [
                {
                    "citation_id": "cit_a",
                    "source_kind": "article",
                    "rag_citation": {
                        "stable_document_id": "doc_1",
                        "base_id": "base_1",
                        "record_generation": 3,
                        "reading_record_id": "rec_1",
                    },
                }
            ]
        }
    ]
    episode = emergency_compact([], ok_turn_runs, turn_range=(1, 1))
    assert len(episode.source_bindings) == 1
    assert episode.source_bindings[0].binding_id == "cit_a"
    assert episode.source_bindings[0].source_type == "article"


# ---------------------------------------------------------------------------
# emergency_full_snapshot
# ---------------------------------------------------------------------------


def test_emergency_full_snapshot_watermark_is_deterministic():
    """R1.6 P0-1 + R1.6.1 P0-1: watermark follows canonical revision."""
    messages = [
        _user_msg("m1", "Q1"),
        _assistant_msg("m2", answer_blocks=[{"text": "A1"}]),
        _user_msg("m3", "Q2"),
        _assistant_msg("m4", answer_blocks=[{"text": "A2"}]),
    ]
    snap1 = emergency_full_snapshot(messages, [], thread_id="t1")
    snap2 = emergency_full_snapshot(messages, [], thread_id="t1")
    assert snap1.watermark == snap2.watermark
    # R1.6.1 P0-1: verify against the structured revision-digest watermark.
    # User digest = SHA256(content_md).
    # Assistant digest = SHA256(JSON({run_id, answer_texts, web_outcome})).
    # The test messages don't set canonical_turn_run_id → run_id="".
    user_d1 = hashlib.sha256(b"Q1").hexdigest()
    asst_d1_input = json.dumps(
        {"run_id": "", "answer_texts": ["A1"], "web_outcome": ""},
        sort_keys=True, ensure_ascii=False,
    )
    asst_d1 = hashlib.sha256(asst_d1_input.encode("utf-8")).hexdigest()
    user_d2 = hashlib.sha256(b"Q2").hexdigest()
    asst_d2_input = json.dumps(
        {"run_id": "", "answer_texts": ["A2"], "web_outcome": ""},
        sort_keys=True, ensure_ascii=False,
    )
    asst_d2 = hashlib.sha256(asst_d2_input.encode("utf-8")).hexdigest()
    expected = hashlib.sha256(
        json.dumps(
            [
                ("m1", "user", user_d1),
                ("m2", "assistant", asst_d1),
                ("m3", "user", user_d2),
                ("m4", "assistant", asst_d2),
            ],
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    assert snap1.watermark == expected


def test_emergency_full_snapshot_no_episodes_when_within_recent_window():
    """Threads shorter than recent_pairs produce empty episodes list."""
    messages = [
        _user_msg("m1", "Q1"),
        _assistant_msg("m2", answer_blocks=[{"text": "A1"}]),
    ]
    snap = emergency_full_snapshot(messages, [], thread_id="t1", recent_pairs=6)
    assert snap.episodes == []
    assert snap.last_compacted_at is None
    assert snap.version == "thread_memory_v1"
    assert snap.thread_id == "t1"


def test_emergency_full_snapshot_compacts_aged_segment():
    """Aged turns (beyond recent_pairs) are compacted into one episode."""
    messages: list[dict] = []
    for i in range(10):
        messages.append(_user_msg(f"u{i}", f"Q{i}"))
        messages.append(
            _assistant_msg(f"a{i}", answer_blocks=[{"text": f"A{i}"}])
        )
    snap = emergency_full_snapshot(messages, [], thread_id="t1", recent_pairs=6)
    assert len(snap.episodes) == 1
    ep = snap.episodes[0]
    # 10 user msgs total, recent_pairs=6 → aged has 4 user msgs → turn_range (1,4)
    # A1 schema: turn_range is a TurnRange Pydantic model.
    assert ep.turn_range.start == 1
    assert ep.turn_range.end == 4
    assert ep.episode_id == "ep_1_4"
    # R1.6.1 P0-1: watermark follows canonical revision via structured
    # digest. user digest = SHA-256(content_md); assistant digest =
    # SHA-256(JSON({run_id:"", answer_texts:[A{i}], web_outcome:""})).
    # The structured form fixes the single-element join bug (separator
    # join of one element is the element itself, losing the run_id).
    triples = []
    for i in range(10):
        triples.append(
            (f"u{i}", "user", hashlib.sha256(f"Q{i}".encode()).hexdigest())
        )
        asst_input = json.dumps(
            {"run_id": "", "answer_texts": [f"A{i}"], "web_outcome": ""},
            sort_keys=True, ensure_ascii=False,
        )
        triples.append(
            (
                f"a{i}",
                "assistant",
                hashlib.sha256(asst_input.encode("utf-8")).hexdigest(),
            )
        )
    expected = hashlib.sha256(
        json.dumps(triples, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    assert snap.watermark == expected


def test_emergency_full_snapshot_recent_pairs_minimum_validation():
    with pytest.raises(ValueError):
        emergency_full_snapshot([], [], thread_id="t1", recent_pairs=0)


def test_emergency_full_snapshot_returns_snapshot_instance():
    snap = emergency_full_snapshot([], [], thread_id="t1")
    assert isinstance(snap, ThreadMemorySnapshot)
    assert snap.episodes == []
