"""Tests for ResolvePlanningDeps factory — build_reader_ask_resolve_planning_deps."""

from __future__ import annotations

from functools import partial

from app.schemas.reader_ask import ReaderAskCurrentRecordAffordances
from app.services.reader_ask import planner_runtime as planner_runtime_svc
from app.services.reader_ask import resolver as resolver_svc
from app.services.reader_ask import supplements as supplements_svc
from app.services.reader_ask.agent_invocation import build_reader_ask_planner_model_route
from app.services.reader_ask.planning_deps_factory import build_reader_ask_resolve_planning_deps


def _affordances_cb(**kwargs: object) -> ReaderAskCurrentRecordAffordances:
    return ReaderAskCurrentRecordAffordances(
        title="Test Record",
        available_context_capabilities=["article_overview"],
        has_article_overview=True,
    )


async def _load_bundle_cb(user_id: object, record_id: object) -> object:
    return object()


_RERANKER = object()


class TestFactoryReturnsResolvePlanningDeps:
    """Factory returns a ResolvePlanningDeps instance."""

    def test_return_type(self) -> None:
        deps = build_reader_ask_resolve_planning_deps(
            current_record_affordances_cb=_affordances_cb,
            load_record_bundle_cb=_load_bundle_cb,
            reference_reranker=_RERANKER,
        )
        assert isinstance(deps, planner_runtime_svc.ResolvePlanningDeps)


class TestRunPlannerDepsWiring:
    """RunPlannerDeps callbacks are wired correctly."""

    def test_current_record_affordances_cb_passthrough(self) -> None:
        deps = build_reader_ask_resolve_planning_deps(
            current_record_affordances_cb=_affordances_cb,
            load_record_bundle_cb=_load_bundle_cb,
            reference_reranker=None,
        )
        assert deps.run_planner_deps.current_record_affordances_cb is _affordances_cb

    def test_build_model_route_cb_delegates_to_planner_model_route(self) -> None:
        deps = build_reader_ask_resolve_planning_deps(
            current_record_affordances_cb=_affordances_cb,
            load_record_bundle_cb=_load_bundle_cb,
            reference_reranker=None,
        )
        callback = deps.run_planner_deps.build_model_route_cb
        assert isinstance(callback, partial)
        assert callback.func is build_reader_ask_planner_model_route
        assert callback.args == (None,)


class TestPassthroughCallbacks:
    """load_record_bundle_cb and reference_reranker are passed through."""

    def test_load_record_bundle_cb_passthrough(self) -> None:
        deps = build_reader_ask_resolve_planning_deps(
            current_record_affordances_cb=_affordances_cb,
            load_record_bundle_cb=_load_bundle_cb,
            reference_reranker=None,
        )
        assert deps.load_record_bundle_cb is _load_bundle_cb

    def test_reference_reranker_passthrough(self) -> None:
        deps = build_reader_ask_resolve_planning_deps(
            current_record_affordances_cb=_affordances_cb,
            load_record_bundle_cb=_load_bundle_cb,
            reference_reranker=_RERANKER,
        )
        assert deps.reference_reranker is _RERANKER

    def test_reference_reranker_none(self) -> None:
        deps = build_reader_ask_resolve_planning_deps(
            current_record_affordances_cb=_affordances_cb,
            load_record_bundle_cb=_load_bundle_cb,
            reference_reranker=None,
        )
        assert deps.reference_reranker is None


class TestInternalCallbackWiring:
    """Resolver, structured asset, and supplements callbacks point to expected functions."""

    def test_resolve_known_references_cb(self) -> None:
        deps = build_reader_ask_resolve_planning_deps(
            current_record_affordances_cb=_affordances_cb,
            load_record_bundle_cb=_load_bundle_cb,
            reference_reranker=None,
        )
        assert deps.resolve_known_references_cb is resolver_svc.resolve_known_references

    def test_resolve_structured_asset_refs_cb(self) -> None:
        deps = build_reader_ask_resolve_planning_deps(
            current_record_affordances_cb=_affordances_cb,
            load_record_bundle_cb=_load_bundle_cb,
            reference_reranker=None,
        )
        assert (
            deps.resolve_structured_asset_refs_cb
            is resolver_svc.resolve_structured_asset_references
        )

    def test_list_supplements_cb(self) -> None:
        deps = build_reader_ask_resolve_planning_deps(
            current_record_affordances_cb=_affordances_cb,
            load_record_bundle_cb=_load_bundle_cb,
            reference_reranker=None,
        )
        assert deps.list_supplements_cb is supplements_svc.list_supplements_for_record
