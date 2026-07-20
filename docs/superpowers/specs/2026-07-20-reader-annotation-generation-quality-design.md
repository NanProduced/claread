# Reader Annotation Generation Quality Design

**Status:** Approved design; implementation planning pending
**Date:** 2026-07-20
**Scope:** Reader Agentic Orchestration `vocabulary`, `grammar_bundle`, and `translation` generation quality

## 1. Purpose

Claread uses generated annotations to help Chinese-native readers improve English reading ability. The current output is structurally valid but has recurring product-quality problems:

- `grammar_note` explanations become repetitive, academic, and exam-slogan-heavy after several annotations.
- `sentence_analysis.analysis` often repeats the already-rendered chunks instead of teaching a reading method.
- `grammar_note` and `sentence_analysis` sometimes explain the same learning point twice.
- `context_gloss` is either omitted or stretched to cover weak, multiword interpretations.
- `phrase_gloss` uses an ambiguous six-value taxonomy and a rigid seven-word rejection rule.
- variant policy lines over-script the output and cause repeated mentions of speed, exams, paraphrases, or rhetorical intent.
- translation policies sometimes mix translation with vocabulary, grammar, rhetoric, or exam coaching.

This design improves annotation selection and explanation quality without changing the Reader Orchestration topology or introducing a new prompt-management system.

## 2. Design Principles

1. **Teaching value before annotation count.** Empty output is valid when the text has no worthwhile learning point.
2. **Current text before generic rules.** An explanation starts from what the form means and does in the current sentence.
3. **Differentiation is a soft lens.** `reading_goal` and `reading_variant` influence selection, depth, terminology, and optional angles; they do not prescribe a repeated script.
4. **Language is not a rigid taxonomy.** Deterministic validation protects structural integrity, but does not try to encode all linguistic judgment.
5. **One annotation, one primary learning job.** Vocabulary items do not duplicate each other, translation does not teach grammar, and grammar item types normally do not repeat the same point.
6. **Markdown is an affordance, not a template.** The model may use emphasis, inline code, or a short list when useful, but no fixed Markdown layout is required.
7. **No legacy-data burden.** The repository is in development; old vocabulary enum values and stored business records do not need migration or compatibility handling.

## 3. Scope and Non-Goals

### 3.1 In scope

- Vocabulary candidate and published-layer contracts.
- Vocabulary, grammar bundle, and translation system prompts.
- Variant-first prompt policy content.
- Per-unit and batch prompt parity.
- Vocabulary worker semantic guards that conflict with the new contract.
- Web DTOs, projection, mark types, subtype labels, and Phrase Quick Peek rendering.
- Deterministic backend and frontend contract tests.
- A guarded local-database reset after implementation and before final database-backed verification.

### 3.2 Out of scope

- Prompt registry or Directus prompt-management architecture changes.
- Eval Center changes, LLM-as-a-Judge, evaluation datasets, or automated quality scoring.
- Few-shot selection, few-shot RAG, or user-learning RAG.
- New workers, model routes, planners, job types, or orchestration topology.
- Changes to grounding authority, UTF-16 offsets, hashes, publisher fencing, or snapshot ownership.
- Broad Reader UI redesign outside Phrase Quick Peek and its existing detail presentation.
- Data migration, enum aliases, fallback conversion, or compatibility branches for old records.
- Automated acceptance of subjective LLM quality; the user owns final content-quality review.

## 4. Architecture Boundary

The existing execution shape remains authoritative:

- `vocabulary` generates all three vocabulary item types.
- `grammar_bundle` jointly generates `grammar_note` and `sentence_analysis`.
- `translation` generates translation groups.
- Workers resolve model-selected source text into deterministic source anchors.
- Existing layer publishers, Reader events, snapshots, and Web projection remain the transport path.

This slice changes content contracts and policy wording inside those boundaries. It must not introduce a second generation path or bypass existing anchoring and publication.

## 5. Vocabulary Contract

### 5.1 `vocab_highlight`

