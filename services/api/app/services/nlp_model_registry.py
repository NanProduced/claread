"""Narrow shared spaCy model lifecycle registry.

This module owns ONLY the process-level lifecycle of spaCy language
pipelines (availability check + lazy load + caching). It performs no
text processing of its own: callers decide which pipes to disable and
how to use the returned ``Language`` object.

Why this module exists
---------------------
The repository already contains a domain-specific dictionary spaCy loader
(``app.services.dictionary.nlp``). The Reader
sentence-segmentation provider needs a parser-backed ``en_core_web_sm``
pipeline, and adding a third independent loader inline in the reader
base builder is intentionally not duplicated. This registry is the
narrow interface that the Reader and future consumers can share.

Guarantees
----------
- ``get_english_pipeline`` returns ``None`` when spaCy or the
  ``en_core_web_sm`` package is unavailable, or when loading fails.
  It never raises for unavailability; callers must treat ``None`` as
  "use the fallback".
- Pipelines are cached per ``disable`` pipe configuration. Loading the
  same configuration twice returns the same ``Language`` instance.
- Availability checks are negatively cached with a retry interval
  (mirroring the dictionary loader) so a missing model does not cost a
  package lookup per request.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence

logger = logging.getLogger(__name__)

_ENGLISH_MODEL_NAME = "en_core_web_sm"
_NEGATIVE_RETRY_INTERVAL_SECONDS = 300.0

_lock = threading.Lock()
# Cached pipelines keyed by the frozen disable-pipe configuration.
_pipelines: dict[tuple[str, ...], object] = {}
# Tristate availability: None = unchecked, True/False = cached result.
_model_available: bool | None = None
_last_availability_check: float = 0.0


def spacy_english_model_available() -> bool:
    """Return whether spaCy and ``en_core_web_sm`` are importable/installed.

    The result is cached. A negative result is re-checked at most once
    per :data:`_NEGATIVE_RETRY_INTERVAL_SECONDS` so a model installed
    after process start is eventually picked up without paying a lookup
    on every call.
    """
    global _model_available, _last_availability_check
    with _lock:
        now = time.monotonic()
        if _model_available is True:
            return True
        if (
            _model_available is False
            and (now - _last_availability_check) < _NEGATIVE_RETRY_INTERVAL_SECONDS
        ):
            return False
        _last_availability_check = now
        try:
            import spacy.util

            available = bool(spacy.util.is_package(_ENGLISH_MODEL_NAME))
        except ImportError:
            available = False
        if not available:
            logger.warning(
                "nlp_model_registry: spaCy model %s unavailable. "
                "Callers must use their fallback. Install with: "
                "python -m spacy download %s",
                _ENGLISH_MODEL_NAME,
                _ENGLISH_MODEL_NAME,
            )
        _model_available = available
        return available


def get_english_pipeline(disable: Sequence[str] = ()) -> object | None:
    """Lazily load (and cache) an ``en_core_web_sm`` pipeline.

    Args:
        disable: spaCy pipe names to disable for this configuration,
            e.g. ``("ner", "tagger")``. The cache is keyed by this
            tuple, so different configurations load separate pipeline
            instances.

    Returns:
        The cached ``spacy.language.Language`` instance, or ``None``
        when spaCy / the model is unavailable or loading fails. This
        function never raises for unavailability; callers must handle
        ``None`` (and should still guard their ``nlp(text)`` calls,
        because runtime errors are possible even after a successful
        load).
    """
    global _model_available, _last_availability_check
    key = tuple(disable)
    with _lock:
        cached = _pipelines.get(key)
    if cached is not None:
        return cached
    if not spacy_english_model_available():
        return None
    try:
        import spacy

        pipeline = spacy.load(_ENGLISH_MODEL_NAME, disable=list(key))
    except Exception as exc:  # noqa: BLE001 - load failures must not escape
        with _lock:
            _model_available = False
            _last_availability_check = time.monotonic()
        logger.warning(
            "nlp_model_registry: failed to load %s (disable=%s): %s. "
            "Callers must use their fallback.",
            _ENGLISH_MODEL_NAME,
            key,
            exc,
        )
        return None
    with _lock:
        existing = _pipelines.get(key)
        if existing is not None:
            return existing
        _pipelines[key] = pipeline
        _model_available = True
    logger.info(
        "nlp_model_registry: loaded %s (disable=%s)",
        _ENGLISH_MODEL_NAME,
        key,
    )
    return pipeline


def reset_registry_for_tests() -> None:
    """Clear all cached state. TEST USE ONLY."""
    global _model_available, _last_availability_check
    with _lock:
        _pipelines.clear()
        _model_available = None
        _last_availability_check = 0.0
