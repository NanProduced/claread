import asyncio

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.analysis import AnalyzeRequest, RenderSceneModel
from app.schemas.internal.analysis import (
    AnnotationOutput,
    PhraseGloss,
    SentenceTranslation,
    VocabHighlight,
)
from app.schemas.internal.drafts import GrammarDraft, TranslationDraft, VocabularyDraft
from app.services.analysis.preprocess.input_preparation import prepare_input
from app.services.analysis.postprocess.projection import project_to_render_scene
from app.services.analysis.planning.goal_planner import build_goal_execution_plan
from app.workflow import analyze_nodes


async def _fake_run_vocabulary_span(*args, **kwargs):
    return {
        "output": VocabularyDraft(
            vocab_highlights=[
                VocabHighlight(sentence_id="s1", text="constitutional")
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
                SentenceTranslation(sentence_id="s1", translation_zh="店主不得不采取极端措施阻止商店扒手。"),
                SentenceTranslation(sentence_id="s2", translation_zh="令人不安的是，每天都有针对店员的暴力事件。"),
            ]
        ),
        "usage": {"input_tokens": 40, "output_tokens": 20, "total_tokens": 60},
    }


async def _raise_span(*args, **kwargs):
    raise RuntimeError("agent failed")


async def _invalid_vocab_span(*args, **kwargs):
    invalid = VocabHighlight.model_construct(
        type="vocab_highlight",
        sentence_id="s1",
        text="extreme lengths",
        occurrence=None,
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
    assert body["inline_marks"] == []


def test_projection_keeps_stable_ids_when_prior_mark_is_dropped() -> None:
    prepared_input = prepare_input("This sentence mentions this first. Another sentence mentions leverage clearly.")
    plan = build_goal_execution_plan("daily_reading", "intermediate_reading")

    baseline = project_to_render_scene(
        annotation_output=AnnotationOutput(
            annotations=[VocabHighlight(sentence_id="s2", text="leverage")],
            sentence_translations=[
                SentenceTranslation(sentence_id="s1", translation_zh="第一句先提到了 this。"),
                SentenceTranslation(sentence_id="s2", translation_zh="第二句清楚地提到了 leverage。"),
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
                SentenceTranslation(sentence_id="s1", translation_zh="第一句先提到了 this。"),
                SentenceTranslation(sentence_id="s2", translation_zh="第二句清楚地提到了 leverage。"),
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
        "goal_execution_plan": analyze_nodes.build_goal_execution_plan("daily_reading", "intermediate_reading"),
        "payload": AnalyzeRequest.model_validate(
            {                "request_id": "req-usage",
                "text": "Sentence one. Sentence two.",
                "source_type": "user_input",
                "reading_goal": "daily_reading",
                "reading_variant": "intermediate_reading",
            }
        ),
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

    assert result == {}


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
