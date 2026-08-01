"""Versioned translation prompt profiles for Reader Structured Source.

The semantic role and the automatic-layer policy answer different questions:
the policy decides whether an automatic layer may run, while this module
decides how an admitted translation should be phrased.  Keeping the two
seams separate prevents all T-only roles (for example headings and source
callouts) from silently sharing one prompt contract.

This resolver is deterministic and does not classify content with an LLM.
Unknown or legacy metadata deliberately falls back to the prose profile so
that legacy automatic behaviour remains fail-open.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from .semantic_classifier import SEMANTIC_CONTRACT_V1

TRANSLATION_PROMPT_PROFILE_VERSION: Final[str] = (
    "reader_translation_prompt_profile_v1"
)
TRANSLATION_PROMPT_PROFILE_CONTRACT_VERSION: Final[str] = (
    "reader_translation_prompt_profile_contract_v1"
)

TRANSLATION_PROFILE_PROSE: Final[str] = "prose"
TRANSLATION_PROFILE_HEADING: Final[str] = "heading"
TRANSLATION_PROFILE_QUOTATION: Final[str] = "quotation"
TRANSLATION_PROFILE_CITATION_REFERENCE: Final[str] = "citation_reference"
TRANSLATION_PROFILE_SOURCE_CALLOUT: Final[str] = "source_callout"
TRANSLATION_PROFILE_EXPLICIT_SECTION: Final[str] = "explicit_section"

TRANSLATION_PROMPT_PROFILE_IDS: Final[frozenset[str]] = frozenset(
    {
        TRANSLATION_PROFILE_PROSE,
        TRANSLATION_PROFILE_HEADING,
        TRANSLATION_PROFILE_QUOTATION,
        TRANSLATION_PROFILE_CITATION_REFERENCE,
        TRANSLATION_PROFILE_SOURCE_CALLOUT,
        TRANSLATION_PROFILE_EXPLICIT_SECTION,
    }
)


@dataclass(frozen=True, slots=True)
class TranslationPromptProfile:
    """A stable, auditable prompt contract for one translation role."""

    profile_id: str
    version: str
    prompt_lines: tuple[str, ...]

    @property
    def key(self) -> str:
        """Return the versioned cache/audit key for this profile."""

        return f"{self.version}:{self.profile_id}"

    @property
    def content_hash(self) -> str:
        """Return a stable hash of the prompt text behind this profile.

        The version is the intentional product-level cutover marker.  The
        content hash is an additional worker fence so an accidental edit
        without a version bump cannot reinterpret an already queued job.
        """

        canonical = json.dumps(
            {
                "profile_id": self.profile_id,
                "version": self.version,
                "prompt_lines": list(self.prompt_lines),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_PROFILE_PROMPT_LINES: Final[dict[str, tuple[str, ...]]] = {
    TRANSLATION_PROFILE_PROSE: (
        "Translate prose accurately and naturally into the target language.",
        "Preserve the source meaning, logical relations, modality, and reading-group boundaries.",
        "Return translation only; do not add teaching commentary or facts absent from the source.",
    ),
    TRANSLATION_PROFILE_HEADING: (
        "Translate this heading concisely as a heading, preserving its scope and emphasis.",
        "Do not turn the heading into a sentence, explanation, or added "
        "punctuation-heavy subtitle.",
    ),
    TRANSLATION_PROFILE_QUOTATION: (
        "Translate the quotation while preserving the quoted voice, modality, "
        "attribution, and wording distinctions.",
        "Do not add interpretation or rewrite the quotation as an explanation.",
    ),
    TRANSLATION_PROFILE_CITATION_REFERENCE: (
        "Translate citation prose conservatively while preserving authors, titles, "
        "years, journal or book names, identifiers, and URLs.",
        "Do not invent bibliographic facts or translate an identifier into a different value.",
    ),
    TRANSLATION_PROFILE_SOURCE_CALLOUT: (
        "Translate the source callout while preserving its tone, emphasis, "
        "paragraph/list structure, and reading order.",
        "Translate only the callout content; do not add a notice, explanation, "
        "or facts outside the callout.",
    ),
    TRANSLATION_PROFILE_EXPLICIT_SECTION: (
        "Translate this user-requested section in full, preserving source order, "
        "structure, and meaning.",
        "This explicit request is independent of automatic-layer policy; return "
        "translation only without commentary.",
    ),
}


def get_translation_prompt_profile(profile_id: str | None) -> TranslationPromptProfile:
    """Return a known profile, failing open to ``prose`` for unknown ids."""

    selected = profile_id if profile_id in TRANSLATION_PROMPT_PROFILE_IDS else (
        TRANSLATION_PROFILE_PROSE
    )
    return TranslationPromptProfile(
        profile_id=selected,
        version=TRANSLATION_PROMPT_PROFILE_VERSION,
        prompt_lines=_PROFILE_PROMPT_LINES[selected],
    )


def resolve_translation_prompt_profile(
    *,
    contract_version: object,
    block_type: object,
    content_role: object,
    explicit_section: bool = False,
) -> TranslationPromptProfile:
    """Resolve a profile from persisted semantic facts.

    ``explicit_section`` is checked first because USER_EXPLICIT section
    translation is admitted independently of the automatic policy.  A
    missing or unknown semantic contract is legacy/future data and therefore
    falls back to the prose profile rather than guessing a structural role.
    """

    if explicit_section:
        return get_translation_prompt_profile(TRANSLATION_PROFILE_EXPLICIT_SECTION)

    if contract_version != SEMANTIC_CONTRACT_V1:
        return get_translation_prompt_profile(TRANSLATION_PROFILE_PROSE)

    normalized_block_type = (
        block_type.strip().lower() if isinstance(block_type, str) else ""
    )
    normalized_role = (
        content_role.strip().lower() if isinstance(content_role, str) else ""
    )

    # Structural heading identity is carried by block_type because headings
    # intentionally have a null content_role in semantic_contract_v1.
    if normalized_block_type == "heading":
        return get_translation_prompt_profile(TRANSLATION_PROFILE_HEADING)
    if normalized_role == "quotation":
        return get_translation_prompt_profile(TRANSLATION_PROFILE_QUOTATION)
    if normalized_role == "citation_reference":
        return get_translation_prompt_profile(TRANSLATION_PROFILE_CITATION_REFERENCE)
    if normalized_role == "source_callout":
        return get_translation_prompt_profile(TRANSLATION_PROFILE_SOURCE_CALLOUT)

    # prose, list prose, prompt questions, link-only/code/table roles (which
    # are normally filtered out by automatic policy), and unknown future roles
    # all use the deterministic fail-open base contract when a translation
    # job nevertheless reaches this seam.
    return get_translation_prompt_profile(TRANSLATION_PROFILE_PROSE)


def resolve_translation_prompt_profile_for_unit(
    metadata_json: Mapping[str, Any] | None,
    *,
    block_type: object,
    explicit_section: bool = False,
) -> TranslationPromptProfile:
    """Resolve a profile from a ``reading_units.metadata_json`` value."""

    semantic: Mapping[str, Any] = {}
    if isinstance(metadata_json, Mapping):
        candidate = metadata_json.get("semantic")
        if isinstance(candidate, Mapping):
            semantic = candidate
    return resolve_translation_prompt_profile(
        contract_version=semantic.get("contract_version"),
        block_type=block_type,
        content_role=semantic.get("content_role"),
        explicit_section=explicit_section,
    )


def build_translation_prompt_profile_manifest(
    units: Sequence[Mapping[str, Any]],
    *,
    explicit_section: bool = False,
) -> list[dict[str, Any]]:
    """Build the ordered, auditable profile manifest for target units.

    ``units`` is intentionally a small DB-fact map rather than a model or
    prompt object.  The same pure function is used at bootstrap and worker
    load time, so a changed block role, profile registry, or prompt text
    produces a different manifest hash before any provider call.
    """

    ordered_units = sorted(
        units,
        key=lambda unit: (
            int(unit.get("order_index") or 0),
            str(unit.get("unit_id") or ""),
        ),
    )
    manifest: list[dict[str, Any]] = []
    for unit in ordered_units:
        unit_id = str(unit.get("unit_id") or "")
        if not unit_id:
            raise ValueError("translation profile manifest unit_id is required")
        metadata_raw = unit.get("metadata_json")
        metadata = metadata_raw if isinstance(metadata_raw, Mapping) else {}
        semantic_raw = metadata.get("semantic")
        semantic = semantic_raw if isinstance(semantic_raw, Mapping) else {}
        block_type = str(unit.get("unit_type") or unit.get("block_type") or "")
        content_role_raw = semantic.get("content_role")
        content_role = (
            str(content_role_raw) if isinstance(content_role_raw, str) else None
        )
        profile = resolve_translation_prompt_profile(
            contract_version=semantic.get("contract_version"),
            block_type=block_type,
            content_role=content_role,
            explicit_section=explicit_section,
        )
        manifest.append(
            {
                "unit_id": unit_id,
                "order_index": int(unit.get("order_index") or 0),
                "block_type": block_type,
                "content_role": content_role,
                "profile_id": profile.profile_id,
                "profile_version": profile.version,
                "profile_content_hash": profile.content_hash,
            }
        )
    return manifest


def build_translation_prompt_profile_contract(
    units: Sequence[Mapping[str, Any]],
    *,
    explicit_section: bool = False,
) -> dict[str, Any]:
    """Build the frozen profile contract written to a translation job."""

    manifest = build_translation_prompt_profile_manifest(
        units,
        explicit_section=explicit_section,
    )
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return {
        "contract_version": TRANSLATION_PROMPT_PROFILE_CONTRACT_VERSION,
        "profile_version": TRANSLATION_PROMPT_PROFILE_VERSION,
        "manifest": manifest,
        "manifest_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def translation_prompt_profile_input_fields(
    contract: Mapping[str, Any],
    *,
    fingerprint_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Flatten a frozen profile contract for JSONB input/envelope fields."""

    fields = {
        "translation_prompt_profile_contract_version": str(
            contract["contract_version"]
        ),
        "translation_prompt_profile_version": str(contract["profile_version"]),
        "translation_prompt_profile_manifest": list(contract["manifest"]),
        "translation_prompt_profile_manifest_hash": str(contract["manifest_hash"]),
    }
    if fingerprint_contract is not None:
        fields["translation_prompt_profile_fingerprint_hash"] = str(
            fingerprint_contract["manifest_hash"]
        )
    else:
        fields["translation_prompt_profile_fingerprint_hash"] = str(
            contract["manifest_hash"]
        )
    return fields


