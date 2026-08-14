# task-history: (renamed from test_d6_a0_static_boundary.py)
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.chain_reader_parse,
    pytest.mark.seam_pure_unit,
    pytest.mark.life_permanent_regression,
]

# This test lives at services/api/tests/test_static_boundary.py.
# The Python package we want to audit lives at services/api/app/, so we
# walk one parent up to reach services/api/, then descend into `app/`.
# Walking two parents would land on services/ and miss every audit target.
REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"


def _python_files(relative_dir: Path) -> list[Path]:
    absolute = APP_DIR / relative_dir
    if not absolute.is_dir():
        return []
    return sorted(path for path in absolute.rglob("*.py"))


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _has_module_import(source: str, module: str) -> bool:
    needle = f"from {module}"
    if needle in source:
        return True
    needle = f"import {module}"
    return needle in source


def test_user_editorial_assets_is_schema_only_no_runtime_service_import() -> None:
    """Boundary guard.

    `app.schemas.user_editorial_assets` ships only draft anchor DTOs plus
    `UserEditorialAssetScope`. It must not be wired into any runtime service
    (`app/services/*`) except the dedicated Reading Record anchor gate module
    added in /.
    """
    service_files = _python_files(Path("services"))

    allowlist = {
        "app/services/reader_orchestration/anchor_gate.py",
    }
    offenders: list[str] = []
    target_module = "app.schemas.user_editorial_assets"

    for path in service_files:
        source = _read_text(path)
        rel = path.relative_to(REPO_ROOT).as_posix()
        if _has_module_import(source, target_module) and rel not in allowlist:
            offenders.append(rel)

    assert offenders == [], (
        "user_editorial_assets may only be imported by the dedicated "
        "reader_orchestration anchor gate; offenders: " + ", ".join(offenders)
    )


def test_user_editorial_assets_is_schema_only_no_agent_import() -> None:
    """Boundary guard.

    Same rule as the service-side guard: agents under `app/agents/*` must not
    import the schema-only `user_editorial_assets` until (Ask tool
    signature switch) lands. Agent runtime stays on legacy `target_sentence_id`
    / `target_key` until that work ships.
    """
    agent_files = _python_files(Path("agents"))
    offenders: list[str] = []
    target_module = "app.schemas.user_editorial_assets"

    for path in agent_files:
        source = _read_text(path)
        if _has_module_import(source, target_module):
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == [], (
        "user_editorial_assets must remain schema-only; "
        "the following agent files must not import it yet: " + ", ".join(offenders)
    )


def test_user_editorial_asset_anchor_set_is_schema_only_no_runtime_import() -> None:
    """Multi-anchor decision guard.

    `UserEditorialAssetAnchorSet` / `UserEditorialAssetAnchorRange` are
    schema-only drafts for future multi_text writes. V1c production remains
    single-range first, so runtime service/agent/route code must not import or
    reference these symbols until a follow-up explicitly implements
    persistence.
    """
    runtime_files = [
        *_python_files(Path("services")),
        *_python_files(Path("agents")),
        *_python_files(Path("api/routes")),
    ]
    forbidden_symbols = (
        "UserEditorialAssetAnchorSet",
        "UserEditorialAssetAnchorRange",
    )
    offenders: list[str] = []

    for path in runtime_files:
        source = _read_text(path)
        rel = path.relative_to(REPO_ROOT).as_posix()
        for symbol in forbidden_symbols:
            if symbol in source:
                offenders.append(f"{rel} -> {symbol}")

    assert offenders == [], (
        "multi-anchor DTOs are schema-only (schema layer only); runtime offenders: " + ", ".join(offenders)
    )


def test_reader_orchestration_does_not_import_reader_ask_as_fact_source() -> None:
    """Boundary guard.

    The new `reader_orchestration` package must not reach into the legacy
    `reader_ask` package to look up Reading Record facts. Reader Record fact
    lookup goes through the snapshot path; `reader_ask` keeps its own legacy
    `render_scene_json` `target_key` fact source until (Ask supplement
    write cutover) lands.
    """
    orchestration_files = _python_files(Path("services/reader_orchestration"))
    offenders: list[str] = []

    forbidden_roots = (
        "app.services.reader_ask",
        "app.agents.reader_ask",
    )

    for path in orchestration_files:
        source = _read_text(path)
        for forbidden in forbidden_roots:
            if _has_module_import(source, forbidden):
                offenders.append(f"{path.relative_to(REPO_ROOT)} -> {forbidden}")

    assert offenders == [], (
        "reader_orchestration must not import reader_ask as a fact source; "
        "the following cross-package imports are forbidden until the ask/orchestration boundary is explicitly reopened: " + ", ".join(offenders)
    )


