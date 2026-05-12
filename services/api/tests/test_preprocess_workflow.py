from app.schemas.common import TextSpan
from app.schemas.internal.analysis import PreparedSentence
from app.services.analysis.postprocess.anchor_resolution import resolve_text_anchor
from app.services.analysis.preprocess.input_preparation import prepare_input
from app.services.analysis.planning.goal_planner import build_goal_execution_plan


def test_prepare_input_sanitizes_markup_links_and_code() -> None:
    prepared = prepare_input(
        "<div>Hello</div> Visit https://example.com now.\n\n```python\nprint('x')\n```"
    )

    assert prepared.render_text == "Hello\nVisit now."
    assert "remove_url" in prepared.sanitize_report.actions
    assert "remove_code_fence" in prepared.sanitize_report.actions
    assert prepared.sentences[0].text == "Hello\nVisit now."


def test_build_goal_execution_plan_preserves_beginner_profile_and_policies() -> None:
    plan = build_goal_execution_plan("daily_reading", "beginner_reading")

    assert plan.prompt_profile == "daily_beginner"
    assert plan.policy.grammar_focus == "explicit_split"
    assert plan.policy.vocabulary_focus == "high_value_only"


def test_resolve_anchor_supports_exact_and_normalized_match() -> None:
    sentence = PreparedSentence(
        sentence_id="s1",
        paragraph_id="p1",
        text='The store said "high-value" products were targeted.',
        sentence_span=TextSpan(start=10, end=59),
    )

    exact = resolve_text_anchor(sentence, "products")
    normalized = resolve_text_anchor(sentence, "high value")

    assert exact is not None
    assert exact.start == 38
    assert normalized is not None
    assert normalized.start == 26


def test_resolve_anchor_drops_ambiguous_occurrence_without_index() -> None:
    sentence = PreparedSentence(
        sentence_id="s1",
        paragraph_id="p1",
        text="Chocolate is chocolate for chocolate lovers.",
        sentence_span=TextSpan(start=0, end=42),
    )

    assert resolve_text_anchor(sentence, "chocolate") is None
    assert resolve_text_anchor(sentence, "chocolate", anchor_occurrence=2) is not None
