"""Compatibility import for the relocated neutral credit service.

New runtime code must import :mod:`app.services.credits` directly.  This
shim remains only while the legacy analysis package is physically removed.
"""

from app.services.credits import *  # noqa: F401,F403
