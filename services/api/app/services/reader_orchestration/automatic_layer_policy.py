"""Versioned automatic layer policy resolver + bootstrap target seam.

This module is the **single source** for:

1. ``resolve_automatic_layer_policy`` — pure, versioned resolver that maps
   ``(contract_version, block_type, payload_json)`` → automatic T/V/G/S.
2. USER_EXPLICIT section translation admission is independent of automatic
   ``allows=false`` for the translation layer only (see fence below);
   vocabulary / grammar never inherit that exemption.
3. ``load_automatic_layer_targets`` / ``filter_units_for_automatic_layer`` —
   the only bootstrap filter used by per-unit, compact, grouped, and grammar-window.
4. Unit ``metadata_json`` materialisation helpers and job version fence.
5. Shadow would-skip structured logging (no new DB event types).

Contract:
  - ``semantic_contract_v1`` + ``automatic_layer_policy_v1`` (frozen).
  - Legacy = missing ``contract_version`` only → fail-open (all automatic layers on).
  - Reload must call the **recorded** resolver version, never latest by default.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Final, Literal

from .semantic_classifier import (
    CONTENT_ROLES,
    SEMANTIC_CONTRACT_V1,
    extract_content_role,
    extract_contract_version,
    is_legacy_semantic,
    is_shadow_only_classification,
)

logger = logging.getLogger(__name__)

AUTOMATIC_LAYER_POLICY_RESOLVER_V1: Final[str] = "automatic_layer_policy_v1"
LATEST_RESOLVER_VERSION: Final[str] = AUTOMATIC_LAYER_POLICY_RESOLVER_V1

AutomaticLayerName = Literal[
    "translation",
    "vocabulary",
    "grammar_note",
    "sentence_analysis",
]

AUTOMATIC_LAYERS: Final[tuple[AutomaticLayerName, ...]] = (
    "translation",
    "vocabulary",
    "grammar_note",
    "sentence_analysis",
)

# Snapshot / Web DTO camelCase keys (projection only).
_DTO_LAYER_KEYS: Final[dict[AutomaticLayerName, str]] = {
    "translation": "translation",
    "vocabulary": "vocabulary",
    "grammar_note": "grammarNote",
    "sentence_analysis": "sentenceAnalysis",
}

SEMANTIC_POLICY_VERSION_MISMATCH_CODE: Final[str] = "semantic_policy_version_mismatch"
SEMANTIC_LAYER_DISALLOWED_CODE: Final[str] = "semantic_automatic_layer_disallowed"
SEMANTIC_FENCE_KEY_CONTRACT: Final[str] = "semantic_contract_version"
SEMANTIC_FENCE_KEY_RESOLVER: Final[str] = "automatic_layer_policy_resolver_version"
SEMANTIC_FENCE_KEY_LAYER: Final[str] = "automatic_layer_name"
SEMANTIC_FENCE_KEY_MODE: Final[str] = "semantic_policy_mode"

# Product matrix defaults for semantic_contract_v1 / resolver v1 (2026-07-29 repair).
_ALL_ON = (True, True, True, True)
_ALL_OFF = (False, False, False, False)
_T_ONLY = (True, False, False, False)

AutomaticPolicyMode = Literal["off", "shadow", "enforce"]
DEFAULT_AUTOMATIC_POLICY_MODE: Final[AutomaticPolicyMode] = "enforce"
# Pre-mode-fence jobs (fence present, mode missing): keep prior worker behaviour
# which enforced allows(layer). Documented compatibility constant.
LEGACY_MISSING_MODE_COMPAT: Final[AutomaticPolicyMode] = "enforce"
SEMANTIC_FENCE_FAILURE_CODES: Final[frozenset[str]] = frozenset(
    {
        SEMANTIC_POLICY_VERSION_MISMATCH_CODE,
        SEMANTIC_LAYER_DISALLOWED_CODE,
    }
)


@dataclass(frozen=True, slots=True)
class AutomaticLayerPolicy:
    translation: bool
    vocabulary: bool
    grammar_note: bool
    sentence_analysis: bool

    def allows(self, layer: AutomaticLayerName) -> bool:
        return bool(getattr(self, layer))

    def as_dict(self) -> dict[str, bool]:
        return {
            "translation": self.translation,
            "vocabulary": self.vocabulary,
            "grammar_note": self.grammar_note,
            "sentence_analysis": self.sentence_analysis,
        }

    def as_dto(self) -> dict[str, bool]:
        """CamelCase projection for snapshot / Web DTO."""
        raw = self.as_dict()
        return {_DTO_LAYER_KEYS[k]: raw[k] for k in AUTOMATIC_LAYERS}  # type: ignore[index]

    @classmethod
    def all_on(cls) -> AutomaticLayerPolicy:
        return cls(*_ALL_ON)

    @classmethod
    def all_off(cls) -> AutomaticLayerPolicy:
        return cls(*_ALL_OFF)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> AutomaticLayerPolicy | None:
        """Strict parse: exactly the four persisted keys, each a native bool.

        Missing keys, extra keys, strings (including ``"false"``),
        integers (including ``0`` / ``1``), and any other truthy/falsy
        stand-ins return ``None`` — only the exact writer shape produced
        by :meth:`as_dict` is well-formed.
        """
        if not isinstance(data, Mapping):
            return None
        if set(data.keys()) != {
            "translation",
            "vocabulary",
            "grammar_note",
            "sentence_analysis",
        }:
            return None
        values = (
            data["translation"],
            data["vocabulary"],
            data["grammar_note"],
            data["sentence_analysis"],
        )
        if any(not isinstance(value, bool) for value in values):
            return None
        return cls(*values)


@dataclass(frozen=True, slots=True)
class ResolvedAutomaticLayerPolicy:
    policy: AutomaticLayerPolicy
    contract_version: str | None
    resolver_version: str
    content_role: str | None
    is_legacy: bool
    # When True, classification was shadow-only; policy equals fail-open/legacy-like
    # behaviour for that role (resolver already applied fail-open).
    shadow_only: bool = False


class SemanticPolicyVersionMismatch(Exception):
    """Job fence / unit metadata semantic versions disagree."""

    def __init__(self, message: str, *, code: str = SEMANTIC_POLICY_VERSION_MISMATCH_CODE) -> None:
        super().__init__(message)
        self.code = code


# Stable code returned by the shared fence builder when a bootstrap batch
# contains mixed contract / resolver versions across its target units. Same
# code surface as the worker-side fence validator so audit consumers can
# collapse bootstrap-time and worker-time rejections into one bucket.
SEMANTIC_FENCE_INCONSISTENT_CODE: Final[str] = "semantic_fence_inconsistent"


class SemanticFenceConstructionError(Exception):
    """Shared semantic fence builder cannot produce a single fence identity.

    Raised by :func:`generation_semantic_fence_from_targets` when the target
    units carry mixed contract versions, mixed resolver versions, or a mix of
    legacy and semantic units. Bootstrap callers (automatic and section) MUST
    fail closed before any reader_jobs / reader_runs row is persisted.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = SEMANTIC_FENCE_INCONSISTENT_CODE,
    ) -> None:
        super().__init__(message)
        self.code = code


