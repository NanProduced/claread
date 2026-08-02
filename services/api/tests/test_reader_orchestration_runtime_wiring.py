from __future__ import annotations

import ast
import tomllib
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = API_ROOT / "app" / "main.py"
ROUTE_PATH = API_ROOT / "app" / "api" / "routes" / "reader_orchestration.py"
ORCHESTRATOR_PATH = API_ROOT / "app" / "services" / "reader_orchestration" / "orchestrator.py"
PYPROJECT_PATH = API_ROOT / "pyproject.toml"


def _parse_module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _find_async_function(module: ast.Module, name: str) -> ast.AsyncFunctionDef:
    for node in module.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Async function {name!r} not found")


def _find_async_method(
    module: ast.Module,
    *,
    class_name: str,
    method_name: str,
) -> ast.AsyncFunctionDef:
    for node in module.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for child in node.body:
            if isinstance(child, ast.AsyncFunctionDef) and child.name == method_name:
                return child
    raise AssertionError(f"Async method {class_name}.{method_name} not found")


def _call_target(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parent = _call_target(func.value)
        if parent is None:
            return func.attr
        return f"{parent}.{func.attr}"
    return None


def _collect_call_targets(node: ast.AST) -> list[str]:
    targets: list[str] = []

    class _Collector(ast.NodeVisitor):
        def visit_Call(self, call: ast.Call) -> None:
            target = _call_target(call.func)
            if target is not None:
                targets.append(target)
            self.generic_visit(call)

    _Collector().visit(node)
    return targets


def test_pyproject_registers_reader_worker_console_script() -> None:
    pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))

    assert pyproject["project"]["scripts"]["reader-enhancement-worker"] == (
        "scripts.run_reader_enhancement_worker:main"
    )
    assert "scripts" in pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]


def test_main_module_does_not_reference_reader_worker_runtime_components() -> None:
    source = MAIN_PATH.read_text(encoding="utf-8")

    for forbidden in (
        "ReaderEnhancementWorkerLoopService",
        "run_reader_enhancement_worker",
        "ReaderEnhancementPipelineRunner",
        "reader_orchestration.worker_loop",
        "reader_orchestration.pipeline_runner",
    ):
        assert forbidden not in source


def test_unified_input_route_remains_request_serving_only() -> None:
    source = ROUTE_PATH.read_text(encoding="utf-8")
    module = _parse_module(ROUTE_PATH)
    submit_route = _find_async_function(module, "submit_reader_input")
    call_targets = _collect_call_targets(submit_route)

    assert "evaluate_input_suitability" in call_targets

    for forbidden in (
        "ReaderEnhancementPipelineRunner",
        "ReaderEnhancementWorkerLoopService",
        "run_reader_enhancement_worker",
        "process_next_translation_job",
        "tick_translation_worker",
    ):
        assert forbidden not in source

    assert not any(target.endswith("process_next_translation_job") for target in call_targets)
    assert not any(target.endswith("tick_translation_worker") for target in call_targets)


def test_orchestrator_submit_only_persists_article_ready_and_bootstraps_initial_jobs() -> None:
    source = ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    module = _parse_module(ORCHESTRATOR_PATH)
    method = _find_async_method(
        module,
        class_name="ReaderOrchestrator",
        method_name="submit_plain_text_and_bootstrap_translation",
    )

    assert "ReaderEnhancementPipelineRunner" not in source
    assert "ReaderEnhancementWorkerLoopService" not in source
    assert "run_reader_enhancement_worker" not in source

    assert _collect_call_targets(method) == [
        "self._article_ready_service.submit_plain_text",
        "uuid4",
        "self._title_bootstrap_service.bootstrap_display_title_job",
        "self._bootstrap_service.bootstrap_translation_run",
    ]
