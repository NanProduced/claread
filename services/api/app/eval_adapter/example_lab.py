"""Example Lab AI generation of RAG fields.

Provides rule-based generation by default, with optional LLM enhancement for
grammar_tags, structure_signals, teaching_goal, and retrieval_text.

The LLM path is delegated to
:func:`app.llm.structured_completion.run_structured_completion`, which is the
shared OpenAI-compatible structured JSON helper used by Workflow compare judge
and any other eval surface that needs the same model_profile -> base_url /
api_key / model_name resolution.
"""

from __future__ import annotations

import time
from typing import Any

from app.config.settings import get_settings
from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION
from app.llm.structured_completion import (
    StructuredCompletionError,
    run_structured_completion,
)
from app.llm.types import ModelSelection, RouteModelSelection

VALID_GRAMMAR_TAGS = [
    "general", "nonfinite", "inversion", "parallelism", "nested_clause",
    "object_clause", "relative_clause", "nonrestrictive_relative_clause",
    "participle_adverbial", "participle_attribute", "appositive_clause",
    "main_clause_interruption", "passive_voice",
]

VALID_STRUCTURE_SIGNALS = [
    "has_wh_clause", "local_structure", "has_inversion", "has_that_clause",
    "has_comma_insertion", "nested_structure", "leading_vbn", "leading_ving", "long_sentence",
]

VALID_TEACHING_GOALS = [
    "focused", "balanced", "structural", "explicit_split", "structural_logic",
    "explicit_exam", "speed_support", "rhetorical", "info_extraction",
]

LLM_SYSTEM_PROMPT = """\
You are a grammar analysis assistant for an English reading education app called Claread. \
Your task is to generate RAG (Retrieval-Augmented Generation) metadata fields for a few-shot example entry.

## Context

The example entry will be stored in a vector database (Zilliz) for semantic retrieval. \
When the Claread app processes a new English sentence, it retrieves the most relevant few-shot examples \
from the vector database to guide the LLM's output format and quality.

## Input

You will receive:
- **sentence_text**: The English sentence being annotated
- **output_type**: Either "grammar_note" or "sentence_analysis"
- **reading_variant**: The reading mode context (e.g., gaokao, cet, intensive_reading)
- **label**: The Chinese label/title for this grammar point
- **note_zh** (optional): The Chinese grammar explanation
- **analysis_zh** (optional): The Chinese sentence structure analysis

## Output

You must return a JSON object with these fields:

1. **grammar_tags** (array of strings): Grammar category tags. Must be from this allowed list:
   {grammar_tags}

   Use the Chinese label and English keywords to select the most specific tags. Reference patterns:
   - 定语从句 → relative_clause
   - 非限制性定语从句 → nonrestrictive_relative_clause
   - 宾语从句 / 名词性从句 → object_clause
   - 同位语从句 → appositive_clause
   - 过去分词作状语 / 分词结果状语 → participle_adverbial
   - 过去分词后置定语 → participle_attribute
   - 倒装 / 倒装结构 / 虚拟条件句倒装 / 虚拟倒装 / 否定副词前置 → inversion
   - 被动语态 → passive_voice
   - 反复 / 动词并列 / 明喻 → parallelism
   - 插入语 → main_clause_interruption
   - 限制性定语从句 / 介词+关系代词 → relative_clause
   - 让步 / 转折 → nested_clause
   - "give up" in sentence → nonfinite
   - "not only" in sentence → inversion + parallelism
   - A sentence with no special grammar → ["general"]

2. **structure_signals** (array of strings): Structural signals detected from the sentence. Must be from this allowed list:
   {structure_signals}

   Detect structural features:
   - Long sentences (>20 words) → include "long_sentence"
   - Sentences starting with past participle (-ed, e.g., "Frustrated by...") → include "leading_vbn"
   - Sentences starting with present participle (-ing, e.g., "Looking at...", "Running toward...") → include "leading_ving"
   - Sentences with "that" clause → include "has_that_clause"
   - Sentences with "which" or "who" clauses → include "has_wh_clause"
   - Sentences with comma insertion/parenthetical → include "has_comma_insertion"
   - Sentences with inversion (Never / Rarely / Not only / Had + subj at start) → include "has_inversion"
   - Sentences with nested clauses → include "nested_structure"
   - Short/simple sentences → include "local_structure"

3. **teaching_goal** (string): The pedagogical focus. Must be one of:
   {teaching_goals}

   Guidelines:
   - "explicit_exam" for exam-oriented variants (gaokao, default)
   - "speed_support" for cet (fast reading focus)
   - "structural" for kaoyan (complex structure focus)
   - "rhetorical" for tem (rhetorical analysis)
   - "info_extraction" for ielts_toefl
   - "explicit_split" for beginner_reading (step-by-step)
   - "balanced" for intermediate_reading and intensive_reading
   - "structural_logic" for academic_general
   - "focused" for single-point grammar focus

4. **retrieval_text** (string): A structured multi-line text for embedding, following this EXACT format:
   output_type=<type>
   variant=<variant>
   grammar_tags=<comma-separated tags>
   signals=<comma-separated signals>
   teaching_goal=<goal>
   sentence=<original sentence>
   label=<Chinese label>

## Rules

1. grammar_tags MUST come from the allowed list. Choose the most specific tags that apply based on the label and sentence structure.
2. structure_signals MUST come from the allowed list. Detect structural features from the sentence text.
3. teaching_goal should match the reading variant's pedagogical focus.
4. retrieval_text MUST follow the exact key=value format above, one per line.
5. Return ONLY the JSON object, no markdown fences or explanation.
6. Be thorough in your analysis - consider both the label and the actual sentence structure.
""".format(
    grammar_tags=", ".join(VALID_GRAMMAR_TAGS),
    structure_signals=", ".join(VALID_STRUCTURE_SIGNALS),
    teaching_goals=", ".join(VALID_TEACHING_GOALS),
)


