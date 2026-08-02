"""Example Lab AI generation of RAG fields.

Provides rule-based generation by default, with optional LLM enhancement for
grammar_tags and retrieval_text.

The LLM path is delegated to
:func:`app.llm.structured_completion.run_structured_completion`, which is the
shared OpenAI-compatible structured JSON helper used by Workflow compare judge
and any other eval surface that needs the same model_profile -> base_url /
api_key / model_name resolution.

Rule hints combine:
- ``_rule_extract_grammar_tags`` for grammar_tags (Chinese label regex)
- ``_rule_build_retrieval_text`` for retrieval_text
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.llm.routes import MODEL_ROUTE_ANNOTATION_GENERATION
from app.services.prompting.rag.grammar_tag_normalization import (
    _rule_extract_grammar_tags,
    normalize_grammar_tags,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# grammar_tags: open vocabulary with normalization
# ---------------------------------------------------------------------------

# Recommended tag vocabulary — used as LLM prompt guidance, NOT as a closed
# enumeration.  New tags are allowed as long as they pass normalization.
RECOMMENDED_GRAMMAR_TAGS: list[str] = [
    "relative_clause",
    "restrictive_relative_clause",
    "nonrestrictive_relative_clause",
    "inversion",
    "past_participle_adverbial",
    "past_participle_attribute",
    "present_participle_adverbial",
    "appositive_clause",
    "subject_clause_fronting",
    "object_clause",
    "passive_voice",
    "parallelism",
    "nonfinite",
    "nested_clause",
    "main_clause_interruption",
]

# ---------------------------------------------------------------------------
# LLM prompt
# ---------------------------------------------------------------------------

LLM_SYSTEM_PROMPT = """\
You are a grammar analysis assistant for Claread, an English reading education app. \
Generate RAG metadata for a few-shot example entry.

## Output

Return a JSON object with these fields (in this order):

1. **grammar_tags** (array of 1-5 strings): Open-vocabulary snake_case English \
tags describing the grammar / structure phenomena. Each tag should express ONE \
phenomenon. Prefer these recommended tags when applicable: {recommended_tags}
   Rules:
   - Use English lowercase snake_case.
   - Each tag expresses one grammar/structure phenomenon.
   - Do NOT include variant-style words (e.g. gaokao, exam).
   - Do NOT include generic words (e.g. general, complex).
2. **retrieval_text** (string): A structured text for embedding retrieval. \
Use EXACTLY this format, one field per line (colon + space separator):
   variant: <variant>
   output_type: <type>
   grammar_tags: <comma-separated tags>
   label: <Chinese label>
   source_sentence: <original sentence>
   explanation: <explanation text>
   Where:
   - For grammar_note: explanation = the note_zh content
   - For sentence_analysis: explanation = the analysis_zh content
3. **rationale** (string, ≤ 200 chars): A short Chinese sentence explaining WHY \
you chose these grammar_tags. Reference the label and the key syntactic features \
you detected. Keep it concise — one or two sentences.

## Rules

1. grammar_tags must be specific and informative. Choose the most specific match.
2. retrieval_text MUST follow the format exactly. The explanation field should \
capture the teaching style and content of the output.
3. Return ONLY the JSON object, no markdown fences or extra prose.
""".format(
    recommended_tags=", ".join(RECOMMENDED_GRAMMAR_TAGS),
)


def _build_rule_hints(sentence_text: str, label: str, output_type: str) -> dict[str, Any]:
    """Build rule-based hints for confidence assessment (not sent to LLM).

    The hints are computed locally and used only by ``_assess_confidence`` to
    compare against the LLM's output. The LLM itself receives only the cleaned
    sentence_text/label/output_fragment, so the rule engine and the LLM each
    do their own work and the agreement is measured post-hoc.
    """
    grammar_tags = _rule_extract_grammar_tags(label, output_type)
    return {
        "grammar_tags": grammar_tags,
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


# Required keys in retrieval_text (colon-separated format)
_RETRIEVAL_TEXT_REQUIRED_KEYS = (
    "variant", "output_type", "grammar_tags", "label", "source_sentence", "explanation",
)


def _validate_retrieval_text(text: str) -> bool:
    """Check whether retrieval_text conforms to the canonical colon-separated format.

    Each line must be ``key: value``. All required keys must be present.
    """
    found_keys: set[str] = set()
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" not in line:
            return False
        key = line.split(":", 1)[0].strip()
        if not key:
            return False
        found_keys.add(key)
    return all(k in found_keys for k in _RETRIEVAL_TEXT_REQUIRED_KEYS)


def _validate_and_normalize(
    result: dict[str, Any],
    *,
    output_type: str = "grammar_note",
    variant: str = "intermediate_reading",
    sentence: str = "",
    label: str = "",
    output_fragment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and normalize the LLM output."""
    raw_tags = result.get("grammar_tags", [])
    if not isinstance(raw_tags, list):
        raw_tags = []
    grammar_tags = normalize_grammar_tags(raw_tags) or ["unclassified"]

    retrieval_text = str(result.get("retrieval_text", "") or "").strip()
    if not retrieval_text or not _validate_retrieval_text(retrieval_text):
        # Rebuild retrieval_text from validated fields if LLM didn't provide
        # it or provided a malformed one
        explanation = _extract_explanation(output_fragment or {}, output_type)
        retrieval_text = _build_retrieval_text(
            variant=variant,
            output_type=output_type,
            grammar_tags=grammar_tags,
            label=label,
            sentence=sentence,
            explanation=explanation,
        )

    return {
        "grammar_tags": grammar_tags,
        "retrieval_text": retrieval_text,
    }


