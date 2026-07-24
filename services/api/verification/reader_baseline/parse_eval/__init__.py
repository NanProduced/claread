"""``parse_eval`` — Task 5A-R1 split package.

This package replaces the previous monolithic
``parse_eval_artifact.py`` / ``parse_eval_gate.py`` with a
maintainable module layout:

- :mod:`.constants` — frozen version literals + forbidden key markers
- :mod:`.schema` — the ``reader_parse_eval_artifact.v1`` Pydantic
  contract (closed-schema, strictly typed)
- :mod:`.fixture_builder` — hermetic **fixture-only** producer that
  builds an artifact from a :class:`GoldenSample` for offline gate
  tests. This is NOT the official producer; it is explicitly marked
  as fixture-grade.
- :mod:`.reader_adapter` — the official API-side adapter that maps a
  ``ReaderPlateSnapshot`` + ``ReaderPipelineRunSummary`` into an
  artifact. This is the seam between the Reader runtime and the
  portable eval contract.
- :mod:`.gate` — the deterministic gate. Unlike the previous gate,
  ``run_gate`` receives a separate ``CanonicalTextEvidence`` so it can
  recompute the full-text SHA-256, UTF-16 length, and per-unit /
  per-segment FNV-1a hashes without embedding the full text in the
  artifact.
- :mod:`.legacy_sidecar` — legacy baseline freeze helpers (unavailable
  status builder + frozen baseline recorder for Task 5B).

All modules are hermetic for the offline path: no DB, no LLM, no
spaCy, no ``app`` runtime import. The reader_adapter imports the
Reader schemas at type-check time only (``TYPE_CHECKING``) and
accepts duck-typed objects at runtime so the artifact stays
portable.
"""

from __future__ import annotations