The existing role remains unchanged: a single word worth noticing or accumulating when no special contextual-sense explanation is required.

Existing fields remain valid:

- `headword`
- optional `brief_explanation`
- optional `reason`

It must not be emitted over the same source occurrence when a `context_gloss` or whole-expression `phrase_gloss` provides greater learning value.

### 5.2 `context_gloss`

`context_gloss` becomes a single-lexical-item annotation.

Requirements:

- Anchored `selected_text` contains no whitespace after trimming.
- Hyphenated words, apostrophe forms, and ordinary inflected forms remain valid single lexical items.
- `gloss` states the precise meaning in the current context.
- `reason` explains what in the context selects that meaning, or why the common/default meaning is insufficient.
- A weak generic statement such as “这里需要结合语境理解” is not sufficient.
- A multiword unit whose meaning depends on the combination belongs to `phrase_gloss`.

The backend should reject or skip whitespace-containing `context_gloss` candidates with a diagnostic rather than reinterpret them.

### 5.3 `phrase_gloss`

The published and candidate contracts become:

```text
item_type: "phrase_gloss"
anchor
phrase
phrase_type
gloss
learning_note?   # simplified-Chinese Markdown
example?         # English example sentence
```

Field behavior:

- `gloss` is required and gives the current whole-expression meaning directly.
- `learning_note` is optional. It adds genuine learning value such as usage, composition, contrast, register, or a helpful distinction. It must not restate the gloss at greater length.
- `example` is optional and remains in English. It is included only when a natural example improves transfer.
- `learning_note` may use bold emphasis, inline code, and a short unordered list. Raw HTML and Markdown headings are not part of the contract.
- The existing bounded vocabulary-note length can also bound `learning_note`; this design does not require an unbounded content field.

The new taxonomy is intentionally limited to four values:

| ID | Chinese label | Meaning |
|---|---|---|
| `verb_expression` | 动词短语 | A verb-centered multiword expression, including phrasal verbs, prepositional verbs, and conventional verb combinations. |
| `fixed_collocation` | 固定搭配 | A conventional combination that is not primarily an idiom, name/term, or verb expression. |
| `name_or_term` | 专名及术语 | A multiword proper name, institution, place, work title, or domain concept. |
| `idiom` | 习语 | A conventional figurative or non-compositional expression whose meaning is not reliably recovered word by word. |

The old values `collocation`, `phrasal_verb`, `proper_noun`, `compound`, and `other` are removed. There are no aliases and no `other` fallback. If a candidate is not a useful instance of one of the four types, the model should skip it.

### 5.4 Phrase span safety

The existing seven-word limit must be removed from both the prompt and deterministic guard. Phrase validity is not defined by a universal word count.

The backend retains only structural safety checks:

- exact continuous anchoring;
- field length bounds;
- source occurrence uniqueness where required by the existing resolver;
- rejection of an obvious complete sentence, including a selected span ending in terminal sentence punctuation.

The prompt, not a word-count heuristic, carries the semantic distinction between a lexical expression and a clause or complete statement.

## 6. Grammar Bundle Behavior

### 6.1 Selection

The model selects points that create meaningful comprehension or learning value. “Grammar” uses the broad Chinese English-teaching sense and may include:

- sentence and clause structure;
- non-finite and finite verb choices;
- tense, aspect, voice, agreement, and modality;
- comparison, negation, emphasis, ellipsis, inversion, coordination, and attachment;
- lexical grammar, complementation, usage restrictions, and commonly confused constructions;
- cohesion, reference, logical relation, information structure, and form-meaning distinctions relevant to reading.

This list broadens candidate awareness; it is not a coverage checklist. Basic transparent structures remain unannotated.

### 6.2 `grammar_note`

A useful note should make the current expression easier to understand and more reusable. It normally:

1. identifies the form or contrast that matters;
2. explains what it means or does in this sentence;
3. optionally adds the most useful expansion.

