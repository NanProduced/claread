"""Shared Daily Reader teaching contracts (stdlib-only).

Single source of truth for the deterministic defense lines shared by the
services/api runtime and the evals stack (which imports this package via
a sys.path bootstrap — see ``evals/claread_eval/daily_reader``). Hard
constraint: stdlib only — no pydantic, no network, no DB — so both
virtualenvs can import it.
"""
