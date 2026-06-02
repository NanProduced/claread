from __future__ import annotations

import json
from typing import Any

from claread_eval.node_lab_judge.schemas import (
    EvidenceItem,
    PairwisePacket,
    ProbePacket,
    RubricPacket,
    TranslationOutputUnit,
)


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        default=lambda item: item.model_dump(mode="json") if hasattr(item, "model_dump") else str(item),
    )


def _prompt_item(item: EvidenceItem) -> dict[str, Any]:
    return {
        "item_id": item.item_id,
        "item_type": item.item_type,
        "sentence_id": item.sentence_id,
        "label": item.label,
        "source_excerpt": item.source_excerpt,
        "sentence_text": item.sentence_text,
        "explanation": item.explanation,
        "anchor_texts": item.anchor_texts,
    }


def _prompt_translation_unit(unit: TranslationOutputUnit) -> dict[str, Any]:
    return {
        "sentence_id": unit.sentence_id,
        "source_sentence": unit.source_sentence,
        "translation": unit.translation,
        "translation_strategy_hint": unit.translation_strategy_hint,
    }


def build_rubric_prompts(packet: RubricPacket) -> tuple[str, str]:
    system_prompt = (
        "你是 Claread Node Lab 的评审员。"
        "请严格基于给定 rubric 和证据打分。"
        "每条 criterion 只能返回 0 或 1，且必须给出简短中文理由。"
        "不要补充未提供的背景，不要输出 schema 之外的内容。"
    )
    output_requirements = {
        "每条 criterion": {"score": "0或1", "reason": "简短中文", "evidence": "可选"},
    }
    if packet.strategy == "translation_output_review":
        output_requirements["baseline"] = "仅返回 output_level_scores，并给 aggregate"
        output_requirements["candidate"] = "仅返回 output_level_scores，并给 aggregate"
        baseline_payload = {
            "participant": packet.baseline.participant,
            "translations": [_prompt_translation_unit(unit) for unit in packet.baseline.output_units],
        }
        candidate_payload = {
            "participant": packet.candidate.participant,
            "translations": [_prompt_translation_unit(unit) for unit in packet.candidate.output_units],
        }
    else:
        output_requirements["baseline"] = "逐条返回 items，并给 aggregate"
        output_requirements["candidate"] = "逐条返回 items，并给 aggregate"
        baseline_payload = {
            "participant": packet.baseline.participant,
            "item_count_by_type": packet.baseline.item_count_by_type,
            "items": [_prompt_item(item) for item in packet.baseline.items],
        }
        candidate_payload = {
            "participant": packet.candidate.participant,
            "item_count_by_type": packet.candidate.item_count_by_type,
            "items": [_prompt_item(item) for item in packet.candidate.items],
        }
    user_prompt = _json(
        {
            "评测对象": {
                "node": packet.node_name,
                "strategy": packet.strategy,
                "method": packet.method,
                "reading_goal": packet.reading_goal,
                "reading_variant": packet.reading_variant,
            },
            "场景解释": {
                "主要目标": packet.context.primary_intent,
                "用户画像": packet.context.user_profile,
                "帮助方式": packet.context.help_style,
            },
            "rubric_bundle": packet.rubric_bundle,
            "baseline": baseline_payload,
            "candidate": candidate_payload,
            "compare_summary": packet.compare_summary,
            "输出要求": output_requirements,
        }
    )
    return system_prompt, user_prompt


def build_pairwise_prompts(packet: PairwisePacket) -> tuple[str, str]:
    system_prompt = (
        "你是 Claread Node Lab 的整体对比评审员。"
        "请基于原文、精选标注证据和轻量风险提醒，比较 baseline 与 candidate 的整体讲解质量与策略适配度。"
        "不要重新检查锚点、对象边界、spans/chunks 或结构化 JSON 正确性。"
        "不要把回答写成 rubric 打分总结。"
    )
    payload: dict[str, Any] = {
        "评测对象": {
            "node": packet.node_name,
            "strategy": packet.strategy,
            "reading_goal": packet.reading_goal,
            "reading_variant": packet.reading_variant,
        },
        "场景解释": {
            "主要目标": packet.context.primary_intent,
            "用户画像": packet.context.user_profile,
            "帮助方式": packet.context.help_style,
        },
        "aggregate_watchouts": {
            "aggregate": packet.aggregate,
            "watchouts": packet.watchouts,
        },
        "任务": packet.question,
        "输出要求": {
            "preferred_side": "baseline|candidate|mixed|inconclusive",
            "overall_judgment": "一句整体判断",
            "baseline_strengths": "1-3条",
            "candidate_strengths": "1-3条",
            "baseline_risks": "1-3条",
            "candidate_risks": "1-3条",
            "manual_check_points": "0-3条",
        },
    }
    if packet.node_name == "translation":
        payload["compare_units"] = [unit.model_dump(mode="json") for unit in packet.translation_units]
    else:
        payload["compare_units"] = [unit.model_dump(mode="json") for unit in packet.sentence_units]
    return system_prompt, _json(payload)


def build_probe_prompts(packet: ProbePacket) -> tuple[str, str]:
    system_prompt = (
        "你是 Claread Node Lab 的反模板化专项评审员。"
        "请只回答给定 probe 问题。"
        "不要执行 rubric 打分，不要扩展成泛化建议。"
    )
    user_prompt = _json(
        {
            "评测对象": {
                "node": packet.node_name,
                "strategy": packet.strategy,
                "reading_goal": packet.reading_goal,
                "reading_variant": packet.reading_variant,
            },
            "场景解释": {
                "主要目标": packet.context.primary_intent,
                "用户画像": packet.context.user_profile,
                "帮助方式": packet.context.help_style,
            },
            "baseline_items": [_prompt_item(item) for item in packet.baseline_items],
            "candidate_items": [_prompt_item(item) for item in packet.candidate_items],
            "probe_questions": [question.model_dump(mode="json") for question in packet.questions],
            "输出要求": {
                "questions": [
                    {
                        "question_id": "对应问题 id",
                        "detected": "true/false",
                        "description": "简短中文判断",
                        "evidence": ["可选证据"],
                    }
                ],
                "summary": "一句总评",
            },
        }
    )
    return system_prompt, user_prompt
