"""Example Lab AI generation of RAG fields.

Provides rule-based generation by default, with optional LLM enhancement for
grammar_tags, structure_signals, teaching_goal, and retrieval_text.

The LLM path is delegated to
:func:`app.llm.structured_completion.run_structured_completion`, which is the
shared OpenAI-compatible structured JSON helper used by Workflow compare judge
and any other eval surface that needs the same model_profile -> base_url /
api_key / model_name resolution.

Rule hints combine:
- ``_rule_extract_grammar_tags`` for grammar_tags (Chinese label regex)
- ``grammar_retrieval_hints.extract_signals`` for structure_signals
  (shared with the RAG query-side pipeline)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION
from app.services.analysis.prompting.rag.grammar_retrieval_hints import extract_signals

logger = logging.getLogger(__name__)

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
You are a grammar analysis assistant for Claread, an English reading education app. \
Generate RAG metadata for a few-shot example entry.

## Output

Return a JSON object with these fields (in this order):

1. **grammar_tags** (array of strings): MUST be from: {grammar_tags}
2. **structure_signals** (array of strings): MUST be from: {structure_signals}
3. **teaching_goal** (string): MUST be one of: {teaching_goals}
4. **retrieval_text** (string): EXACTLY this key=value format, one per line:
   output_type=<type>
   variant=<variant>
   grammar_tags=<comma-separated tags>
   signals=<comma-separated signals>
   teaching_goal=<goal>
   sentence=<original sentence>
   label=<Chinese label>
5. **rationale** (string, ≤ 200 chars): A short Chinese sentence explaining WHY you \
chose these grammar_tags / structure_signals / teaching_goal. Reference the label \
and the key syntactic features you detected. Keep it concise — one or two sentences.

## Rules

1. All enum values MUST come from the allowed lists. Choose the most specific match.
2. Detect structure_signals from the actual sentence text (length, leading verb \
form, that/which clauses, comma insertions, inversion triggers, nesting).
3. teaching_goal should match the reading variant's pedagogical focus.
4. retrieval_text MUST follow the key=value format exactly.
5. Return ONLY the JSON object, no markdown fences or extra prose.
""".format(
    grammar_tags=", ".join(VALID_GRAMMAR_TAGS),
    structure_signals=", ".join(VALID_STRUCTURE_SIGNALS),
    teaching_goals=", ".join(VALID_TEACHING_GOALS),
)


def _build_rule_hints(sentence_text: str, label: str, output_type: str) -> dict[str, Any]:
    """Build rule-based hints for confidence assessment (not sent to LLM).

    The hints are computed locally and used only by ``_assess_confidence`` to
    compare against the LLM's output. The LLM itself receives only the cleaned
    sentence_text/label/output_fragment, so the rule engine and the LLM each
    do their own work and the agreement is measured post-hoc.
    """
    grammar_tags = _rule_extract_grammar_tags(label, output_type)
    structure_signals = extract_signals(sentence_text).to_signal_list()
    return {
        "grammar_tags": grammar_tags,
        "structure_signals": structure_signals,
    }


def _build_llm_user_prompt(
    sentence_text: str,
    output_fragment: dict[str, Any],
    reading_variant: str,
) -> str:
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


def _validate_and_normalize(
    result: dict[str, Any],
    *,
    output_type: str = "grammar_note",
    variant: str = "intermediate_reading",
    sentence: str = "",
    label: str = "",
) -> dict[str, Any]:
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
            f"output_type={output_type}",
            f"variant={variant}",
            f"grammar_tags={', '.join(sorted(grammar_tags))}",
            f"signals={', '.join(sorted(structure_signals))}",
            f"teaching_goal={teaching_goal}",
            f"sentence={sentence}",
            f"label={label}",
        ])

    return {
        "grammar_tags": sorted(grammar_tags),
        "structure_signals": sorted(structure_signals),
        "teaching_goal": teaching_goal,
        "retrieval_text": retrieval_text,
    }