Possible expansions include an example, a contrast, a Chinese-English difference, a common learner mistake, or an exam-relevant distinction. None is mandatory. The model must not produce a fixed sequence of “structure / rule / exam point / example” for every item.

Terminology is secondary to comprehension. When a standard term such as “过去分词” is useful, the note also explains it in plain learner-facing language. Generic claims such as “这是高考高频考点” are only acceptable when followed by concrete, relevant teaching content.

Markdown remains available, but the current “2–4 sentences” style template should not force every note into the same shape. A short explanation, a contrast with two examples, or a compact list may each be appropriate.

### 6.3 `sentence_analysis`

`sentence_analysis` is selected for a whole-sentence comprehension obstacle, not because a sentence crosses a word-count or clause-count threshold.

Responsibilities are separated:

- `chunks` provide the structural map.
- `analysis` explains how to navigate that map, reconstruct the meaning, and handle the difficult reading order.

The `analysis` must not:

- enumerate every chunk again;
- repeat each chunk label and text in prose;
- reproduce the complete Chinese translation;
- use terminology as a substitute for explaining the reading obstacle.

A good analysis highlights the main line, identifies what can temporarily be set aside, explains where an attachment or logical relation returns, and offers a reusable reading action when that action arises naturally. The tone is that of a patient teacher speaking to an English learner, not an academic parser report.

### 6.4 Relationship between grammar item types

For the same learning point, `grammar_note` and `sentence_analysis` normally compete for one slot:

- choose `grammar_note` when a local form, distinction, or usage point is the learning value;
- choose `sentence_analysis` when whole-sentence organization is the comprehension barrier.

Coexistence is allowed when the two items clearly teach different things. Because this is semantic and situational, the backend must not enforce a blanket same-sentence exclusion. The primary enforcement belongs in the shared grammar prompt and variant policy.

## 7. Differentiation Without Templating

The existing `reading_goal` and `reading_variant` contract remains. Differentiation is factored into two layers:

### 7.1 Universal node contract

The agent prompt owns stable requirements:

- item boundaries;
- structural output and anchoring;
- annotation-type responsibilities;
- learner-facing explanation quality;
- avoidance of duplication and fabricated content;
- Markdown safety.

### 7.2 Variant lens

`reader_variants.yaml` owns only soft audience calibration:

- which candidate points are most likely to matter;
- how much terminology the audience can comfortably use;
- appropriate explanation depth;
- optional exam, reading, or stylistic angles when the current point genuinely supports them.

Variant lines must not require every item to mention speed, paraphrase recognition, exam frequency, rhetorical purpose, or practical daily use. If the text does not present a variant-specific opportunity, the lens may have no visible effect on that item.

This design works within the current prompt registry and variant resolver. Moving prompt content to a new management architecture is a separate future task.

## 8. Translation Contract

Translation remains the simplest generation node. Its content prompt should be brief enough not to distract the model from translating.

All variants share the same core requirement:

```text
Translate the given English accurately and completely into natural simplified Chinese.

- Preserve facts, logic, reference, tone, degree, and important limitations.
- Follow natural Chinese expression; reasonable reordering and sentence division are allowed.
- Match the source register without mechanically beautifying it or following English word order.
- Prefer established Chinese renderings for proper names; retain English when no reliable rendering is available or when identification benefits from it.
- Output translation only, without vocabulary, grammar, rhetoric, paraphrase, or exam notes.
```

“Faithfulness, clarity, and appropriate style” is implemented through these operational rules rather than the slogan `信雅达` alone, which could encourage unnecessary literary embellishment.

Variant-specific translation policies that require structural mapping, synonym tips, rhetorical notes, English keyword commentary, or mandatory long-sentence preservation must be removed. If the current resolver requires an explicit translation layer for every variant, keep the entry but make it minimal and non-templating.

Per-unit and batch translation instructions must express the same quality contract while preserving their different grouping/output contracts.

## 9. Web Quick Peek

The new phrase contract must survive the full transport path:

