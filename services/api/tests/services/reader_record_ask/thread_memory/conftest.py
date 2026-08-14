"""Conftest for thread_memory tests.

Injects the fallback stub (``_stub.py``) into ``sys.modules`` only
when real ``schema.py`` / ``mapping.py`` modules are not yet on
disk. Once the base contracts land those modules, the real imports win and the stub
injection becomes a no-op.

 stub: 待 完成后移除
"""

from __future__ import annotations

import importlib
import sys


def _try_real_import(module_path: str) -> object | None:
    try:
        return importlib.import_module(module_path)
    except ImportError:
        return None


_SCHEMA_PATH = "app.services.reader_record_ask.thread_memory.schema"
_MAPPING_PATH = "app.services.reader_record_ask.thread_memory.mapping"


def _ensure_stub_installed() -> None:
    if _try_real_import(_SCHEMA_PATH) is None:
        from tests.services.reader_record_ask.thread_memory import _stub

        sys.modules[_SCHEMA_PATH] = _stub
    if _try_real_import(_MAPPING_PATH) is None:
        from tests.services.reader_record_ask.thread_memory import _stub

        sys.modules[_MAPPING_PATH] = _stub


_ensure_stub_installed()
