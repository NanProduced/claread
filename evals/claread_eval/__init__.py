"""Claread evaluation package (post-cutover).

The legacy Workflow / Node Lab / Eval Center runners, judges, bridges,
adapters, writers, reports and their schemas were physically deleted in the
CUTOVER-CONTROL-EVAL physical phase. The kept evaluation substrates are:

- ``claread_eval.reader_record_ask`` — Reader Record Ask evaluation.
- Vocabulary evaluation — ``graders.vocabulary``,
  ``loader.vocabulary_dataset_loader``, ``runner.vocabulary_runner``,
  ``schemas.vocabulary``.

This module intentionally re-exports nothing; import submodules directly
(e.g. ``from claread_eval.schemas.vocabulary import ...``).
"""
