"""Frozen fixture-grade artifacts for the three fixed corpus samples.

 task 5: ship reviewable artifact fixtures for the three fixed
golden samples (``short_news``, ``reuters_bbc_970``,
``long_article_headings``) plus a manifest recording the input hash,
artifact hash, schema version, and generation path for each.

Design boundaries:

1. The frozen artifacts are produced by the hermetic
   :func:`.fixture_builder.build_fixture_artifact_from_sample` — they
   are fixture-grade (``executor_mode="fake"``,
   ``is_fake=True``), NOT real-LLM outputs. They exist so the gate,
   the schema, and the determinism contract can be audited offline
   against fixed, reviewable bytes.

2. The artifacts are serialized via
   :func:`.gate.serialize_artifact` (sorted keys, no ASCII escaping,
   no extra whitespace) so two runs on the same fixed sample produce
   byte-identical JSON. The manifest records the SHA-256 of that
   canonical JSON, so any drift in the producer algorithm is
   detectable.

3. The manifest is intentionally minimal and deterministic: no wall
   clock, no absolute paths. The ``generator_module`` field records
   the logical module path (not an absolute filesystem path) so the
   manifest is portable across machines.

4. The frozen artifacts directory is a fixed location inside the
   package so tests can locate it via ``importlib.resources`` or
   ``__file__``-relative paths without depending on a working
   directory.

5. The module never calls the real LLM, never touches the DB, and
   never imports the ``app`` runtime. It only depends on
   :mod:`.fixture_builder`, :mod:`.gate`, and the golden sample
   loader.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import (
    ARTIFACT_SCHEMA_VERSION,
    PRODUCER_SEMANTIC_VERSION,
    PRODUCER_VERSION,
)
from .fixture_builder import (
    FIXTURE_PRODUCER_MODULE,
    canonicalize_hermetic,
    sha256_hex,
)
from .gate import artifact_payload_sha256, serialize_artifact
from .schema import ParseEvalArtifactV1

# ---------------------------------------------------------------------------
# Fixed frozen sample ids (subset of the golden corpus)
# ---------------------------------------------------------------------------
#
# These three samples are the frozen artifact set for -. They
# cover the short / medium / long shape bands and are the stable
# targets for the determinism + gate acceptance tests.
# ---------------------------------------------------------------------------

FROZEN_SAMPLE_IDS: tuple[str, ...] = (
    "short_news",
    "reuters_bbc_970",
    "long_article_headings",
)

# Logical module path recorded in the manifest. We record a logical
# path (not an absolute filesystem path) so the manifest is
# portable across machines.
FROZEN_ARTIFACTS_GENERATOR_MODULE: str = (
    "services/api/verification/reader_baseline/parse_eval/frozen_artifacts.py"
)

# Subdirectory inside the package that holds the frozen artifact
# JSON files + manifest. Tests locate it via
# ``Path(__file__).parent / FROZEN_ARTIFACTS_SUBDIR``.
FROZEN_ARTIFACTS_SUBDIR: str = "frozen_artifacts"

# Fixed deterministic clock token for the frozen artifact set. All
# three artifacts are produced with this same token so their
# ``artifact_id`` derivation is stable.
FROZEN_CLOCK_TOKEN: str = "frozen-fixture-artifacts-v1"


# ---------------------------------------------------------------------------
# Manifest entry shape (plain dataclass, serialised to JSON)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FrozenArtifactManifestEntry:
    """One entry in the frozen artifacts manifest."""

    sample_id: str
    source_shape: str
    source_attribution: str
    input_hash: str  # SHA-256 over the canonical text
    canonical_text_length_utf16: int
    canonical_text_length_chars: int
    word_count: int
    artifact_id: str
    artifact_hash: str  # SHA-256 over the canonical JSON of the artifact
    artifact_file: str  # relative path inside FROZEN_ARTIFACTS_SUBDIR
    schema_version: str
    producer_version: str
    producer_semantic_version: str
    deterministic_clock_token: str

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "source_shape": self.source_shape,
            "source_attribution": self.source_attribution,
            "input_hash": self.input_hash,
            "canonical_text_length_utf16": self.canonical_text_length_utf16,
            "canonical_text_length_chars": self.canonical_text_length_chars,
            "word_count": self.word_count,
            "artifact_id": self.artifact_id,
            "artifact_hash": self.artifact_hash,
            "artifact_file": self.artifact_file,
            "schema_version": self.schema_version,
            "producer_version": self.producer_version,
            "producer_semantic_version": self.producer_semantic_version,
            "deterministic_clock_token": self.deterministic_clock_token,
        }


@dataclass(frozen=True, slots=True)
class FrozenArtifactsManifest:
    """Top-level manifest for the frozen artifact set."""

    schema_version: str
    producer_version: str
    producer_semantic_version: str
    generator_module: str
    fixture_producer_module: str
    deterministic_clock_token: str
    artifacts: tuple[FrozenArtifactManifestEntry, ...]

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "producer_version": self.producer_version,
            "producer_semantic_version": self.producer_semantic_version,
            "generator_module": self.generator_module,
            "fixture_producer_module": self.fixture_producer_module,
            "deterministic_clock_token": self.deterministic_clock_token,
            "artifacts": [entry.to_jsonable() for entry in self.artifacts],
        }


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _load_sample(sample_id: str) -> Any:
    """Load a golden sample by id.

    Deferred import so the frozen_artifacts module does not require
    the golden_samples loader at import time (it pulls in PyYAML).
    """
    from verification.reader_baseline.golden_samples import load_sample

    return load_sample(sample_id)


def build_frozen_artifact_for_sample(
    sample_id: str,
    *,
    deterministic_clock_token: str = FROZEN_CLOCK_TOKEN,
) -> tuple[ParseEvalArtifactV1, str, str]:
    """Build the frozen artifact + canonical JSON + input hash for a sample.

    Returns ``(artifact, canonical_json, input_hash)`` where:

    - ``artifact`` is the validated :class:`.schema.ParseEvalArtifactV1`
    - ``canonical_json`` is the canonical serialized JSON (sorted keys,
      no ASCII escaping, no extra whitespace) — byte-identical across
      runs on the same fixed sample
    - ``input_hash`` is the SHA-256 over the canonical text (the
      producer input)
    """
    from .fixture_builder import build_fixture_artifact_from_sample

    sample = _load_sample(sample_id)
    canonical_text = canonicalize_hermetic(sample.plain_text)
    input_hash = sha256_hex(canonical_text)

    artifact = build_fixture_artifact_from_sample(
        sample,
        deterministic_clock_token=deterministic_clock_token,
    )
    canonical_json = serialize_artifact(artifact)
    return artifact, canonical_json, input_hash


def build_frozen_manifest(
    *,
    deterministic_clock_token: str = FROZEN_CLOCK_TOKEN,
) -> tuple[FrozenArtifactsManifest, dict[str, str]]:
    """Build the manifest + a map of ``sample_id -> canonical_json``.

    The returned map is what callers write to disk as the individual
    artifact files. The manifest entries reference the relative
    ``artifact_file`` name for each sample.
    """
    entries: list[FrozenArtifactManifestEntry] = []
    artifacts_json: dict[str, str] = {}

    for sample_id in FROZEN_SAMPLE_IDS:
        artifact, canonical_json, input_hash = build_frozen_artifact_for_sample(
            sample_id, deterministic_clock_token=deterministic_clock_token
        )
        artifact_hash = sha256_hex(canonical_json)
        artifact_file = f"{sample_id}.artifact.v1.json"
        entries.append(
            FrozenArtifactManifestEntry(
                sample_id=sample_id,
                source_shape=artifact.sample.shape,
                source_attribution=artifact.sample.source_attribution,
                input_hash=input_hash,
                canonical_text_length_utf16=artifact.document.canonical_text_length_utf16,
                canonical_text_length_chars=artifact.document.canonical_text_length_chars,
                word_count=artifact.document.word_count,
                artifact_id=artifact.artifact_id,
                artifact_hash=artifact_hash,
                artifact_file=artifact_file,
                schema_version=ARTIFACT_SCHEMA_VERSION,
                producer_version=PRODUCER_VERSION,
                producer_semantic_version=PRODUCER_SEMANTIC_VERSION,
                deterministic_clock_token=deterministic_clock_token,
            )
        )
        artifacts_json[sample_id] = canonical_json

    manifest = FrozenArtifactsManifest(
        schema_version=ARTIFACT_SCHEMA_VERSION,
        producer_version=PRODUCER_VERSION,
        producer_semantic_version=PRODUCER_SEMANTIC_VERSION,
        generator_module=FROZEN_ARTIFACTS_GENERATOR_MODULE,
        fixture_producer_module=FIXTURE_PRODUCER_MODULE,
        deterministic_clock_token=deterministic_clock_token,
        artifacts=tuple(entries),
    )
    return manifest, artifacts_json


# ---------------------------------------------------------------------------
# Disk I/O
# ---------------------------------------------------------------------------


def frozen_artifacts_dir() -> Path:
    """Return the frozen artifacts directory (creates it if missing)."""
    path = Path(__file__).parent / FROZEN_ARTIFACTS_SUBDIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_frozen_artifacts(
    *,
    deterministic_clock_token: str = FROZEN_CLOCK_TOKEN,
) -> Path:
    """Write the 3 frozen artifact JSON files + manifest to disk.

    Returns the directory the files were written to. The output is
    deterministic: running this function twice on the same codebase
    produces byte-identical files.
    """
    manifest, artifacts_json = build_frozen_manifest(
        deterministic_clock_token=deterministic_clock_token
    )
    out_dir = frozen_artifacts_dir()

    for entry in manifest.artifacts:
        artifact_path = out_dir / entry.artifact_file
        artifact_path.write_text(
            artifacts_json[entry.sample_id],
            encoding="utf-8",
        )

    manifest_path = out_dir / "manifest.json"
    manifest_payload = manifest.to_jsonable()
    manifest_json = json.dumps(
        manifest_payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    manifest_path.write_text(manifest_json, encoding="utf-8")
    return out_dir


def load_frozen_manifest() -> FrozenArtifactsManifest:
    """Load the manifest from disk."""
    out_dir = frozen_artifacts_dir()
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"frozen artifacts manifest not found: {manifest_path}"
        )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = tuple(
        FrozenArtifactManifestEntry(
            sample_id=e["sample_id"],
            source_shape=e["source_shape"],
            source_attribution=e["source_attribution"],
            input_hash=e["input_hash"],
            canonical_text_length_utf16=e["canonical_text_length_utf16"],
            canonical_text_length_chars=e["canonical_text_length_chars"],
            word_count=e["word_count"],
            artifact_id=e["artifact_id"],
            artifact_hash=e["artifact_hash"],
            artifact_file=e["artifact_file"],
            schema_version=e["schema_version"],
            producer_version=e["producer_version"],
            producer_semantic_version=e["producer_semantic_version"],
            deterministic_clock_token=e["deterministic_clock_token"],
        )
        for e in payload["artifacts"]
    )
    return FrozenArtifactsManifest(
        schema_version=payload["schema_version"],
        producer_version=payload["producer_version"],
        producer_semantic_version=payload["producer_semantic_version"],
        generator_module=payload["generator_module"],
        fixture_producer_module=payload["fixture_producer_module"],
        deterministic_clock_token=payload["deterministic_clock_token"],
        artifacts=entries,
    )


def load_frozen_artifact_json(sample_id: str) -> str:
    """Load the raw canonical JSON text of a frozen artifact."""
    manifest = load_frozen_manifest()
    entry = next(
        (e for e in manifest.artifacts if e.sample_id == sample_id), None
    )
    if entry is None:
        raise FileNotFoundError(
            f"no frozen artifact for sample {sample_id!r} in manifest"
        )
    artifact_path = frozen_artifacts_dir() / entry.artifact_file
    if not artifact_path.exists():
        raise FileNotFoundError(
            f"frozen artifact file missing: {artifact_path}"
        )
    return artifact_path.read_text(encoding="utf-8")


def verify_frozen_artifacts() -> tuple[bool, list[str]]:
    """Verify the on-disk frozen artifacts match a fresh regeneration.

    Returns ``(ok, messages)``. ``ok`` is True iff every artifact file
    and the manifest are byte-identical to a fresh regeneration from
    the same fixed samples. ``messages`` is a list of human-readable
    drift descriptions (empty when ``ok`` is True).
    """
    messages: list[str] = []
    fresh_manifest, fresh_artifacts_json = build_frozen_manifest()
    out_dir = frozen_artifacts_dir()

    # Verify manifest file
    fresh_manifest_json = json.dumps(
        fresh_manifest.to_jsonable(),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        messages.append(f"manifest missing: {manifest_path}")
    else:
        on_disk = manifest_path.read_text(encoding="utf-8")
        if on_disk != fresh_manifest_json:
            messages.append(
                f"manifest drift: on-disk SHA-256={sha256_hex(on_disk)} "
                f"!= fresh SHA-256={sha256_hex(fresh_manifest_json)}"
            )

    # Verify each artifact file
    for entry in fresh_manifest.artifacts:
        artifact_path = out_dir / entry.artifact_file
        if not artifact_path.exists():
            messages.append(f"artifact missing: {artifact_path}")
            continue
        on_disk = artifact_path.read_text(encoding="utf-8")
        fresh = fresh_artifacts_json[entry.sample_id]
        if on_disk != fresh:
            messages.append(
                f"artifact drift for {entry.sample_id!r}: "
                f"on-disk SHA-256={sha256_hex(on_disk)} "
                f"!= fresh SHA-256={sha256_hex(fresh)}"
            )

    return (len(messages) == 0, messages)


__all__ = [
    "FROZEN_SAMPLE_IDS",
    "FROZEN_ARTIFACTS_GENERATOR_MODULE",
    "FROZEN_ARTIFACTS_SUBDIR",
    "FROZEN_CLOCK_TOKEN",
    "FrozenArtifactManifestEntry",
    "FrozenArtifactsManifest",
    "build_frozen_artifact_for_sample",
    "build_frozen_manifest",
    "frozen_artifacts_dir",
    "write_frozen_artifacts",
    "load_frozen_manifest",
    "load_frozen_artifact_json",
    "verify_frozen_artifacts",
    "artifact_payload_sha256",
]
