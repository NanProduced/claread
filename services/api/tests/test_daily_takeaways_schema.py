"""T-03: CloseReadingTakeaways schema validation tests.

Covers: writing_moves 0-2, move_type Chinese labels, reusable_pattern optional.
"""

from __future__ import annotations

import pytest

from app.schemas.internal.daily_drafts import (
    CloseReadingTakeaways,
    WritingMove,
)


def _base_takeaways(**overrides):
    defaults = {
        "article_takeaway": "test takeaway",
        "key_expressions": [],
        "sentence_notes": [],
        "writing_moves": [],
        "discussion_questions": ["Q1?", "Q2?"],
    }
    defaults.update(overrides)
    return defaults


def test_writing_moves_empty():
    data = _base_takeaways(writing_moves=[])
    obj = CloseReadingTakeaways.model_validate(data)
    assert obj.writing_moves == []


def test_writing_moves_single():
    data = _base_takeaways(
        writing_moves=[
            {
                "anchor": "By 3 p.m., the streets were empty.",
                "paragraph_id": "p_0",
                "move_type": "用时间点制造紧迫感",
                "explanation": "具体时间让叙事更紧迫",
            }
        ]
    )
    obj = CloseReadingTakeaways.model_validate(data)
    assert len(obj.writing_moves) == 1
    assert obj.writing_moves[0].move_type == "用时间点制造紧迫感"
    assert obj.writing_moves[0].reusable_pattern is None


def test_writing_moves_two():
    data = _base_takeaways(
        writing_moves=[
            {
                "anchor": "It was, he admitted, a mistake.",
                "paragraph_id": "p_1",
                "move_type": "先让步再转折",
                "explanation": "先承认再转折，增加说服力",
                "reusable_pattern": "It was, [sb] admitted, [contrast]. / 虽然……但……",
            },
            {
                "anchor": "Not once. Not twice. Three times.",
                "paragraph_id": "p_2",
                "move_type": "用重复递进制造冲击",
                "explanation": "重复叠加产生节奏和力度",
            },
        ]
    )
    obj = CloseReadingTakeaways.model_validate(data)
    assert len(obj.writing_moves) == 2
    assert obj.writing_moves[0].reusable_pattern is not None
    assert obj.writing_moves[1].reusable_pattern is None


def test_writing_moves_exceeds_max():
    data = _base_takeaways(
        writing_moves=[
            {
                "anchor": f"anchor {i}",
                "paragraph_id": f"p_{i}",
                "move_type": f"写法{i}",
                "explanation": f"解释{i}",
            }
            for i in range(3)
        ]
    )
    with pytest.raises(Exception):
        CloseReadingTakeaways.model_validate(data)


def test_writing_move_chinese_label():
    data = _base_takeaways(
        writing_moves=[
            {
                "anchor": "The city, once bustling, fell silent.",
                "paragraph_id": "p_0",
                "move_type": "用对比制造画面感",
                "explanation": "前后对比让画面更鲜明",
                "reusable_pattern": "[A], once [B], [C]. / 曾经……如今……",
            }
        ]
    )
    obj = CloseReadingTakeaways.model_validate(data)
    move = obj.writing_moves[0]
    assert move.move_type == "用对比制造画面感"
    assert move.reusable_pattern == "[A], once [B], [C]. / 曾经……如今……"


def test_default_factory_writing_moves():
    data = _base_takeaways()
    del data["writing_moves"]
    obj = CloseReadingTakeaways.model_validate(data)
    assert obj.writing_moves == []
