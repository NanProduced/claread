"""Shared refinement field addressing and frozen derivation-field pre-check.

Single implementation consumed by the production workflow and the evals
runner. Stdlib only — no pydantic, network, or DB.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

# Inputs of derive_translation_unit_ids (prototype.py) that refinement may
# address. Traced from the function signature plus both production/evals
# callers — not from the R2 narrative list.
FROZEN_TOP_LEVEL_FIELDS = frozenset({"effective_difficulty", "high_difficulty_unit_ids"})
FROZEN_NESTED_ATTRS: dict[str, frozenset[str]] = {
    "comprehension_checkpoints": frozenset({"evidence_paragraph_ids"}),
    "language_targets": frozenset({"paragraph_id"}),
    "sentence_maps": frozenset({"paragraph_id"}),
}

BLUEPRINT_ONLY_FIELDS = (
    "article_type",
    "effective_difficulty",
    "title_zh",
    "subtitle_zh",
    "tags_zh",
    "reading_mission",
    "reading_mission_stance",
    "learning_objectives",
    "structure_map",
    "selected_paragraph_ids",
)
PACKAGE_ONLY_FIELDS = (
    "high_difficulty_unit_ids",
    "language_targets",
    "sentence_maps",
    "translations_by_paragraph_id",
)
DUAL_CONTAINER_FIELDS = ("comprehension_checkpoints", "transfer_task")

REVIEW_FROZEN_DERIVATION_CONTRACT = (
    "Derivation-input fields are frozen at refinement and may only justify FAIL, never a patch: "
    "blueprint.effective_difficulty; learning_package.high_difficulty_unit_ids; "
    "comprehension_checkpoints.*.evidence_paragraph_ids; language_targets.*.paragraph_id; "
    "sentence_maps.*.paragraph_id. Issue.field must name a content field. "
    "Top-level field ownership: blueprint only: article_type, effective_difficulty, title_zh, "
    "subtitle_zh, tags_zh, reading_mission, reading_mission_stance, learning_objectives, "
    "structure_map, selected_paragraph_ids; learning_package only: high_difficulty_unit_ids, "
    "language_targets, sentence_maps, translations_by_paragraph_id; both containers (prefix "
    "selects the copy): comprehension_checkpoints, transfer_task. "
    "lesson_blueprint aliases blueprint."
)

_CONTAINER_PREFIXES = frozenset({"learning_package", "blueprint", "lesson_blueprint"})
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def root_of(text: str) -> str:
    return re.split(r"[.\[]", text, maxsplit=1)[0]


def _path_after_prefix(raw_field: str) -> str:
    prefix, dot, rest = raw_field.partition(".")
    if dot and prefix in _CONTAINER_PREFIXES:
        return rest
    return raw_field


def derivation_freeze_loc(raw_field: str) -> tuple[str, ...] | None:
    """Normalize an issue/patch path to a freeze loc, or None if content.

    Nested parents (`language_targets`, `sentence_maps`,
    `comprehension_checkpoints`) freeze when the path names a frozen
    attribute *or* stops at the parent/item (R7: `language_targets[2]`
    has no ident after the root, then the patch mutated `paragraph_id`).
    A content leaf (`meaning_zh`) is not a freeze.
    """
    path = _path_after_prefix(raw_field)
    idents = _IDENT_RE.findall(path)
    if not idents:
        return None
    root = idents[0]
    if root in FROZEN_TOP_LEVEL_FIELDS:
        return (root,)
    nested = FROZEN_NESTED_ATTRS.get(root)
    if not nested:
        return None
    tails = idents[1:]
    frozen_tails = tuple(ident for ident in tails if ident in nested)
    if frozen_tails:
        return (root, *frozen_tails)
    # `language_targets[2]` has no ident after the root (the index is
    # digits). Treat that item path as freeze-closed; a bare parent
    # (`language_targets`) remains a content field for whole-list patches
    # that do not touch frozen attrs.
    if not tails and re.search(r"\[\d+\]", path):
        return (root, *sorted(nested))
    return None


def is_frozen_derivation_path(raw_field: str) -> bool:
    return derivation_freeze_loc(raw_field) is not None


def collect_fields_to_fix(
    issues: Sequence[Mapping[str, Any]],
    package: Mapping[str, Any],
    blueprint: Mapping[str, Any],
) -> tuple[dict[str, Any], str | None, str | None]:
    """Resolve issue.field paths into a top-level fields_to_fix map.

    Returns (fields, error_code, raw_field). error_code is
    ``refinement_field_unknown`` or ``frozen_derivation_field``.
    """
    fields_to_fix: dict[str, Any] = {}
    for issue in issues:
        raw_field = str(issue.get("field", ""))
        prefix, dot, rest = raw_field.partition(".")
        if dot and prefix == "learning_package":
            container, top_field = package, root_of(rest)
        elif dot and prefix in ("blueprint", "lesson_blueprint"):
            container, top_field = blueprint, root_of(rest)
        else:
            top_field = root_of(raw_field)
            if top_field in package:
                container = package
            elif top_field in blueprint:
                container = blueprint
            else:
                container = None
        if container is None or top_field not in container:
            return {}, "refinement_field_unknown", raw_field
        if is_frozen_derivation_path(raw_field):
            return {}, "frozen_derivation_field", raw_field
        if top_field in fields_to_fix:
            continue
        fields_to_fix[top_field] = json.loads(json.dumps(container[top_field]))
    return fields_to_fix, None, None


def _nested_projection(value: Any, attrs: frozenset[str]) -> list[Any]:
    if not isinstance(value, list):
        return [("non-list", value)]
    projected: list[Any] = []
    for item in value:
        if not isinstance(item, Mapping):
            projected.append(("non-map", item))
        else:
            projected.append(tuple((key, item.get(key)) for key in sorted(attrs)))
    return projected


def preapply_patch_violations(
    patch: Mapping[str, Any],
    package: Mapping[str, Any],
    blueprint: Mapping[str, Any],
    fields_to_fix: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Unknown target, allowlist, and frozen derivation-input patch checks."""
    violations: list[dict[str, Any]] = []
    for key in sorted(patch):
        if key not in package and key not in blueprint:
            violations.append({"container": None, "error_type": "unknown_target", "loc": [key]})
            continue
        container_name = "learning_package" if key in package else "blueprint"
        if key not in fields_to_fix:
            violations.append(
                {"container": container_name, "error_type": "outside_allowlist", "loc": [key]}
            )
            continue
        if key in FROZEN_TOP_LEVEL_FIELDS:
            violations.append(
                {
                    "container": container_name,
                    "error_type": "frozen_derivation_field",
                    "loc": [key],
                }
            )
            continue
        nested = FROZEN_NESTED_ATTRS.get(key)
        current = package[key] if key in package else blueprint[key]
        if nested and _nested_projection(current, nested) != _nested_projection(patch[key], nested):
            loc = derivation_freeze_loc(f"{key}.{next(iter(sorted(nested)))}")
            violations.append(
                {
                    "container": container_name,
                    "error_type": "frozen_derivation_field",
                    "loc": list(loc) if loc else [key, *sorted(nested)],
                }
            )
    return violations