def _policy_from_flags(flags: tuple[bool, bool, bool, bool]) -> AutomaticLayerPolicy:
    return AutomaticLayerPolicy(
        translation=flags[0],
        vocabulary=flags[1],
        grammar_note=flags[2],
        sentence_analysis=flags[3],
    )


def _resolve_v1(
    *,
    block_type: str | None,
    content_role: str | None,
    shadow_only: bool,
) -> AutomaticLayerPolicy:
    """Product matrix for semantic_contract_v1 / automatic_layer_policy_v1.

    Frozen repair matrix (2026-07-29):
      - prose / list prose: T/V/G/S all on
      - heading: T-only (vocabulary off)
      - citation_reference / quotation / source_callout: T-only
      - code / table* / link_only: all off
      - Markdown ``blockquote`` structure is always T-only — even when
        role classification is shadow-only or ambiguous. ``shadow_only``
        must not re-open V/G/S for blockquotes.
    """
    bt = (block_type or "").strip()

    # Structural exclusions (role is null but contract is present).
    if bt in {"code_block", "table_cell", "table", "table_row"}:
        return _policy_from_flags(_ALL_OFF)

    if bt == "heading":
        return _policy_from_flags(_T_ONLY)

    # Structural blockquote: always T-only regardless of shadow_only.
    # Ordinary `>` quotes and asides share this structural gate.
    if bt == "blockquote":
        return _policy_from_flags(_T_ONLY)

    # Shadow-only roles (question / weak citation without section) fail-open
    # to prose. They must not invent forced exclusions.
    if shadow_only:
        return _policy_from_flags(_ALL_ON)

    if content_role == "link_only":
        return _policy_from_flags(_ALL_OFF)

    if content_role in {"citation_reference", "source_callout", "quotation"}:
        return _policy_from_flags(_T_ONLY)

    # prose / list_item / caption / prompt_question (non-shadow) → all on.
    if bt in {"paragraph", "list_item", "caption"} or content_role in {
        "prose",
        "prompt_question",
        None,
    }:
        return _policy_from_flags(_ALL_ON)

    # Unknown typed blocks with a contract marker: fail-open (all on).
    return _policy_from_flags(_ALL_ON)


def parse_automatic_policy_mode(value: object) -> AutomaticPolicyMode:
    """Parse and validate a mode value. Raises ``ValueError`` on illegal input."""
    if not isinstance(value, str):
        raise ValueError(
            f"reader_automatic_layer_policy_mode must be str, got {type(value)!r}"
        )
    mode = value.strip().lower()
    if mode not in {"off", "shadow", "enforce"}:
        raise ValueError(
            "reader_automatic_layer_policy_mode must be one of "
            f"'off'|'shadow'|'enforce', got {value!r}"
        )
    return mode  # type: ignore[return-value]


def get_automatic_layer_policy_mode(
    override: AutomaticPolicyMode | None = None,
) -> AutomaticPolicyMode:
    """Return off | shadow | enforce (single config for all topologies).

    Does not swallow invalid configuration: Settings construction fails closed
    on illegal values via ``Literal``; this helper only reads the validated
    setting or an explicit override.
    """
    if override is not None:
        return parse_automatic_policy_mode(override)
    from app.config.settings import get_settings

    return parse_automatic_policy_mode(get_settings().reader_automatic_layer_policy_mode)


def resolve_automatic_layer_policy(
    *,
    contract_version: str | None,
    block_type: str | None,
    payload_json: Mapping[str, Any] | None,
    interpretation_policy: Mapping[str, Any] | None = None,  # noqa: ARG001 — reserved
    resolver_version: str | None = None,
) -> ResolvedAutomaticLayerPolicy:
    """Resolve automatic T/V/G/S for a block.

    ``resolver_version`` selects which pure function runs. When omitted,
    uses ``LATEST_RESOLVER_VERSION``. Callers reloading a generation MUST
    pass the version recorded on the unit, never rely on latest implicitly.
    """
    del interpretation_policy  # reserved for future matrix inputs; unused in v1

    recorded_or_latest = (resolver_version or LATEST_RESOLVER_VERSION).strip()
    if recorded_or_latest not in {AUTOMATIC_LAYER_POLICY_RESOLVER_V1}:
        # Unknown historical resolver: fail-open rather than invent all-false.
        return ResolvedAutomaticLayerPolicy(
            policy=AutomaticLayerPolicy.all_on(),
            contract_version=contract_version,
            resolver_version=recorded_or_latest,
            content_role=extract_content_role(payload_json),
            is_legacy=contract_version is None,
            shadow_only=False,
        )

    if contract_version is None or is_legacy_semantic(payload_json):
        return ResolvedAutomaticLayerPolicy(
            policy=AutomaticLayerPolicy.all_on(),
            contract_version=None,
            resolver_version=recorded_or_latest,
            content_role=None,
            is_legacy=True,
            shadow_only=False,
        )

    # Unknown contract versions fail-open.
    if contract_version != SEMANTIC_CONTRACT_V1:
        return ResolvedAutomaticLayerPolicy(
            policy=AutomaticLayerPolicy.all_on(),
            contract_version=contract_version,
            resolver_version=recorded_or_latest,
            content_role=extract_content_role(payload_json),
            is_legacy=False,
            shadow_only=False,
        )

    content_role = extract_content_role(payload_json)
    shadow_only = is_shadow_only_classification(payload_json)
    policy = _resolve_v1(
        block_type=block_type,
        content_role=content_role,
        shadow_only=shadow_only,
    )
    return ResolvedAutomaticLayerPolicy(
        policy=policy,
        contract_version=contract_version,
        resolver_version=AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
        content_role=(
            content_role
            if content_role in CONTENT_ROLES or content_role is None
            else None
        ),
        is_legacy=False,
        shadow_only=shadow_only,
    )


