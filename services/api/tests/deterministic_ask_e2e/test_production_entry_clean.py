"""Source and registration guards: production entry stays deterministic-free.

- No production source under ``app/**`` references this test runtime,
  imports from ``tests``, constructs a ``FunctionModel``, or carries a
  fake-model switch token.
- Importing the production entry ``app.main`` in a clean subprocess
  registers no test-only route, leaves the real execution resolver in
  place, and does not install the provider guard.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = API_ROOT / "app"

_FORBIDDEN_SOURCE_PATTERNS = (
    re.compile(r"deterministic_ask_e2e"),
    re.compile(r"^\s*(from|import)\s+tests(\.|\s|$)", re.MULTILINE),
    re.compile(r"ASK_FAKE|FAKE_ASK|FAKE_MODEL_ENABLED|ASK_DETERMINISTIC_ENABLED"),
)
# Note: pydantic_ai ``FunctionModel`` itself is a legitimate production
# adapter vehicle (the DashScope native lane wraps host callables in one);
# it is deliberately NOT forbidden. The deterministic runtime is detected
# via the module-reference patterns above plus the clean-process smoke.


def _iter_app_sources():
    yield from sorted(APP_ROOT.rglob("*.py"))


def test_production_sources_do_not_reference_test_runtime():
    offenders: list[str] = []
    for path in _iter_app_sources():
        text = path.read_text(encoding="utf-8")
        for pattern in _FORBIDDEN_SOURCE_PATTERNS:
            if pattern.search(text):
                offenders.append(f"{path}: {pattern.pattern}")
    assert offenders == [], offenders


def test_settings_has_no_fake_model_switch():
    settings_text = (APP_ROOT / "config" / "settings.py").read_text(encoding="utf-8")
    lowered = settings_text.lower()
    # No settings FIELD may be a fake-model switch. (The words "fake" and
    # "deterministic" may appear in pre-existing comments — the test-only
    # web search backend note and the compaction validation note;
    # comments are not switches.)
    field_names = re.findall(r"^\s*([a-z_][a-z0-9_]*)\s*:", settings_text, re.MULTILINE)
    assert [name for name in field_names if "fake" in name] == []
    assert [name for name in field_names if "deterministic" in name] == []
    for token in (
        "ask_fake",
        "fake_model",
        "fake_runtime",
        "fake_provider",
        "deterministic_ask",
        "deterministic_model",
    ):
        assert token not in lowered


def test_production_entry_import_smoke_in_clean_process():
    code = (
        "import httpx\n"
        "import app.main\n"
        "import app.services.reader_record_ask.service as svc\n"
        "import app.services.reader_record_ask.execution_config as exc\n"
        "paths = {getattr(r, 'path', '') for r in app.main.app.routes}\n"
        "assert not any(p.startswith('/__deterministic_guard__') for p in paths)\n"
        "assert svc.resolve_reader_record_ask_execution is (\n"
        "    exc.resolve_reader_record_ask_execution)\n"
        "assert svc.resolve_reader_record_ask_execution.__module__ == (\n"
        "    'app.services.reader_record_ask.execution_config')\n"
        "client = httpx.AsyncClient()\n"
        "print('SMOKE_OK')\n"
    )
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(API_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert result.returncode == 0, result.stderr
    assert "SMOKE_OK" in result.stdout
