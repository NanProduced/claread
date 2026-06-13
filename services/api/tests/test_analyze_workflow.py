import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.analysis import AnalyzeRequest, RenderSceneModel
from app.schemas.internal.analysis import (
    AnnotationOutput,
    PhraseGloss,
    SentenceTranslation,
    VocabHighlight,
)
from app.schemas.internal.drafts import (
    AnchorQuote,
    DraftGrammarNote,
    DraftPhraseGloss,
    DraftVocabHighlight,
    GrammarDraft,
    TranslationDraft,
    VocabularyDraft,
)
from app.schemas.internal.normalized import DropLogEntry, NormalizedAnnotationResult
from app.services.analysis.planning.goal_planner import build_goal_execution_plan
from app.services.analysis.postprocess.projection import project_to_render_scene
from app.services.analysis.preprocess.input_preparation import prepare_input
from app.workflow import analyze_nodes, learning_workflow


async def _fake_run_vocabulary_span(*args, **kwargs):
    return {
        "output": VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="constitutional")
            ],
            phrase_glosses=[],
            context_glosses=[],
        )
    }


async def _fake_run_grammar_span(*args, **kwargs):
    return {"output": GrammarDraft(grammar_notes=[], sentence_analyses=[])}


async def _fake_run_translation_span(*args, **kwargs):
    return {
        "output": TranslationDraft(
            title="店铺防盗与店员安全挑战",
            sentence_translations=[
                SentenceTranslation(
                    sentence_id="s1",
                    translation_zh="店主不得不采取极端措施阻止商店扒手。",
                ),
                SentenceTranslation(
                    sentence_id="s2",
                    translation_zh="令人不安的是，每天都有针对店员的暴力事件。",
                ),
            ]
        ),
        "usage": {"input_tokens": 40, "output_tokens": 20, "total_tokens": 60},
    }


async def _raise_span(*args, **kwargs):
    raise RuntimeError("agent failed")


async def _invalid_vocab_span(*args, **kwargs):
    invalid = DraftVocabHighlight.model_construct(
        type="vocab_highlight",
        sentence_id="s1",
        text="extreme lengths",
    )
    return {
        "output": VocabularyDraft(
            vocab_highlights=[invalid],
            phrase_glosses=[],
            context_glosses=[],
        ),
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }


async def _usage_vocab_span(*args, **kwargs):
    return {
        "output": VocabularyDraft(vocab_highlights=[], phrase_glosses=[], context_glosses=[]),
        "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
    }


async def _usage_grammar_span(*args, **kwargs):
    return {
        "output": GrammarDraft(grammar_notes=[], sentence_analyses=[]),
        "usage": {"input_tokens": 13, "output_tokens": 9, "total_tokens": 22},
    }


async def _usage_translation_span(*args, **kwargs):
    return {
        "output": TranslationDraft(title="句子示例", sentence_translations=[]),
        "usage": {"input_tokens": 17, "output_tokens": 11, "total_tokens": 28},
    }


def test_analyze_route_returns_v30_payload(monkeypatch) -> None:
    monkeypatch.setattr(analyze_nodes, "_run_vocabulary_llm_span", _fake_run_vocabulary_span)
    monkeypatch.setattr(analyze_nodes, "_run_grammar_llm_span", _fake_run_grammar_span)
    monkeypatch.setattr(analyze_nodes, "_run_translation_llm_span", _fake_run_translation_span)

    client = TestClient(app)
    response = client.post(
        "/analyze",
        json={
            "text": (
                "Shopkeepers are facing a constitutional dispute. "
                "Disturbingly, there are daily incidents of violence against workers."
            ),
            "reading_goal": "daily_reading",
            "reading_variant": "intermediate_reading",
            "source_type": "user_input",
        },
    )
    assert response.status_code == 200
    body = response.json()
    RenderSceneModel.model_validate(body)
    assert body["schema_version"] == "3.0.0"
    assert body["request"]["profile_id"] == "daily_intermediate"
    assert len(body["inline_marks"]) == 1
    assert len(body["sentence_entries"]) == 0
    assert len(body["translations"]) == 2


