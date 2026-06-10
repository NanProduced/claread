from app.config.settings import Settings
from app.llm.registry import build_model_registry
from app.llm.routes import (
    MODEL_ROUTE_READER_ASK,
    MODEL_ROUTE_READER_ASK_PLANNER,
    MODEL_ROUTE_READER_ASK_REPLAN,
)


def test_reader_ask_route_uses_ask_claread_profile() -> None:
    settings = Settings(
        annotation_model_profile="annotation",
        ask_claread_profile="ask_claread",
        reader_ask_planner_model_profile="",
    )

    registry = build_model_registry(settings)

    assert registry.route_defaults[MODEL_ROUTE_READER_ASK] == "ask_claread"


def test_reader_ask_planner_route_falls_back_to_ask_claread_profile() -> None:
    settings = Settings(
        annotation_model_profile="annotation",
        ask_claread_profile="ask_claread",
        reader_ask_planner_model_profile="",
    )

    registry = build_model_registry(settings)

    assert registry.route_defaults[MODEL_ROUTE_READER_ASK_PLANNER] == "ask_claread"


def test_reader_ask_planner_route_prefers_explicit_profile() -> None:
    settings = Settings(
        annotation_model_profile="annotation",
        ask_claread_profile="ask_claread",
        reader_ask_planner_model_profile="reader_ask_planner",
    )

    registry = build_model_registry(settings)

    assert registry.route_defaults[MODEL_ROUTE_READER_ASK_PLANNER] == "reader_ask_planner"


def test_reader_ask_replan_route_falls_back_to_ask_claread_profile() -> None:
    settings = Settings(
        annotation_model_profile="annotation",
        ask_claread_profile="ask_claread",
        reader_ask_replan_model_profile="",
    )

    registry = build_model_registry(settings)

    assert registry.route_defaults[MODEL_ROUTE_READER_ASK_REPLAN] == "ask_claread"


def test_reader_ask_replan_route_prefers_explicit_profile() -> None:
    settings = Settings(
        annotation_model_profile="annotation",
        ask_claread_profile="ask_claread",
        reader_ask_replan_model_profile="reader_ask_replan",
    )

    registry = build_model_registry(settings)

    assert registry.route_defaults[MODEL_ROUTE_READER_ASK_REPLAN] == "reader_ask_replan"
