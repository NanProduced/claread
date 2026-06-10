"""Factory for ResolvePlanningDeps — centralises the repeated planner deps assembly.

This module extracts the 4 identical ResolvePlanningDeps / RunPlannerDeps
constructions that appear in both the primary stream and retry paths of
service.py.  It does NOT own planner semantic decisions, resolver behaviour,
context runtime, reranker defaults, or persistence.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from app.llm.types import ModelSelection
from app.schemas.reader_ask import ReaderAskCurrentRecordAffordances
from app.services.reader_ask import planner_runtime as planner_runtime_svc
from app.services.reader_ask import resolver as resolver_svc
from app.services.reader_ask import supplements as supplements_svc
from app.services.reader_ask.agent_invocation import make_reader_ask_planner_model_route_cb


def build_reader_ask_resolve_planning_deps(
    *,
    current_record_affordances_cb: Callable[..., ReaderAskCurrentRecordAffordances],
    load_record_bundle_cb: Callable[[UUID, UUID], Awaitable[Any]],
    reference_reranker: Any | None,
    model_selection: ModelSelection | None = None,
) -> planner_runtime_svc.ResolvePlanningDeps:
    """Construct ResolvePlanningDeps with the standard callback wiring.

    Parameters that vary per call site (affordances, bundle loader, reranker)
    are explicit.  Callbacks that are always the same (resolver, supplements,
    model route) are wired internally.
    """
    return planner_runtime_svc.ResolvePlanningDeps(
        run_planner_deps=planner_runtime_svc.RunPlannerDeps(
            current_record_affordances_cb=current_record_affordances_cb,
            build_model_route_cb=make_reader_ask_planner_model_route_cb(model_selection),
        ),
        resolve_known_references_cb=resolver_svc.resolve_known_references,
        load_record_bundle_cb=load_record_bundle_cb,
        resolve_structured_asset_refs_cb=resolver_svc.resolve_structured_asset_references,
        list_supplements_cb=supplements_svc.list_supplements_for_record,
        reference_reranker=reference_reranker,
    )