def test_analyze_route_returns_empty_result_when_all_agents_fail(monkeypatch) -> None:
    monkeypatch.setattr(analyze_nodes, "_run_vocabulary_llm_span", _raise_span)
    monkeypatch.setattr(analyze_nodes, "_run_grammar_llm_span", _raise_span)
    monkeypatch.setattr(analyze_nodes, "_run_translation_llm_span", _raise_span)

    client = TestClient(app)
    response = client.post(
        "/analyze",
        json={
            "text": "This is a valid article. It has two sentences.",
            "reading_goal": "exam",
            "reading_variant": "cet",
            "source_type": "user_input",
        },
    )
    assert response.status_code == 200
    body = response.json()
    warning_codes = {warning["code"] for warning in body["warnings"]}
    assert "VOCABULARY_AGENT_FAILED" in warning_codes
    assert "GRAMMAR_AGENT_FAILED" in warning_codes
    assert "TRANSLATION_AGENT_FAILED" in warning_codes
    assert body["inline_marks"] == []
    assert body["sentence_entries"] == []

def test_analyze_route_returns_academic_render_scene() -> None:
    client = TestClient(app)
    response = client.post(
        "/analyze",
        json={
            "text": "This paper investigates representation learning in sparse settings.",
            "reading_goal": "academic",
            "reading_variant": "academic_general",
            "source_type": "user_input",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "3.0.0-academic"


def test_analyze_route_surfaces_draft_validation_warnings(monkeypatch) -> None:
    monkeypatch.setattr(analyze_nodes, "_run_vocabulary_llm_span", _invalid_vocab_span)
    monkeypatch.setattr(analyze_nodes, "_run_grammar_llm_span", _fake_run_grammar_span)
    monkeypatch.setattr(analyze_nodes, "_run_translation_llm_span", _fake_run_translation_span)
    monkeypatch.setattr(learning_workflow, "should_trigger_repair", lambda *_args, **_kwargs: False)

    client = TestClient(app)
    response = client.post(
        "/analyze",
        json={
            "text": (
                "Shopkeepers are having to go to extreme lengths to stop shoplifters. "
                "Disturbingly, there are daily incidents of violence against workers."
            ),
            "reading_goal": "daily_reading",
            "reading_variant": "intermediate_reading",
            "source_type": "user_input",
        },
    )
    assert response.status_code == 200
    body = response.json()
    warning_codes = {warning["code"] for warning in body["warnings"]}
    assert "DRAFT_VALIDATION" in warning_codes


def test_projection_keeps_stable_ids_when_prior_mark_is_dropped() -> None:
    prepared_input = prepare_input(
        "This sentence mentions this first. "
        "Another sentence mentions leverage clearly."
    )
    plan = build_goal_execution_plan("daily_reading", "intermediate_reading")

    baseline = project_to_render_scene(
        annotation_output=AnnotationOutput(
            annotations=[VocabHighlight(sentence_id="s2", text="leverage")],
            sentence_translations=[
                SentenceTranslation(
                    sentence_id="s1",
                    translation_zh="第一句先提到了 this。",
                ),
                SentenceTranslation(
                    sentence_id="s2",
                    translation_zh="第二句清楚地提到了 leverage。",
                ),
            ],
        ),
        prepared_input=prepared_input,
        source_type="user_input",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        profile_id=plan.prompt_profile,
        request_id="req-1",
    )

    with_dropped_prefix = project_to_render_scene(
        annotation_output=AnnotationOutput(
            annotations=[
                PhraseGloss(
                    sentence_id="s1",
                    text="missing anchor",
                    phrase_type="collocation",
                    zh="缺失锚点",
                ),
                VocabHighlight(sentence_id="s2", text="leverage"),
            ],
            sentence_translations=[
                SentenceTranslation(
                    sentence_id="s1",
                    translation_zh="第一句先提到了 this。",
                ),
                SentenceTranslation(
                    sentence_id="s2",
                    translation_zh="第二句清楚地提到了 leverage。",
                ),
            ],
        ),
        prepared_input=prepared_input,
        source_type="user_input",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        profile_id=plan.prompt_profile,
        request_id="req-2",
    )

    assert baseline.result.inline_marks[0].id == with_dropped_prefix.result.inline_marks[0].id
    assert with_dropped_prefix.dropped_count == 1


def test_parallel_agents_aggregate_usage_summary(monkeypatch) -> None:
    monkeypatch.setattr(analyze_nodes, "_run_vocabulary_llm_span", _usage_vocab_span)
    monkeypatch.setattr(analyze_nodes, "_run_grammar_llm_span", _usage_grammar_span)
    monkeypatch.setattr(analyze_nodes, "_run_translation_llm_span", _usage_translation_span)

    prepared_input = prepare_input("Sentence one. Sentence two.")
    state = {
        "prepared_input": prepared_input,
        "goal_execution_plan": analyze_nodes.build_goal_execution_plan(
            "daily_reading", "intermediate_reading"
        ),
        "payload": AnalyzeRequest.model_validate({
            "request_id": "req-usage",
            "text": "Sentence one. Sentence two.",
            "source_type": "user_input",
            "reading_goal": "daily_reading",
            "reading_variant": "intermediate_reading",
        }),
    }

    result = asyncio.run(analyze_nodes._run_parallel_agents(state, model_selection=None))

    assert result["usage_summary"]["available"] is True
    assert result["usage_summary"]["aggregate"] == {
        "input_tokens": 41,
        "output_tokens": 27,
        "total_tokens": 68,
    }
    assert result["usage_summary"]["per_agent"]["vocabulary"]["total_tokens"] == 18

def test_derive_user_config_node_reuses_precomputed_plan(monkeypatch) -> None:
    precomputed_plan = build_goal_execution_plan("daily_reading", "intermediate_reading")

    def _fail(*args, **kwargs):
        raise AssertionError("build_goal_execution_plan should not be called")

    monkeypatch.setattr(analyze_nodes, "build_goal_execution_plan", _fail)

    result = asyncio.run(
        analyze_nodes.derive_user_config_node(
            {
                "goal_execution_plan": precomputed_plan,
                "payload": AnalyzeRequest.model_validate(
                    {
                        "request_id": "req-precomputed",
                        "text": "Sentence one.",
                        "source_type": "user_input",
                        "reading_goal": "daily_reading",
                        "reading_variant": "intermediate_reading",
                    }
                ),
            }
        )
    )

    # node_timings is always returned; the key assertion is that
    # build_goal_execution_plan was not called (monkeypatched to _fail).
    assert "goal_execution_plan" not in result
    assert "node_timings" in result
    assert "derive_user_config" in result["node_timings"]


class _FakeRunTree:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def set(self, **kwargs) -> None:
        self.calls.append(kwargs)


class _FakeAgentRunResult:
    def __init__(self, output, usage: dict[str, object] | None = None) -> None:
        self.output = output
        self._usage = usage

    def usage(self):
        return self._usage


class _FakeRunUsage:
    def __init__(self, *, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.details = {}


def test_llm_span_sets_usage_metadata_for_langsmith(monkeypatch) -> None:
    async def _fake_vocabulary_agent(*args, **kwargs):
        return _FakeAgentRunResult(
            VocabularyDraft(vocab_highlights=[], phrase_glosses=[], context_glosses=[]),
            _FakeRunUsage(input_tokens=11, output_tokens=7),
        )

    monkeypatch.setattr(analyze_nodes, "run_vocabulary_agent", _fake_vocabulary_agent)
    prompt_strategy = analyze_nodes.build_vocabulary_bundle(
        analyze_nodes.build_goal_execution_plan("daily_reading", "intermediate_reading")
    ).prompt_strategy

    deps = analyze_nodes.VocabularyAgentDeps(
        sentences=[{"sentence_id": "s1", "text": "Sentence one."}],
        prompt_strategy=prompt_strategy,
        examples=[],
    )

    result = asyncio.run(
        analyze_nodes._run_vocabulary_llm_span(
            deps=deps,
            metadata={"node": "vocabulary_agent"},
            model_selection=None,
        )
    )

    assert result["usage_metadata"] == {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}


def _drop(reason: str, stage: str = "grounding") -> DropLogEntry:
    return DropLogEntry(
        source_agent="vocabulary",
        annotation_type="vocab_highlight",
        sentence_id="s1",
        anchor_text="missing",
        drop_reason=reason,
        drop_stage=stage,
        dropped_at=datetime.now(),
    )


def test_should_repair_ignores_deterministic_cleanup_drops() -> None:
    normalized_result = NormalizedAnnotationResult(
        annotations=[],
        sentence_translations=[],
        drop_log=[
            _drop("duplicate", "deduplication"),
            _drop("subsumed_by_phrase_gloss", "conflict_resolution"),
            _drop("density_exceeded_max_3", "density_control"),
        ],
    )

    assert learning_workflow._should_repair({"normalized_result": normalized_result}) is False


def test_should_repair_triggers_for_zero_annotations_with_repair_worthy_drop() -> None:
    normalized_result = NormalizedAnnotationResult(
        annotations=[],
        sentence_translations=[],
        drop_log=[_drop("anchor_not_substring", "grounding")],
    )

    assert learning_workflow._should_repair({"normalized_result": normalized_result}) is True


def test_repair_agent_node_uses_same_zero_annotation_repair_rule(monkeypatch) -> None:
    repaired = NormalizedAnnotationResult(annotations=[], sentence_translations=[], drop_log=[])
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

    text = "Languages change."
    state = {
        "payload": AnalyzeRequest.model_validate(
            {
                "request_id": "req-repair",
                "text": text,
                "source_type": "user_input",
                "reading_goal": "daily_reading",
                "reading_variant": "intermediate_reading",
            }
        ),
        "prepared_input": prepare_input(text),
        "goal_execution_plan": build_goal_execution_plan("daily_reading", "intermediate_reading"),
        "vocabulary_draft": VocabularyDraft(
            vocab_highlights=[], phrase_glosses=[], context_glosses=[]
        ),
        "grammar_draft": GrammarDraft(grammar_notes=[], sentence_analyses=[]),
        "translation_draft": TranslationDraft(title="测试标题", sentence_translations=[]),
        "normalized_result": NormalizedAnnotationResult(
            annotations=[],
            sentence_translations=[],
            drop_log=[_drop("anchor_not_substring", "grounding")],
        ),
    }

    result = asyncio.run(analyze_nodes.repair_agent_node(state, config={}))  # type: ignore[arg-type]

    assert result["repair_request"]["repaired"] is True
    assert "repair_worthy_drops: 1" in result["repair_request"]["error_context"]
    repair_mock.assert_awaited_once()


# ── Phase 2.4B: Workflow mainline switch tests ────────────────────────


def _vocab_draft_with_phrase() -> VocabularyDraft:
    return VocabularyDraft(
        vocab_highlights=[
            DraftVocabHighlight(sentence_id="s1", text="prompted"),
        ],
        phrase_glosses=[
            DraftPhraseGloss(
                sentence_id="s1",
                label="prompt sb to do sth",
                anchor_quotes=[AnchorQuote(text="prompted"), AnchorQuote(text="to rethink")],
                phrase_type="phrasal_verb",
                zh="促使某人做某事",
            )
        ],
        context_glosses=[],
    )


def _grammar_draft_with_note() -> GrammarDraft:
    return GrammarDraft(
        grammar_notes=[
            DraftGrammarNote(
                sentence_id="s1",
                grammar_point="not only 句首倒装",
                anchor_quotes=[AnchorQuote(text="Not only did"), AnchorQuote(text="but they also")],
                note_zh="Not only 位于句首时使用部分倒装。",
            )
        ],
        sentence_analyses=[],
    )


def test_workflow_outputs_range_anchor(monkeypatch) -> None:
    """Workflow 正常输出至少一个 range anchor。"""

    async def _fake_vocab(*a, **kw):
        return {"output": _vocab_draft_with_phrase()}

    async def _fake_grammar(*a, **kw):
        return {"output": GrammarDraft(grammar_notes=[], sentence_analyses=[])}

    async def _fake_translation(*a, **kw):
        return {
            "output": TranslationDraft(
                title="测试",
                sentence_translations=[
                    SentenceTranslation(sentence_id="s1", translation_zh="结果促使团队。"),
                ],
            ),
        }

    monkeypatch.setattr(analyze_nodes, "_run_vocabulary_llm_span", _fake_vocab)
    monkeypatch.setattr(analyze_nodes, "_run_grammar_llm_span", _fake_grammar)
    monkeypatch.setattr(analyze_nodes, "_run_translation_llm_span", _fake_translation)

    client = TestClient(app)
    response = client.post(
        "/analyze",
        json={
            "text": "The results prompted the team to rethink their approach.",
            "reading_goal": "daily_reading",
            "reading_variant": "intermediate_reading",
            "source_type": "user_input",
        },
    )
    assert response.status_code == 200
    body = response.json()
    anchor_kinds = {mark["anchor"]["kind"] for mark in body["inline_marks"]}
    assert "range" in anchor_kinds or "multi_range" in anchor_kinds


def test_workflow_multi_span_outputs_multi_range_anchor(monkeypatch) -> None:
    """Multi-span phrase/grammar 输出 multi_range anchor。"""

    # Quotes matching "Not only did the results prompt the team,
    # but they also changed the plan."
    vocab_draft = VocabularyDraft(
        vocab_highlights=[
            DraftVocabHighlight(sentence_id="s1", text="prompt"),
        ],
        phrase_glosses=[
            DraftPhraseGloss(
                sentence_id="s1",
                label="prompt sb to do sth",
                anchor_quotes=[AnchorQuote(text="prompt"), AnchorQuote(text="the team")],
                phrase_type="phrasal_verb",
                zh="促使某人做某事",
            )
        ],
        context_glosses=[],
    )

    async def _fake_vocab(*a, **kw):
        return {"output": vocab_draft}

    async def _fake_grammar(*a, **kw):
        return {"output": _grammar_draft_with_note()}

    async def _fake_translation(*a, **kw):
        return {
            "output": TranslationDraft(
                title="测试",
                sentence_translations=[
                    SentenceTranslation(
                        sentence_id="s1",
                        translation_zh="不仅结果促使了团队，他们还改变了计划。",
                    ),
                ],
            ),
        }

    monkeypatch.setattr(analyze_nodes, "_run_vocabulary_llm_span", _fake_vocab)
    monkeypatch.setattr(analyze_nodes, "_run_grammar_llm_span", _fake_grammar)
    monkeypatch.setattr(analyze_nodes, "_run_translation_llm_span", _fake_translation)

    client = TestClient(app)
    response = client.post(
        "/analyze",
        json={
            "text": "Not only did the results prompt the team, but they also changed the plan.",
            "reading_goal": "daily_reading",
            "reading_variant": "intermediate_reading",
            "source_type": "user_input",
        },
    )
    assert response.status_code == 200
    body = response.json()
    multi_range_marks = [
        m for m in body["inline_marks"]
        if m["anchor"]["kind"] == "multi_range"
    ]
    assert len(multi_range_marks) >= 1
    # Multi-range should have multiple ranges
    for mark in multi_range_marks:
        assert len(mark["anchor"]["ranges"]) >= 2


def test_projection_warning_enters_render_scene_warnings() -> None:
    """Range projection warning 会进入 render_scene.warnings / state warnings。"""
    from app.schemas.internal.normalized import (
        CanonicalSpan,
        NormalizedAnnotationResult,
        NormalizedVocabHighlight,
    )

    prepared_input = prepare_input("The results prompted the team.")
    plan = build_goal_execution_plan("daily_reading", "intermediate_reading")

    # Create a normalized result with an intentionally wrong span
    normalized_result = NormalizedAnnotationResult(
        annotations=[],
        normalized_annotations=[
            NormalizedVocabHighlight(
                sentence_id="s1",
                spans=[
                    CanonicalSpan(
                        sentence_id="s1",
                        start=12,
                        end=20,
                        text="WRONGTEXT",
                        resolution_kind="exact",
                    )
                ],
            )
        ],
        sentence_translations=[
            SentenceTranslation(sentence_id="s1", translation_zh="翻译"),
        ],
    )

    state = {
        "payload": AnalyzeRequest.model_validate({
            "request_id": "req-warn",
            "text": "The results prompted the team.",
            "source_type": "user_input",
            "reading_goal": "daily_reading",
            "reading_variant": "intermediate_reading",
        }),
        "prepared_input": prepared_input,
        "goal_execution_plan": plan,
        "normalized_result": normalized_result,
        "warnings": [],
    }

    result = asyncio.run(analyze_nodes.project_render_scene_node(state))
    render_scene = result["render_scene"]

    # The warning should appear in render_scene.warnings
    warning_codes = {w.code for w in render_scene.warnings}
    assert "canonical_range_validation_failed" in warning_codes


def test_old_projection_still_works() -> None:
    """旧 project_to_render_scene 测试不受影响。"""
    prepared_input = prepare_input(
        "This sentence mentions this first. "
        "Another sentence mentions leverage clearly."
    )
    plan = build_goal_execution_plan("daily_reading", "intermediate_reading")

    outcome = project_to_render_scene(
        annotation_output=AnnotationOutput(
            annotations=[VocabHighlight(sentence_id="s2", text="leverage")],
            sentence_translations=[
                SentenceTranslation(sentence_id="s1", translation_zh="第一句。"),
                SentenceTranslation(sentence_id="s2", translation_zh="第二句。"),
            ],
        ),
        prepared_input=prepared_input,
        source_type="user_input",
        reading_goal="daily_reading",
        reading_variant="intermediate_reading",
        profile_id=plan.prompt_profile,
        request_id="req-old",
    )

    assert len(outcome.result.inline_marks) == 1
    assert outcome.result.inline_marks[0].anchor.kind == "text"
