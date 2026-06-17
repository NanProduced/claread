from __future__ import annotations

from typing import Final, Literal

# Round 18: ``reader_ask_planner`` route and its public DTO field have both
# been removed. Ask Claread resolves only the main answer and repair/replan
# routes.
ModelRoute = Literal[
    "annotation_generation",
    "dict_ai",
    "reader_ask",
    "reader_ask_replan",
    "daily_annotation",
    "daily_analysis",
    "daily_review",
    "rag_embedding",
    "rag_rerank",
]

MODEL_ROUTE_ANNOTATION_GENERATION: Final[ModelRoute] = "annotation_generation"
MODEL_ROUTE_DICT_AI: Final[ModelRoute] = "dict_ai"
MODEL_ROUTE_READER_ASK: Final[ModelRoute] = "reader_ask"
MODEL_ROUTE_READER_ASK_REPLAN: Final[ModelRoute] = "reader_ask_replan"
MODEL_ROUTE_DAILY_ANNOTATION: Final[ModelRoute] = "daily_annotation"
MODEL_ROUTE_DAILY_ANALYSIS: Final[ModelRoute] = "daily_analysis"
MODEL_ROUTE_DAILY_REVIEW: Final[ModelRoute] = "daily_review"
MODEL_ROUTE_RAG_EMBEDDING: Final[ModelRoute] = "rag_embedding"
MODEL_ROUTE_RAG_RERANK: Final[ModelRoute] = "rag_rerank"

ALL_MODEL_ROUTES: tuple[ModelRoute, ...] = (
    MODEL_ROUTE_ANNOTATION_GENERATION,
    MODEL_ROUTE_DICT_AI,
    MODEL_ROUTE_READER_ASK,
    MODEL_ROUTE_READER_ASK_REPLAN,
    MODEL_ROUTE_DAILY_ANNOTATION,
    MODEL_ROUTE_DAILY_ANALYSIS,
    MODEL_ROUTE_DAILY_REVIEW,
    MODEL_ROUTE_RAG_EMBEDDING,
    MODEL_ROUTE_RAG_RERANK,
)