def _extract_explanation(output_fragment: dict[str, Any], output_type: str) -> str:
    """Extract the explanation text from output_fragment based on type.

    - grammar_note → note_zh
    - sentence_analysis → analysis_zh
    """
    if output_type == "sentence_analysis":
        return str(output_fragment.get("analysis_zh", "") or "")
    return str(output_fragment.get("note_zh", "") or "")


def _build_retrieval_text(
    *,
    variant: str,
    output_type: str,
    grammar_tags: list[str],
    label: str,
    sentence: str,
    explanation: str,
) -> str:
    """Build retrieval_text in the canonical format per design doc."""
    return "\n".join([
        f"variant: {variant}",
        f"output_type: {output_type}",
        f"grammar_tags: {', '.join(grammar_tags)}",
        f"label: {label}",
        f"source_sentence: {sentence}",
        f"explanation: {explanation}",
    ])


def _assess_confidence(validated: dict[str, Any], rule_hints: dict[str, Any]) -> str:
    """Assess confidence based on agreement between LLM and rule engine."""
    llm_tags = set(validated.get("grammar_tags", []))
    rule_tags = set(rule_hints.get("grammar_tags", []))

    tags_agree = bool(llm_tags & rule_tags) if rule_tags else False

    if tags_agree:
        return "high"
    if llm_tags:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Rule-based fallback
# ---------------------------------------------------------------------------

async def generate_rag_fields(
    sentence_text: str,
    output_fragment: dict[str, Any],
    reading_variant: str,
    model_profile: str | None = None,
    *,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Generate RAG fields using LLM with rule-based fallback.

    Returns dict with: grammar_tags, retrieval_text, derived_by,
    generated_by, latency_ms, confidence, reasoning, fallback_reason,
    model_name, profile_name, usage.

    generated_by values:
      - "llm": LLM call succeeded
      - "llm_fallback": LLM call failed, rule-based fallback succeeded
    """
    effective_model_profile = str(model_profile or "").strip() or None
    start_ms = int(time.time() * 1000)

    # Always compute rule hints first (used for confidence assessment and as fallback)
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
        routes={
            MODEL_ROUTE_ANNOTATION_GENERATION: RouteModelSelection(
                profile=effective_model_profile
            )
        },
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
            output_fragment=output_fragment,
        )
        latency_ms = int(time.time() * 1000) - start_ms
        rationale = str(result.parsed.get("rationale", ""))[:300]
        confidence = _assess_confidence(validated, rule_hints)
        return {
            **validated,
            "derived_by": f"llm:{result.model_name}",
            "generated_by": "llm",
            "latency_ms": latency_ms,
            "confidence": confidence,
            "reasoning": rationale,
            "fallback_reason": "",
            "model_name": result.model_name,
            "profile_name": result.profile_name,
            "usage": result.usage,
        }
    except StructuredCompletionError as exc:
        logger.warning("Example Lab LLM generation failed, falling back to rule-based: %s", exc)
        fallback_reason = str(exc)[:500]

    # Fallback to rule engine
    grammar_tags = rule_hints["grammar_tags"]
    explanation = _extract_explanation(output_fragment, output_type)
    retrieval_text = _build_retrieval_text(
        variant=reading_variant,
        output_type=output_type,
        grammar_tags=grammar_tags,
        label=label,
        sentence=sentence_text,
        explanation=explanation,
    )
    latency_ms = int(time.time() * 1000) - start_ms
    return {
        "grammar_tags": grammar_tags,
        "retrieval_text": retrieval_text,
        "derived_by": "rule_engine",
        "generated_by": "llm_fallback",
        "latency_ms": latency_ms,
        "confidence": "medium",
        "reasoning": "",
        "fallback_reason": fallback_reason,
        "model_name": "",
        "profile_name": effective_model_profile or "",
        "usage": None,
    }
