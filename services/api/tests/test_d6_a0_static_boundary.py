from __future__ import annotations

from pathlib import Path

# This test lives at services/api/tests/test_d6_a0_static_boundary.py.
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
    """D6-A0 boundary guard.

    `app.schemas.user_editorial_assets` ships only draft anchor DTOs plus
    `UserEditorialAssetScope`. It must not be wired into any runtime service
    (`app/services/*`) except the dedicated Reading Record anchor gate module
    added in D6-U1/D6-A1.
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
        "reader_orchestration anchor gate; offenders: "
        + ", ".join(offenders)
    )


def test_user_editorial_assets_is_schema_only_no_agent_import() -> None:
    """D6-A0 boundary guard.

    Same rule as the service-side guard: agents under `app/agents/*` must not
    import the schema-only `user_editorial_assets` until D6-A3 (Ask tool
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
        "the following agent files must not import it yet: "
        + ", ".join(offenders)
    )


def test_user_editorial_asset_anchor_set_is_schema_only_no_runtime_import() -> None:
    """D6-U2 multi-anchor decision guard.

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
        "multi-anchor DTOs are schema-only in D6-U2; runtime offenders: "
        + ", ".join(offenders)
    )


def test_reader_orchestration_does_not_import_reader_ask_as_fact_source() -> None:
    """D6-A0 boundary guard.

    The new `reader_orchestration` package must not reach into the legacy
    `reader_ask` package to look up Reading Record facts. Reader Record fact
    lookup goes through the snapshot path; `reader_ask` keeps its own legacy
    `render_scene_json` / `target_key` fact source until D6-A4 (Ask supplement
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
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)} -> {forbidden}"
                )

    assert offenders == [], (
        "reader_orchestration must not import reader_ask as a fact source; "
        "the following cross-package imports are forbidden until D6-A4: "
        + ", ".join(offenders)
    )


def test_reader_record_api_does_not_read_render_scene_json() -> None:
    """D6-A0 boundary guard.

    Files that participate in the new `Reader Record` API surface
    (under `app/services/reader_orchestration` and the `reader_plate_*`
    modules) must not read `render_scene_json` / `load_render_scene` /
    `ReaderSceneResponse` as the fact source for Reading Record snapshots.
    Legacy `render_scene_json` remains acceptable inside `reader_ask/`,
    `reader_scene.py`, `reader_notes.py`, `user_annotations.py` because those
    services are explicitly kept on the legacy `/app/reader/{recordId}` path
    until D6-A4..A6.
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
        "or ReaderSceneResponse as a fact source; offenders: "
        + ", ".join(offenders)
    )


def test_legacy_services_only_import_allowlisted_reader_orchestration_modules() -> None:
    """D6-A5 narrow allowlist guard.

    The legacy `user_annotations.py` and `reader_notes.py` services must
    NOT reach into the new `reader_orchestration` package broadly. D6-A5
    intentionally introduces two narrow imports:

    - `app.services.reader_orchestration.anchor_gate` — for the dual-
      contract validation branch on the new `anchor` field.
    - `app.services.reader_orchestration.repository` — for the lazy
      `ReaderOrchestrationRepository` default constructor used by the
      same branch.

    Any other import from `app.services.reader_orchestration.*` would
    silently broaden the cross-package coupling and is forbidden until a
    follow-up explicitly widens this allowlist.
    """
    allowlist = {
        "app.services.reader_orchestration.anchor_gate",
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
            if not (stripped.startswith("from app.services.reader_orchestration")
                    or stripped.startswith("import app.services.reader_orchestration")):
                continue
            # Normalise to a module path; strip the leading `from ` and any
            # trailing `import (...)` newline artefacts.
            if stripped.startswith("from "):
                module = stripped[len("from "):].split(" import ")[0].strip()
            else:
                module = stripped[len("import "):].split(" import ")[0].strip().rstrip(",")
            if module not in allowlist:
                offenders.append(f"{relative.as_posix()} -> {module}")

    assert offenders == [], (
        "user_annotations.py / reader_notes.py may only import the "
        "narrow allowlist "
        + ", ".join(sorted(allowlist))
        + "; offenders: "
        + ", ".join(offenders)
    )


def test_reader_record_ask_modules_do_not_import_legacy_runtime_or_scene() -> None:
    """D6-A6 minimal-slice boundary guard.

    The new Reading Record Ask modules may validate Reading Record snapshot
    facts, but they must not import the legacy `reader_ask` runtime or the old
    render-scene service as hidden fact sources.
    """
    target_files = [
        APP_DIR / "services/reader_record_ask/service.py",
        APP_DIR / "services/reader_record_ask/context_envelope.py",
        APP_DIR / "services/reader_record_ask/tool_contracts.py",
        APP_DIR / "services/reader_record_ask/evidence.py",
        APP_DIR / "services/reader_record_ask/__init__.py",
        APP_DIR / "api/routes/reader_record_ask.py",
    ]
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
        "fact sources; offenders: "
        + ", ".join(offenders)
    )
