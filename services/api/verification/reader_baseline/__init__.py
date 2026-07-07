"""Reader baseline comparison support library.

This package is a read-only observation harness for the Reader Agentic
Orchestration refactor. It does not modify business logic. It loads the
fixed golden sample set, runs the new orchestration chain via the
existing dev-only smoke harness, and produces comparable metrics
between the new chain and the legacy ``article_analysis`` workflow
contract.

Layout:

- ``golden_samples`` -- loaders for the corpus under
  ``verification/golden_samples``.
- ``new_chain`` -- extractors for new orchestration metrics
  (``ReaderPipelineRunSummary`` and ``ReaderPlateSnapshot``).
- ``old_chain`` -- contract introspection for the legacy
  ``article_analysis`` workflow. The legacy chain has no
  deterministic fake executor, so end-to-end execution requires a
  real LLM credential. The introspection is always available; the
  end-to-end path is opt-in via ``READER_BASELINE_REAL_LLM=1``.
- ``report`` -- render a structured JSON / Markdown comparison
  record.

The package is intentionally import-safe: importing it does not
initialise the database pool or call any LLM.
"""

from __future__ import annotations

__all__ = [
    "golden_samples",
    "new_chain",
    "old_chain",
    "report",
    "schema_setup",
    "cli_helpers",
]
