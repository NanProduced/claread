"""Test-only deterministic Ask v2 acceptance runtime.

This package is test infrastructure. It is NEVER imported by ``app.main``
or any production module; production entry ``app.main:app`` cannot select
the deterministic runtime through any environment flag. The only entry
that installs it is ``deterministic_ask_e2e.app`` launched explicitly via
Uvicorn for cross-stack acceptance (see ``app.py`` docstring).

Layout:

- ``guard.py`` — fail-closed external provider guard (network surfaces).
- ``models.py`` — deterministic PydanticAI ``FunctionModel`` producing
  legal Ask v2 output grounded in server-minted ``evh_`` handles.
- ``execution.py`` — patches the Ask execution resolver so the real
  production stream runs the deterministic model; blocks the production
  auto-wire fallback.
- ``app.py`` — test-only Uvicorn app: real ``create_app()`` + guard +
  deterministic execution + guard-report diagnostic route.
- ``test_bootstrap.py`` / ``test_production_entry_clean.py`` — focused
  gates (no real DB).
- ``test_cross_stack_live.py`` — opt-in live Browser/BFF/API/PG
  acceptance (requires the test-only API, and optionally Web, running).
"""