def build_unit_semantic_metadata(
    *,
    contract_version: str | None,
    content_role: str | None,
    policy: AutomaticLayerPolicy | None,
    resolver_version: str | None,
) -> dict[str, Any] | None:
    """Build the ``metadata_json.semantic`` object, or None for legacy units."""
    if contract_version is None:
        return None
    body: dict[str, Any] = {
        "contract_version": contract_version,
        "content_role": content_role,
        "resolver_version": resolver_version or AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
    }
    if policy is not None:
        body["automatic_layer_policy"] = policy.as_dict()
    return body


def build_reading_unit_metadata_json(
    *,
    sentence_provider: str | None = None,
    contract_version: str | None = None,
    content_role: str | None = None,
    automatic_layer_policy: AutomaticLayerPolicy | None = None,
    resolver_version: str | None = None,
) -> dict[str, Any]:
    """Materialise ``reading_units.metadata_json`` (sentence_provider + semantic)."""
    meta: dict[str, Any] = {}
    if sentence_provider:
        meta["sentence_provider"] = sentence_provider
    semantic = build_unit_semantic_metadata(
        contract_version=contract_version,
        content_role=content_role,
        policy=automatic_layer_policy,
        resolver_version=resolver_version,
    )
    if semantic is not None:
        meta["semantic"] = semantic
    return meta


