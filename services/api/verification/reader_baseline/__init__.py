"""Reader baseline comparison support library.

This package is a read-only observation harness for the Reader Agentic
Orchestration refactor. It does not modify business logic. It loads the
fixed golden sample set, runs the new orchestration chain via the
existing dev-only smoke harness, and produces structured metrics
for the new chain over a fixed golden sample corpus.

Layout:

- ``golden_samples`` -- loaders for the corpus under
  ``verification/golden_samples``.
- ``new_chain`` -- extractors for new orchestration metrics
  (``ReaderPipelineRunSummary`` and ``ReaderPlateSnapshot``).
- ``report`` -- render a structured JSON / Markdown comparison
  record.

The package is intentionally import-safe: importing it does not
initialise the database pool or call any LLM.
"""

from __future__ import annotations

__all__ = [
    "golden_samples",
    "new_chain",
    "report",
    "schema_setup",
    "cli_helpers",
]
