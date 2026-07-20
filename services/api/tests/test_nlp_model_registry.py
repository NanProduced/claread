"""Unit tests for the shared spaCy model registry (R7-1 rework).

Covers the module-state contract of ``nlp_model_registry`` with mocks
only - these tests never touch the real spaCy installation:

- model/package unavailable -> ``None``, no load attempt;
- load failure -> ``None``;
- negative cache: a failed load is NOT retried within the retry
  interval (the missing ``global`` regression from the R7-1 review);
- ``reset_registry_for_tests`` clears the negative cache so the next
  call retries;
- successful loads are cached and reused per pipe configuration.
"""
from __future__ import annotations

from unittest import mock

import pytest

from app.services import nlp_model_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    nlp_model_registry.reset_registry_for_tests()
    yield
    nlp_model_registry.reset_registry_for_tests()


def test_unavailable_package_returns_none_without_load_attempt() -> None:
    with (
        mock.patch("spacy.util.is_package", return_value=False) as is_package,
        mock.patch("spacy.load") as load,
    ):
        assert nlp_model_registry.get_english_pipeline() is None

    assert is_package.call_count == 1
    load.assert_not_called()


def test_load_failure_returns_none() -> None:
    with (
        mock.patch("spacy.util.is_package", return_value=True),
        mock.patch("spacy.load", side_effect=OSError("corrupt model")) as load,
    ):
        assert nlp_model_registry.get_english_pipeline() is None

    assert load.call_count == 1


def test_negative_cache_prevents_repeated_load_attempts() -> None:
    """Regression: the load-failure path must persist the negative
    availability cache (module-level ``global`` writes), so subsequent
    calls within the retry interval neither re-check the package nor
    re-attempt ``spacy.load``."""
    with (
        mock.patch("spacy.util.is_package", return_value=True) as is_package,
        mock.patch("spacy.load", side_effect=OSError("corrupt model")) as load,
    ):
        assert nlp_model_registry.get_english_pipeline() is None
        assert nlp_model_registry.get_english_pipeline() is None
        assert nlp_model_registry.get_english_pipeline() is None

    # One failed attempt total; the package check is negatively cached
    # too, so it also ran exactly once.
    assert load.call_count == 1
    assert is_package.call_count == 1


def test_reset_registry_allows_retry_after_failure() -> None:
    sentinel = object()
    with (
        mock.patch("spacy.util.is_package", return_value=True),
        mock.patch("spacy.load", side_effect=[OSError("first attempt fails"), sentinel]) as load,
    ):
        assert nlp_model_registry.get_english_pipeline() is None
        nlp_model_registry.reset_registry_for_tests()
        assert nlp_model_registry.get_english_pipeline() is sentinel

    assert load.call_count == 2


def test_successful_load_reuses_cached_instance() -> None:
    sentinel = object()
    with (
        mock.patch("spacy.util.is_package", return_value=True),
        mock.patch("spacy.load", return_value=sentinel) as load,
    ):
        first = nlp_model_registry.get_english_pipeline()
        second = nlp_model_registry.get_english_pipeline()

    assert first is sentinel
    assert second is sentinel
    assert load.call_count == 1


def test_disable_configurations_are_cached_separately() -> None:
    pipelines = [object(), object()]
    with (
        mock.patch("spacy.util.is_package", return_value=True),
        mock.patch("spacy.load", side_effect=pipelines) as load,
    ):
        reader_pipeline = nlp_model_registry.get_english_pipeline(
            disable=("ner", "tagger")
        )
        full_pipeline = nlp_model_registry.get_english_pipeline(disable=())
        # Repeated calls hit the per-configuration caches.
        assert (
            nlp_model_registry.get_english_pipeline(disable=("ner", "tagger"))
            is reader_pipeline
        )
        assert nlp_model_registry.get_english_pipeline(disable=()) is full_pipeline

    assert reader_pipeline is pipelines[0]
    assert full_pipeline is pipelines[1]
    assert load.call_count == 2


def test_spacy_english_model_available_caches_positive_result() -> None:
    with mock.patch("spacy.util.is_package", return_value=True) as is_package:
        assert nlp_model_registry.spacy_english_model_available() is True
        assert nlp_model_registry.spacy_english_model_available() is True

    assert is_package.call_count == 1