def _resolve_profile(model_profile: str, *, settings=None) -> ModelSelection:
    """Build a ``ModelSelection`` that pins the annotation route to a profile.

    ``run_structured_completion`` is the single place that turns the selection
    into a base_url / api_key / model_name triple, so this helper only has to
    express intent ("use this profile") without re-implementing resolution.
    """
    route_selection = RouteModelSelection(profile=model_profile)
    return ModelSelection(
        default_profile=model_profile,
        routes={MODEL_ROUTE_ANNOTATION_GENERATION: route_selection},
    )


def _build_user_prompt(
    sentence_text: str,
    output_fragment: dict[str, Any],
    reading_variant: str,
) -> str:
    """Build the user prompt with full context from the example entry."""
    output_type = output_fragment.get("type", "grammar_note")
    label = output_fragment.get("label", "")
    note_zh = output_fragment.get("note_zh", "")
    analysis_zh = output_fragment.get("analysis_zh", "")

    lines = [
        f"sentence_text: {sentence_text}",
        f"output_type: {output_type}",
        f"reading_variant: {reading_variant}",
        f"label: {label}",
    ]
    if note_zh:
        lines.append(f"note_zh: {note_zh}")
    if analysis_zh:
        lines.append(f"analysis_zh: {analysis_zh}")

    return "\n".join(lines)


