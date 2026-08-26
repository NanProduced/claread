"""Regression lock: caplog must keep capturing app.* logs after app.main import.

``app.main`` runs ``setup_logging()`` at import time (module-level side
effect), which attaches console/file handlers to the ``app`` logger tree and
disables its propagation to the root logger. pytest imports every test module
in one process during collection, so once ANY test module imports
``app.main``, every later caplog assertion on ``app.*`` loggers would silently
capture nothing (caplog captures through a root-logger handler) unless the
test infrastructure re-enables propagation per test. See
``tests/conftest.py`` for the isolation fixture this suite locks in.
"""

from __future__ import annotations

import logging

import pytest

from app.main import app

# Snapshot taken at module import, immediately after app.main (and therefore
# setup_logging) has run. Import-time capture matters: the per-test conftest
# fixture re-enables propagation during each test, so reading the live value
# inside a test would observe the isolated state, not the polluted one.
_APP_LOGGER_PROPAGATE_AT_IMPORT = logging.getLogger("app").propagate

_PROBE_LOGGER_NAME = "app.services.caplog_isolation_probe"
_PROBE_MESSAGE = "app logger caplog isolation probe"


def test_app_main_import_leaves_app_logger_non_propagating() -> None:
    # Documents the pollution source this regression suite guards against:
    # importing app.main leaves the "app" logger non-propagating for the
    # rest of the pytest process. If production logging semantics ever
    # change here, update the conftest isolation accordingly.
    assert app is not None
    assert _APP_LOGGER_PROPAGATE_AT_IMPORT is False


def test_app_records_reachable_by_caplog_after_app_main_import(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Even though app.main was imported earlier in this same pytest process
    # (module import above), app.* records must still reach caplog.
    logger = logging.getLogger(_PROBE_LOGGER_NAME)
    with caplog.at_level(logging.WARNING, logger=_PROBE_LOGGER_NAME):
        logger.warning(_PROBE_MESSAGE)

    # Match on the message, not record.name: the app's
    # BusinessModuleFormatter rewrites record.name in place while formatting
    # (the app console handler runs before the record propagates to the root
    # logger), so the captured record's name is no longer the dotted path.
    probe_records = [r for r in caplog.records if _PROBE_MESSAGE in r.getMessage()]
    assert [r.getMessage() for r in probe_records] == [_PROBE_MESSAGE]
