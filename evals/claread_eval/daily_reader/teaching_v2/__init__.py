"""Daily Reader teaching-contract v2 eval modules (P-2, fully offline).

Additive to v1: v1 files are imported and reused unchanged, never
modified. Modules: ``schema`` (plain-dict validators), ``gates`` (the 12
deterministic hard gates), ``judge`` (8-dimension judge contract, no
network path in P-2), ``review`` (per-teaching-point human gate),
``report`` (run assembly / markdown report / cost placeholder).

Since P-5A the deterministic contract/defense functions live in the
shared stdlib-only package ``app.services.daily_reader.teaching``;
this package composes them with the eval-only (gold-dependent) parts.
Bootstrap below follows the harness precedent (sys.path injection of
the services/api root).
"""

from __future__ import annotations

import sys
from pathlib import Path

SERVICES_API_ROOT = Path(__file__).resolve().parents[4] / "services" / "api"
if str(SERVICES_API_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_API_ROOT))
