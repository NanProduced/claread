"""DATA-SCHEMA-BASELINE single-baseline compose mount pin.

The local fresh-init path applies exactly one file:
``infra/migrations/0001_initial.sql`` mounted into
``/docker-entrypoint-initdb.d/``. This test fails closed if:

- ``infra/migrations/`` contains anything other than ``0001_initial.sql``
  (legacy per-step migrations and the eval-center / llm-config subdirs must
  stay deleted), or
- ``docker-compose.local.yml`` mounts any other initdb file (which could
  resurrect legacy Eval control-plane tables).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = REPO_ROOT / "infra" / "docker" / "docker-compose.local.yml"
MIGRATIONS_DIR = REPO_ROOT / "infra" / "migrations"
BASELINE_NAME = "0001_initial.sql"


def test_migrations_dir_contains_only_the_single_baseline() -> None:
    assert MIGRATIONS_DIR.is_dir(), "infra/migrations must exist"
    entries = sorted(path.name for path in MIGRATIONS_DIR.iterdir())
    assert entries == [BASELINE_NAME], (
        "infra/migrations must contain ONLY 0001_initial.sql after "
        f"DATA-SCHEMA-BASELINE; found: {entries}"
    )


def test_compose_mounts_only_the_single_baseline() -> None:
    compose_text = COMPOSE_PATH.read_text(encoding="utf-8")
    initdb_lines = [
        line.strip()
        for line in compose_text.splitlines()
        if "docker-entrypoint-initdb.d" in line
    ]
    assert len(initdb_lines) == 1, (
        "docker-compose.local.yml must mount exactly one initdb file; found: "
        f"{initdb_lines}"
    )
    assert f"/docker-entrypoint-initdb.d/{BASELINE_NAME}" in initdb_lines[0], (
        "docker-compose.local.yml must mount ../migrations/0001_initial.sql "
        f"into initdb; found: {initdb_lines[0]}"
    )
