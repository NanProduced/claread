"""Sanity check for BBC fixture constants."""
from tests.fixtures.bbc_cd6684a0_input import (
    BBC_ARTICLE_TEXT,
    BBC_RECORD_ID,
    BBC_SOURCE_LANGUAGE,
    BBC_ARTICLE_TITLE,
    assert_fixture_text_length,
)
from tests.fixtures.bbc_cd6684a0_expected_windows import (
    EXPECTED_WINDOW_COUNT_MIN,
    EXPECTED_WINDOW_COUNT_MAX,
    EXPECTED_GRAMMAR_NOTE_TOTAL_MAX,
    EXPECTED_SENTENCE_ANALYSIS_TOTAL_MAX,
)


def test_bbc_fixture_record_id_is_uuid():
    from uuid import UUID
    assert isinstance(BBC_RECORD_ID, UUID)
    assert str(BBC_RECORD_ID) == "cd6684a0-c31b-4474-ba8e-ed0039a6c4ee"


def test_bbc_fixture_text_length():
    assert_fixture_text_length()


def test_bbc_fixture_source_language():
    assert BBC_SOURCE_LANGUAGE == "en"


def test_bbc_fixture_title_present():
    assert len(BBC_ARTICLE_TITLE) > 0


def test_bbc_fixture_text_not_empty():
    assert len(BBC_ARTICLE_TEXT) > 1000


def test_bbc_fixture_expected_windows_count_range():
    assert 3 <= EXPECTED_WINDOW_COUNT_MIN <= EXPECTED_WINDOW_COUNT_MAX <= 5


def test_bbc_fixture_expected_grammar_note_budget():
    assert EXPECTED_GRAMMAR_NOTE_TOTAL_MAX == 14


def test_bbc_fixture_expected_sentence_analysis_budget():
    assert EXPECTED_SENTENCE_ANALYSIS_TOTAL_MAX == 3