```text
candidate output
  -> published vocabulary layer
  -> Reader snapshot DTO
  -> Web DTO / projection / mark
  -> Reader Quick Peek and structured detail card
```

Phrase display behavior:

1. The header shows the phrase and the Chinese subtype label.
2. `gloss` is always the primary, immediately scannable content.
3. `learning_note` is rendered only when present, using the allowed Markdown subset through a safe renderer.
4. `example` is rendered in a separate example block only when present.
5. Missing optional fields leave no empty labels, separators, or placeholder text.
6. Longer valid content uses the existing floating-surface scrolling behavior rather than crude text truncation.
7. Raw HTML and `dangerouslySetInnerHTML` are prohibited.

The four subtype labels are:

- `verb_expression` -> `动词短语`
- `fixed_collocation` -> `固定搭配`
- `name_or_term` -> `专名及术语`
- `idiom` -> `习语`

No other Reader presentation redesign is part of this slice.

## 10. Failure and Validation Behavior

- Unknown old or invented `phrase_type` values fail candidate schema validation; no fallback category is synthesized.
- Multiword `context_gloss` candidates are skipped with diagnostics rather than converted to `phrase_gloss` automatically.
- Invalid or ambiguous anchors continue to follow existing fail-closed/skip behavior.
- Optional `learning_note` absence is normal and does not degrade the item.
- Prompt instructions guide Markdown shape; the Web rendering path must remain safe even when model Markdown is imperfect.
- Grammar overlap is not deterministically deleted unless existing exact duplicate policy already applies; semantic competition remains a generation responsibility.
- Translation name consistency is best-effort. No article-level entity-resolution node or shared terminology state is added.

## 11. Implementation Work Packages

### WP1: Vocabulary domain contract

- Replace the phrase enum in backend candidate and published schemas.
- Add `learning_note` through candidate hydration and layer output.
- Add the single-lexical-item `context_gloss` guard and diagnostics.
- Remove the seven-word phrase guard while retaining structural safety.
- Update vocabulary prompt definitions and per-unit/batch parity.

### WP2: Grammar quality contract

- Rewrite the grammar bundle system prompt around the responsibilities in Section 6.
- Remove fixed-length and fixed-format pressure.
- Encode grammar-note/sentence-analysis semantic competition.
- Preserve current structured output and deterministic anchor resolution.

### WP3: Variant policy cleanup

- Rewrite vocabulary and grammar policy lines as soft lenses.
- Remove mandatory exam slogans and repetitive output angles.
- Keep every legal variant explicit so the current fail-closed resolver contract remains intact.

### WP4: Translation simplification

- Reduce the per-unit system prompt to the common translation contract.
- Apply equivalent quality wording to the batch instructions.
- Remove teaching commentary from translation variant policies and test fixtures.

### WP5: Web contract and Quick Peek

- Update API DTOs, mark types, projection, bridge helpers, subtype labels, and fixtures.
- Carry and render optional `learning_note` safely.
- Adjust Phrase Quick Peek/detail conditional layout.

### WP6: Deterministic verification

- Update backend schema, executor, worker, publisher, strategy, and prompt-composition tests.
- Update Web DTO, projection, Quick Peek, detail-card, and label tests.
- Run focused regression suites for all three reader-layer workers and affected Reader presentation code after the reset gate described below.

## 12. Verification Matrix

### 12.1 Backend contract cases

- Each new phrase type validates and round-trips.
- Each old phrase type is rejected.
- `learning_note=None` and a valid Markdown string both publish and serialize.
- A legitimate phrase longer than seven words is not rejected only for its length.
- An obvious complete sentence is rejected as a phrase.
- A single hyphenated or apostrophe word is eligible for `context_gloss`.
- A whitespace-containing `context_gloss` is skipped with a bounded diagnostic.
- Vocabulary per-unit and batch prompts include equivalent item semantics.
- Grammar prompts state chunk/analysis separation and same-point competition.
- Translation prompts exclude synonym, grammar, rhetoric, and exam coaching.
- Existing anchoring, fencing, usage attribution, and layer publication regressions pass.

