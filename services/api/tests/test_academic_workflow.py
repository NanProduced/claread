from app.schemas.common import TextSpan
from app.schemas.internal.academic_drafts import (
    AcademicSentenceTranslation,
    AcademicTranslationDraft,
    ContentSummary,
    InterpretationNote,
    LogicNote,
    ParagraphRole,
    TermDraft,
    TermNote,
    UnderstandingDraft,
)
from app.schemas.internal.academic_normalized import AcademicNormalizedResult
from app.schemas.internal.analysis import PreparedSentence
from app.schemas.internal.execution_plan import AcademicGoalPolicy
from app.schemas.analysis import AcademicRenderSceneModel
from app.services.analysis.postprocess.academic_normalize import academic_normalize_and_ground
from app.services.analysis.postprocess.academic_projection import project_to_academic_render_scene
from app.services.analysis.preprocess.input_preparation import prepare_input
from app.services.analysis.planning.goal_planner import build_goal_execution_plan
from app.workflow.academic_workflow import build_academic_graph


def _sentence(sentence_id: str, text: str) -> PreparedSentence:
    return PreparedSentence(
        sentence_id=sentence_id,
        paragraph_id="p1",
        text=text,
        sentence_span=TextSpan(start=0, end=len(text)),
    )


def test_academic_request_no_longer_501() -> None:
    graph = build_academic_graph()
    assert graph is not None
    plan = build_goal_execution_plan("academic", "academic_general")
    assert plan.topology_mode == "academic"
    assert plan.output_mode == "academic_scene"
    assert plan.academic_policy is not None


def test_term_and_translation_parallel_pipeline() -> None:
    sentences = [
        _sentence("s1", "The study employed a longitudinal design."),
        _sentence("s2", "The results suggest that the effect may be significant."),
    ]
    term_draft = TermDraft(term_notes=[
        TermNote(
            sentence_ids=["s1"],
            text="longitudinal",
            term_category="technical",
            zh="纵向的",
            context_definition="研究方法语境下指长期跟踪研究的设计",
            discipline="research_methodology",
        ),
    ])
    translation_draft = AcademicTranslationDraft(
        title="纵向研究设计",
        sentence_translations=[
            AcademicSentenceTranslation(sentence_id="s1", translation_zh="该研究采用了纵向设计。"),
            AcademicSentenceTranslation(sentence_id="s2", translation_zh="结果表明，该效应可能是显著的。"),
        ],
    )
    understanding_draft = UnderstandingDraft()

    policy = AcademicGoalPolicy()
    result = academic_normalize_and_ground(
        term_draft, translation_draft, understanding_draft, sentences, policy,
    )
    assert len(result.term_annotations) == 1
    assert len(result.sentence_translations) == 2
    assert result.title == "纵向研究设计"


def test_understanding_agent_empty_interpretation_notes_not_error() -> None:
    sentences = [_sentence("s1", "This is a simple sentence.")]
    term_draft = TermDraft(term_notes=[])
    translation_draft = AcademicTranslationDraft(
        title="简单文本",
        sentence_translations=[
            AcademicSentenceTranslation(sentence_id="s1", translation_zh="这是一个简单的句子。"),
        ],
    )
    understanding_draft = UnderstandingDraft(
        logic_notes=[],
        interpretation_notes=[],
    )

    policy = AcademicGoalPolicy()
    result = academic_normalize_and_ground(
        term_draft, translation_draft, understanding_draft, sentences, policy,
    )
    assert result.interpretation_notes == []
    assert len(result.sentence_translations) == 1


