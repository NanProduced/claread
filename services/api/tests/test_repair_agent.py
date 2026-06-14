"""Tests for repair agent patch prompt builder."""
from __future__ import annotations

from app.agents.repair_agent import (
    RepairPatchDeps,
    build_repair_patch_prompt,
)
from app.schemas.internal.repair import RepairPatchRequest, RepairTarget


def _make_target(**overrides: object) -> RepairTarget:
    defaults = dict(
        source_agent="grammar",
        annotation_type="phrase_gloss",
        sentence_id="s1",
        anchor_text="Hello",
        drop_reason="quote_not_found",
        drop_stage="ground",
        is_canonical=True,
        draft_payload=None,
    )
    defaults.update(overrides)
    return RepairTarget(**defaults)  # type: ignore[arg-type]


def test_patch_prompt_contains_affected_sentences() -> None:
    req = RepairPatchRequest(
        sentences=[{"sentence_id": "s1", "text": "Hello world"}],
        targets=[_make_target()],
    )
    prompt = build_repair_patch_prompt(RepairPatchDeps(patch_request=req))

    assert "s1" in prompt
    assert "Hello world" in prompt


def test_patch_prompt_contains_drop_reason() -> None:
    req = RepairPatchRequest(
        sentences=[{"sentence_id": "s1", "text": "Hello"}],
        targets=[_make_target(drop_reason="quote_not_found")],
    )
    prompt = build_repair_patch_prompt(RepairPatchDeps(patch_request=req))

    assert "quote_not_found" in prompt


def test_patch_prompt_does_not_contain_unrelated_draft_sections() -> None:
    req = RepairPatchRequest(
        sentences=[{"sentence_id": "s1", "text": "Hello"}],
        targets=[
            _make_target(source_agent="grammar", draft_payload=None),
        ],
    )
    prompt = build_repair_patch_prompt(RepairPatchDeps(patch_request=req))

    # build_repair_patch_prompt 只输出 per-target 信息，不会出现完整 draft dump
    assert "vocabulary_draft" not in prompt
    assert "translation_draft" not in prompt


def test_patch_prompt_includes_draft_payload_when_present() -> None:
    payload = {"type": "vocab_highlight", "text": "example", "sentence_id": "s1"}
    req = RepairPatchRequest(
        sentences=[{"sentence_id": "s1", "text": "Hello"}],
        targets=[_make_target(draft_payload=payload)],
    )
    prompt = build_repair_patch_prompt(RepairPatchDeps(patch_request=req))

    assert "example" in prompt