def _assess_confidence(validated: dict[str, Any], rule_hints: dict[str, Any]) -> str:
    """Assess confidence based on agreement between LLM and rule engine."""
    llm_tags = set(validated.get("grammar_tags", []))
    rule_tags = set(rule_hints.get("grammar_tags", []))
    llm_signals = set(validated.get("structure_signals", []))
    rule_signals = set(rule_hints.get("structure_signals", []))

    tags_agree = bool(llm_tags & rule_tags) if rule_tags else False
    signals_agree = bool(llm_signals & rule_signals) if rule_signals else False

    if tags_agree and signals_agree:
        return "high"
    if tags_agree or signals_agree:
        return "medium"
    return "low"


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
    "intensive_reading": "balanced",
    "gaokao": "explicit_exam",
    "cet": "speed_support",
    "kaoyan": "structural",
    "tem": "rhetorical",
    "ielts_toefl": "info_extraction",
    "intermediate_reading": "balanced",
    "academic_general": "structural_logic",
}


async def generate_rag_fields(
    sentence_text: str,
    output_fragment: dict[str, Any],
    reading_variant: str,
    model_profile: str | None = None,
    *,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Generate RAG fields using LLM with rule-based hints.

    Returns dict with: grammar_tags, structure_signals, teaching_goal,
    retrieval_text, generated_by, latency_ms, confidence, reasoning,
    model_name, profile_name, usage.

    generated_by values:
      - "llm": LLM call succeeded
      - "llm_fallback": LLM call failed, rule-based fallback succeeded
    """
    effective_model_profile = str(model_profile or "").strip() or None
    start_ms = int(time.time() * 1000)

    # Always compute rule hints first (used as LLM hints and as fallback)
    label = str(output_fragment.get("label", "") or "")
    output_type = str(output_fragment.get("type", "grammar_note") or "grammar_note")
    rule_hints = _build_rule_hints(sentence_text, label, output_type)

    if not effective_model_profile:
        raise ValueError(
            "model_profile is required for AI generation. "
            "Please select an AI model from the dropdown."
        )

    # LLM mode
    from app.config.settings import get_settings
    from app.llm.structured_completion import StructuredCompletionError, run_structured_completion
    from app.llm.types import ModelSelection, RouteModelSelection

    settings = get_settings()
    selection = ModelSelection(
        default_profile=effective_model_profile,
        routes={MODEL_ROUTE_ANNOTATION_GENERATION: RouteModelSelection(profile=effective_model_profile)},
    )
    user_prompt = _build_llm_user_prompt(sentence_text, output_fragment, reading_variant)

    try:
        result = await run_structured_completion(
            settings=settings,
            route=MODEL_ROUTE_ANNOTATION_GENERATION,
            selection=selection,
            system_prompt=LLM_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            timeout_seconds=timeout_seconds,
            temperature=0.0,
            max_tokens=512,
        )
        validated = _validate_and_normalize(
            result.parsed,
            output_type=output_type,
            variant=reading_variant,
            sentence=sentence_text,
            label=label,
        )
        latency_ms = int(time.time() * 1000) - start_ms
        rationale = str(result.parsed.get("rationale", ""))[:300]
        confidence = _assess_confidence(validated, rule_hints)
        return {
            **validated,
            "generated_by": "llm",
            "latency_ms": latency_ms,
            "confidence": confidence,
            "reasoning": rationale,
            "model_name": result.model_name,
            "profile_name": result.profile_name,
            "usage": result.usage,
        }
    except StructuredCompletionError as exc:
        logger.warning("Example Lab LLM generation failed, falling back to rule-based: %s", exc)

    # Fallback to rule engine
    teaching_goal = _VARIANT_TEACHING_GOAL.get(reading_variant, "balanced")
    grammar_tags = rule_hints["grammar_tags"]
    structure_signals = rule_hints["structure_signals"]
    retrieval_text = _rule_build_retrieval_text(
        output_type=output_type,
        variant=reading_variant,
        grammar_tags=grammar_tags,
        structure_signals=structure_signals,
        teaching_goal=teaching_goal,
        sentence=sentence_text,
        label=label,
    )
    latency_ms = int(time.time() * 1000) - start_ms
    return {
        "grammar_tags": grammar_tags,
        "structure_signals": structure_signals,
        "teaching_goal": teaching_goal,
        "retrieval_text": retrieval_text,
        "generated_by": "llm_fallback",
        "latency_ms": latency_ms,
        "confidence": "medium",
        "reasoning": "",
        "model_name": "",
        "profile_name": effective_model_profile or "",
        "usage": None,
    }