def test_p2_fields_empty_no_error() -> None:
    sentences = [_sentence("s1", "The data was analyzed using regression.")]
    term_draft = TermDraft(term_notes=[])
    translation_draft = AcademicTranslationDraft(
        title="回归分析",
        sentence_translations=[
            AcademicSentenceTranslation(sentence_id="s1", translation_zh="数据使用回归进行了分析。"),
        ],
    )
    understanding_draft = UnderstandingDraft(
        logic_notes=[],
        interpretation_notes=[],
        paragraph_roles=[],
        content_summary=None,
    )

    policy = AcademicGoalPolicy()
    result = academic_normalize_and_ground(
        term_draft, translation_draft, understanding_draft, sentences, policy,
    )
    assert result.paragraph_roles == []
    assert result.content_summary is None

    prepared = prepare_input("The data was analyzed using regression.")
    outcome = project_to_academic_render_scene(
        normalized_result=result,
        prepared_input=prepared,
        source_type="user_input",
        reading_goal="academic",
        reading_variant="academic_general",
        profile_id="academic_general",
        request_id="test-p2-empty",
    )
    assert isinstance(outcome.result, AcademicRenderSceneModel)


def test_academic_render_scene_model_validates() -> None:
    prepared = prepare_input("The study employed a longitudinal design to track cognitive changes.")
    plan = build_goal_execution_plan("academic", "academic_general")

    sentences = prepared.sentences
    term_draft = TermDraft(term_notes=[
        TermNote(
            sentence_ids=[sentences[0].sentence_id],
            text="longitudinal",
            term_category="technical",
            zh="纵向的",
            context_definition="长期跟踪研究的设计",
        ),
    ])
    translation_draft = AcademicTranslationDraft(
        title="纵向研究",
        sentence_translations=[
            AcademicSentenceTranslation(
                sentence_id=sentences[0].sentence_id,
                translation_zh="该研究采用了纵向设计来追踪认知变化。",
            ),
        ],
    )
    understanding_draft = UnderstandingDraft(
        logic_notes=[
            LogicNote(
                sentence_ids=[sentences[0].sentence_id],
                logic_type="elaboration",
                anchor_text="to track",
                explanation="说明纵向设计的目的",
            ),
        ],
        interpretation_notes=[],
    )

    policy = plan.academic_policy or AcademicGoalPolicy()
    normalized = academic_normalize_and_ground(
        term_draft, translation_draft, understanding_draft, sentences, policy,
    )

    outcome = project_to_academic_render_scene(
        normalized_result=normalized,
        prepared_input=prepared,
        source_type="user_input",
        reading_goal="academic",
        reading_variant="academic_general",
        profile_id=plan.prompt_profile,
        request_id="test-validate",
    )

    assert outcome.result.schema_version == "3.0.0-academic"
    assert len(outcome.result.inline_marks) >= 1
    assert len(outcome.result.sentence_entries) >= 1
    assert len(outcome.result.translations) >= 1

    for mark in outcome.result.inline_marks:
        assert mark.annotation_type in ("term_note", "logic_note")
        assert mark.visual_tone in ("term", "logic")


def test_logic_and_interpretation_density() -> None:
    sentences = [_sentence("s1", "Although the effect was small, it was statistically significant.")]

    term_draft = TermDraft(term_notes=[])
    translation_draft = AcademicTranslationDraft(
        title="效应分析",
        sentence_translations=[
            AcademicSentenceTranslation(sentence_id="s1", translation_zh="尽管效应较小，但在统计上是显著的。"),
        ],
    )
    understanding_draft = UnderstandingDraft(
        logic_notes=[
            LogicNote(
                sentence_ids=["s1"],
                logic_type="concession",
                anchor_text="Although",
                explanation="让步：承认效应小",
            ),
            LogicNote(
                sentence_ids=["s1"],
                logic_type="evidence",
                anchor_text="statistically significant",
                explanation="统计显著",
            ),
            LogicNote(
                sentence_ids=["s1"],
                logic_type="contrast",
                anchor_text="small",
                explanation="对比",
            ),
        ],
        interpretation_notes=[
            InterpretationNote(
                sentence_id="s1",
                interpretation="尽管效应量小，但统计检验拒绝了零假设",
                interpretation_type="disambiguation",
            ),
        ],
    )

    policy = AcademicGoalPolicy(logic_density=2, interpretation_density=1)
    result = academic_normalize_and_ground(
        term_draft, translation_draft, understanding_draft, sentences, policy,
    )

    assert len(result.logic_notes) <= 2
    assert len(result.interpretation_notes) <= 1