def _validate_and_normalize(result: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the LLM output against allowed values."""
    grammar_tags = [
        t for t in result.get("grammar_tags", [])
        if t in VALID_GRAMMAR_TAGS
    ] or ["general"]

    structure_signals = [
        s for s in result.get("structure_signals", [])
        if s in VALID_STRUCTURE_SIGNALS
    ] or ["local_structure"]

    teaching_goal = result.get("teaching_goal", "balanced")
    if teaching_goal not in VALID_TEACHING_GOALS:
        teaching_goal = "balanced"

    retrieval_text = result.get("retrieval_text", "")
    if not retrieval_text:
        # Build retrieval_text from validated fields if LLM didn't provide it
        retrieval_text = "\n".join([
            f"output_type={result.get('output_type', 'grammar_note')}",
            f"variant={result.get('variant', 'default')}",
            f"grammar_tags={', '.join(sorted(grammar_tags))}",
            f"signals={', '.join(sorted(structure_signals))}",
            f"teaching_goal={teaching_goal}",
            f"sentence={result.get('sentence', '')}",
            f"label={result.get('label', '')}",
        ])

    return {
        "grammar_tags": sorted(grammar_tags),
        "structure_signals": sorted(structure_signals),
        "teaching_goal": teaching_goal,
        "retrieval_text": retrieval_text,
    }


# ---------------------------------------------------------------------------
# Rule-based fallback (port of generate_grammar_seed.py:55-107)
# ---------------------------------------------------------------------------

# Compact subset of the original 24-pattern LABEL_TAG_PATTERNS, prioritized
# for fast deterministic coverage. The LLM prompt now also references these
# patterns (see LLM_SYSTEM_PROMPT "Reference patterns"), so the two layers
# agree on the same mapping.
_LABEL_TAG_PATTERNS: list[tuple[str, list[str]]] = [
    ("非限制性定语从句", ["nonrestrictive_relative_clause"]),
    ("同位语从句", ["appositive_clause"]),
    ("过去分词后置定语", ["participle_attribute"]),
    ("过去分词作状语", ["participle_adverbial"]),
    ("分词结果状语", ["participle_adverbial"]),
    ("名词性从句", ["object_clause"]),
    ("介词+关系代词", ["relative_clause"]),
    ("限制性定语从句", ["relative_clause"]),
    ("否定副词前置", ["inversion"]),
    ("虚拟条件句倒装", ["inversion"]),
    ("虚拟倒装", ["inversion"]),
    ("倒装结构", ["inversion"]),
    ("主句插入", ["main_clause_interruption"]),
    ("动词并列", ["parallelism"]),
    ("明喻", ["parallelism"]),
    ("give up", ["nonfinite"]),
    ("not only", ["inversion", "parallelism"]),
]

# Order matters: more specific patterns must come before less specific ones.
_GENERAL_TAG_PATTERNS: list[tuple[str, list[str]]] = [
    ("宾语从句", ["object_clause"]),
    ("定语从句", ["relative_clause"]),
    ("倒装", ["inversion"]),
    ("被动语态", ["passive_voice"]),
    ("插入", ["main_clause_interruption"]),
    ("反复", ["parallelism"]),
    ("让步", ["nested_clause"]),
    ("转折", ["nested_clause"]),
]


def _rule_extract_grammar_tags(label: str, output_type: str) -> list[str]:
    """Rule-based grammar tag extraction from Chinese label.

    Returns sorted list of tags. Falls back to ["general"] if nothing matches.
    """
    tags: set[str] = set()
    for pattern, tag_list in _LABEL_TAG_PATTERNS:
        if pattern in label:
            tags.update(tag_list)
    for pattern, tag_list in _GENERAL_TAG_PATTERNS:
        if pattern in label:
            tags.update(tag_list)
    if output_type == "sentence_analysis" and ("定语从句" in label or "宾语从句" in label):
        tags.add("nested_clause")
    if not tags:
        tags.add("general")
    return sorted(tags)


def _rule_extract_structure_signals(sentence: str, label: str) -> list[str]:
    """Rule-based structure signal extraction from sentence + label.

    Returns sorted list. Falls back to ["local_structure"] if nothing matches.
    """
    import re
    signals: set[str] = set()
    words = sentence.split()
    if len(words) > 20:
        signals.add("long_sentence")
    if re.match(r"^[A-Za-z]+ed\b", sentence):
        signals.add("leading_vbn")
    if re.match(r"^[A-Za-z]+ing\b", sentence):
        signals.add("leading_ving")
    if re.search(r"\bthat\b", sentence, re.IGNORECASE):
        signals.add("has_that_clause")
    if re.search(r"\b(?:which|who)\b", sentence, re.IGNORECASE):
        signals.add("has_wh_clause")
    if (sentence.count(",") >= 2) or re.search(r",\s*(?:which|who|whose|whom)\b", sentence):
        signals.add("has_comma_insertion")
    if re.search(r"\b(?:Never|Rarely|Not only|Had)\b", sentence):
        signals.add("has_inversion")
    if "插入" in label:
        signals.add("has_comma_insertion")
    if "从句" in label and (
        "定语从句" not in label and "宾语从句" not in label
    ) or label.count("从句") > 1:
        signals.add("nested_structure")
    if not signals:
        signals.add("local_structure")
    return sorted(signals)


def _rule_build_retrieval_text(
    output_type: str,
    variant: str,
    grammar_tags: list[str],
    structure_signals: list[str],
    teaching_goal: str,
    sentence: str,
    label: str,
) -> str:
    """Build retrieval_text in the canonical key=value format."""
    return "\n".join([
        f"output_type={output_type}",
        f"variant={variant}",
        f"grammar_tags={', '.join(grammar_tags)}",
        f"signals={', '.join(structure_signals)}",
        f"teaching_goal={teaching_goal}",
        f"sentence={sentence}",
        f"label={label}",
    ])


# Map reading_variant → default teaching_goal (mirrors generate_grammar_seed.py:16-25).
_VARIANT_TEACHING_GOAL: dict[str, str] = {
    "beginner_reading": "explicit_split",
    "default": "explicit_exam",
    "intensive_reading": "balanced",
    "gaokao": "explicit_exam",
    "cet": "speed_support",
    "kaoyan": "structural",
    "tem": "rhetorical",
    "ielts_toefl": "info_extraction",
    "intermediate_reading": "balanced",
    "academic_general": "structural_logic",
}


def _rule_generate_rag_fields(
    sentence_text: str,
    output_fragment: dict[str, Any],
    reading_variant: str,
) -> dict[str, Any]:
    """Pure rule-based generation, used as fallback when LLM call fails."""
    label = str(output_fragment.get("label", "") or "")
    output_type = str(output_fragment.get("type", "grammar_note") or "grammar_note")
    grammar_tags = _rule_extract_grammar_tags(label, output_type)
    structure_signals = _rule_extract_structure_signals(sentence_text, label)
    teaching_goal = _VARIANT_TEACHING_GOAL.get(reading_variant, "balanced")
    retrieval_text = _rule_build_retrieval_text(
        output_type=output_type,
        variant=reading_variant,
        grammar_tags=grammar_tags,
        structure_signals=structure_signals,
        teaching_goal=teaching_goal,
        sentence=sentence_text,
        label=label,
    )
    return {
        "grammar_tags": grammar_tags,
        "structure_signals": structure_signals,
        "teaching_goal": teaching_goal,
        "retrieval_text": retrieval_text,
    }


async def generate_rag_fields(
    sentence_text: str,
    output_fragment: dict[str, Any],
    reading_variant: str,
    model_profile: str | None = None,
    *,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Generate RAG fields, using rules by default and LLM when requested.

    Returns dict with: grammar_tags, structure_signals, teaching_goal,
    retrieval_text, generated_by, latency_ms.

    generated_by values:
      - "rule": no model_profile provided; pure rule-based generation
      - "llm": LLM call succeeded
      - "llm_fallback": LLM call failed, rule-based fallback succeeded
    """
    effective_model_profile = str(model_profile or "").strip() or None
    start_ms = int(time.time() * 1000)

    if not effective_model_profile:
        fallback = _rule_generate_rag_fields(sentence_text, output_fragment, reading_variant)
        latency_ms = int(time.time() * 1000) - start_ms
        return {
            **fallback,
            "generated_by": "rule",
            "latency_ms": latency_ms,
        }

    selection = _resolve_profile(effective_model_profile)
    user_prompt = _build_user_prompt(sentence_text, output_fragment, reading_variant)

    try:
        result = await run_structured_completion(
            settings=get_settings(),
            route=MODEL_ROUTE_ANNOTATION_GENERATION,
            selection=selection,
            system_prompt=LLM_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            timeout_seconds=timeout_seconds,
            temperature=0.2,
            max_tokens=1024,
        )
        validated = _validate_and_normalize(result.parsed)
        latency_ms = int(time.time() * 1000) - start_ms
        return {
            **validated,
            "generated_by": "llm",
            "latency_ms": latency_ms,
        }
    except StructuredCompletionError:
        # Surface only LLM-layer failures. Validation errors from the rule
        # path are caught by the rule-based fallback below.
        pass

    # Fallback path: rule-based extraction
    fallback = _rule_generate_rag_fields(sentence_text, output_fragment, reading_variant)
    latency_ms = int(time.time() * 1000) - start_ms
    return {
        **fallback,
        "generated_by": "llm_fallback",
        "latency_ms": latency_ms,
    }
