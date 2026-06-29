"""Focused tests for the Reader Orchestration variant-first strategy resolver.

These tests do NOT call any worker. They only exercise the resolver Interface
and assert structural completeness, fail-closed behavior, hash determinism,
and representative prompt-fragment differences between variants.
"""

from __future__ import annotations

from copy import deepcopy
from types import MappingProxyType

import pytest

from app.schemas.reader_orchestration import (
    READER_ORCHESTRATION_GOAL_VARIANT_MAP,
)
from app.services.reader_orchestration import (
    READER_VARIANT_POLICY_SCHEMA_VERSION,
    READER_VARIANT_POLICY_VERSION,
    READER_VARIANT_REQUIRED_LAYERS,
    ReaderStrategyResolverError,
    ReaderVariantStrategy,
    load_reader_variant_policy_doc,
    resolve_reader_variant_strategy,
)

# All legal (goal, variant) pairs in the new orchestration scope.
LEGAL_PAIRS: list[tuple[str, str]] = [
    (goal, variant)
    for goal, variants in READER_ORCHESTRATION_GOAL_VARIANT_MAP.items()
    for variant in sorted(variants)
]


# ---------------------------------------------------------------------------#
# Structural completeness: every legal variant resolves with 4 layers.
# ---------------------------------------------------------------------------#


@pytest.mark.parametrize("goal,variant", LEGAL_PAIRS)
def test_every_legal_variant_resolves_with_all_four_layers(
    goal: str, variant: str
) -> None:
    strategy = resolve_reader_variant_strategy(goal, variant)

    assert isinstance(strategy, ReaderVariantStrategy)
    assert strategy.reading_goal == goal
    assert strategy.reading_variant == variant
    assert strategy.strategy_version == READER_VARIANT_POLICY_VERSION
    assert strategy.profile_id
    assert isinstance(strategy.annotation_density, int)
    assert strategy.annotation_density >= 0
    assert strategy.strategy_hash
    assert len(strategy.strategy_hash) == 64  # sha256 hex

    assert set(strategy.layers.keys()) == set(READER_VARIANT_REQUIRED_LAYERS)
    for layer_name in READER_VARIANT_REQUIRED_LAYERS:
        layer = strategy.layers[layer_name]
        assert layer.prompt_lines  # non-empty tuple
        assert all(isinstance(line, str) and line for line in layer.prompt_lines)
        assert layer.policy_hash
        assert len(layer.policy_hash) == 64


def test_resolver_loads_policy_doc_from_default_file() -> None:
    doc = load_reader_variant_policy_doc()
    assert doc["strategy_version"] == READER_VARIANT_POLICY_VERSION
    assert isinstance(doc["variants"], dict)
    # Every legal variant has an explicit entry.
    for _goal, variant in LEGAL_PAIRS:
        assert variant in doc["variants"]


# ---------------------------------------------------------------------------#
# Fail-closed: academic, cross-goal pairs, unknown variants, missing layers.
# ---------------------------------------------------------------------------#


@pytest.mark.parametrize(
    "goal,variant",
    [
        ("academic", "academic_general"),
        ("academic", "beginner_reading"),
        ("academic_general", "beginner_reading"),
    ],
)
def test_academic_fails_closed(goal: str, variant: str) -> None:
    with pytest.raises(ReaderStrategyResolverError, match="not supported"):
        resolve_reader_variant_strategy(goal, variant)


@pytest.mark.parametrize(
    "goal,variant",
    [
        ("daily_reading", "gaokao"),
        ("exam", "beginner_reading"),
        ("exam", "intensive_reading"),
        ("daily_reading", "ielts_toefl"),
    ],
)
def test_cross_goal_pairs_fail_closed(goal: str, variant: str) -> None:
    with pytest.raises(ReaderStrategyResolverError, match="does not belong"):
        resolve_reader_variant_strategy(goal, variant)


def test_unknown_variant_fails_closed() -> None:
    with pytest.raises(ReaderStrategyResolverError, match="does not belong"):
        resolve_reader_variant_strategy("daily_reading", "totally_unknown_variant")


def test_missing_variant_entry_fails_closed() -> None:
    """A policy doc that omits a legal variant must fail closed, not fall back."""
    doc = load_reader_variant_policy_doc()
    trimmed = deepcopy(doc)
    del trimmed["variants"]["cet"]
    with pytest.raises(ReaderStrategyResolverError, match="no explicit entry"):
        resolve_reader_variant_strategy("exam", "cet", policy_doc=trimmed)


