"""Compatibility module alias for the Reader Record Ask catalog.

The module object itself is replaced so test-only patches made through this
historical import path affect the neutral implementation too. Production code
imports ``app.services.reader_record_ask.model_options`` directly.
"""

import sys

from app.services.reader_record_ask import model_options as _implementation

sys.modules[__name__] = _implementation
