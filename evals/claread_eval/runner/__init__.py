"""Evaluation runners (post-cutover).

The legacy config-driven / manual-case / simple runners and the CLI
entrypoint were physically deleted. The only kept runner is
:mod:`claread_eval.runner.vocabulary_runner`; import it directly.
"""
