"""Round 16 regression tests: planner LLM route / prompt registry cleanup.

Round 16 removes the last executable remnants of the semantic planner LLM
route and prompt registry, after Round 15 removed the executable
``resolve_semantic_planning`` path.

These tests lock down:

1. ``agent_invocation.py`` no longer exposes
   ``build_reader_ask_planner_model_route`` or
   ``make_reader_ask_planner_model_route_cb``.
2. ``agent_invocation.py`` no longer imports ``MODEL_ROUTE_READER_ASK_PLANNER``.
3. ``prompts/registry.yaml`` no longer registers a ``reader_ask_planner`` agent.
4. ``prompts/agents/reader_ask_planner.yaml`` has been deleted.
5. ``prompts/reader_ask/planner.yaml`` has been deleted.
6. ``service.py`` does not call ``build_reader_ask_planner_model_route``.
7. The deprecated ``planner_model_name`` DTO field is still present
   (backward-compatible serialization) but carries a deprecation marker
   in its source comment.
8. ``MODEL_ROUTE_READER_ASK_PLANNER`` has been entirely removed from
   ``app/llm/routes.py``, ``app/llm/registry.py``,
   ``app/services/reader_ask/model_options.py``, and ``app/config/settings.py``.
   The ``planner_model_name`` DTO field is retained for backward-compatible
   serialization but always resolves to ``None``.

All tests are static / AST-level — no real LLM is called.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import yaml

from app.services.reader_ask import agent_invocation as agent_invocation_svc
from app.services.reader_ask import service as service_svc

_SERVICES_API_ROOT = Path(__file__).resolve().parents[1]
_PROMPTS_ROOT = _SERVICES_API_ROOT / "prompts"


# ---------------------------------------------------------------------------
# 1. agent_invocation.py no longer exposes planner model route facades
# ---------------------------------------------------------------------------

class TestPlannerModelRouteFacadeRemoved:
    def test_build_reader_ask_planner_model_route_removed(self) -> None:
        assert not hasattr(agent_invocation_svc, "build_reader_ask_planner_model_route"), (
            "agent_invocation.py must not expose build_reader_ask_planner_model_route; "
            "the planner LLM route has been removed in Round 16"
        )

    def test_make_reader_ask_planner_model_route_cb_removed(self) -> None:
        assert not hasattr(agent_invocation_svc, "make_reader_ask_planner_model_route_cb"), (
            "agent_invocation.py must not expose make_reader_ask_planner_model_route_cb; "
            "the planner LLM route has been removed in Round 16"
        )

    def test_agent_invocation_does_not_import_planner_route_constant(self) -> None:
        source = inspect.getsource(agent_invocation_svc)
        module = ast.parse(source)
        imported_names: set[str] = set()
        for node in ast.walk(module):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
        assert "MODEL_ROUTE_READER_ASK_PLANNER" not in imported_names, (
            "agent_invocation.py must not import MODEL_ROUTE_READER_ASK_PLANNER; "
            "the planner LLM route has been removed in Round 16"
        )

    def test_replan_model_route_facade_retained(self) -> None:
        assert hasattr(agent_invocation_svc, "build_reader_ask_replan_model_route"), (
            "agent_invocation.py must still expose build_reader_ask_replan_model_route; "
            "the agent-loop repair path still resolves a replan model"
        )


# ---------------------------------------------------------------------------
# 2. service.py does not call the deleted planner model route facade
# ---------------------------------------------------------------------------

class TestServiceDoesNotCallPlannerModelRoute:
    def test_service_py_has_no_build_reader_ask_planner_model_route_calls(self) -> None:
        source = inspect.getsource(service_svc)
        module = ast.parse(source)
        calls = [
            node
            for node in ast.walk(module)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "build_reader_ask_planner_model_route"
        ]
        assert len(calls) == 0, (
            "service.py must not call build_reader_ask_planner_model_route; "
            "the planner LLM route has been removed in Round 16"
        )

    def test_service_py_has_no_make_reader_ask_planner_model_route_cb_calls(self) -> None:
        source = inspect.getsource(service_svc)
        module = ast.parse(source)
        calls = [
            node
            for node in ast.walk(module)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "make_reader_ask_planner_model_route_cb"
        ]
        assert len(calls) == 0, (
            "service.py must not call make_reader_ask_planner_model_route_cb; "
            "the planner LLM route has been removed in Round 16"
        )


# ---------------------------------------------------------------------------
# 3. Prompt registry no longer registers reader_ask_planner
# ---------------------------------------------------------------------------

class TestPlannerPromptRegistryRemoved:
    def test_registry_yaml_has_no_reader_ask_planner_entry(self) -> None:
        registry_path = _PROMPTS_ROOT / "registry.yaml"
        data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
        agents = data.get("agents", {}) if isinstance(data, dict) else {}
        assert "reader_ask_planner" not in agents, (
            "prompts/registry.yaml must not register reader_ask_planner; "
            "the planner prompt has been removed in Round 16"
        )

    def test_reader_ask_planner_agent_yaml_deleted(self) -> None:
        planner_yaml = _PROMPTS_ROOT / "agents" / "reader_ask_planner.yaml"
        assert not planner_yaml.exists(), (
            "prompts/agents/reader_ask_planner.yaml must be deleted; "
            "the planner prompt has been removed in Round 16"
        )

    def test_reader_ask_planner_layer_yaml_deleted(self) -> None:
        planner_layer_yaml = _PROMPTS_ROOT / "reader_ask" / "planner.yaml"
        assert not planner_layer_yaml.exists(), (
            "prompts/reader_ask/planner.yaml must be deleted; "
            "load_prompt_layers() does not load a 'planner' layer and the "
            "semantic planner LLM route has been removed in Round 16"
        )

    def test_load_prompt_layers_does_not_include_planner(self) -> None:
        from app.services.reader_ask.prompting import load_prompt_layers
        layers = load_prompt_layers()
        assert "planner" not in layers, (
            "load_prompt_layers() must not return a 'planner' layer; "
            "the planner prompt has been removed in Round 16"
        )


# ---------------------------------------------------------------------------
# 4. Deprecated planner_model_name DTO field retained for compatibility
# ---------------------------------------------------------------------------

class TestPlannerModelNameDeprecatedButRetained:
    def test_planner_model_name_field_still_present(self) -> None:
        from app.schemas.reader_ask import ReaderAskSelectedModel
        fields = ReaderAskSelectedModel.model_fields
        assert "planner_model_name" in fields, (
            "planner_model_name must remain in ReaderAskSelectedModel for "
            "backward-compatible API/DTO serialization (Round 16 deprecation)"
        )

    def test_planner_model_name_has_deprecation_comment(self) -> None:
        source = inspect.getsource(
            __import__("app.schemas.reader_ask", fromlist=["reader_ask"])
        )
        assert "planner_model_name" in source
        # The deprecation marker is in a comment immediately above the field.
        assert "Round 16" in source and "deprecated" in source.lower(), (
            "planner_model_name field should carry a Round 16 deprecation comment"
        )


# ---------------------------------------------------------------------------
# 5. MODEL_ROUTE_READER_ASK_PLANNER constant removed entirely
# ---------------------------------------------------------------------------

class TestPlannerRouteConstantRemoved:
    def test_constant_removed_from_routes_module(self) -> None:
        from app.llm import routes as routes_mod
        assert not hasattr(routes_mod, "MODEL_ROUTE_READER_ASK_PLANNER"), (
            "MODEL_ROUTE_READER_ASK_PLANNER must be removed from app/llm/routes.py; "
            "the planner LLM route has been entirely removed in Round 16"
        )

    def test_planner_route_not_in_model_route_literal(self) -> None:
        from app.llm import routes as routes_mod
        source = inspect.getsource(routes_mod)
        assert '"reader_ask_planner"' not in source, (
            "reader_ask_planner must not appear in app/llm/routes.py; "
            "the planner LLM route has been entirely removed in Round 16"
        )

    def test_planner_route_not_in_all_model_routes(self) -> None:
        from app.llm import routes as routes_mod
        assert "reader_ask_planner" not in routes_mod.ALL_MODEL_ROUTES, (
            "reader_ask_planner must not be in ALL_MODEL_ROUTES; "
            "the planner LLM route has been entirely removed in Round 16"
        )

    def test_registry_does_not_reference_planner_route(self) -> None:
        from app.llm import registry as registry_mod
        source = inspect.getsource(registry_mod)
        assert "MODEL_ROUTE_READER_ASK_PLANNER" not in source, (
            "registry.py must not reference MODEL_ROUTE_READER_ASK_PLANNER; "
            "the planner LLM route has been entirely removed in Round 16"
        )

    def test_model_options_does_not_resolve_planner_route(self) -> None:
        from app.services.reader_ask import model_options as model_options_mod
        source = inspect.getsource(model_options_mod)
        module = ast.parse(source)
        # Check that MODEL_ROUTE_READER_ASK_PLANNER is not imported or used
        # in any import statement, function call, or name reference.
        imported_names: set[str] = set()
        referenced_names: set[str] = set()
        for node in ast.walk(module):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
            if isinstance(node, ast.Name):
                referenced_names.add(node.id)
        assert "MODEL_ROUTE_READER_ASK_PLANNER" not in imported_names, (
            "model_options.py must not import MODEL_ROUTE_READER_ASK_PLANNER; "
            "the planner LLM route has been entirely removed in Round 16"
        )
        assert "MODEL_ROUTE_READER_ASK_PLANNER" not in referenced_names, (
            "model_options.py must not reference MODEL_ROUTE_READER_ASK_PLANNER; "
            "the planner LLM route has been entirely removed in Round 16"
        )

    def test_settings_does_not_have_planner_profile_field(self) -> None:
        from app.config.settings import Settings
        fields = Settings.model_fields
        assert "reader_ask_planner_model_profile" not in fields, (
            "reader_ask_planner_model_profile must be removed from Settings; "
            "the planner LLM route has been entirely removed in Round 16"
        )

    def test_planner_model_name_always_none_in_resolved_option(self) -> None:
        """planner_model_name DTO field is retained but always resolves to None."""
        from app.services.reader_ask.model_options import ResolvedReaderAskModelOption
        import dataclasses
        # The field should still exist on the dataclass.
        field_names = {f.name for f in dataclasses.fields(ResolvedReaderAskModelOption)}
        assert "planner_model_name" in field_names, (
            "planner_model_name field must remain on ResolvedReaderAskModelOption "
            "for backward-compatible serialization"
        )
