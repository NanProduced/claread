"""Helpers shared between the baseline CLI and the focused tests.

The CLI is a top-level script under ``services/api/scripts/``; we
still want the test suite to be able to call into it without
duplicating the logic. Anything in this module is part of the
baseline harness and is observation-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from verification.reader_baseline.golden_samples import GoldenSample


@dataclass(frozen=True, slots=True)
class ReadingMetadataOverrides:
    """Minimal shape used by :func:`resolve_reading_metadata`.

    The CLI builds one of these from its own ``CliArgs``; the test
    suite builds one with only the two fields the helper actually
    reads. Anything else is ignored.
    """

    reading_goal: str | None
    reading_variant: str | None


def resolve_reading_metadata(
    *,
    sample: GoldenSample,
    overrides: ReadingMetadataOverrides,
) -> tuple[str, str]:
    """Pick the (reading_goal, reading_variant) the chain runs with.

    ``overrides`` wins over the per-sample manifest, which wins over
    the chain default ``("daily_reading", "intermediate_reading")``.
    The chain then runs with the resolved pair, so the baseline
    report reflects the metadata the run actually used.
    """
    goal = overrides.reading_goal or sample.reading_goal
    variant = overrides.reading_variant or sample.reading_variant
    return goal, variant


def _read_overrides(args: Any) -> ReadingMetadataOverrides:
    """Adapt a ``CliArgs`` instance (or any object with the two
    fields) into a :class:`ReadingMetadataOverrides` so the CLI
    and the tests can call the same helper.
    """
    return ReadingMetadataOverrides(
        reading_goal=getattr(args, "reading_goal", None),
        reading_variant=getattr(args, "reading_variant", None),
    )


__all__ = [
    "ReadingMetadataOverrides",
    "resolve_reading_metadata",
]