def test_cross_sentence_annotation_validates_all_sentence_ids() -> None:
    sentences = [
        _sentence("s1", "The study employed a longitudinal design."),
        _sentence("s2", "The results suggest that the effect may be significant."),
    ]

    term_draft = TermDraft(term_notes=[
        TermNote(
            sentence_ids=["s1", "s2"],
            text="longitudinal",
            term_category="technical",
            zh="纵向的",
            context_definition="长期跟踪研究的设计",
        ),
    ])
    translation_draft = AcademicTranslationDraft(
        title="纵向研究",
        sentence_translations=[
            AcademicSentenceTranslation(sentence_id="s1", translation_zh="该研究采用了纵向设计。"),
            AcademicSentenceTranslation(sentence_id="s2", translation_zh="结果表明该效应可能显著。"),
        ],
    )
    understanding_draft = UnderstandingDraft(
        logic_notes=[
            LogicNote(
                sentence_ids=["s1", "s2"],
                logic_type="evidence",
                anchor_text="employed",
                explanation="跨句证据链：从方法到结果的支撑",
            ),
        ],
    )

    policy = AcademicGoalPolicy()
    result = academic_normalize_and_ground(
        term_draft, translation_draft, understanding_draft, sentences, policy,
    )
    assert len(result.term_annotations) == 1
    assert result.term_annotations[0].sentence_ids == ["s1", "s2"]
    assert len(result.logic_notes) == 1
    assert result.logic_notes[0].sentence_ids == ["s1", "s2"]

    prepared = prepare_input(
        "The study employed a longitudinal design. The results suggest that the effect may be significant."
    )
    outcome = project_to_academic_render_scene(
        normalized_result=result,
        prepared_input=prepared,
        source_type="user_input",
        reading_goal="academic",
        reading_variant="academic_general",
        profile_id="academic_general",
        request_id="test-cross-sentence",
    )

    term_entries = [e for e in outcome.result.sentence_entries if e.entry_type == "term_note"]
    logic_entries = [e for e in outcome.result.sentence_entries if e.entry_type == "logic_note"]

    assert len(term_entries) == 2
    term_sids = {e.sentence_id for e in term_entries}
    assert term_sids == {"s1", "s2"}

    primary_term = [e for e in term_entries if e.sentence_id == "s1"][0]
    cross_term = [e for e in term_entries if e.sentence_id == "s2"][0]
    assert not primary_term.label.startswith("↗")
    assert cross_term.label.startswith("↗")

    assert len(logic_entries) == 2
    logic_sids = {e.sentence_id for e in logic_entries}
    assert logic_sids == {"s1", "s2"}

    term_inline = [m for m in outcome.result.inline_marks if m.annotation_type == "term_note"]
    assert len(term_inline) == 1
    assert term_inline[0].anchor.kind == "text"
    assert term_inline[0].anchor.sentence_id == "s1"


def test_cross_sentence_annotation_drops_when_secondary_sid_invalid() -> None:
    sentences = [
        _sentence("s1", "The study employed a longitudinal design."),
    ]

    term_draft = TermDraft(term_notes=[
        TermNote(
            sentence_ids=["s1", "s_nonexistent"],
            text="longitudinal",
            term_category="technical",
            zh="纵向的",
            context_definition="长期跟踪研究的设计",
        ),
    ])
    translation_draft = AcademicTranslationDraft(
        title="纵向研究",
        sentence_translations=[
            AcademicSentenceTranslation(sentence_id="s1", translation_zh="该研究采用了纵向设计。"),
        ],
    )
    understanding_draft = UnderstandingDraft(
        logic_notes=[
            LogicNote(
                sentence_ids=["s1", "s_nonexistent"],
                logic_type="evidence",
                anchor_text="employed",
                explanation="跨句证据",
            ),
        ],
    )

    policy = AcademicGoalPolicy()
    result = academic_normalize_and_ground(
        term_draft, translation_draft, understanding_draft, sentences, policy,
    )
    assert len(result.term_annotations) == 0
    assert len(result.logic_notes) == 0
    assert any("sentence_id_not_found" in e.drop_reason for e in result.drop_log)


