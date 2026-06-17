"""Tests for UTF-16 offset conversion and validation utilities."""

from app.services.analysis.postprocess.utf16_offsets import (
    python_offset_to_utf16,
    python_range_to_utf16_range,
    utf16_slice_text,
    validate_utf16_range,
)


class TestPythonOffsetToUtf16:
    def test_ascii(self) -> None:
        assert python_offset_to_utf16("Hello", 0) == 0
        assert python_offset_to_utf16("Hello", 3) == 3
        assert python_offset_to_utf16("Hello", 5) == 5

    def test_chinese(self) -> None:
        # Chinese characters are in BMP, 1 code point = 1 UTF-16 unit
        assert python_offset_to_utf16("你好世界", 0) == 0
        assert python_offset_to_utf16("你好世界", 2) == 2
        assert python_offset_to_utf16("你好世界", 4) == 4

    def test_emoji_surrogate_pair(self) -> None:
        # U+1F600 (😀) is a supplementary character: 1 code point = 2 UTF-16 units
        text = "Hi😀!"
        # offset 0 -> 0
        assert python_offset_to_utf16(text, 0) == 0
        # offset 2 (before emoji) -> 2
        assert python_offset_to_utf16(text, 2) == 2
        # offset 3 (after emoji, at '!') -> 4 (emoji took 2 units)
        assert python_offset_to_utf16(text, 3) == 4

    def test_mixed_text(self) -> None:
        text = "Hello你好😀world"
        # "Hello" = 5 units, "你好" = 2 units, "😀" = 2 units, "world" = 5 units
        assert python_offset_to_utf16(text, 0) == 0
        assert python_offset_to_utf16(text, 5) == 5  # after "Hello"
        assert python_offset_to_utf16(text, 7) == 7  # after "你好"
        assert python_offset_to_utf16(text, 8) == 9  # after "😀" (2 units)
        assert python_offset_to_utf16(text, 13) == 14  # after "world"


class TestUtf16SliceText:
    def test_ascii(self) -> None:
        assert utf16_slice_text("Hello", 0, 5) == "Hello"
        assert utf16_slice_text("Hello", 1, 4) == "ell"

    def test_chinese(self) -> None:
        assert utf16_slice_text("你好世界", 0, 2) == "你好"
        assert utf16_slice_text("你好世界", 2, 4) == "世界"

    def test_emoji(self) -> None:
        text = "Hi😀!"
        assert utf16_slice_text(text, 0, 2) == "Hi"
        assert utf16_slice_text(text, 2, 4) == "😀"
        assert utf16_slice_text(text, 4, 5) == "!"

    def test_mixed(self) -> None:
        text = "Hello你好😀world"
        # "Hello" = units 0-4, "你好" = units 5-6, "😀" = units 7-8, "world" = units 9-13
        assert utf16_slice_text(text, 0, 5) == "Hello"
        assert utf16_slice_text(text, 5, 7) == "你好"
        assert utf16_slice_text(text, 7, 9) == "😀"
        assert utf16_slice_text(text, 9, 14) == "world"

    def test_out_of_bounds_returns_empty(self) -> None:
        assert utf16_slice_text("Hi", 0, 10) == ""
        assert utf16_slice_text("Hi", 5, 10) == ""


class TestValidateUtf16Range:
    def test_ascii_valid(self) -> None:
        assert validate_utf16_range("Hello", 0, 5, "Hello") is True
        assert validate_utf16_range("Hello", 1, 4, "ell") is True

    def test_ascii_invalid(self) -> None:
        assert validate_utf16_range("Hello", 0, 5, "World") is False

    def test_chinese_valid(self) -> None:
        assert validate_utf16_range("你好世界", 0, 2, "你好") is True

    def test_emoji_valid(self) -> None:
        assert validate_utf16_range("Hi😀!", 2, 4, "😀") is True

    def test_out_of_bounds(self) -> None:
        assert validate_utf16_range("Hi", 0, 10, "Hi") is False
        assert validate_utf16_range("Hi", -1, 2, "Hi") is False


class TestPythonRangeToUtf16Range:
    def test_ascii_sentence(self) -> None:
        render_text = "The cat sat."
        # sentence starts at 0, "cat" is at python offset 4-7
        result = python_range_to_utf16_range(
            render_text=render_text,
            sentence_text=render_text,
            sentence_start_in_render=0,
            python_start=4,
            python_end=7,
            expected_text="cat",
        )
        assert result == (4, 7)

    def test_chinese_mixed_sentence(self) -> None:
        render_text = "Hello你好世界"
        # "你好" is at python offset 5-7, utf16 offset 5-7 (BMP)
        result = python_range_to_utf16_range(
            render_text=render_text,
            sentence_text=render_text,
            sentence_start_in_render=0,
            python_start=5,
            python_end=7,
            expected_text="你好",
        )
        assert result == (5, 7)

    def test_emoji_sentence(self) -> None:
        render_text = "Say😀hi"
        # "😀" is at python offset 3-4, utf16 offset 3-5
        result = python_range_to_utf16_range(
            render_text=render_text,
            sentence_text=render_text,
            sentence_start_in_render=0,
            python_start=3,
            python_end=4,
            expected_text="😀",
        )
        assert result == (3, 5)

    def test_sentence_with_offset(self) -> None:
        render_text = "First. The cat sat."
        # sentence "The cat sat." starts at python offset 7
        # "cat" is at python offset 11-14, sentence-local 4-7
        result = python_range_to_utf16_range(
            render_text=render_text,
            sentence_text="The cat sat.",
            sentence_start_in_render=7,
            python_start=11,
            python_end=14,
            expected_text="cat",
        )
        assert result == (4, 7)

    def test_mismatch_returns_none(self) -> None:
        render_text = "The cat sat."
        result = python_range_to_utf16_range(
            render_text=render_text,
            sentence_text=render_text,
            sentence_start_in_render=0,
            python_start=4,
            python_end=7,
            expected_text="dog",  # wrong text
        )
        assert result is None

    def test_out_of_bounds_returns_none(self) -> None:
        render_text = "Hi"
        result = python_range_to_utf16_range(
            render_text=render_text,
            sentence_text=render_text,
            sentence_start_in_render=0,
            python_start=0,
            python_end=10,  # out of bounds
            expected_text="Hi",
        )
        assert result is None