def compose_translation_prompt_profile_fingerprint_token(
    contract: Mapping[str, Any],
) -> str:
    """Return the operation-fingerprint token for a frozen profile contract."""

    return (
        "prompt_profile:"
        f"{contract['contract_version']}:"
        f"{contract['profile_version']}:"
        f"{contract['manifest_hash']}"
    )


__all__ = [
    "TRANSLATION_PROFILE_CITATION_REFERENCE",
    "TRANSLATION_PROFILE_EXPLICIT_SECTION",
    "TRANSLATION_PROFILE_HEADING",
    "TRANSLATION_PROFILE_PROSE",
    "TRANSLATION_PROFILE_QUOTATION",
    "TRANSLATION_PROFILE_SOURCE_CALLOUT",
    "TRANSLATION_PROMPT_PROFILE_CONTRACT_VERSION",
    "TRANSLATION_PROMPT_PROFILE_IDS",
    "TRANSLATION_PROMPT_PROFILE_VERSION",
    "TranslationPromptProfile",
    "build_translation_prompt_profile_contract",
    "build_translation_prompt_profile_manifest",
    "compose_translation_prompt_profile_fingerprint_token",
    "get_translation_prompt_profile",
    "resolve_translation_prompt_profile",
    "resolve_translation_prompt_profile_for_unit",
    "translation_prompt_profile_input_fields",
]