def test_interpretation_density_zero_returns_empty() -> None:
    sentences = [_sentence("s1", "The effect was statistically significant.")]

    term_draft = TermDraft(term_notes=[])
    translation_draft = AcademicTranslationDraft(
        title="效应分析",
        sentence_translations=[
            AcademicSentenceTranslation(sentence_id="s1", translation_zh="该效应在统计上是显著的。"),
        ],
    )
    understanding_draft = UnderstandingDraft(
        interpretation_notes=[
            InterpretationNote(
                sentence_id="s1",
                interpretation="统计检验拒绝了零假设",
                interpretation_type="disambiguation",
            ),
        ],
    )

    policy = AcademicGoalPolicy(interpretation_density=0)
    result = academic_normalize_and_ground(
        term_draft, translation_draft, understanding_draft, sentences, policy,
    )
    assert len(result.interpretation_notes) == 0
    assert any("density_disabled" in e.drop_reason for e in result.drop_log)


def test_drop_log_preserved_in_normalized_result() -> None:
    sentences = [_sentence("s1", "The effect was small.")]

    term_draft = TermDraft(term_notes=[
        TermNote(
            sentence_ids=["s_nonexistent"],
            text="effect",
            term_category="technical",
            zh="效应",
            context_definition="统计效应",
        ),
    ])
    translation_draft = AcademicTranslationDraft(
        title="效应",
        sentence_translations=[
            AcademicSentenceTranslation(sentence_id="s1", translation_zh="效应较小。"),
        ],
    )
    understanding_draft = UnderstandingDraft()

    policy = AcademicGoalPolicy()
    result = academic_normalize_and_ground(
        term_draft, translation_draft, understanding_draft, sentences, policy,
    )
    assert len(result.drop_log) > 0
    assert result.drop_log[0].source_agent == "term"
    assert result.drop_log[0].annotation_type == "term_note"


def test_task_submit_response_accepts_academic_render_scene() -> None:
    from app.schemas.tasks import TaskSubmitResponse
    from uuid import uuid4

    scene = AcademicRenderSceneModel(
        schema_version="3.0.0-academic",
        request={"request_id": "test", "source_type": "user_input", "reading_goal": "academic", "reading_variant": "academic_general", "profile_id": "academic_general"},
        article={"source_type": "user_input", "source_text": "test", "render_text": "test", "paragraphs": [], "sentences": []},
        translations=[],
        inline_marks=[],
        sentence_entries=[],
    )

    response = TaskSubmitResponse(
        task_id=uuid4(),
        record_id=uuid4(),
        status="succeeded",
        created=True,
        render_scene=scene,
    )
    assert response.render_scene is not None
    assert response.render_scene.schema_version == "3.0.0-academic"


def test_quality_state_normal_when_translations_present() -> None:
    sentences = [_sentence("s1", "The cat sat on the mat and looked around.")]
    term_draft = TermDraft(term_notes=[])
    translation_draft = AcademicTranslationDraft(
        title="简单文本",
        sentence_translations=[
            AcademicSentenceTranslation(sentence_id="s1", translation_zh="猫坐在垫子上四处张望。"),
        ],
    )
    understanding_draft = UnderstandingDraft()

    policy = AcademicGoalPolicy()
    result = academic_normalize_and_ground(
        term_draft, translation_draft, understanding_draft, sentences, policy,
    )
    assert result.quality_state == "normal"
    assert result.quality_issues == []


def test_quality_state_degraded_when_translations_missing() -> None:
    sentences = [_sentence("s1", "The data was analyzed using regression.")]
    term_draft = TermDraft(term_notes=[])
    translation_draft = AcademicTranslationDraft(
        title="回归分析",
        sentence_translations=[],
    )
    understanding_draft = UnderstandingDraft()

    policy = AcademicGoalPolicy()
    result = academic_normalize_and_ground(
        term_draft, translation_draft, understanding_draft, sentences, policy,
    )
    assert result.quality_state == "degraded"
    assert any("translations_missing" in issue for issue in result.quality_issues)