def test_reader_record_api_does_not_read_render_scene_json() -> None:
    """Boundary guard.

    Files that participate in the new `Reader Record` API surface
    (under `app/services/reader_orchestration` and the `reader_plate_*`
    modules) must not read `render_scene_json` / `load_render_scene` /
    `ReaderSceneResponse` as the fact source for Reading Record snapshots.
    Legacy `render_scene_json` remains acceptable inside `reader_ask/`,
    `reader_scene.py`, `reader_notes.py`, `user_annotations.py` because those
    services are explicitly kept on the legacy `/app/reader/{recordId}` path
    until...
    """
    forbidden_strings = (
        "render_scene_json",
        "load_render_scene",
        "ReaderSceneResponse",
        "ReaderSceneResponseDto",
    )

    allowed_paths = {
        "app/services/reader_ask",
        "app/services/reader_scene.py",
        "app/services/reader_notes.py",
        "app/services/user_annotations.py",
    }

    orchestration_files = _python_files(Path("services/reader_orchestration"))
    offenders: list[str] = []

    for path in orchestration_files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if any(rel.startswith(allowed) for allowed in allowed_paths):
            continue
        source = _read_text(path)
        for forbidden in forbidden_strings:
            if forbidden in source:
                offenders.append(f"{rel} -> {forbidden}")

    assert offenders == [], (
        "new Reader Record path must not read legacy render_scene_json "
        "or ReaderSceneResponse as a fact source; offenders: " + ", ".join(offenders)
    )


def test_retained_rr_asset_writers_only_import_allowlisted_modules() -> None:
    """Narrow boundary for retained Reading Record asset writers.

    ``user_annotations.py`` and ``reader_notes.py`` are retained RR asset
    writers. They may use only the four explicit orchestration seams needed
    for anchor validation, event persistence, repository access, and the
    representation payload contract. This is intentionally not a wildcard
    allowlist for ``reader_orchestration`` imports.
    """
    allowlist = {
        "app.services.reader_orchestration.anchor_gate",
        "app.services.reader_orchestration.event_runtime",
        "app.services.reader_orchestration.representation_event_payload",
        "app.services.reader_orchestration.repository",
    }

    legacy_paths = [
        Path("services/user_annotations.py"),
        Path("services/reader_notes.py"),
    ]

    offenders: list[str] = []
    for relative in legacy_paths:
        absolute = APP_DIR / relative
        if not absolute.is_file():
            continue
        source = _read_text(absolute)
        for raw_line in source.splitlines():
            stripped = raw_line.strip()
            if not (
                stripped.startswith("from app.services.reader_orchestration")
                or stripped.startswith("import app.services.reader_orchestration")
            ):
                continue
            # Normalise to a module path; strip the leading `from ` and any
            # trailing `import (...)` newline artefacts.
            if stripped.startswith("from "):
                module = stripped[len("from ") :].split(" import ")[0].strip()
            else:
                module = stripped[len("import ") :].split(" import ")[0].strip().rstrip(",")
            if module not in allowlist:
                offenders.append(f"{relative.as_posix()} -> {module}")

    assert offenders == [], (
        "retained RR asset writers may only import the explicit allowlist "
        + ", ".join(sorted(allowlist))
        + "; offenders: "
        + ", ".join(offenders)
    )


def test_reader_record_ask_modules_do_not_import_legacy_runtime_or_scene() -> None:
    """Minimal-slice boundary guard.

    The new Reading Record Ask modules may validate Reading Record snapshot
    facts, but they must not import the legacy `reader_ask` runtime or the old
    render-scene service as hidden fact sources.
    """
    package_dir = APP_DIR / "services/reader_record_ask"
    target_files = sorted(package_dir.glob("*.py"))
    target_files.append(APP_DIR / "api/routes/reader_record_ask.py")
    forbidden_modules = (
        "app.services.reader_ask",
        "app.services.reader_scene",
    )
    forbidden_strings = (
        "render_scene_json",
        "load_render_scene",
    )

    offenders: list[str] = []
    for path in target_files:
        if not path.is_file():
            continue
        source = _read_text(path)
        rel = path.relative_to(REPO_ROOT).as_posix()
        for module in forbidden_modules:
            if _has_module_import(source, module):
                offenders.append(f"{rel} -> {module}")
        for forbidden in forbidden_strings:
            if forbidden in source:
                offenders.append(f"{rel} -> {forbidden}")

    assert offenders == [], (
        "reader_record_ask must stay off legacy reader_ask/runtime scene "
        "fact sources; offenders: " + ", ".join(offenders)
    )