def test_missing_layer_fails_closed() -> None:
    """A variant entry missing a required layer must fail closed."""
    doc = load_reader_variant_policy_doc()
    trimmed = deepcopy(doc)
    del trimmed["variants"]["gaokao"]["layers"]["grammar_bundle"]
    with pytest.raises(ReaderStrategyResolverError, match="missing required layer"):
        resolve_reader_variant_strategy("exam", "gaokao", policy_doc=trimmed)


def test_empty_layer_lines_fail_closed() -> None:
    """A layer with an empty lines list must fail closed."""
    doc = load_reader_variant_policy_doc()
    trimmed = deepcopy(doc)
    trimmed["variants"]["cet"]["layers"]["vocabulary"]["lines"] = []
    with pytest.raises(ReaderStrategyResolverError, match="non-empty 'lines'"):
        resolve_reader_variant_strategy("exam", "cet", policy_doc=trimmed)


def test_variant_declared_goal_mismatch_fails_closed() -> None:
    """If the YAML entry declares a different goal than the request, fail."""
    doc = load_reader_variant_policy_doc()
    trimmed = deepcopy(doc)
    trimmed["variants"]["gaokao"]["reading_goal"] = "daily_reading"
    with pytest.raises(ReaderStrategyResolverError, match="declares reading_goal"):
        resolve_reader_variant_strategy("exam", "gaokao", policy_doc=trimmed)


# ---------------------------------------------------------------------------#
# Hash determinism and differentiation.
# ---------------------------------------------------------------------------#


def test_strategy_hash_is_deterministic_across_calls() -> None:
    s1 = resolve_reader_variant_strategy("exam", "cet")
    s2 = resolve_reader_variant_strategy("exam", "cet")
    assert s1.strategy_hash == s2.strategy_hash
    for layer in READER_VARIANT_REQUIRED_LAYERS:
        assert s1.layers[layer].policy_hash == s2.layers[layer].policy_hash


def test_daily_and_exam_variant_hashes_differ() -> None:
    daily = resolve_reader_variant_strategy("daily_reading", "intermediate_reading")
    exam = resolve_reader_variant_strategy("exam", "cet")
    assert daily.strategy_hash != exam.strategy_hash


def test_different_variants_within_same_goal_have_different_hashes() -> None:
    variants = [
        resolve_reader_variant_strategy("exam", v)
        for v in ("gaokao", "cet", "kaoyan", "tem", "ielts_toefl")
    ]
    hashes = {s.strategy_hash for s in variants}
    assert len(hashes) == len(variants)


# ---------------------------------------------------------------------------#
# Representative prompt-fragment differences.
# ---------------------------------------------------------------------------#


def test_exam_grammar_bundle_lines_differ_between_cet_gaokao_kaoyan() -> None:
    cet = resolve_reader_variant_strategy("exam", "cet")
    gaokao = resolve_reader_variant_strategy("exam", "gaokao")
    kaoyan = resolve_reader_variant_strategy("exam", "kaoyan")

    cet_lines = cet.layers["grammar_bundle"].prompt_lines
    gaokao_lines = gaokao.layers["grammar_bundle"].prompt_lines
    kaoyan_lines = kaoyan.layers["grammar_bundle"].prompt_lines

    assert cet_lines != gaokao_lines
    assert cet_lines != kaoyan_lines
    assert gaokao_lines != kaoyan_lines

    # Representative short fragments that distinguish the three exam variants.
    assert any("四六级" in line for line in cet_lines)
    assert any("高考" in line for line in gaokao_lines)
    assert any("考研" in line for line in kaoyan_lines)


def test_daily_vocabulary_lines_differ_across_three_variants() -> None:
    beginner = resolve_reader_variant_strategy("daily_reading", "beginner_reading")
    intermediate = resolve_reader_variant_strategy(
        "daily_reading", "intermediate_reading"
    )
    intensive = resolve_reader_variant_strategy("daily_reading", "intensive_reading")

    beg_lines = beginner.layers["vocabulary"].prompt_lines
    int_lines = intermediate.layers["vocabulary"].prompt_lines
    ins_lines = intensive.layers["vocabulary"].prompt_lines

    assert beg_lines != int_lines
    assert beg_lines != ins_lines
    assert int_lines != ins_lines


