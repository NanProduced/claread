"""A-4: highlight cross-paragraph dedup + translation single source of truth.

Seams:
- ``_normalize_highlight_key`` / ``_dedupe_highlights`` (projection after reconcile)
- takeaways prompt assembly (full paragraph translations, no 1500-char cap)
- ``_snap_sentence_translations`` (SentenceNote.translation ⊆ paragraph translation)
"""

from __future__ import annotations

from app.agents.daily_vocab_agent import DailyVocabAgentDeps, build_daily_vocab_prompt
from app.schemas.internal.daily_drafts import SentenceNote
from app.services.prompting.prompt_loader import load_agent_instructions

# Prompt / schema leftovers
# ---------------------------------------------------------------------------


class TestVocabPromptLeftovers:
    def test_yaml_has_no_unformatted_batch_placeholders(self):
        text = load_agent_instructions("daily_vocab")
        assert "{batch_index}" not in text
        assert "{total_batches}" not in text

    def test_yaml_asks_no_cross_batch_repeat(self):
        text = load_agent_instructions("daily_vocab")
        assert "跨批不重复标注同一表达" in text

    def test_batch_info_still_formatted_in_user_prompt(self):
        prompt = build_daily_vocab_prompt(
            DailyVocabAgentDeps(
                paragraphs=[{"paragraph_id": "p_0", "text": "hello"}],
                batch_index=1,
                total_batches=3,
            )
        )
        assert "第 2 批（共 3 批）" in prompt


class TestSentenceNoteSchema:
    def test_translation_field_requires_verbatim_span(self):
        desc = SentenceNote.model_fields["translation"].description or ""
        assert "段译" in desc or "paragraph" in desc.lower()
        assert "重新翻译" in desc or "retranslat" in desc.lower()