def test_reader_record_ask_independent_runtime_avoids_legacy_agent_seams() -> None:
    """Round-2 guard for the independent agent loop modules only.

    ``service.py`` remains a temporary facade over ask_runtime and is
    intentionally excluded — it must not make this guard fail.  When a
    future cutover rewires the production path, service.py can join this
    allowlist of independent modules.
    """
    package_dir = APP_DIR / "services/reader_record_ask"
    # Independent runtime modules (exclude production facade service.py).
    independent_names = {
        "agent.py",
        "runtime.py",
        "runtime_deps.py",
        "runtime_events.py",
        "read_range_executor.py",
        "search_current_article_executor.py",
        "article_rag_port.py",
        "article_rag_adapter.py",
        "finalizer.py",
        "document_access.py",
        "evidence_registry.py",
        "initial_anchor_evidence.py",
        "fence.py",
        "context_envelope.py",
        "tool_contracts.py",
        "evidence.py",
        "baseline_context.py",
        "production_stream.py",
        "production_wiring.py",
        "repository.py",
        "sse.py",
        "envelope_builder.py",
    }
    forbidden_modules = (
        "app.agents.reader_ask_agent",
        "app.services.reader_ask",
        "app.services.ask_runtime",
        "app.services.reader_scene",
        # Old Article RAG auto-injection bridge (must not be reused).
        "app.services.reader_orchestration.article_rag_ask_prompt_bridge",
        "app.services.reader_orchestration.article_rag_ask_prompt_attachment",
        "app.services.reader_orchestration.article_rag_ask_integration_adapter",
        "app.services.reader_orchestration.article_rag_ask_runtime_adapter",
        "app.services.reader_orchestration.article_rag_ask_prompt_assembly",
        "app.services.reader_orchestration.article_rag_ask_prompt_section",
        "app.services.reader_orchestration.article_rag_ask_context_provider",
        "app.services.reader_orchestration.article_rag_ask_context_composer",
        "app.services.reader_orchestration.article_rag_ask_context_resolver",
    )
    forbidden_substrings = (
        "reader_ask_planner",
        "planner_runtime",
        "cross_record",
        "resolve_known_reference",
        "deictic_",
        "hint_contract",
        "ReaderAskRuntimeState",
        "build_reader_ask_agent",
        "ArticleRagPromptIntegration",
        "article_rag_ask_prompt_bridge",
    )

    offenders: list[str] = []
    for path in sorted(package_dir.glob("*.py")):
        if path.name not in independent_names:
            continue
        source = _read_text(path)
        rel = path.relative_to(REPO_ROOT).as_posix()
        for module in forbidden_modules:
            if _has_module_import(source, module):
                offenders.append(f"{rel} -> {module}")
        for needle in forbidden_substrings:
            if needle in source:
                offenders.append(f"{rel} -> {needle}")

    assert offenders == [], (
        "independent Reading Record Ask runtime must not depend on legacy "
        "agent/planner/hint/ask_runtime seams; offenders: " + ", ".join(offenders)
    )


# ---------------------------------------------------------------------------
# ARCH-OPT- Phase L — Article RAG Ask exit guard
#
# The 9 ``article_rag_ask_*`` modules under ``reader_orchestration`` are
# the retired Ask prompt-integration chain.  Production Ask flows through
# ``reader_record_ask`` (production_stream -> article_rag_adapter ->
# ArticleRagSearchPort).  Phase P physically deleted the legacy
# files; the guards below keep the cluster dead — production code,
# the canonical acceptance test, and the operational runbook must
# never import or recommend them again.
# ---------------------------------------------------------------------------

ARTICLE_RAG_ASK_EXIT_MODULES: tuple[str, ...] = (
    "app.services.reader_orchestration.article_rag_ask_context_composer",
    "app.services.reader_orchestration.article_rag_ask_context_provider",
    "app.services.reader_orchestration.article_rag_ask_context_resolver",
    "app.services.reader_orchestration.article_rag_ask_integration_adapter",
    "app.services.reader_orchestration.article_rag_ask_prompt_assembly",
    "app.services.reader_orchestration.article_rag_ask_prompt_attachment",
    "app.services.reader_orchestration.article_rag_ask_prompt_bridge",
    "app.services.reader_orchestration.article_rag_ask_prompt_section",
    "app.services.reader_orchestration.article_rag_ask_runtime_adapter",
)

_ARTICLE_RAG_ASK_EXIT_LEGACY_FILES = frozenset(
    (APP_DIR / "services" / "reader_orchestration" / f"{name.split('.')[-1]}.py")
    for name in ARTICLE_RAG_ASK_EXIT_MODULES
)