def test_quality_state_degraded_when_term_empty_with_academic_density() -> None:
    sentences = [
        _sentence("s1", "The longitudinal regression analysis demonstrated a statistically significant correlation coefficient."),
    ]
    term_draft = TermDraft(term_notes=[])
    translation_draft = AcademicTranslationDraft(
        title="回归分析",
        sentence_translations=[
            AcademicSentenceTranslation(sentence_id="s1", translation_zh="纵向回归分析表明了统计上显著的相关系数。"),
        ],
    )
    understanding_draft = UnderstandingDraft()

    policy = AcademicGoalPolicy()
    result = academic_normalize_and_ground(
        term_draft, translation_draft, understanding_draft, sentences, policy,
    )
    assert result.quality_state == "degraded"
    assert any("term_annotations_empty_with_academic_density" in issue for issue in result.quality_issues)


def test_quality_state_normal_when_term_empty_without_academic_density() -> None:
    sentences = [_sentence("s1", "The cat sat on the mat.")]
    term_draft = TermDraft(term_notes=[])
    translation_draft = AcademicTranslationDraft(
        title="简单文本",
        sentence_translations=[
            AcademicSentenceTranslation(sentence_id="s1", translation_zh="猫坐在垫子上。"),
        ],
    )
    understanding_draft = UnderstandingDraft()

    policy = AcademicGoalPolicy()
    result = academic_normalize_and_ground(
        term_draft, translation_draft, understanding_draft, sentences, policy,
    )
    assert result.quality_state == "normal"


def test_concept_opposition_term_category_accepted() -> None:
    sentences = [_sentence("s1", "The nature versus nurture debate continues.")]
    term_draft = TermDraft(term_notes=[
        TermNote(
            sentence_ids=["s1"],
            text="nature versus nurture",
            term_category="concept_opposition",
            zh="先天与后天",
            context_definition="关于行为发展是先天遗传还是后天环境决定的经典争论",
        ),
    ])
    translation_draft = AcademicTranslationDraft(
        title="先天与后天",
        sentence_translations=[
            AcademicSentenceTranslation(sentence_id="s1", translation_zh="先天与后天的争论仍在继续。"),
        ],
    )
    understanding_draft = UnderstandingDraft()

    policy = AcademicGoalPolicy()
    result = academic_normalize_and_ground(
        term_draft, translation_draft, understanding_draft, sentences, policy,
    )
    assert len(result.term_annotations) == 1
    assert result.term_annotations[0].term_category == "concept_opposition"


def test_extended_logic_types_accepted() -> None:
    sentences = [_sentence("s1", "We hypothesize that the intervention reduces symptoms. Thus, the treatment is effective.")]
    understanding_draft = UnderstandingDraft(
        logic_notes=[
            LogicNote(
                sentence_ids=["s1"],
                logic_type="hypothesis",
                anchor_text="hypothesize",
                explanation="研究假设",
            ),
            LogicNote(
                sentence_ids=["s1"],
                logic_type="conclusion",
                anchor_text="Thus",
                explanation="结论推导",
            ),
        ],
    )
    term_draft = TermDraft(term_notes=[])
    translation_draft = AcademicTranslationDraft(
        title="干预研究",
        sentence_translations=[
            AcademicSentenceTranslation(sentence_id="s1", translation_zh="我们假设干预能减少症状。因此，治疗是有效的。"),
        ],
    )

    policy = AcademicGoalPolicy(logic_density=3)
    result = academic_normalize_and_ground(
        term_draft, translation_draft, understanding_draft, sentences, policy,
    )
    logic_types = {n.logic_type for n in result.logic_notes}
    assert "hypothesis" in logic_types
    assert "conclusion" in logic_types