def read_unit_semantic_metadata(
    metadata_json: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(metadata_json, Mapping):
        return None
    semantic = metadata_json.get("semantic")
    return dict(semantic) if isinstance(semantic, Mapping) else None


STRUCTURAL_INTEGRITY_OVERRIDE_VERSION: Final[str] = "structural_integrity_override_v1"
SEMANTIC_INTEGRITY_OVERRIDE_KEY: Final[str] = "semantic_integrity_override"


def build_semantic_integrity_override(*, reason_code: str) -> dict[str, Any]:
    """Persisted ``metadata_json.semantic_integrity_override`` object.

    The recorded policy is always the recorded all-off produced by the
    same :class:`AutomaticLayerPolicy` interface (never a second key
    mapping); ``reason_code`` shares the diagnostics code vocabulary.
    """
    return {
        "override_version": STRUCTURAL_INTEGRITY_OVERRIDE_VERSION,
        "policy": AutomaticLayerPolicy.all_off().as_dict(),
        "reason_code": reason_code,
    }


def _read_semantic_integrity_override(
    metadata_json: Mapping[str, Any] | None,
) -> AutomaticLayerPolicy | None:
    """Well-formed recorded override policy, or None when absent/malformed.

    Well-formed means: exact override version plus a policy mapping that
    passes the strict :meth:`AutomaticLayerPolicy.from_mapping` parse.
    Malformed overrides fall back to the caller's existing paths.
    """
    if not isinstance(metadata_json, Mapping):
        return None
    override = metadata_json.get(SEMANTIC_INTEGRITY_OVERRIDE_KEY)
    if not isinstance(override, Mapping):
        return None
    if override.get("override_version") != STRUCTURAL_INTEGRITY_OVERRIDE_VERSION:
        return None
    reason_code = override.get("reason_code")
    if not isinstance(reason_code, str) or not reason_code:
        return None
    return AutomaticLayerPolicy.from_mapping(override.get("policy"))


def policy_from_unit_metadata(
    metadata_json: Mapping[str, Any] | None,
    *,
    block_type: str | None = None,
    payload_json: Mapping[str, Any] | None = None,
    prefer_recorded: bool = True,
) -> ResolvedAutomaticLayerPolicy:
    """Load policy from unit metadata, re-resolving with the **recorded** version.

    Fail-open rules:
      - missing/invalid metadata → legacy all-on
      - prefer_recorded uses stored automatic_layer_policy when well-formed
      - still returns resolver_version from the record for fence checks
    """
    # The structural integrity override is the strongest recorded truth:
    # a well-formed recorded all-off wins before any semantic / legacy
    # path (same prefer_recorded fence semantics). Malformed overrides
    # fall through to the existing paths.
    override_policy = _read_semantic_integrity_override(metadata_json)
    if override_policy is not None:
        semantic_for_identity = read_unit_semantic_metadata(metadata_json) or {}
        override_contract = semantic_for_identity.get("contract_version")
        override_resolver = semantic_for_identity.get("resolver_version")
        return ResolvedAutomaticLayerPolicy(
            policy=override_policy,
            contract_version=(
                override_contract if isinstance(override_contract, str) else None
            ),
            resolver_version=(
                override_resolver
                if isinstance(override_resolver, str) and override_resolver.strip()
                else AUTOMATIC_LAYER_POLICY_RESOLVER_V1
            ),
            content_role=None,
            is_legacy=False,
        )

    semantic = read_unit_semantic_metadata(metadata_json)
    if semantic is None:
        return resolve_automatic_layer_policy(
            contract_version=None,
            block_type=block_type,
            payload_json=payload_json,
            resolver_version=AUTOMATIC_LAYER_POLICY_RESOLVER_V1,
        )

    contract_version = semantic.get("contract_version")
    if not isinstance(contract_version, str) or not contract_version.strip():
        return resolve_automatic_layer_policy(
            contract_version=None,
            block_type=block_type,
            payload_json=payload_json,
        )

    resolver_version = semantic.get("resolver_version")
    if not isinstance(resolver_version, str) or not resolver_version.strip():
        resolver_version = AUTOMATIC_LAYER_POLICY_RESOLVER_V1

    content_role = semantic.get("content_role")
    if content_role is not None and content_role not in CONTENT_ROLES:
        content_role = None

    if prefer_recorded:
        recorded = AutomaticLayerPolicy.from_mapping(
            semantic.get("automatic_layer_policy")
            if isinstance(semantic.get("automatic_layer_policy"), Mapping)
            else None
        )
        if recorded is not None:
            return ResolvedAutomaticLayerPolicy(
                policy=recorded,
                contract_version=contract_version,
                resolver_version=resolver_version,
                content_role=(
                    content_role
                    if isinstance(content_role, str) or content_role is None
                    else None
                ),
                is_legacy=False,
            )

    # Re-resolve with the recorded resolver version (not latest by default).
    # Prefer unit payload if provided; otherwise synthesize from stored role.
    synthetic_payload: dict[str, Any]
    if payload_json is not None:
        synthetic_payload = dict(payload_json)
    else:
        synthetic_payload = {
            "semantic": {
                "contract_version": contract_version,
                "content_role": content_role,
            }
        }
    return resolve_automatic_layer_policy(
        contract_version=contract_version,
        block_type=block_type,
        payload_json=synthetic_payload,
        resolver_version=resolver_version,
    )


def unit_allows_automatic_layer(
    metadata_json: Mapping[str, Any] | None,
    layer: AutomaticLayerName,
    *,
    block_type: str | None = None,
) -> bool:
    resolved = policy_from_unit_metadata(metadata_json, block_type=block_type)
    return resolved.policy.allows(layer)


def unit_allows_any_grammar(metadata_json: Mapping[str, Any] | None) -> bool:
    resolved = policy_from_unit_metadata(metadata_json)
    return resolved.policy.grammar_note or resolved.policy.sentence_analysis


@dataclass(frozen=True, slots=True)
class AutomaticLayerTargetUnit:
    unit_id: str
    order_index: int
    metadata_json: dict[str, Any]
    base_start_utf16: int | None = None
    base_end_utf16: int | None = None
    text_hash: str | None = None
    unit_type: str | None = None
    contract_version: str | None = None
    resolver_version: str | None = None
    content_role: str | None = None
    policy: AutomaticLayerPolicy = AutomaticLayerPolicy.all_on()


def filter_units_for_automatic_layer(
    units: Sequence[Mapping[str, Any]],
    layer: AutomaticLayerName,
    *,
    mode: AutomaticPolicyMode | None = None,
    record_id: str | None = None,
    generation: int | None = None,
    shadow_log: bool = True,
) -> list[dict[str, Any]]:
    """Filter unit row mappings before target_unit_ids / window planning.

    Modes (single config for all topologies):
      - ``off``: keep all units; no would-skip log (legacy pre-policy behaviour).
      - ``shadow``: keep all units; log would-skip using the same resolver.
      - ``enforce``: drop disallowed units; log would-skip.

    Shadow and enforce always share :func:`policy_from_unit_metadata`.
    """
    resolved_mode = get_automatic_layer_policy_mode(mode)
    if resolved_mode == "off":
        return [dict(u) for u in units]

    kept: list[dict[str, Any]] = []
    would_skip: list[str] = []
    resolver_versions: set[str] = set()

    for raw in units:
        unit = dict(raw)
        meta = unit.get("metadata_json")
        if not isinstance(meta, Mapping):
            meta = {}
        resolved = policy_from_unit_metadata(
            meta,
            block_type=unit.get("stable_block_type") or unit.get("block_type"),
        )
        resolver_versions.add(resolved.resolver_version)
        unit_id = str(unit.get("unit_id"))
        allowed = resolved.policy.allows(layer)
        if not allowed:
            would_skip.append(unit_id)
            if resolved_mode == "shadow":
                kept.append(unit)
            continue
        kept.append(unit)

    if shadow_log and would_skip:
        log_automatic_layer_shadow(
            record_id=record_id,
            generation=generation,
            resolver_version=",".join(sorted(resolver_versions)) or LATEST_RESOLVER_VERSION,
            layer=layer,
            would_skip_unit_ids=would_skip,
            mode=resolved_mode,
        )
    return kept


def materialize_target_units(
    units: Sequence[Mapping[str, Any]],
    layer: AutomaticLayerName,
    *,
    mode: AutomaticPolicyMode | None = None,
    record_id: str | None = None,
    generation: int | None = None,
) -> list[AutomaticLayerTargetUnit]:
    """Convert filtered row mappings into typed targets."""
    filtered = filter_units_for_automatic_layer(
        units,
        layer,
        mode=mode,
        record_id=record_id,
        generation=generation,
    )
    targets: list[AutomaticLayerTargetUnit] = []
    for unit in filtered:
        meta = unit.get("metadata_json")
        if not isinstance(meta, Mapping):
            meta = {}
        resolved = policy_from_unit_metadata(
            meta,
            block_type=unit.get("stable_block_type") or unit.get("block_type"),
        )
        targets.append(
            AutomaticLayerTargetUnit(
                unit_id=str(unit["unit_id"]),
                order_index=int(unit.get("order_index") or 0),
                metadata_json=dict(meta),
                base_start_utf16=(
                    int(unit["base_start_utf16"])
                    if unit.get("base_start_utf16") is not None
                    else None
                ),
                base_end_utf16=(
                    int(unit["base_end_utf16"])
                    if unit.get("base_end_utf16") is not None
                    else None
                ),
                text_hash=str(unit["text_hash"]) if unit.get("text_hash") is not None else None,
                unit_type=str(unit["unit_type"]) if unit.get("unit_type") is not None else None,
                contract_version=resolved.contract_version,
                resolver_version=resolved.resolver_version,
                content_role=resolved.content_role,
                policy=resolved.policy,
            )
        )
    return targets


async def load_automatic_layer_targets(
    conn: Any,
    *,
    record_id: Any,
    base_id: Any,
    generation: int,
    layer: AutomaticLayerName,
    mode: AutomaticPolicyMode | None = None,
    published_layer_types: Sequence[str] | None = None,
) -> list[AutomaticLayerTargetUnit]:
    """Load candidate units missing published layers, then apply policy filter.

    This is the unified bootstrap seam. Callers must use it (or
    :func:`filter_units_for_automatic_layer` on an equivalent SELECT)
    **before** building ``target_unit_ids`` / window plans.
    """
    layer_types = list(published_layer_types or _default_published_layer_types(layer))
    rows = await conn.fetch(
        """
        SELECT
            u.unit_id,
            u.order_index,
            u.base_start_utf16,
            u.base_end_utf16,
            u.text_hash,
            u.unit_type,
            u.metadata_json
        FROM reading_units u
        WHERE u.reading_record_id = $1
          AND u.base_id = $2
          AND NOT EXISTS (
              SELECT 1
              FROM enhancement_layers layer
              WHERE layer.reading_record_id = u.reading_record_id
                AND layer.base_id = u.base_id
                AND layer.generation = $3
                AND layer.layer_type = ANY($4::text[])
                AND layer.target_scope = 'unit'
                AND layer.target_key = u.unit_id
                AND layer.status = 'published'
          )
        ORDER BY u.order_index ASC
        """,
        record_id,
        base_id,
        generation,
        layer_types,
    )
    unit_maps: list[dict[str, Any]] = []
    for row in rows:
        meta = row["metadata_json"]
        if hasattr(meta, "keys"):
            meta_dict = dict(meta)
        elif isinstance(meta, str):
            import json

            try:
                parsed = json.loads(meta)
                meta_dict = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                meta_dict = {}
        else:
            meta_dict = meta if isinstance(meta, dict) else {}
        unit_maps.append(
            {
                "unit_id": str(row["unit_id"]),
                "order_index": int(row["order_index"]),
                "base_start_utf16": int(row["base_start_utf16"]),
                "base_end_utf16": int(row["base_end_utf16"]),
                "text_hash": str(row["text_hash"]),
                "unit_type": str(row["unit_type"]),
                "metadata_json": meta_dict,
            }
        )
    return materialize_target_units(
        unit_maps,
        layer,
        mode=mode,
        record_id=str(record_id),
        generation=generation,
    )


def _default_published_layer_types(layer: AutomaticLayerName) -> list[str]:
    if layer in {"grammar_note", "sentence_analysis"}:
        # Grammar bootstrap skips units that already have either layer.
        return ["grammar_note", "sentence_analysis"]
    return [layer]


def generation_semantic_fence_from_targets(
    targets: Sequence[AutomaticLayerTargetUnit],
) -> dict[str, str | None]:
    """Derive a single semantic fence identity for a bootstrap batch.

    Single source of truth for both automatic and explicit-section topologies.
    Returns a fence dict; never silently picks one version out of a mixed set.

    Contract:
    - Empty targets → legacy fence (preserves pre-fence compatibility for
      callers that explicitly opt into "no units" such as no-op bootstrap).
    - All legacy (no contract_version on any target) → legacy fence
      ``{contract: None, resolver: "legacy_open"}``.
    - Uniform non-None contract + uniform resolver → that exact pair.
    - Any mixed combination (mixed non-None contracts, mixed non-None
      resolvers, or a mix of legacy-None and semantic-non-None contract)
      → :class:`SemanticFenceConstructionError`. Bootstrap callers MUST
      fail closed before persisting any reader_jobs / reader_runs row.
    """
    contracts: set[str] = set()
    resolvers: set[str] = set()
    has_legacy: bool = False
    has_semantic: bool = False
    for t in targets:
        cv = t.contract_version
        if cv:
            has_semantic = True
            contracts.add(cv)
        else:
            has_legacy = True
        rv = t.resolver_version
        if rv:
            resolvers.add(rv)

    if not targets:
        # Empty batch: legacy fence, no identity to disagree on.
        return {
            SEMANTIC_FENCE_KEY_CONTRACT: None,
            SEMANTIC_FENCE_KEY_RESOLVER: "legacy_open",
        }

    if has_legacy and has_semantic:
        raise SemanticFenceConstructionError(
            "semantic fence cannot mix legacy and semantic contract units",
        )
    if not has_semantic and not has_legacy:
        # Defensive: every target had None contract_version and no resolver.
        return {
            SEMANTIC_FENCE_KEY_CONTRACT: None,
            SEMANTIC_FENCE_KEY_RESOLVER: "legacy_open",
        }
    if not has_semantic:
        # Pure legacy batch.
        return {
            SEMANTIC_FENCE_KEY_CONTRACT: None,
            SEMANTIC_FENCE_KEY_RESOLVER: "legacy_open",
        }

    # has_semantic is True and has_legacy is False here.
    if len(contracts) > 1:
        raise SemanticFenceConstructionError(
            "semantic fence cannot mix contract versions across target units",
        )
    if len(resolvers) > 1:
        raise SemanticFenceConstructionError(
            "semantic fence cannot mix resolver versions across target units",
        )
    contract = next(iter(contracts))
    resolver = next(iter(resolvers)) if resolvers else LATEST_RESOLVER_VERSION
    return {
        SEMANTIC_FENCE_KEY_CONTRACT: contract,
        SEMANTIC_FENCE_KEY_RESOLVER: resolver,
    }


def compose_semantic_fingerprint_token(
    fence: Mapping[str, str | None],
    *,
    mode: AutomaticPolicyMode | str | None = None,
) -> str:
    """Fingerprint token: contract + resolver + frozen policy mode.

    Mode is part of the idempotency identity so off/shadow/enforce jobs never
    collide under the same strategy hash.
    """
    contract = fence.get(SEMANTIC_FENCE_KEY_CONTRACT) or "legacy"
    resolver = fence.get(SEMANTIC_FENCE_KEY_RESOLVER) or "legacy_open"
    frozen_mode = mode or fence.get(SEMANTIC_FENCE_KEY_MODE) or DEFAULT_AUTOMATIC_POLICY_MODE
    return f"sem:{contract}:{resolver}:mode:{frozen_mode}"


class SemanticLayerDisallowed(Exception):
    """Unit automatic policy forbids the requested automatic layer."""

    def __init__(self, message: str, *, code: str = SEMANTIC_LAYER_DISALLOWED_CODE) -> None:
        super().__init__(message)
        self.code = code


class SemanticFenceError(Exception):
    """Union of version mismatch / layer disallowed for worker handlers."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def is_semantic_fence_failure_code(code: str | None) -> bool:
    return bool(code) and code in SEMANTIC_FENCE_FAILURE_CODES


def build_semantic_fence_input_fields(
    fence: Mapping[str, str | None],
    *,
    layer: AutomaticLayerName | str,
    mode: AutomaticPolicyMode | str | None = None,
) -> dict[str, Any]:
    """Fields written into automatic job input_json / envelope_json.

    ``semantic_policy_mode`` is frozen at job creation and is the only mode
    workers may consult for that job.
    """
    frozen_mode = mode or fence.get(SEMANTIC_FENCE_KEY_MODE) or get_automatic_layer_policy_mode()
    frozen_mode = parse_automatic_policy_mode(frozen_mode)
    return {
        SEMANTIC_FENCE_KEY_CONTRACT: fence.get(SEMANTIC_FENCE_KEY_CONTRACT),
        SEMANTIC_FENCE_KEY_RESOLVER: fence.get(SEMANTIC_FENCE_KEY_RESOLVER),
        SEMANTIC_FENCE_KEY_LAYER: layer,
        SEMANTIC_FENCE_KEY_MODE: frozen_mode,
    }


def resolve_job_semantic_policy_mode(
    job_input: Mapping[str, Any] | None,
) -> AutomaticPolicyMode:
    """Mode frozen on the job, with legacy-missing-mode compatibility.

    - Job stamped mode → use it (validated).
    - Fence present but mode missing → ``LEGACY_MISSING_MODE_COMPAT`` (enforce),
      preserving pre-mode worker behaviour that applied allows(layer).
    - No fence at all → callers skip validation entirely before calling this.
    """
    job_input = job_input or {}
    raw = job_input.get(SEMANTIC_FENCE_KEY_MODE)
    if raw is None or raw == "":
        return LEGACY_MISSING_MODE_COMPAT
    return parse_automatic_policy_mode(raw)


def _job_claims_section_translation(
    *,
    job_input: Mapping[str, Any],
    operation_fingerprint: str | None,
) -> bool:
    """True when job presents any section-translation claim markers."""
    from .job_bootstrap import _fingerprint_matches_base
    from .section_lane import (
        SECTION_REQUEST_ORIGIN,
        TRANSLATION_SECTION_OPERATION_FINGERPRINT,
    )

    has_origin = job_input.get("request_origin") == SECTION_REQUEST_ORIGIN
    has_fp = bool(operation_fingerprint) and _fingerprint_matches_base(
        str(operation_fingerprint), TRANSLATION_SECTION_OPERATION_FINGERPRINT
    )
    return has_origin or has_fp


def is_trusted_explicit_section_translation_job(
    *,
    job_input: Mapping[str, Any] | None,
    operation_fingerprint: str | None,
    trusted_record_id: str | None = None,
    trusted_base_id: str | None = None,
    trusted_generation: int | None = None,
    trusted_target_key: str | None = None,
    trusted_loaded_unit_ids: Sequence[str] | None = None,
    trusted_base_ordered_units: Sequence[Any] | None = None,
    trusted_anchor_to_unit: Mapping[str, str] | None = None,
) -> bool:
    """Trusted USER_EXPLICIT section translation identity (no free-string alone).

    Requires ALL of:
      1. section fingerprint base + ``request_origin=section_v1``
      2. ``section_identity`` parses via ``parse_section_identity_mapping``
      3. identity record/base/generation match trusted DB job row
      4. identity range + anchors match trusted DB ``target_key``
      5. DB universe ``expand_closed_unit_range`` equals both
         trusted loaded unit ids and ``input_json.target_unit_ids``
         (order-exact)
      6. anchors both absent or both present; when present, DB
         ``anchor_segments`` map proves ownership of start/end units

    Missing trusted geometric facts → False (not trusted).
    """
    from .job_bootstrap import _fingerprint_matches_base
    from .section_identity import (
        SectionIdentityError,
        SectionUnit,
        decode_section_target_key,
        expand_closed_unit_range,
        parse_section_identity_mapping,
    )
    from .section_lane import (
        SECTION_REQUEST_ORIGIN,
        TRANSLATION_SECTION_OPERATION_FINGERPRINT,
    )

    job_input = job_input or {}
    if not operation_fingerprint:
        return False
    if not _fingerprint_matches_base(
        str(operation_fingerprint), TRANSLATION_SECTION_OPERATION_FINGERPRINT
    ):
        return False
    if job_input.get("request_origin") != SECTION_REQUEST_ORIGIN:
        return False
    if (
        trusted_record_id is None
        or trusted_base_id is None
        or trusted_generation is None
        or not trusted_target_key
        or not trusted_loaded_unit_ids
        or not trusted_base_ordered_units
    ):
        return False
    try:
        identity = parse_section_identity_mapping(job_input.get("section_identity"))
        key_start, key_end, key_sa, key_ea = decode_section_target_key(
            str(trusted_target_key)
        )
    except SectionIdentityError:
        return False
    if identity.record_id != str(trusted_record_id):
        return False
    if identity.base_id != str(trusted_base_id):
        return False
    if identity.generation != int(trusted_generation):
        return False
    if identity.start_unit_id != key_start or identity.end_unit_id != key_end:
        return False
    id_sa = identity.start_anchor_segment_id or None
    id_ea = identity.end_anchor_segment_id or None
    if id_sa != key_sa or id_ea != key_ea:
        return False

    # Anchors: both absent or both present (no half-claim).
    if (id_sa is None) != (id_ea is None):
        return False
    if id_sa is not None and id_ea is not None:
        ownership = trusted_anchor_to_unit or {}
        if ownership.get(id_sa) != identity.start_unit_id:
            return False
        if ownership.get(id_ea) != identity.end_unit_id:
            return False

    # Normalize universe to SectionUnit for expand_closed_unit_range.
    ordered: list[SectionUnit] = []
    for item in trusted_base_ordered_units:
        if isinstance(item, SectionUnit):
            ordered.append(item)
        elif isinstance(item, Mapping):
            ordered.append(
                SectionUnit(
                    unit_id=str(item["unit_id"]),
                    order_index=int(item["order_index"]),
                )
            )
        else:
            return False
    try:
        canonical = expand_closed_unit_range(
            start_unit_id=identity.start_unit_id,
            end_unit_id=identity.end_unit_id,
            ordered_units=ordered,
        )
    except SectionIdentityError:
        return False

    loaded = tuple(str(u) for u in trusted_loaded_unit_ids)
    if loaded != canonical:
        return False

    raw_targets = job_input.get("target_unit_ids")
    if raw_targets is None:
        return False
    declared = tuple(str(u) for u in list(raw_targets))
    if declared != canonical:
        return False
    return True


def validate_automatic_job_semantic_fence(
    *,
    job_input: Mapping[str, Any] | None,
    layer: AutomaticLayerName | str,
    unit_metadata_list: Sequence[Mapping[str, Any] | None],
    layers_any: Sequence[AutomaticLayerName] | None = None,
    operation_fingerprint: str | None = None,
    trusted_record_id: str | None = None,
    trusted_base_id: str | None = None,
    trusted_generation: int | None = None,
    trusted_target_key: str | None = None,
    trusted_loaded_unit_ids: Sequence[str] | None = None,
    trusted_base_ordered_units: Sequence[Any] | None = None,
    trusted_anchor_to_unit: Mapping[str, str] | None = None,
) -> None:
    """Shared pre-model fence for all automatic worker topologies.

    State flow:
      1. No fence keys → pre-feature job, skip entirely (compat).
      2. Fence present → ``automatic_layer_name`` must exist and equal the
         worker expected layer exactly (no ``grammar_bundle`` alias).
         ``layers_any`` only affects policy admission, not job identity.
      3. Per-unit contract/resolver always checked.
      4. Explicit section translation: only translation lane; full DB
         geometry bind; incomplete claim fail-closed.
      5. ``allows(layer)=false`` under frozen ``enforce`` skipped only for
         fully trusted section translation.

    Mode source of truth: ``job_input.semantic_policy_mode`` frozen at
    bootstrap. Missing mode with fence present → enforce (compat).

    Raises :class:`SemanticFenceError` with stable ``code``.
    """
    job_input = job_input or {}

    has_fence = (
        SEMANTIC_FENCE_KEY_RESOLVER in job_input
        or SEMANTIC_FENCE_KEY_CONTRACT in job_input
        or SEMANTIC_FENCE_KEY_LAYER in job_input
        or SEMANTIC_FENCE_KEY_MODE in job_input
    )
    if not has_fence:
        return

    frozen_mode = resolve_job_semantic_policy_mode(job_input)
    enforce_layer_admission = frozen_mode == "enforce"

    job_contract = job_input.get(SEMANTIC_FENCE_KEY_CONTRACT)
    job_resolver = job_input.get(SEMANTIC_FENCE_KEY_RESOLVER)
    job_layer = job_input.get(SEMANTIC_FENCE_KEY_LAYER)
    # Fail-closed layer identity: required whenever any fence key is present.
    if job_layer is None or job_layer == "":
        raise SemanticFenceError(
            "fenced automatic job is missing automatic_layer_name",
            code=SEMANTIC_POLICY_VERSION_MISMATCH_CODE,
        )
    if str(job_layer) != str(layer):
        raise SemanticFenceError(
            f"automatic layer name mismatch: job={job_layer!r} worker={layer!r}",
            code=SEMANTIC_POLICY_VERSION_MISMATCH_CODE,
        )

    # Explicit section exemption applies only to the translation lane and
    # never skips contract/resolver checks below.
    skip_allows_for_explicit_translation = False
    if str(layer) == "translation" and layers_any is None:
        claims_section = _job_claims_section_translation(
            job_input=job_input,
            operation_fingerprint=operation_fingerprint,
        )
        if claims_section:
            trusted = is_trusted_explicit_section_translation_job(
                job_input=job_input,
                operation_fingerprint=operation_fingerprint,
                trusted_record_id=trusted_record_id,
                trusted_base_id=trusted_base_id,
                trusted_generation=trusted_generation,
                trusted_target_key=trusted_target_key,
                trusted_loaded_unit_ids=trusted_loaded_unit_ids,
                trusted_base_ordered_units=trusted_base_ordered_units,
                trusted_anchor_to_unit=trusted_anchor_to_unit,
            )
            if not trusted:
                raise SemanticFenceError(
                    "section translation identity is incomplete or does not "
                    "bind to trusted job target fields",
                    code=SEMANTIC_POLICY_VERSION_MISMATCH_CODE,
                )
            skip_allows_for_explicit_translation = True

    for meta in unit_metadata_list:
        resolved = policy_from_unit_metadata(meta if isinstance(meta, Mapping) else None)
        unit_contract = resolved.contract_version
        unit_resolver = (
            "legacy_open" if resolved.is_legacy else resolved.resolver_version
        )
        norm_job_contract = (
            job_contract if job_contract not in (None, "", "legacy") else None
        )
        norm_job_resolver = job_resolver or "legacy_open"

        if norm_job_contract != unit_contract:
            raise SemanticFenceError(
                f"semantic contract mismatch: job={norm_job_contract!r} "
                f"unit={unit_contract!r}",
                code=SEMANTIC_POLICY_VERSION_MISMATCH_CODE,
            )
        if norm_job_resolver != unit_resolver and not (
            norm_job_resolver == "legacy_open" and resolved.is_legacy
        ):
            if norm_job_resolver != resolved.resolver_version:
                raise SemanticFenceError(
                    f"semantic resolver mismatch: job={norm_job_resolver!r} "
                    f"unit={unit_resolver!r}",
                    code=SEMANTIC_POLICY_VERSION_MISMATCH_CODE,
                )

        # Legacy units (no contract) remain fully automatic.
        if resolved.is_legacy:
            continue

        # off / shadow: do not intercept on allows=false.
        if not enforce_layer_admission:
            continue

        # Trusted USER_EXPLICIT section translation: only skip allows(false)
        # for the translation layer. contract/resolver already enforced.
        if skip_allows_for_explicit_translation:
            continue

        if layers_any:
            if not any(resolved.policy.allows(ly) for ly in layers_any):  # type: ignore[arg-type]
                raise SemanticFenceError(
                    f"automatic layers {list(layers_any)!r} disallowed by unit policy",
                    code=SEMANTIC_LAYER_DISALLOWED_CODE,
                )
        elif not resolved.policy.allows(layer):  # type: ignore[arg-type]
            raise SemanticFenceError(
                f"automatic layer {layer!r} disallowed by unit policy",
                code=SEMANTIC_LAYER_DISALLOWED_CODE,
            )


def filter_units_for_any_grammar(
    units: Sequence[Mapping[str, Any]],
    *,
    mode: AutomaticPolicyMode | None = None,
    record_id: str | None = None,
    generation: int | None = None,
) -> list[dict[str, Any]]:
    """Mode-aware filter for grammar_bundle / grammar-window (note OR sentence_analysis).

    Same off/shadow/enforce semantics as :func:`filter_units_for_automatic_layer`
    but a unit is kept when **either** grammar layer is allowed under enforce.
    """
    resolved_mode = get_automatic_layer_policy_mode(mode)
    if resolved_mode == "off":
        return [dict(u) for u in units]

    kept: list[dict[str, Any]] = []
    would_skip: list[str] = []
    resolver_versions: set[str] = set()
    for raw in units:
        unit = dict(raw)
        meta = unit.get("metadata_json")
        if not isinstance(meta, Mapping):
            meta = {}
        resolved = policy_from_unit_metadata(meta)
        resolver_versions.add(resolved.resolver_version)
        unit_id = str(unit.get("unit_id"))
        allowed = resolved.policy.grammar_note or resolved.policy.sentence_analysis
        if not allowed:
            would_skip.append(unit_id)
            if resolved_mode == "shadow":
                kept.append(unit)
            continue
        kept.append(unit)

    if would_skip:
        log_automatic_layer_shadow(
            record_id=record_id,
            generation=generation,
            resolver_version=",".join(sorted(resolver_versions)) or LATEST_RESOLVER_VERSION,
            layer="grammar_note",
            would_skip_unit_ids=would_skip,
            mode=resolved_mode,
        )
    return kept


# Back-compat alias used by the first translation fence wiring.
def validate_job_unit_semantic_fence(
    *,
    job_input: Mapping[str, Any] | None,
    unit_metadata_list: Sequence[Mapping[str, Any] | None],
    layer: AutomaticLayerName | str = "translation",
    operation_fingerprint: str | None = None,
    trusted_record_id: str | None = None,
    trusted_base_id: str | None = None,
    trusted_generation: int | None = None,
    trusted_target_key: str | None = None,
    trusted_loaded_unit_ids: Sequence[str] | None = None,
    trusted_base_ordered_units: Sequence[Any] | None = None,
    trusted_anchor_to_unit: Mapping[str, str] | None = None,
) -> None:
    try:
        validate_automatic_job_semantic_fence(
            job_input=job_input,
            layer=layer,
            unit_metadata_list=unit_metadata_list,
            operation_fingerprint=operation_fingerprint,
            trusted_record_id=trusted_record_id,
            trusted_base_id=trusted_base_id,
            trusted_generation=trusted_generation,
            trusted_target_key=trusted_target_key,
            trusted_loaded_unit_ids=trusted_loaded_unit_ids,
            trusted_base_ordered_units=trusted_base_ordered_units,
            trusted_anchor_to_unit=trusted_anchor_to_unit,
        )
    except SemanticFenceError as exc:
        if exc.code == SEMANTIC_LAYER_DISALLOWED_CODE:
            raise SemanticLayerDisallowed(str(exc), code=exc.code) from exc
        raise SemanticPolicyVersionMismatch(str(exc), code=exc.code) from exc


def log_automatic_layer_shadow(
    *,
    record_id: str | None,
    generation: int | None,
    resolver_version: str,
    layer: AutomaticLayerName,
    would_skip_unit_ids: Sequence[str],
    mode: str,
) -> None:
    """Aggregate structured log for shadow / enforce skip observability.

    Must not invent reader_events / reader_job_events types. Keys required
    by the design: record / generation / resolver / layer.
    """
    if not would_skip_unit_ids:
        return
    logger.info(
        "automatic_layer_policy_skip",
        extra={
            "event": "automatic_layer_policy_skip",
            "record_id": record_id,
            "generation": generation,
            "resolver_version": resolver_version,
            "layer": layer,
            "mode": mode,
            "would_skip_count": len(would_skip_unit_ids),
            "would_skip_unit_ids": list(would_skip_unit_ids)[:50],
        },
    )


def resolve_policy_for_stable_block(
    *,
    block_type: str | None,
    payload_json: Mapping[str, Any] | None,
    resolver_version: str | None = None,
) -> ResolvedAutomaticLayerPolicy:
    """Convenience for freeze/base_builder: resolve from a stable block payload."""
    return resolve_automatic_layer_policy(
        contract_version=extract_contract_version(payload_json),
        block_type=block_type,
        payload_json=payload_json,
        resolver_version=resolver_version,
    )


# Re-export for callers that only import this module.
__all__ = [
    "AUTOMATIC_LAYER_POLICY_RESOLVER_V1",
    "AUTOMATIC_LAYERS",
    "AutomaticLayerName",
    "AutomaticLayerPolicy",
    "AutomaticLayerTargetUnit",
    "AutomaticPolicyMode",
    "DEFAULT_AUTOMATIC_POLICY_MODE",
    "LATEST_RESOLVER_VERSION",
    "ResolvedAutomaticLayerPolicy",
    "SEMANTIC_FENCE_FAILURE_CODES",
    "SEMANTIC_FENCE_INCONSISTENT_CODE",
    "LEGACY_MISSING_MODE_COMPAT",
    "SEMANTIC_FENCE_KEY_CONTRACT",
    "SEMANTIC_FENCE_KEY_LAYER",
    "SEMANTIC_FENCE_KEY_MODE",
    "SEMANTIC_FENCE_KEY_RESOLVER",
    "SEMANTIC_LAYER_DISALLOWED_CODE",
    "SEMANTIC_POLICY_VERSION_MISMATCH_CODE",
    "SemanticFenceConstructionError",
    "SemanticFenceError",
    "SemanticLayerDisallowed",
    "SemanticPolicyVersionMismatch",
    "build_reading_unit_metadata_json",
    "build_semantic_fence_input_fields",
    "build_unit_semantic_metadata",
    "compose_semantic_fingerprint_token",
    "filter_units_for_any_grammar",
    "filter_units_for_automatic_layer",
    "generation_semantic_fence_from_targets",
    "get_automatic_layer_policy_mode",
    "is_semantic_fence_failure_code",
    "is_trusted_explicit_section_translation_job",
    "load_automatic_layer_targets",
    "log_automatic_layer_shadow",
    "materialize_target_units",
    "parse_automatic_policy_mode",
    "policy_from_unit_metadata",
    "read_unit_semantic_metadata",
    "resolve_automatic_layer_policy",
    "resolve_job_semantic_policy_mode",
    "resolve_policy_for_stable_block",
    "unit_allows_any_grammar",
    "unit_allows_automatic_layer",
    "validate_automatic_job_semantic_fence",
    "validate_job_unit_semantic_fence",
    "asdict",
]
