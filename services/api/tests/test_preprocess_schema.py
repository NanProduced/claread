from app.schemas.analysis import AnalyzeRequest, RenderSceneModel


def test_analyze_request_rejects_goal_variant_mismatch() -> None:
    try:
        AnalyzeRequest.model_validate(
            {
                "text": "This is a test article.",
                "reading_goal": "academic",
                "reading_variant": "cet",
            }
        )
    except Exception as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("expected goal/variant validation error")


def test_analyze_request_accepts_kaoyan_variant() -> None:
    result = AnalyzeRequest.model_validate(
        {
            "text": "This is a test article.",
            "reading_goal": "exam",
            "reading_variant": "kaoyan",
        }
    )

    assert result.reading_goal == "exam"
    assert result.reading_variant == "kaoyan"


def test_analyze_request_accepts_tem_variant() -> None:
    result = AnalyzeRequest.model_validate(
        {
            "text": "This is a test article.",
            "reading_goal": "exam",
            "reading_variant": "tem",
        }
    )

    assert result.reading_goal == "exam"
    assert result.reading_variant == "tem"


def test_render_scene_model_schema_accepts_minimal_valid_payload() -> None:
    payload = {
        "schema_version": "3.0.0",
        "request": {
            "request_id": "req-001",
            "source_type": "user_input",
            "reading_goal": "daily_reading",
            "reading_variant": "intermediate_reading",
            "profile_id": "daily_intermediate",
        },
        "article": {
            "source_type": "user_input",
            "source_text": "This is a test article.",
            "render_text": "This is a test article.",
            "paragraphs": [
                {
                    "paragraph_id": "p1",
                    "text": "This is a test article.",
                    "render_span": {"start": 0, "end": 23},
                    "sentence_ids": ["s1"],
                }
            ],
            "sentences": [
                {
                    "sentence_id": "s1",
                    "paragraph_id": "p1",
                    "text": "This is a test article.",
                    "sentence_span": {"start": 0, "end": 23},
                }
            ],
        },
        "translations": [{"sentence_id": "s1", "translation_zh": "这是一篇测试文章。"}],
        "inline_marks": [],
        "sentence_entries": [],
        "warnings": [],
    }

    result = RenderSceneModel.model_validate(payload)
    assert result.schema_version == "3.0.0"
    assert result.request.profile_id == "daily_intermediate"
    assert result.article.sentences[0].sentence_span.start == 0
