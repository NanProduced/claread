from __future__ import annotations

from typing import Final, Literal

ModelRoute = Literal[
    "annotation_generation",
    "daily_annotation",
    "daily_analysis",
    "daily_review",
]

MODEL_ROUTE_ANNOTATION_GENERATION: Final[ModelRoute] = "annotation_generation"
MODEL_ROUTE_DAILY_ANNOTATION: Final[ModelRoute] = "daily_annotation"
MODEL_ROUTE_DAILY_ANALYSIS: Final[ModelRoute] = "daily_analysis"
MODEL_ROUTE_DAILY_REVIEW: Final[ModelRoute] = "daily_review"

ALL_MODEL_ROUTES: tuple[ModelRoute, ...] = (
    MODEL_ROUTE_ANNOTATION_GENERATION,
    MODEL_ROUTE_DAILY_ANNOTATION,
    MODEL_ROUTE_DAILY_ANALYSIS,
    MODEL_ROUTE_DAILY_REVIEW,
)