def test_daily_grammar_bundle_lines_differ_across_three_variants() -> None:
    beginner = resolve_reader_variant_strategy("daily_reading", "beginner_reading")
    intermediate = resolve_reader_variant_strategy(
        "daily_reading", "intermediate_reading"
    )
    intensive = resolve_reader_variant_strategy("daily_reading", "intensive_reading")

    beg_lines = beginner.layers["grammar_bundle"].prompt_lines
    int_lines = intermediate.layers["grammar_bundle"].prompt_lines
    ins_lines = intensive.layers["grammar_bundle"].prompt_lines

    assert beg_lines != int_lines
    assert beg_lines != ins_lines
    assert int_lines != ins_lines


def test_each_variant_has_distinct_profile_id() -> None:
    profile_ids = {
        resolve_reader_variant_strategy(goal, variant).profile_id
        for goal, variant in LEGAL_PAIRS
    }
    assert len(profile_ids) == len(LEGAL_PAIRS)


def test_ask_layer_has_variant_specific_fragment() -> None:
    """The ask layer must carry a variant-specific marker, not a generic line."""
    cet = resolve_reader_variant_strategy("exam", "cet")
    gaokao = resolve_reader_variant_strategy("exam", "gaokao")

    cet_ask = " ".join(cet.layers["ask"].prompt_lines)
    gaokao_ask = " ".join(gaokao.layers["ask"].prompt_lines)

    assert "四六级" in cet_ask
    assert "高考" in gaokao_ask
    assert cet_ask != gaokao_ask


# ---------------------------------------------------------------------------#
# Layer policy hash isolation.
# ---------------------------------------------------------------------------#


def test_layer_policy_hash_changes_when_lines_change() -> None:
    doc = load_reader_variant_policy_doc()
    trimmed = deepcopy(doc)
    original = resolve_reader_variant_strategy("exam", "cet", policy_doc=doc)
    trimmed["variants"]["cet"]["layers"]["translation"]["lines"].append(
        "额外测试行。"
    )
    modified = resolve_reader_variant_strategy("exam", "cet", policy_doc=trimmed)

    assert (
        original.layers["translation"].policy_hash
        != modified.layers["translation"].policy_hash
    )
    assert original.strategy_hash != modified.strategy_hash
    # Unchanged layers keep the same hash.
    assert (
        original.layers["vocabulary"].policy_hash
        == modified.layers["vocabulary"].policy_hash
    )


# ---------------------------------------------------------------------------#
# Policy document top-level shape validation (shared helper).
# ---------------------------------------------------------------------------#


def test_policy_doc_schema_version_mismatch_fails_closed() -> None:
    """A policy_doc with the wrong schema_version must fail closed."""
    doc = load_reader_variant_policy_doc()
    bad = deepcopy(doc)
    bad["schema_version"] = READER_VARIANT_POLICY_SCHEMA_VERSION + 1
    with pytest.raises(
        ReaderStrategyResolverError, match="schema_version mismatch"
    ):
        resolve_reader_variant_strategy(
            "exam", "cet", policy_doc=bad
        )


def test_policy_doc_schema_version_missing_fails_closed() -> None:
    """A policy_doc that omits schema_version must fail closed."""
    doc = load_reader_variant_policy_doc()
    bad = deepcopy(doc)
    del bad["schema_version"]
    with pytest.raises(
        ReaderStrategyResolverError, match="schema_version mismatch"
    ):
        resolve_reader_variant_strategy(
            "exam", "cet", policy_doc=bad
        )


def test_policy_doc_strategy_version_mismatch_fails_closed() -> None:
    """A policy_doc with the wrong strategy_version must fail closed."""
    doc = load_reader_variant_policy_doc()
    bad = deepcopy(doc)
    bad["strategy_version"] = "reader_variant_policy_v2"
    with pytest.raises(
        ReaderStrategyResolverError, match="strategy_version mismatch"
    ):
        resolve_reader_variant_strategy(
            "exam", "cet", policy_doc=bad
        )


def test_policy_doc_strategy_version_missing_fails_closed() -> None:
    """A policy_doc that omits strategy_version must fail closed."""
    doc = load_reader_variant_policy_doc()
    bad = deepcopy(doc)
    del bad["strategy_version"]
    with pytest.raises(
        ReaderStrategyResolverError, match="strategy_version mismatch"
    ):
        resolve_reader_variant_strategy(
            "exam", "cet", policy_doc=bad
        )


