"""Web search provider wire fixtures (G2).

**Status: PROBE REQUIRED**

This subpackage hosts OFFLINE wire fixtures for the upcoming provider
transports (Qwen ``dashscope_responses`` and DeepSeek
``deepseek_anthropic``). Fixtures are deterministic — no real HTTP
calls, no real SDK imports.

The fixtures are hand-authored protocol drafts built from public docs.
They are NOT official captures and are NOT evidence that the wire
shapes match the real provider endpoints. Every field — model name,
tool shape, event ordering, citation behaviour — must be validated
by a real G3 smoke run before any GO decision. Treat all conclusions
as PROBE REQUIRED until backed by a real SDK call capture.

G3 smoke tests (real provider calls) live elsewhere and require
explicit Owner approval; they are NOT part of this subpackage.
"""
