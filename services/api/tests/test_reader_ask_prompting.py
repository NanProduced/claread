from app.services.analysis.prompting.prompt_loader import load_agent_instructions
from app.agents.reader_ask_tool_registry import RESERVED_TOOL_NAMES
from app.services.reader_ask.prompting import load_prompt_layers


def test_load_prompt_layers_reads_reader_ask_prompt_files() -> None:
    layers = load_prompt_layers()

    assert set(layers) == {"system", "answer", "schema", "policy_examples"}
    assert "Ask Claread" in layers["system"]


# ---------------------------------------------------------------------------
# Round 2→5: prompt ↔ tool surface alignment.
#
# The system prompt (loaded via ``load_agent_instructions("reader_ask")``)
# is the model's view of which tools exist. It must NOT mention deprecated
# tools (``lookup_dictionary_entry``, ``run_dictionary_ai_context_explain``)
# or the fully removed ``search_user_vocabulary``, and MUST mention the
# Round 2 tools the registry exposes (``get_user_vocabulary_book``,
# ``resolve_known_reference``, ``suggest_prompts``).
# ---------------------------------------------------------------------------


def test_reader_ask_prompt_does_not_mention_deprecated_tools() -> None:
    """The system prompt must not encourage the model to call tools
    that the Round 2 registry marked ``agent_callable=False``."""
    prompt = load_agent_instructions("reader_ask")

    for deprecated in (
        "search_user_vocabulary",
        "lookup_dictionary_entry",
        "run_dictionary_ai_context_explain",
        "lookup_record_by_embedding",
    ):
        assert deprecated not in prompt, (
            f"reader_ask prompt still references '{deprecated}'; "
            "the tool is no longer agent-callable in Round 2."
        )


def test_reader_ask_prompt_mentions_round2_tools() -> None:
    """The system prompt must point the model at the new tools that
    are actually exposed to the main agent."""
    prompt = load_agent_instructions("reader_ask")

    for required in (
        "get_record_context",
        "get_record_insights",
        "get_user_vocabulary_book",
        "resolve_known_reference",
        "suggest_prompts",
        "propose_save_note",
        "propose_save_highlight",
        "generate_sentence_annotation",
    ):
        assert required in prompt, (
            f"reader_ask prompt must reference Round 2 tool '{required}'"
        )


def test_reader_ask_prompt_documents_resolver_three_states() -> None:
    """The system prompt must teach the model how to handle each
    resolver state — resolved / ambiguous / not_found — so the main
    loop produces correct user-facing behavior (HITL vs L3 nudge)."""
    prompt = load_agent_instructions("reader_ask")

    for state in ("resolved", "ambiguous", "not_found"):
        assert state in prompt, (
            f"reader_ask prompt must describe resolver state '{state}'"
        )


def test_reader_ask_prompt_separates_cross_record_hint_from_followup_hint() -> None:
    """Cross-record resolution is a tool-use hint, not a local-anchor
    clarification hint."""
    prompt = load_agent_instructions("reader_ask")

    assert "cross_record_intent_hint" in prompt
    assert "followup_hint" in prompt
    assert "resolve_known_reference(query, top_k=5)" in prompt
    assert "不是追问提示" in prompt
    assert "不要把跨文章引用解析写进 `followup_hint`" in prompt


def test_reader_ask_prompt_documents_suggest_prompts_bounds() -> None:
    """The prompt must specify the 2-3 suggestion bound and the
    label/prompt character caps so the model's suggestions pass
    the agent tool layer's validation."""
    prompt = load_agent_instructions("reader_ask")

    # The bound and the cap (any phrasing; we just need the digits
    # present in some form). 2-3 / 40 / 200 are the Round 2 contracts.
    for needle in ("2", "3", "40", "200"):
        assert needle in prompt, (
            f"reader_ask prompt must include '{needle}' for suggest_prompts bounds"
        )


def test_reader_ask_prompt_suggest_prompts_count_is_2_to_3_only() -> None:
    """The Round 2 contract is strictly 2-3 suggestions. The prompt
    must NOT mention "1-3" or "1" as a lower bound — the agent tool
    layer rejects &lt;2 with a warning and would silently drop the
    chip row."""
    prompt = load_agent_instructions("reader_ask")

    # Pull the suggest_prompts paragraph so the test doesn't get fooled
    # by stray "1" or "3" elsewhere in the file.
    in_suggest = False
    paragraph_lines: list[str] = []
    for line in prompt.splitlines():
        if "suggest_prompts" in line:
            in_suggest = True
            paragraph_lines.append(line)
            continue
        if in_suggest:
            if line.startswith("  - "):
                # Next bullet — done with the suggest_prompts paragraph.
                break
            paragraph_lines.append(line)
    paragraph = "\n".join(paragraph_lines)
    assert "suggest_prompts" in paragraph, (
        "could not locate the suggest_prompts paragraph in reader_ask prompt"
    )

    # The "1" check is brittle (the digit appears in many places), so
    # we check the explicit conflicting phrasing.
    for forbidden in ("1-3", "1 至 3", "1-2"):
        assert forbidden not in paragraph, (
            f"reader_ask prompt contains conflicting suggestion count "
            f"'{forbidden}' in the suggest_prompts paragraph; "
            "the Round 2 contract is 2-3 only."
        )

    # Positive check: the digit 2 must appear (as part of "2-3" or
    # "2 个") in the suggest_prompts paragraph.
    assert "2" in paragraph, (
        "reader_ask prompt's suggest_prompts paragraph must mention '2'"
    )


def test_reader_ask_prompt_documents_scope_for_get_record_context() -> None:
    """The prompt must teach the model that ``get_record_context`` has
    a ``scope`` arg (window/paragraph/full) and that the default is
    ``window`` — without this, models default to full and blow the
    context budget."""
    prompt = load_agent_instructions("reader_ask")

    for token in ("scope", "window", "paragraph", "full"):
        assert token in prompt, (
            f"reader_ask prompt must reference scope='{token}' for get_record_context"
        )


# ---------------------------------------------------------------------------
# Round 5: prompt must not mention deprecated/reserved tool names
# ---------------------------------------------------------------------------


def test_prompt_does_not_mention_deprecated_tool_names() -> None:
    """The system prompt must not mention deprecated dictionary tool names."""
    prompt = load_agent_instructions("reader_ask")
    for name in ("lookup_dictionary_entry", "run_dictionary_ai_context_explain"):
        assert name not in prompt, (
            f"reader_ask prompt references deprecated tool '{name}'; "
            "deprecated tools must never appear in the prompt."
        )


def test_prompt_does_not_mention_reserved_tool_names() -> None:
    """The system prompt must not mention any tool name in RESERVED_TOOL_NAMES."""
    prompt = load_agent_instructions("reader_ask")
    for name in RESERVED_TOOL_NAMES:
        assert name not in prompt, (
            f"reader_ask prompt references reserved tool '{name}'; "
            "reserved tools must never appear in the prompt."
        )
