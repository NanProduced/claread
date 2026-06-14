"""Phase 0 debug metrics tests.

覆盖：
- node_timings 基本结构
- annotation_stats 统计（draft/normalized/drop/anchor）
- repair_stats 未触发和触发
- debug snapshot 新字段
- eval adapter schema 新字段
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

from app.eval_adapter.schemas import ArticleAnalysisEvalResult
from app.schemas.analysis import AnalyzeRequest
from app.schemas.internal.analysis import SentenceTranslation, VocabHighlight
from app.schemas.internal.drafts import (
    DraftVocabHighlight,
    GrammarDraft,
    TranslationDraft,
    VocabularyDraft,
)
from app.schemas.internal.normalized import DropLogEntry, NormalizedAnnotationResult
from app.services.analysis.debug_snapshots import (
    build_annotation_stats_summary,
    build_canonical_drop_log_entries,
    build_drop_log_summary,
    build_node_timings_summary,
    build_repair_stats_summary,
)
from app.services.analysis.planning.goal_planner import build_goal_execution_plan
from app.services.analysis.preprocess.input_preparation import prepare_input
from app.workflow import analyze_nodes

# ── Helpers ──────────────────────────────────────────────────────────


def _drop(
    reason: str,
    stage: str = "grounding",
    annotation_type: str = "vocab_highlight",
    source_agent: str = "vocabulary",
) -> DropLogEntry:
    return DropLogEntry(
        source_agent=source_agent,
        annotation_type=annotation_type,
        sentence_id="s1",
        anchor_text="missing",
        drop_reason=reason,
        drop_stage=stage,
        dropped_at=datetime.now(),
    )


def _make_state(**overrides) -> dict:
    text = "Sentence one. Sentence two."
    base = {
        "payload": AnalyzeRequest.model_validate(
            {
                "request_id": "req-test",
                "text": text,
                "source_type": "user_input",
                "reading_goal": "daily_reading",
                "reading_variant": "intermediate_reading",
            }
        ),
        "prepared_input": prepare_input(text),
        "goal_execution_plan": build_goal_execution_plan(
            "daily_reading", "intermediate_reading"
        ),
        "vocabulary_draft": VocabularyDraft(
            vocab_highlights=[], phrase_glosses=[], context_glosses=[]
        ),
        "grammar_draft": GrammarDraft(grammar_notes=[], sentence_analyses=[]),
        "translation_draft": TranslationDraft(title="测试", sentence_translations=[]),
        "warnings": [],
    }
    base.update(overrides)
    return base


# ── Fake agent spans ─────────────────────────────────────────────────


async def _fake_vocab_span(*args, **kwargs):
    return {
        "output": VocabularyDraft(
            vocab_highlights=[DraftVocabHighlight(sentence_id="s1", text="Sentence")],
            phrase_glosses=[],
            context_glosses=[],
        ),
    }


async def _fake_grammar_span(*args, **kwargs):
    return {"output": GrammarDraft(grammar_notes=[], sentence_analyses=[])}


async def _fake_translation_span(*args, **kwargs):
    return {
        "output": TranslationDraft(
            title="测试标题",
            sentence_translations=[
                SentenceTranslation(sentence_id="s1", translation_zh="第一句。"),
                SentenceTranslation(sentence_id="s2", translation_zh="第二句。"),
            ],
        ),
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }


# ── node_timings tests ───────────────────────────────────────────────


def test_prepare_input_node_records_timing():
    state = _make_state()
    result = asyncio.run(analyze_nodes.prepare_input_node(state))
    assert "node_timings" in result
    assert "prepare_input" in result["node_timings"]
    assert result["node_timings"]["prepare_input"] >= 0


def test_derive_user_config_node_records_timing():
    state = _make_state()
    # Remove plan so it gets computed
    del state["goal_execution_plan"]
    result = asyncio.run(analyze_nodes.derive_user_config_node(state, config={}))
    assert "node_timings" in result
    assert "derive_user_config" in result["node_timings"]


def test_derive_user_config_node_records_timing_when_plan_exists():
    state = _make_state()
    result = asyncio.run(analyze_nodes.derive_user_config_node(state, config={}))
    assert "node_timings" in result
    assert "derive_user_config" in result["node_timings"]


def test_parallel_agents_node_records_timing(monkeypatch):
    monkeypatch.setattr(analyze_nodes, "_run_vocabulary_llm_span", _fake_vocab_span)
    monkeypatch.setattr(analyze_nodes, "_run_grammar_llm_span", _fake_grammar_span)
    monkeypatch.setattr(analyze_nodes, "_run_translation_llm_span", _fake_translation_span)

    state = _make_state()
    result = asyncio.run(analyze_nodes.parallel_agents_node(state, config={}))
    timings = result.get("node_timings", {})
    assert "parallel_agents" in timings
    assert "vocabulary_agent" in timings
    assert "grammar_agent" in timings
    assert "translation_agent" in timings
    assert timings["parallel_agents"] >= 0


def test_parallel_agents_node_records_timing_when_agent_fails(monkeypatch):
    async def _fail_span(*args, **kwargs):
        raise RuntimeError("agent failed")

    monkeypatch.setattr(analyze_nodes, "_run_vocabulary_llm_span", _fail_span)
    monkeypatch.setattr(analyze_nodes, "_run_grammar_llm_span", _fake_grammar_span)
    monkeypatch.setattr(analyze_nodes, "_run_translation_llm_span", _fake_translation_span)

    state = _make_state()
    result = asyncio.run(analyze_nodes.parallel_agents_node(state, config={}))
    timings = result.get("node_timings", {})
    warning_codes = {warning.code for warning in result.get("warnings", [])}

    assert "vocabulary_agent" in timings
    assert "grammar_agent" in timings
    assert "translation_agent" in timings
    assert timings["vocabulary_agent"] >= 0
    assert "VOCABULARY_AGENT_FAILED" in warning_codes


def test_normalize_and_ground_node_records_timing():
    state = _make_state()
    result = asyncio.run(analyze_nodes.normalize_and_ground_node(state))
    timings = result.get("node_timings", {})
    assert "normalize_and_ground" in timings


def test_project_render_scene_node_records_timing():
    state = _make_state(
        normalized_result=NormalizedAnnotationResult(
            annotations=[], sentence_translations=[], drop_log=[]
        ),
    )
    result = asyncio.run(analyze_nodes.project_render_scene_node(state))
    timings = result.get("node_timings", {})
    assert "project_render_scene" in timings


def test_assemble_result_node_records_timing():
    text = "Sentence one. Sentence two."
    plan = build_goal_execution_plan(
        "daily_reading", "intermediate_reading"
    )

    from app.schemas.analysis import (
        AnalyzeRequestMeta,
        ArticleStructure,
        RenderSceneModel,
    )

    render_scene = RenderSceneModel(
        request=AnalyzeRequestMeta(
            request_id="req-test",
            source_type="user_input",
            reading_goal="daily_reading",
            reading_variant="intermediate_reading",
            profile_id=plan.prompt_profile,
        ),
        article=ArticleStructure(
            source_type="user_input",
            source_text=text,
            render_text=text,
            paragraphs=[], sentences=[],
        ),
        translations=[], inline_marks=[],
        sentence_entries=[], warnings=[],
    )
    state = _make_state(render_scene=render_scene)
    result = asyncio.run(analyze_nodes.assemble_result_node(state))
    timings = result.get("node_timings", {})
    assert "assemble_result" in timings


def test_full_workflow_node_timings_keys(monkeypatch):
    """运行完整 learning workflow，验证所有顶层节点计时 key 存在。"""
    monkeypatch.setattr(analyze_nodes, "_run_vocabulary_llm_span", _fake_vocab_span)
    monkeypatch.setattr(analyze_nodes, "_run_grammar_llm_span", _fake_grammar_span)
    monkeypatch.setattr(analyze_nodes, "_run_translation_llm_span", _fake_translation_span)

    payload = AnalyzeRequest.model_validate(
        {
            "text": "Sentence one. Sentence two.",
            "reading_goal": "daily_reading",
            "reading_variant": "intermediate_reading",
            "source_type": "user_input",
        }
    )
    from app.workflow.analyze import run_article_analysis_with_state
    result = asyncio.run(run_article_analysis_with_state(payload))

    timings = result.get("node_timings", {})
    expected_top_keys = {
        "prepare_input", "derive_user_config", "parallel_agents",
        "normalize_and_ground", "project_render_scene", "assemble_result",
    }
    for key in expected_top_keys:
        assert key in timings, f"Missing timing key: {key}"
        assert isinstance(timings[key], float), f"Timing {key} should be float"

    # Agent sub-timings (独立计时，不再全部相等)
    for agent_key in ("vocabulary_agent", "grammar_agent", "translation_agent"):
        assert agent_key in timings, f"Missing agent timing key: {agent_key}"
        assert isinstance(timings[agent_key], float)

    # P1 fix: 正常流程（不触发 repair）repair_stats 也应存在
    repair_stats = result.get("repair_stats")
    assert repair_stats is not None
    assert "repair_triggered" in repair_stats


# ── annotation_stats tests ───────────────────────────────────────────


def test_normalize_and_ground_node_produces_annotation_stats():
    state = _make_state(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[DraftVocabHighlight(sentence_id="s1", text="Sentence")],
            phrase_glosses=[],
            context_glosses=[],
        ),
    )
    result = asyncio.run(analyze_nodes.normalize_and_ground_node(state))
    stats = result.get("annotation_stats")
    assert stats is not None
    assert "draft_counts" in stats
    assert "normalized_counts" in stats
    assert "drop_counts_by_type" in stats
    assert "drop_counts_by_reason" in stats
    assert "drop_counts_by_stage" in stats
    assert "anchor_drop_summary" in stats
    assert "canonical_stats" in stats
    canonical_stats = stats["canonical_stats"]
    assert canonical_stats is not None
    assert canonical_stats["canonical_normalized_counts"].get("vocab_highlight") == 1
    assert canonical_stats["canonical_span_count"] == 1
    # P1 fix: normalize_and_ground_node 也产出 repair_stats
    repair = result.get("repair_stats")
    assert repair is not None
    assert "repair_triggered" in repair
    assert "trigger_threshold" in repair
    assert "pre_repair_annotation_count" in repair


def test_annotation_stats_draft_counts():
    state = _make_state(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[DraftVocabHighlight(sentence_id="s1", text="Sentence")],
            phrase_glosses=[],
            context_glosses=[],
        ),
    )
    result = asyncio.run(analyze_nodes.normalize_and_ground_node(state))
    stats = result["annotation_stats"]
    assert stats["draft_counts"].get("vocab_highlight", 0) >= 1


def test_annotation_stats_anchor_drop_summary():
    drop_log = [
        _drop("anchor_not_substring", annotation_type="phrase_gloss"),
        _drop("anchor_not_substring", annotation_type="grammar_note"),
        _drop("duplicate", stage="deduplication", annotation_type="vocab_highlight"),
    ]
    # 直接测试 _anchor_drop_summary，不需要构造完整 state
    summary = analyze_nodes._anchor_drop_summary(drop_log)
    assert summary["total_anchor_drops"] == 2
    assert len(summary["by_annotation_type_and_reason"]) == 2


def test_anchor_drop_summary_includes_schematic_reason():
    """P2 fix: schematic_anchor_not_groundable 应被纳入 anchor drop 汇总。"""
    drop_log = [
        _drop(
            "schematic_anchor_not_groundable",
            annotation_type="grammar_note",
        ),
        _drop("anchor_not_substring", annotation_type="phrase_gloss"),
        _drop("duplicate", stage="deduplication"),
    ]
    summary = analyze_nodes._anchor_drop_summary(drop_log)
    assert summary["total_anchor_drops"] == 2
    reasons = {
        entry["drop_reason"]
        for entry in summary["by_annotation_type_and_reason"]
    }
    assert "schematic_anchor_not_groundable" in reasons


# ── repair_stats tests ───────────────────────────────────────────────


def test_normalize_and_ground_repair_decision_not_triggered():
    """P1 fix: normalize_and_ground_node 在不触发 repair 时也写入 repair_stats。"""
    state = _make_state()
    result = asyncio.run(analyze_nodes.normalize_and_ground_node(state))
    stats = result.get("repair_stats")
    assert stats is not None
    assert stats["repair_triggered"] is False
    assert stats["trigger_threshold"] == 0.35
    assert stats["trigger_reason"] is None
    assert isinstance(stats["pre_repair_annotation_count"], int)
    assert stats["post_repair_annotation_count"] is None
    assert stats["repair_elapsed_s"] is None
    assert stats["repair_succeeded"] is None


def test_normalize_and_ground_repair_decision_triggered(monkeypatch):
    """normalize_and_ground_node 在应触发 repair 时写入 trigger_reason。"""
    drop_log = [_drop("anchor_not_substring")]
    # 手动构造一个会触发 repair 的 normalized_result
    normalized_result = NormalizedAnnotationResult(
        annotations=[], sentence_translations=[], drop_log=drop_log,
    )
    repair_mock = AsyncMock(
        return_value={"output": normalized_result, "usage_metadata": None}
    )
    monkeypatch.setattr(
        analyze_nodes, "_run_repair_llm_span", repair_mock
    )
    monkeypatch.setattr(
        analyze_nodes, "_build_agent_trace_metadata",
        lambda *_args, **_kwargs: {"extra": {}},
    )
    # 直接调用 repair_agent_node 来验证 trigger 逻辑
    state = _make_state(normalized_result=normalized_result)
    result = asyncio.run(analyze_nodes.repair_agent_node(state, config={}))
    stats = result.get("repair_stats")
    assert stats is not None
    assert stats["repair_triggered"] is True
    assert stats["trigger_reason"] is not None
    repair_mock.assert_awaited_once()


def test_repair_stats_not_triggered():
    normalized_result = NormalizedAnnotationResult(
        annotations=[VocabHighlight(sentence_id="s1", text="Sentence")],
        sentence_translations=[],
        drop_log=[_drop("duplicate", stage="deduplication")],
    )
    state = _make_state(normalized_result=normalized_result)
    result = asyncio.run(analyze_nodes.repair_agent_node(state, config={}))
    stats = result.get("repair_stats")
    assert stats is not None
    assert stats["repair_triggered"] is False
    assert stats["trigger_threshold"] == 0.35
    assert stats["trigger_reason"] is None
    assert stats["pre_repair_annotation_count"] == 1
    assert stats["post_repair_annotation_count"] is None
    assert stats["repair_elapsed_s"] is None
    assert stats["repair_succeeded"] is None


def test_repair_stats_triggered_and_succeeded(monkeypatch):
    repaired_canonical_drop = _drop(
        "quote_not_found",
        annotation_type="phrase_gloss",
    )
    repaired = NormalizedAnnotationResult(
        annotations=[VocabHighlight(sentence_id="s1", text="Sentence")],
        sentence_translations=[],
        drop_log=[],
        canonical_stats={
            "canonical_anchor_drop_summary": {"total_anchor_drops": 1},
            "canonical_drop_counts_by_reason": {"quote_not_found": 1},
        },
        canonical_drop_log=[repaired_canonical_drop],
    )
    repair_mock = AsyncMock(
        return_value={
            "output": repaired,
            "usage_metadata": {"total_tokens": 1},
        }
    )
    monkeypatch.setattr(
        analyze_nodes, "_run_repair_llm_span", repair_mock
    )
    monkeypatch.setattr(
        analyze_nodes, "_build_agent_trace_metadata",
        lambda *_args, **_kwargs: {"extra": {}},
    )

    # 0 annotations + 1 repair-worthy drop → should trigger repair
    normalized_result = NormalizedAnnotationResult(
        annotations=[],
        sentence_translations=[],
        drop_log=[_drop("anchor_not_substring")],
        canonical_drop_log=[_drop("quote_ambiguous")],
    )
    state = _make_state(
        normalized_result=normalized_result,
        canonical_drop_log=normalized_result.canonical_drop_log,
        annotation_stats={
            "canonical_stats": {
                "canonical_anchor_drop_summary": {"total_anchor_drops": 99},
            },
        },
    )
    result = asyncio.run(analyze_nodes.repair_agent_node(state, config={}))
    stats = result.get("repair_stats")
    assert stats is not None
    assert stats["repair_triggered"] is True
    assert stats["trigger_threshold"] == 0.35
    assert stats["trigger_reason"] is not None
    assert "failure_ratio" in stats["trigger_reason"]
    assert stats["pre_repair_annotation_count"] == 0
    assert stats["post_repair_annotation_count"] == 1
    assert stats["repair_elapsed_s"] is not None
    assert stats["repair_succeeded"] is True
    assert result["canonical_drop_log"] == [repaired_canonical_drop]
    assert result["annotation_stats"]["canonical_stats"] == repaired.canonical_stats
    assert result["annotation_stats"]["normalized_counts"] == {"vocab_highlight": 1}


def test_repair_stats_triggered_but_failed(monkeypatch):
    async def _fail_repair(*args, **kwargs):
        raise RuntimeError("repair failed")

    monkeypatch.setattr(
        analyze_nodes, "_run_repair_llm_span", _fail_repair
    )
    monkeypatch.setattr(
        analyze_nodes, "_build_agent_trace_metadata",
        lambda *_args, **_kwargs: {"extra": {}},
    )

    normalized_result = NormalizedAnnotationResult(
        annotations=[],
        sentence_translations=[],
        drop_log=[_drop("anchor_not_substring")],
    )
    state = _make_state(normalized_result=normalized_result)
    result = asyncio.run(analyze_nodes.repair_agent_node(state, config={}))
    stats = result.get("repair_stats")
    assert stats is not None
    assert stats["repair_triggered"] is True
    assert stats["repair_succeeded"] is False
    assert stats["repair_elapsed_s"] is None


# ── debug snapshot summary functions ─────────────────────────────────


def test_build_node_timings_summary():
    result = {
        "node_timings": {
            "prepare_input": 0.1,
            "derive_user_config": 0.01,
            "parallel_agents": 5.0,
            "vocabulary_agent": 5.0,
            "grammar_agent": 5.0,
            "translation_agent": 5.0,
            "normalize_and_ground": 0.2,
            "project_render_scene": 0.05,
            "assemble_result": 0.01,
        },
    }
    summary = build_node_timings_summary(result)
    assert summary is not None
    assert "workflow_total" in summary
    # workflow_total should only sum top-level nodes, not agent sub-timings
    expected_total = 0.1 + 0.01 + 5.0 + 0.2 + 0.05 + 0.01
    assert abs(summary["workflow_total"] - expected_total) < 0.01


def test_build_node_timings_summary_returns_none_when_empty():
    assert build_node_timings_summary({}) is None
    assert build_node_timings_summary(None) is None


def test_build_annotation_stats_summary():
    result = {"annotation_stats": {"draft_counts": {"vocab_highlight": 5}}}
    assert build_annotation_stats_summary(result) == {"draft_counts": {"vocab_highlight": 5}}


def test_build_annotation_stats_summary_returns_none():
    assert build_annotation_stats_summary({}) is None
    assert build_annotation_stats_summary(None) is None


def test_build_repair_stats_summary():
    result = {"repair_stats": {"repair_triggered": False}}
    assert build_repair_stats_summary(result) == {"repair_triggered": False}


def test_build_repair_stats_summary_returns_none():
    assert build_repair_stats_summary({}) is None
    assert build_repair_stats_summary(None) is None


def test_build_drop_log_summary_includes_anchor_failure():
    drop_log = [
        _drop("anchor_not_substring", annotation_type="phrase_gloss"),
        _drop("anchor_not_substring", annotation_type="grammar_note"),
        _drop("duplicate", stage="deduplication"),
    ]
    result = {"drop_log": drop_log}
    summary = build_drop_log_summary(result)
    assert summary is not None
    assert "anchor_failure_summary" in summary
    assert summary["anchor_failure_summary"]["total_anchor_drops"] == 2
    assert len(summary["anchor_failure_summary"]["by_annotation_type_and_reason"]) == 2


def test_build_drop_log_summary_anchor_failure_empty():
    drop_log = [_drop("duplicate", stage="deduplication")]
    result = {"drop_log": drop_log}
    summary = build_drop_log_summary(result)
    assert summary is not None
    assert summary["anchor_failure_summary"]["total_anchor_drops"] == 0
    assert summary["anchor_failure_summary"]["by_annotation_type_and_reason"] == []


# ── eval adapter schema test ─────────────────────────────────────────


def test_eval_result_schema_accepts_new_fields():
    data = {
        "eval_adapter_schema_version": "article-analysis-eval-v1",
        "status": "succeeded",
        "request_snapshot": {
            "request_id": "req-1",
            "source_text_hash": "abc123",
            "source_char_count": 100,
            "reading_goal": "daily_reading",
            "reading_variant": "intermediate_reading",
            "source_type": "user_input",
            "extended": False,
            "rag_mode": "off",
            "trace_scope": "off",
        },
        "workflow_identity": {
            "workflow_name": "article_analysis",
            "workflow_version": "3.0.0",
            "topology_mode": "learning",
        },
        "schema_identity": {
            "schema_version": "3.0.0",
            "topology_mode": "learning",
        },
        "prompt_identity": {
            "prompt_version": "test",
        },
        "node_timings": {"prepare_input": 0.1, "parallel_agents": 5.0},
        "annotation_stats": {"draft_counts": {"vocab_highlight": 3}},
        "repair_stats": {"repair_triggered": False},
    }
    result = ArticleAnalysisEvalResult.model_validate(data)
    assert result.node_timings is not None
    assert result.annotation_stats is not None
    assert result.repair_stats is not None
    assert result.repair_stats["repair_triggered"] is False


def test_eval_result_schema_new_fields_default_none():
    data = {
        "eval_adapter_schema_version": "article-analysis-eval-v1",
        "status": "succeeded",
        "request_snapshot": {
            "request_id": "req-1",
            "source_text_hash": "abc123",
            "source_char_count": 100,
            "reading_goal": "daily_reading",
            "reading_variant": "intermediate_reading",
            "source_type": "user_input",
            "extended": False,
            "rag_mode": "off",
            "trace_scope": "off",
        },
        "workflow_identity": {
            "workflow_name": "article_analysis",
            "workflow_version": "3.0.0",
            "topology_mode": "learning",
        },
        "schema_identity": {
            "schema_version": "3.0.0",
            "topology_mode": "learning",
        },
        "prompt_identity": {
            "prompt_version": "test",
        },
    }
    result = ArticleAnalysisEvalResult.model_validate(data)
    assert result.node_timings is None
    assert result.annotation_stats is None
    assert result.repair_stats is None


# ── canonical_stats and canonical_drop_log tests ─────────────────────


def test_normalize_and_ground_node_produces_canonical_stats_with_all_subfields():
    state = _make_state(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[DraftVocabHighlight(sentence_id="s1", text="Sentence")],
            phrase_glosses=[],
            context_glosses=[],
        ),
    )
    result = asyncio.run(analyze_nodes.normalize_and_ground_node(state))
    canonical_stats = result["annotation_stats"]["canonical_stats"]
    assert canonical_stats is not None
    expected_keys = {
        "canonical_normalized_counts",
        "canonical_span_count",
        "canonical_anchor_drop_summary",
        "canonical_drop_counts_by_reason",
        "canonical_drop_counts_by_type",
    }
    assert expected_keys.issubset(set(canonical_stats.keys()))


def test_canonical_drop_log_empty_when_no_drops():
    state = _make_state()
    result = asyncio.run(analyze_nodes.normalize_and_ground_node(state))
    assert isinstance(result["canonical_drop_log"], list)
    assert len(result["canonical_drop_log"]) == 0


def test_canonical_drop_log_multiple_entries():
    drop1 = _drop("anchor_not_substring")
    drop2 = _drop("anchor_not_substring", annotation_type="grammar_note")
    result_dict = {
        "canonical_drop_log": [drop1, drop2],
    }
    entries = build_canonical_drop_log_entries(result_dict)
    assert isinstance(entries, list)
    assert len(entries) == 2
    for entry in entries:
        assert "drop_reason" in entry
        assert "annotation_type" in entry
        assert "sentence_id" in entry


def test_usage_summary_structure_from_parallel_agents(monkeypatch):
    async def _fake_vocab_with_usage(*args, **kwargs):
        return {
            "output": VocabularyDraft(
                vocab_highlights=[DraftVocabHighlight(sentence_id="s1", text="Sentence")],
                phrase_glosses=[],
                context_glosses=[],
            ),
            "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
        }

    async def _fake_grammar_with_usage(*args, **kwargs):
        return {
            "output": GrammarDraft(grammar_notes=[], sentence_analyses=[]),
            "usage": {"input_tokens": 15, "output_tokens": 8, "total_tokens": 23},
        }

    monkeypatch.setattr(analyze_nodes, "_run_vocabulary_llm_span", _fake_vocab_with_usage)
    monkeypatch.setattr(analyze_nodes, "_run_grammar_llm_span", _fake_grammar_with_usage)
    monkeypatch.setattr(analyze_nodes, "_run_translation_llm_span", _fake_translation_span)

    state = _make_state()
    result = asyncio.run(analyze_nodes.parallel_agents_node(state, config={}))
    usage = result["usage_summary"]
    assert usage is not None
    assert usage["available"] is True
    assert "aggregate" in usage
    assert "total_tokens" in usage["aggregate"]
    assert "input_tokens" in usage["aggregate"]
    assert "output_tokens" in usage["aggregate"]
    assert "per_agent" in usage
    assert "vocabulary" in usage["per_agent"]
    assert "grammar" in usage["per_agent"]
    assert "translation" in usage["per_agent"]


def test_debug_snapshot_payload_includes_canonical_drop_log():
    from uuid import uuid4

    from app.services.analysis.debug_snapshots import build_debug_snapshot_payload

    drop1 = _drop("anchor_not_substring")
    drop2 = _drop("quote_not_found", annotation_type="phrase_gloss")
    result_dict = {
        "canonical_drop_log": [drop1, drop2],
        "goal_execution_plan": None,
    }

    payload = build_debug_snapshot_payload(
        record_id=uuid4(),
        task_id=uuid4(),
        source_text="Test text.",
        task_status="completed",
        usage_summary=None,
        latency_ms=100,
        billed_points=0,
        failure_code=None,
        failure_message=None,
        request_id="req-test",
        user_facing_state="completed",
        result=result_dict,
        schema_version="3.0.0",
        prompt_version="test",
    )

    assert "canonical_drop_log_json" in payload
    assert isinstance(payload["canonical_drop_log_json"], list)
    assert len(payload["canonical_drop_log_json"]) == 2
