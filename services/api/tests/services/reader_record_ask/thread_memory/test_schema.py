"""schema.py 单元测试（Pydantic v2 严格契约）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.reader_record_ask.thread_memory.schema import (
    Episode,
    SourceBinding,
    StructuredFact,
    ThreadMemorySnapshot,
    TurnRange,
)


def _minimal_fact(**overrides) -> dict:
    base = {
        "fact_id": "f1",
        "text": "a fact",
        "source_type": "assistant_answer",
        "source_ids": ["msg_1"],
        "confidence": "high",
        "turn_origin": 1,
    }
    base.update(overrides)
    return base


def _minimal_episode(**overrides) -> dict:
    base = {
        "episode_id": "ep1",
        "turn_range": {"start": 1, "end": 2},
        "structured_facts": [_minimal_fact()],
        "source_bindings": [],
        "excluded_content_markers": ["reasoning"],
        "compaction_model": "none",
        "compaction_method": "emergency_deterministic",
        "compaction_timestamp": "2026-07-30T00:00:00Z",
        "compaction_input_watermark": "sha256:abc",
    }
    base.update(overrides)
    return base


def _minimal_snapshot(**overrides) -> dict:
    base = {
        "version": "thread_memory_v1",
        "watermark": "sha256:abc",
        "thread_id": "t1",
        "created_at": "2026-07-30T00:00:00Z",
        "last_compacted_at": "2026-07-30T00:00:00Z",
        "last_compaction_stats": None,
        "episodes": [_minimal_episode()],
    }
    base.update(overrides)
    return base


class TestThreadMemorySnapshotSchema:
    def test_minimal_snapshot_validates(self) -> None:
        snap = ThreadMemorySnapshot.model_validate(_minimal_snapshot())
        assert snap.version == "thread_memory_v1"
        assert snap.watermark == "sha256:abc"

    def test_watermark_required(self) -> None:
        payload = _minimal_snapshot()
        payload.pop("watermark")
        with pytest.raises(ValidationError):
            ThreadMemorySnapshot.model_validate(payload)

    def test_snapshot_rejects_unknown_field(self) -> None:
        payload = _minimal_snapshot()
        payload["unexpected_field"] = "x"
        with pytest.raises(ValidationError):
            ThreadMemorySnapshot.model_validate(payload)

    def test_invalid_version_rejected(self) -> None:
        payload = _minimal_snapshot()
        payload["version"] = "thread_memory_v2"
        with pytest.raises(ValidationError):
            ThreadMemorySnapshot.model_validate(payload)


class TestEpisodeSchema:
    def test_episode_rejects_unknown_field(self) -> None:
        payload = _minimal_episode()
        payload["unexpected_field"] = "x"
        with pytest.raises(ValidationError):
            Episode.model_validate(payload)

    def test_turn_range_rejects_unknown_field(self) -> None:
        payload = {"start": 1, "end": 2, "unexpected": 0}
        with pytest.raises(ValidationError):
            TurnRange.model_validate(payload)

    def test_turn_range_closed_interval(self) -> None:
        tr = TurnRange.model_validate({"start": 3, "end": 5})
        assert tr.start == 3
        assert tr.end == 5


class TestSourceBindingSchema:
    def test_source_binding_rejects_unknown_field(self) -> None:
        payload = {
            "binding_id": "b1",
            "source_type": "article",
            "source_id": "doc1",
            "fence_type": "stable_document",
            "fence_values": {},
            "validity_check": {"status": "unchecked", "last_validated_turn": 0},
            "unexpected_field": "x",
        }
        with pytest.raises(ValidationError):
            SourceBinding.model_validate(payload)

    def test_invalid_fence_type_rejected(self) -> None:
        payload = {
            "binding_id": "b1",
            "source_type": "article",
            "source_id": "doc1",
            "fence_type": "invalid_fence",
            "fence_values": {},
            "validity_check": {"status": "unchecked", "last_validated_turn": 0},
        }
        with pytest.raises(ValidationError):
            SourceBinding.model_validate(payload)


class TestStructuredFactSchema:
    def test_fact_rejects_unknown_field(self) -> None:
        payload = _minimal_fact()
        payload["unexpected_field"] = "x"
        with pytest.raises(ValidationError):
            StructuredFact.model_validate(payload)

    def test_fact_text_max_280(self) -> None:
        # 280 chars passes
        fact = StructuredFact.model_validate(_minimal_fact(text="a" * 280))
        assert len(fact.text) == 280
        # 281 chars fails
        with pytest.raises(ValidationError):
            StructuredFact.model_validate(_minimal_fact(text="a" * 281))

    def test_fact_source_ids_empty_rejected(self) -> None:
        with pytest.raises(ValidationError):
            StructuredFact.model_validate(_minimal_fact(source_ids=[]))

    def test_fact_supersedes_optional(self) -> None:
        fact = StructuredFact.model_validate(_minimal_fact(supersedes=["f0"]))
        assert fact.supersedes == ["f0"]

    def test_fact_supersedes_default_none(self) -> None:
        fact = StructuredFact.model_validate(_minimal_fact())
        assert fact.supersedes is None

    def test_fact_invalid_source_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            StructuredFact.model_validate(
                _minimal_fact(source_type="invalid_kind")
            )

    def test_fact_turn_origin_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            StructuredFact.model_validate(_minimal_fact(turn_origin=0))
