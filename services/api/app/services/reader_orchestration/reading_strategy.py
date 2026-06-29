"""Reader Orchestration variant-first strategy resolver.

This module is the deep Module that turns a `(reading_goal, reading_variant)`
pair into a concrete per-layer prompt policy. Callers (worker prompt builders,
Ask context, job bootstrap) only need to know this Interface:

    resolve_reader_variant_strategy(reading_goal, reading_variant) -> ReaderVariantStrategy

They never need to know legacy focus names (`explicit_exam`, `speed_support`,
`structural`, `exam_priority`, `natural`, ...). Those names were used as
wording sources for `reader_variants.yaml` but are NOT exposed externally.

Fail-closed contract:
    - Missing variant entry in the policy file -> error.
    - Missing required layer in a variant entry -> error.
    - goal/variant pair not in `READER_ORCHESTRATION_GOAL_VARIANT_MAP` -> error.
    - `academic` / `academic_general` -> error (not wired into new orchestration).
    - No `default` fallback. Missing data is always an error.

Hash contract:
    - `strategy_hash` and per-layer `policy_hash` are deterministic sha256 of
      canonical JSON (sort_keys=True, separators=(",", ":")).
    - The `strategy_hash` covers goal, variant, profile_id, annotation_density,
      strategy_version, and all layer prompt_lines. Changing any prompt line
      changes the hash.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from app.schemas.reader_orchestration import (
    READER_ORCHESTRATION_GOAL_VARIANT_MAP,
)

READER_VARIANT_POLICY_VERSION = "reader_variant_policy_v1"
READER_VARIANT_POLICY_SCHEMA_VERSION = 1

REQUIRED_LAYERS: tuple[str, ...] = (
    "translation",
    "vocabulary",
    "grammar_bundle",
    "ask",
)

_POLICY_FILE = (
    Path(__file__).resolve().parents[3]
    / "prompts"
    / "policies"
    / "reader_variants.yaml"
)


class ReaderStrategyResolverError(ValueError):
    """Raised when a variant strategy cannot be resolved (fail-closed)."""


@dataclass(frozen=True, slots=True)
class ReaderVariantLayerPolicy:
    """Concrete prompt policy for a single enhancement layer."""

    prompt_lines: tuple[str, ...]
    policy_hash: str


@dataclass(frozen=True, slots=True)
class ReaderVariantStrategy:
    """Resolved variant-first strategy for a reading record.

    Attributes:
        reading_goal: The goal this variant belongs to.
        reading_variant: The variant key.
        profile_id: Short stable identifier for the variant profile.
        annotation_density: Hint for how many annotations the workers should
            aim for. Not a hard cap; workers still apply quality-first rules.
        strategy_version: The policy file's ``strategy_version`` value.
        strategy_hash: Deterministic sha256 of the full strategy payload.
        layers: Read-only map of layer name -> :class:`ReaderVariantLayerPolicy`.
            The returned mapping is a :class:`types.MappingProxyType` view;
            mutating it (``clear``, ``__setitem__``, ``__delitem__``) raises
            ``TypeError``. Callers that need a mutable copy must construct
            one explicitly.
    """

    reading_goal: str
    reading_variant: str
    profile_id: str
    annotation_density: int
    strategy_version: str
    strategy_hash: str
    layers: Mapping[str, ReaderVariantLayerPolicy]

    def __post_init__(self) -> None:
        # Wrap the supplied mapping in a read-only view so callers cannot
        # mutate ``layers`` and corrupt the resolved payload or its hash.
        # ``object.__setattr__`` is required because the dataclass is frozen.
        if not isinstance(self.layers, MappingProxyType):
            object.__setattr__(
                self, "layers", MappingProxyType(dict(self.layers))
            )


def _validate_policy_doc_shape(doc: Any) -> Mapping[str, Any]:
    """Validate the top-level shape of a reader_variants policy document.

    Both the default file loader (:func:`load_reader_variant_policy_doc`) and
    :func:`resolve_reader_variant_strategy` (for caller-supplied
    ``policy_doc``) call this helper so that on-disk and in-memory policy
    documents are held to the same contract.

    Checks:
        - ``doc`` is a :class:`collections.abc.Mapping`.
        - ``schema_version`` equals
          :data:`READER_VARIANT_POLICY_SCHEMA_VERSION`.
        - ``strategy_version`` equals :data:`READER_VARIANT_POLICY_VERSION`.
        - ``variants`` is a :class:`collections.abc.Mapping`.

    Returns:
        The validated document (same object passed in) on success.

    Raises:
        ReaderStrategyResolverError: On any shape or version mismatch.
    """
    if not isinstance(doc, Mapping):
        raise ReaderStrategyResolverError(
            f"reader_variants policy document must be a mapping at the top "
            f"level; got {type(doc).__name__}"
        )

    schema_version = doc.get("schema_version")
    if schema_version != READER_VARIANT_POLICY_SCHEMA_VERSION:
        raise ReaderStrategyResolverError(
            f"reader_variants policy document schema_version mismatch: "
            f"expected {READER_VARIANT_POLICY_SCHEMA_VERSION!r}, got "
            f"{schema_version!r}"
        )

    strategy_version = doc.get("strategy_version")
    if strategy_version != READER_VARIANT_POLICY_VERSION:
        raise ReaderStrategyResolverError(
            f"reader_variants policy document strategy_version mismatch: "
            f"expected {READER_VARIANT_POLICY_VERSION!r}, got "
            f"{strategy_version!r}"
        )

    variants = doc.get("variants")
    if not isinstance(variants, Mapping):
        raise ReaderStrategyResolverError(
            "reader_variants policy document must contain a 'variants' mapping"
        )

    return doc


def load_reader_variant_policy_doc() -> Mapping[str, Any]:
    """Load and validate the top-level shape of ``reader_variants.yaml``.

    This is the default loader used by :func:`resolve_reader_variant_strategy`.
    Tests can pass a custom ``policy_doc`` to the resolver to exercise
    fail-closed paths without writing to disk.
    """
    with _POLICY_FILE.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)

    return _validate_policy_doc_shape(raw)


def _compute_hash(payload: Any) -> str:
    """Deterministic sha256 of canonical JSON."""
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_str(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReaderStrategyResolverError(f"{label} must be a non-empty string")
    return value


def _resolve_layer_policy(
    variant_key: str,
    layer_name: str,
    layer_data: Any,
) -> ReaderVariantLayerPolicy:
    if not isinstance(layer_data, Mapping):
        raise ReaderStrategyResolverError(
            f"variant {variant_key!r} layer {layer_name!r} must be a mapping"
        )
    raw_lines = layer_data.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ReaderStrategyResolverError(
            f"variant {variant_key!r} layer {layer_name!r} must have a "
            f"non-empty 'lines' list"
        )
    lines: list[str] = []
    for index, item in enumerate(raw_lines):
        if not isinstance(item, str) or not item:
            raise ReaderStrategyResolverError(
                f"variant {variant_key!r} layer {layer_name!r} lines[{index}] "
                f"must be a non-empty string"
            )
        lines.append(item)
    prompt_lines = tuple(lines)
    policy_hash = _compute_hash(
        {"layer": layer_name, "variant": variant_key, "lines": list(prompt_lines)}
    )
    return ReaderVariantLayerPolicy(prompt_lines=prompt_lines, policy_hash=policy_hash)


def resolve_reader_variant_strategy(
    reading_goal: str,
    reading_variant: str,
    *,
    policy_doc: Mapping[str, Any] | None = None,
) -> ReaderVariantStrategy:
    """Resolve a variant-first Reader strategy.

    Args:
        reading_goal: One of the legal goals in
            :data:`READER_ORCHESTRATION_GOAL_VARIANT_MAP`.
        reading_variant: A variant that belongs to ``reading_goal``.
        policy_doc: Optional pre-loaded policy document for testing. When
            ``None``, the default file loader is used.

    Raises:
        ReaderStrategyResolverError: On any missing/mismatched data. This
            includes ``academic`` / ``academic_general``, unknown variants,
            goal/variant mismatch, and missing layers.
    """
    # 1. Contract-level goal/variant pair validation. This rejects
    #    `academic` / `academic_general` and any cross-goal pair before
    #    touching the policy file.
    allowed_variants = READER_ORCHESTRATION_GOAL_VARIANT_MAP.get(reading_goal)
    if allowed_variants is None:
        raise ReaderStrategyResolverError(
            f"reading_goal={reading_goal!r} is not supported in the new Reader "
            f"Orchestration scope"
        )
    if reading_variant not in allowed_variants:
        raise ReaderStrategyResolverError(
            f"reading_variant={reading_variant!r} does not belong to "
            f"reading_goal={reading_goal!r} in the new Reader Orchestration scope"
        )

    # 2. Load policy document (default file or caller-supplied). Both paths
    #    go through the shared top-level shape validator so that on-disk and
    #    in-memory policy docs are held to the same contract (schema_version,
    #    strategy_version, variants mapping).
    doc = (
        load_reader_variant_policy_doc()
        if policy_doc is None
        else _validate_policy_doc_shape(policy_doc)
    )
    strategy_version = doc["strategy_version"]
    variants = doc["variants"]

    # 3. Look up the variant entry. No default fallback.
    variant_entry = variants.get(reading_variant)
    if not isinstance(variant_entry, Mapping):
        raise ReaderStrategyResolverError(
            f"variant {reading_variant!r} has no explicit entry in "
            f"reader_variants.yaml"
        )

    # 4. Cross-check the variant's declared reading_goal matches the request.
    declared_goal = _require_str(
        variant_entry.get("reading_goal"),
        label=f"variant {reading_variant!r} reading_goal",
    )
    if declared_goal != reading_goal:
        raise ReaderStrategyResolverError(
            f"variant {reading_variant!r} declares reading_goal="
            f"{declared_goal!r} but resolver was called with "
            f"reading_goal={reading_goal!r}"
        )

    profile_id = _require_str(
        variant_entry.get("profile_id"),
        label=f"variant {reading_variant!r} profile_id",
    )
    annotation_density_raw = variant_entry.get("annotation_density")
    if not isinstance(annotation_density_raw, int) or annotation_density_raw < 0:
        raise ReaderStrategyResolverError(
            f"variant {reading_variant!r} annotation_density must be a "
            f"non-negative integer"
        )

    # 5. Resolve every required layer. Missing layer = error.
    layers_data = variant_entry.get("layers")
    if not isinstance(layers_data, Mapping):
        raise ReaderStrategyResolverError(
            f"variant {reading_variant!r} must contain a 'layers' mapping"
        )
    layers: dict[str, ReaderVariantLayerPolicy] = {}
    for layer_name in REQUIRED_LAYERS:
        layer_data = layers_data.get(layer_name)
        if layer_data is None:
            raise ReaderStrategyResolverError(
                f"variant {reading_variant!r} is missing required layer "
                f"{layer_name!r}"
            )
        layers[layer_name] = _resolve_layer_policy(
            variant_key=reading_variant,
            layer_name=layer_name,
            layer_data=layer_data,
        )

    # 6. Compute deterministic strategy hash over the full resolved payload.
    strategy_payload = {
        "reading_goal": reading_goal,
        "reading_variant": reading_variant,
        "profile_id": profile_id,
        "annotation_density": annotation_density_raw,
        "strategy_version": strategy_version,
        "layers": {
            name: list(layers[name].prompt_lines) for name in REQUIRED_LAYERS
        },
    }
    strategy_hash = _compute_hash(strategy_payload)

    return ReaderVariantStrategy(
        reading_goal=reading_goal,
        reading_variant=reading_variant,
        profile_id=profile_id,
        annotation_density=annotation_density_raw,
        strategy_version=strategy_version,
        strategy_hash=strategy_hash,
        layers=layers,
    )
