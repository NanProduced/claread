from app.config.settings import Settings
from app.llm.registry import build_model_registry
from app.llm.routes import MODEL_ROUTE_READER_ASK_PLANNER


def test_reader_ask_planner_route_falls_back_to_reader_ask_profile() -> None:
    settings = Settings(
        annotation_model_profile="annotation",
        reader_ask_model_profile="reader_ask",
        reader_ask_planner_model_profile="",
    )

    registry = build_model_registry(settings)

    assert registry.route_defaults[MODEL_ROUTE_READER_ASK_PLANNER] == "reader_ask"


def test_reader_ask_planner_route_prefers_explicit_profile() -> None:
    settings = Settings(
        annotation_model_profile="annotation",
        reader_ask_model_profile="reader_ask",
        reader_ask_planner_model_profile="reader_ask_planner",
    )

    registry = build_model_registry(settings)

    assert registry.route_defaults[MODEL_ROUTE_READER_ASK_PLANNER] == "reader_ask_planner"