def test_production_app_does_not_import_legacy_article_rag_ask_chain() -> None:
    """ARCH-OPT- Phase L/P: legacy cluster stays dead, physically.

    First asserts all 9 retired ``article_rag_ask_*`` files are
    physically absent (Phase P deletion) — restoring any of them
    fails here immediately.  Then scans every ``app/`` module for
    imports of the 9 retired modules with NO exemption for the
    legacy paths, so a revived cluster is caught twice: any
    ``app/`` module (services, agents, routes, schemas) importing
    them is flagged, including a revived legacy file itself.
    """
    revived = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in _ARTICLE_RAG_ASK_EXIT_LEGACY_FILES
        if path.exists()
    )
    assert revived == [], (
        "retired article_rag_ask_* files must stay physically deleted; "
        "revived: " + ", ".join(revived)
    )

    offenders: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source = _read_text(path)
        rel = path.relative_to(REPO_ROOT).as_posix()
        for module in ARTICLE_RAG_ASK_EXIT_MODULES:
            if _has_module_import(source, module):
                offenders.append(f"{rel} -> {module}")

    assert offenders == [], (
        "production code must not import the retired article_rag_ask_* "
        "chain; offenders: " + ", ".join(offenders)
    )


def test_canonical_acceptance_and_runbook_avoid_legacy_article_rag_ask_chain() -> None:
    """ARCH-OPT- Phase L: canonical docs/tests must not recommend the
    retired Ask chain.

    The canonical Article RAG acceptance test and the operational
    runbook are the two authoritative surfaces developers copy from;
    neither may mention the 9 retired ``article_rag_ask_*`` module
    names or their ``ArticleRagAsk*`` classes.  The production chain
    (production_stream -> article_rag_adapter -> ArticleRagSearchPort)
    is the only documented path.
    """
    repo_root = REPO_ROOT.parents[1]
    targets = (
        REPO_ROOT / "tests" / "test_article_rag_single_path_real_acceptance.py",
        repo_root
        / "docs"
        / "initiatives"
        / "reader-agentic-orchestration"
        / "modules"
        / "local-article-rag-runbook.md",
    )
    forbidden_tokens = tuple(
        name.split(".")[-1] for name in ARTICLE_RAG_ASK_EXIT_MODULES
    ) + ("ArticleRagAsk",)

    offenders: list[str] = []
    for path in targets:
        assert path.is_file(), f"guard target missing: {path}"
        source = _read_text(path)
        rel = path.relative_to(repo_root).as_posix()
        for token in forbidden_tokens:
            if token in source:
                offenders.append(f"{rel} -> {token}")

    assert offenders == [], (
        "canonical acceptance test / runbook must not import or "
        "recommend the retired article_rag_ask_* chain; offenders: "
        + ", ".join(offenders)
    )


# ---------------------------------------------------------------------------
# parse_eval single-chain contract guard
#
# The parse_eval artifact contract is single-chain: the retired
# baseline-chain comparison surface (the frozen-baseline sidecar
# module, the freeze schema types, and the unavailable-freeze builder)
# was physically deleted.  The guards below keep the baseline chain
# dead — the sidecar module file must stay physically absent, and the
# retired identifiers must never reappear in production ``app/`` code
# or in the ``verification/`` baseline harness.  ``tests/`` is outside
# the scanned surface by scope, not by exemption: the focused negative
# assertion in ``test_reader_parse_eval.py`` keeps the frozen
# artifacts free of the retired key.
# ---------------------------------------------------------------------------

VERIFICATION_DIR = REPO_ROOT / "verification"

_PARSE_EVAL_RETIRED_BASELINE_SIDECAR_FILE = (
    VERIFICATION_DIR / "reader_baseline" / "parse_eval" / "legacy_sidecar.py"
)

_PARSE_EVAL_RETIRED_BASELINE_CHAIN_MARKERS = (
    "legacy_baseline",
    "legacy_sidecar",
    "LegacyBaselineStatusLiteral",
    "LegacyBaselineFreeze",
    "build_legacy_baseline",
)


def test_parse_eval_single_chain_contract_has_no_retired_baseline_chain_symbols() -> None:
    """parse eval single-chain contract: retired baseline-chain symbols stay gone.

    First asserts the retired baseline sidecar module is physically
    absent — restoring it fails here immediately.  Then scans every
    ``verification/`` and ``app/`` Python module for the retired
    baseline-chain identifiers with no path or identifier exemption,
    so a revived field, literal, builder, or import is caught wherever
    it reappears.
    """
    assert not _PARSE_EVAL_RETIRED_BASELINE_SIDECAR_FILE.exists(), (
        "retired parse eval baseline sidecar module must stay physically "
        "deleted; revived: "
        + _PARSE_EVAL_RETIRED_BASELINE_SIDECAR_FILE.relative_to(REPO_ROOT).as_posix()
    )

    offenders: list[str] = []
    for root in (VERIFICATION_DIR, APP_DIR):
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            source = _read_text(path)
            rel = path.relative_to(REPO_ROOT).as_posix()
            for marker in _PARSE_EVAL_RETIRED_BASELINE_CHAIN_MARKERS:
                if marker in source:
                    offenders.append(f"{rel} -> {marker}")

    assert offenders == [], (
        "parse eval single-chain contract has no retired baseline-chain "
        "symbols; offenders: " + ", ".join(offenders)
    )
