from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = REPO_ROOT / "infra" / "docker" / "docker-compose.local.yml"
MIGRATIONS_DIR = REPO_ROOT / "infra" / "migrations"


def _top_level_migrations() -> list[Path]:
    """Return top-level migration SQL files (excluding subdirs like eval-center)."""
    return sorted(
        path
        for path in MIGRATIONS_DIR.iterdir()
        if path.is_file() and path.suffix == ".sql"
    )


def test_local_compose_mounts_every_top_level_migration() -> None:
    """Pin that docker-compose.local.yml initdb mounts cover all top-level migrations.

    A fresh local DB volume is initialized exclusively from the files mounted into
    ``/docker-entrypoint-initdb.d/``. If a migration is added under
    ``infra/migrations/`` but not mounted here, a rebuilt volume will silently miss
    the schema change (this exact gap caused 0007_source_artifacts and
    0008_reader_jobs_input_artifact_extraction to be absent from local DBs).

    This static test fails closed the moment a new top-level migration lands without
    a matching compose mount line, so the drift cannot recur silently.
    """
    migrations = _top_level_migrations()
    assert migrations, "expected at least one top-level migration file"
    compose_text = COMPOSE_PATH.read_text(encoding="utf-8")

    missing: list[str] = []
    for migration in migrations:
        # Mount lines look like:
        #   - ../migrations/0007_reader_source_artifacts.sql:/docker-entrypoint-initdb.d/0007_reader_source_artifacts.sql:ro
        # Matching on the destination filename is sufficient and stable.
        destination = f"/docker-entrypoint-initdb.d/{migration.name}"
        if destination not in compose_text:
            missing.append(migration.name)

    assert not missing, (
        "docker-compose.local.yml is missing initdb mounts for top-level migrations: "
        f"{missing}. Add a volume mount line for each under services.postgres.volumes "
        f"so a fresh local DB volume applies every migration."
    )