def test_policy_doc_variants_not_a_mapping_fails_closed() -> None:
    """A policy_doc whose variants is a list must fail closed."""
    doc = load_reader_variant_policy_doc()
    bad = deepcopy(doc)
    bad["variants"] = [{"cet": {}}]
    with pytest.raises(
        ReaderStrategyResolverError, match="'variants' mapping"
    ):
        resolve_reader_variant_strategy(
            "exam", "cet", policy_doc=bad
        )


def test_policy_doc_variants_missing_fails_closed() -> None:
    """A policy_doc that omits variants must fail closed."""
    doc = load_reader_variant_policy_doc()
    bad = deepcopy(doc)
    del bad["variants"]
    with pytest.raises(
        ReaderStrategyResolverError, match="'variants' mapping"
    ):
        resolve_reader_variant_strategy(
            "exam", "cet", policy_doc=bad
        )


def test_policy_doc_top_level_not_a_mapping_fails_closed() -> None:
    """A policy_doc that is not a mapping at all must fail closed."""
    with pytest.raises(
        ReaderStrategyResolverError, match="must be a mapping at the top"
    ):
        resolve_reader_variant_strategy(
            "exam", "cet", policy_doc=[("variants", {})]  # type: ignore[arg-type]
        )


def test_default_loader_also_validates_schema_version() -> None:
    """The default file loader must enforce schema_version too. We can't
    easily corrupt the on-disk file, but we can confirm the loaded doc
    carries the expected schema_version."""
    doc = load_reader_variant_policy_doc()
    assert doc["schema_version"] == READER_VARIANT_POLICY_SCHEMA_VERSION
    assert doc["strategy_version"] == READER_VARIANT_POLICY_VERSION


# ---------------------------------------------------------------------------#
# ReaderVariantStrategy.layers immutability.
# ---------------------------------------------------------------------------#


def test_strategy_layers_is_a_mapping_proxy() -> None:
    """Resolved ``layers`` must be a read-only MappingProxyType view."""
    strategy = resolve_reader_variant_strategy("exam", "cet")
    assert isinstance(strategy.layers, MappingProxyType)


def test_strategy_layers_rejects_setitem() -> None:
    strategy = resolve_reader_variant_strategy("exam", "cet")
    with pytest.raises(TypeError):
        strategy.layers["translation"] = strategy.layers["vocabulary"]  # type: ignore[index]


def test_strategy_layers_rejects_delitem() -> None:
    strategy = resolve_reader_variant_strategy("exam", "cet")
    with pytest.raises(TypeError):
        del strategy.layers["translation"]  # type: ignore[arg-type]


def test_strategy_layers_rejects_clear() -> None:
    strategy = resolve_reader_variant_strategy("exam", "cet")
    with pytest.raises((TypeError, AttributeError)):
        strategy.layers.clear()  # type: ignore[attr-defined]


def test_strategy_layers_rejects_pop() -> None:
    strategy = resolve_reader_variant_strategy("exam", "cet")
    with pytest.raises((TypeError, AttributeError)):
        strategy.layers.pop("translation")  # type: ignore[attr-defined]


def test_strategy_layers_rejects_update() -> None:
    strategy = resolve_reader_variant_strategy("exam", "cet")
    with pytest.raises((TypeError, AttributeError)):
        strategy.layers.update({"translation": strategy.layers["vocabulary"]})  # type: ignore[attr-defined]


def test_strategy_layers_detached_from_policy_doc_mutations() -> None:
    """After resolution, mutating the supplied policy_doc must not affect
    the already-resolved strategy's layers or hashes."""
    doc = load_reader_variant_policy_doc()
    strategy = resolve_reader_variant_strategy("exam", "cet", policy_doc=doc)
    original_hash = strategy.strategy_hash
    original_translation_hash = strategy.layers["translation"].policy_hash
    original_lines = strategy.layers["translation"].prompt_lines

    # Mutate the policy doc after resolution.
    doc["variants"]["cet"]["layers"]["translation"]["lines"].append(
        "恶意追加行。"
    )

    # Resolved strategy is unaffected.
    assert strategy.strategy_hash == original_hash
    assert (
        strategy.layers["translation"].policy_hash == original_translation_hash
    )
    assert strategy.layers["translation"].prompt_lines == original_lines