### 12.2 Web contract cases

- Snapshot and projection preserve all four phrase types and `learning_note`.
- Chinese subtype labels map exactly to the four IDs.
- Quick Peek renders gloss-only, gloss-plus-note, gloss-plus-example, and all-fields variants.
- Markdown emphasis, inline code, and a short list render safely.
- Raw HTML is not executed or injected.
- Missing optional fields produce no empty UI chrome.
- Existing `vocab_highlight` and `context_gloss` interactions remain intact.

### 12.3 Subjective quality acceptance

Automated tests do not claim that generated teaching content is good. After technical completion, the user will run and review real article parses. No Eval Center, Judge, few-shot, or RAG work is required for this acceptance.

## 13. Local Database Reset Gate

The vocabulary enum change intentionally has no compatibility layer. A local database reset is therefore required before final database-backed and parsing verification.

The implementation agent must follow this sequence:

1. Finish the code and test-file changes.
2. Run only non-destructive static checks needed to establish that the reset request is ready.
3. Resolve the repository-supported local reset command and its exact database/table effects.
4. Report the command, target database, deleted/rebuilt data, preserved data, and recovery implications to the user.
5. Stop and request explicit approval.
6. Do not run reset, truncate, drop, bulk delete, or equivalent operations without that approval.
7. After approval, execute only the reviewed reset scope.
8. Report what was removed and whether it is recoverable.
9. Recreate the required local schema/bootstrap state.
10. Run the deterministic backend/Web verification and local parsing smoke appropriate to the configured environment.
11. Hand the resulting application state to the user for subjective annotation-quality review.

The reset command is intentionally not hard-coded in this design. It must be resolved against the live repository and local environment immediately before approval so the user sees the actual destructive target rather than a stale command.

## 14. Acceptance Criteria

The implementation is technically complete when:

- the four-value phrase taxonomy is authoritative end to end;
- `learning_note` is optional, preserved, and safely rendered;
- `context_gloss` is single-word and multiword combinations remain representable through `phrase_gloss`;
- the seven-word phrase rejection no longer exists;
- grammar prompts implement learner-facing, non-repetitive responsibilities without a fixed output template;
- grammar note and sentence analysis same-point competition is explicit without an unsafe blanket backend exclusion;
- variant policies act as soft selection/explanation lenses;
- translation prompts are concise, natural-Chinese-first, and translation-only;
- Quick Peek reflects the new phrase contract;
- focused deterministic regressions pass after the approved reset;
- no compatibility, Eval, few-shot, RAG, or prompt-management scope has been introduced;
- the local database was not reset without explicit user approval;
- subjective generation-quality acceptance remains with the user.

## 15. Likely Change Surface

The implementation plan should trace exact references before editing, but the expected surface includes:

- `services/api/prompts/agents/reader_layer_vocabulary.yaml`
- `services/api/prompts/agents/reader_layer_grammar_bundle.yaml`
- `services/api/prompts/agents/reader_layer_translation.yaml`
- `services/api/prompts/policies/reader_variants.yaml`
- `services/api/app/schemas/reader_orchestration.py`
- `services/api/app/services/reader_orchestration/vocabulary_worker.py`
- `services/api/app/services/reader_orchestration/grammar_worker.py`
- `services/api/app/services/reader_orchestration/translation_worker.py`
- focused `services/api/tests/test_reader_orchestration_*` suites
- `apps/web/src/types/api/reader-plate.ts`
- Reader snapshot/projection and dictionary bridge helpers that copy vocabulary fields
- `apps/web/src/components/reader/dictionary/shared.ts`
- `apps/web/src/components/reader/dictionary/ReaderQuickPeek.tsx`
- `apps/web/src/components/reader/dictionary/ReaderStructuredInspectCard.tsx`
- focused Reader DTO, projection, Quick Peek, and detail-card tests

This list is a starting map, not permission for unrelated refactors. Existing user changes in the dirty worktree must be preserved.
