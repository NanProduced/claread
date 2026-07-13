# TMP: Translation V2 Review Synthesis And Final Plan

Date: 2026-06-27

Status: superseded in part by
`translation-v2-phase0-current-state-and-plan-2026-06-30.md`; keep for
truth-boundary background only.

Superseded note:

- Still valid: do not split V1 Chinese translation on the frontend; do not
  re-split Stable Reading Base for display; Reading Unit remains worker
  context; Anchor Segment remains stable grounding; Plate/DOM/Slate paths are
  projection only.
- Outdated: V2.0 should no longer be item-only per-anchor-segment translation
  plus frontend grouping. The current product goal requires V2.0 to produce
  contiguous anchor-segment translation groups with group-level natural Chinese
  translation.
- Outdated: `reading_goal` / `reading_variant` no longer lack a persisted job
  owner. Translation jobs now carry strategy metadata and prompt policy hashes;
  V2 still needs to expose profile metadata in output/quality/snapshot.

Scope:

- New `/app/reader-record/{recordId}` Reader Record surface only.
- Translation V2, bilingual document flow, Stable Source Truth to Plate projection.
- Legacy `/app/reader/{recordId}` remains comparison-only.

## Inputs

- `translation-v2-design-review-1.md`
- `translation-v2-design-review-2.md`
- `translation-v2-design-review-3.md`
- Current code boundary check:
  - `TranslationLayerOutput` is V1 unit-level: `target_language`, `translated_text`, `notes`, `confidence`.
  - `translation_worker.py` loads one Reading Unit as `source_text` and prompts "Translate the following reading unit."
  - `layer_publisher.py` writes translation as `enhancement_layers.target_scope = 'unit'`, with existing `output_json`, `coverage_json`, and `quality_json` columns available.
  - Web projection currently renders each anchor segment as a paragraph and unit translation as one blockquote.

## Review Triage

### Accepted Common Ground

- Do not solve Translation V2 by splitting current V1 Chinese text on the frontend.
- Do not re-split Stable Reading Base just for translation display.
- Keep Reading Unit as worker execution/context window.
- Use Anchor Segment as the grounding unit for Translation V2 items.
- Persist Translation V2 in existing enhancement layer storage first; no new translation item table in the first pass.
- Keep Plate value, Slate path, DOM selection, and display groups out of business truth.
- Keep unit-level V1 translation as fallback only.
- Make source text visually flow by Stable Document Block / paragraph, with anchor segments as inline spans, not mandatory standalone paragraphs.
- Build bilingual display groups in projection/read-model space.
- Keep source text visually primary. Translation should assist reading without becoming the document's dominant lane.

### Accepted With Modification

#### Worker semantic groups / placement hints

Report 2 correctly identifies a real risk: two English sentences can naturally become one Chinese sentence, and a purely frontend grouping policy cannot invent semantic translation grouping safely.

However, adding worker-owned `semantic_groups` or `placement_hints` directly to V2.0 creates three immediate risks:

- It blurs domain truth and display intent before segment-grounded items are proven reliable.
- It increases publisher validation and fallback states before the base V2 item path exists.
- It can turn a visual design decision into worker output contract too early.

Final position:

- V2.0 should ship segment-grounded translation items plus deterministic projection grouping.
- V2.0 must include an alignment/quality spike before implementation is locked.
- If the spike shows item-level translation harms Chinese fluency or bilingual alignment, promote optional worker `semantic_groups` to V2.1.
- V2.1 `semantic_groups`, if introduced, must be non-authoritative hints:
  - group ids must reference existing contiguous segment ids;
  - group text cannot replace per-segment item truth;
  - invalid groups are discarded without discarding valid items;
  - RAG/Ask citations must still resolve to source segment anchors.

#### Reading goal / variant specific translation

The user requirement is valid: translation can differ by reading goal and variant, so Translation V2 cannot pretend all translations are identical.

But the current repo boundary does not yet show a first-class persisted owner for `reading_goal` / `variant` as translation profile truth.

Final position:

- V2.0 supports one default translation profile unless a first-class profile owner exists.
- Before multiple translation variants are allowed, `translation_profile` must be part of:
  - job input,
  - operation fingerprint,
  - output metadata or layer quality/profile metadata,
  - snapshot/read-model metadata for Ask context.
- Prompt-only profile changes are acceptable for a spike, but not acceptable for persisted multi-variant production behavior.

Grill Q2 decision: accepted. Multi-variant translation must be blocked until `translation_profile` is part of job identity, operation fingerprint, and layer/snapshot metadata.

### Rejected

- Rebuilding Stable Reading Base into sentence-level paragraphs for display.
- Treating raw Plate document value as source truth.
- Persisting display groups as the translation layer's authoritative data.
- Letting worker output Plate JSON, callout placement, or UI node paths.
- Creating durable highlights directly on AI translation/grammar/sentence-analysis text in V1.
- Frontend splitting Chinese translation by punctuation to simulate alignment.
- Binding Translation V2 rollout to a full Ask/RAG rewrite.

## Final Architecture

### Source Structure

Use four distinct concepts:

```text
Canonical Text
-> Stable Document Blocks
-> Reading Units
-> Anchor Segments
```

- Canonical Text is immutable source text and offsets.
- Stable Document Blocks preserve author/input paragraph structure.
- Reading Units remain worker scheduling and cost-control windows.
- Anchor Segments remain grounding and enhancement attachment spans.

Important UI rule:

- The page should not render every anchor segment as a standalone visual paragraph.
- A source paragraph should be built from one Stable Document Block / paragraph, with anchor segments represented as inline selectable spans.
- Grammar, vocabulary, notes, and translation can still attach to anchor segments.

### Translation Worker V2

Worker still consumes a full Reading Unit for context, but receives ordered target segments.

Input shape:

```text
unit_context: full unit text
target_language
translation_profile: optional, only if first-class persisted owner exists
targets:
  - anchor_segment_id
  - source_text
  - source_text_hash
  - segment_type
  - boundary_quality
```

V2.0 output shape:

```ts
type TranslationLayerOutputV2 = {
  schema_version: 2;
  target_language: string;
  translation_profile?: {
    key: string;
    version: string;
  };
  items: Array<{
    anchor_segment_id: string;
    source_text: string;
    source_text_hash: string;
    translated_text: string;
    confidence?: "low" | "normal" | "high";
  }>;
  full_translation?: string;
  notes?: string[];
  diagnostics?: string[];
};
```

V2.0 does not include authoritative display groups.

Optional V2.1, spike-gated only:

```ts
type TranslationSemanticGroupHint = {
  anchor_segment_ids: string[];
  translated_text?: string;
  reason?: "semantic_unit" | "quote_flow" | "contrast_pair" | "other";
};
```

These hints may improve display grouping, but cannot become citation truth.

### Publisher And Storage

Keep the existing enhancement layer table path:

- `layer_type = 'translation'`
- `target_scope = 'unit'`
- `target_key = unit_id`
- `output_json = TranslationLayerOutputV2`
- `coverage_json = item coverage, missing segments, alignment status`
- `quality_json = confidence, diagnostics, prompt/model/profile metadata`

Validation policy:

- Unknown `anchor_segment_id`: reject publish.
- `source_text_hash` mismatch: fail closed.
- Duplicate item for a segment: reject publish.
- Missing segment item: publish only if coverage explicitly marks it missing and fallback display can use V1/full translation.
- Invalid optional semantic group hint: discard hint, keep valid items.

Operation fingerprint:

- Always include translation schema version, target language, prompt version, and source generation.
- Include `translation_profile` before supporting multiple variants.

### Projection And Plate Display

Projection derives bilingual display groups from whole translation items. It must never split Chinese translation text.

Deterministic grouping policy:

- Do not cross Stable Document Block boundaries.
- Do not cross quote/list/table boundaries.
- Prefer 1-3 continuous anchor segments.
- Keep very long or low-confidence segments alone.
- Break when grammar note or sentence analysis should appear immediately after a source segment.
- Merge very short adjacent segments when no system cue requires separation.
- Fall back to unit/full translation only when V2 item coverage is insufficient.

Plate node target:

- Use a custom Claread `reader_translation_group` element or an official Plate primitive wrapped with Claread metadata.
- It may visually borrow blockquote styling, but should not pretend to be a generic quote if the semantics are "translation lane".
- Children must remain Plate-selectable, not isolated HTML.

Visual policy:

- Source text: primary, document-like, stable paragraph rhythm.
- Translation: smaller, muted, left rule / quote-like lane, enough whitespace but lower visual weight.
- Grammar and sentence analysis: visible study notes, not collapsed by default.
- Vocabulary marks: inline subtle marks with lookup preserved.
- Display modes can reduce density later, but intensive reading default should show translation and notes clearly.

### Ask Claread And RAG

Ask may use visible selection context from projection, but citations must resolve to stable source facts:

```text
visible selection
-> view-model node
-> source block / unit / anchor segment ids
-> enhancement layer ids/items
-> source-grounded Ask context
```

Do not index UI-only display groups or Plate paths as authoritative RAG truth.

## Implementation Phases

### Phase 1: Plate-Native Readability First

No backend Translation V2 schema change.

- Stop making every anchor segment look like an independent source paragraph where source flow should remain paragraph-like.
- Minimum prerequisite before Translation V2: source text must render by Stable Document Block / paragraph flow, with anchor segments represented as inline anchors rather than standalone visual paragraphs.
- Keep V1 unit translation fallback, but style it as a low-weight translation lane.
- Move selection, lookup, Ask, note, highlight, and copy into one Plate-compatible selection/action pipeline.
- Ensure grammar and sentence-analysis content renders through Plate-compatible children, not isolated HTML.
- Visual check against the Plate mock direction, not pixel-perfect reproduction.

### Spike A: Translation Alignment Quality

Before committing to final V2.0 prompt/schema:

- Sample at least short, medium, long, and quote-heavy units.
- Include cases where two English sentences naturally become one Chinese sentence.
- Compare:
  - item-only per-segment translation,
  - item-only plus `full_translation`,
  - optional semantic group hints.
- Decide whether V2.0 can stay item-only or must promote V2.1 hints earlier.

### Spike B: Bilingual Projection Prototype

Prototype display grouping against fixed Reader Record data:

- source paragraph flow with inline anchors,
- translation group lane,
- grammar note interruption,
- sentence analysis visible block,
- vocabulary marks,
- selection/toolbar behavior across source and AI text.

### Phase 2: Translation V2 Backend

- Add V2 schema next to V1.
- Pass ordered anchor segment targets into the translation prompt.
- Add publisher validation and coverage metadata.
- Keep V1 fallback path.
- Add focused tests for hash mismatch, unknown segment, duplicate item, missing item, and V1 fallback.

### Phase 3: Translation V2 Frontend Projection

- Consume V2 items when available.
- Build deterministic display groups.
- Render Plate-selectable translation group elements.
- Fall back to V1 unit translation when V2 coverage is absent or incomplete.
- Add grouping characterization tests and visual screenshots.

Cross-client decision:

- Accepted/deferred: do not make mini-program consumption part of the current View Model plan.
- The current version optimizes the Web Reader Record + Plate-native document flow.
- Mini-program is expected to be reworked later; that rewrite can decide whether to reuse the same backend facts, grouping policy, or a future renderer-neutral BFF/read-model.
- Do not let Web Plate details become a cross-client contract.

Projection builder caching decision:

- Accepted: do not introduce durable/server-side View Model cache at the start.
- Use a pure builder plus frontend memory caching such as `useMemo` while UI behavior is stabilizing.
- Revisit persistent/BFF caching only if real long-article benchmarks show projection construction consistently exceeds roughly 50ms for 10k-20k English words with translation, grammar, sentence analysis, vocabulary, and user assets enabled, or if interaction updates visibly drop frames.
- Avoid early cache complexity around invalidation, layer update consistency, and debugging.

### Phase 4: Optional V2.1 Semantic Group Hints

Only after Spike A proves they are needed:

- Add optional non-authoritative group hints.
- Validate continuity and boundary constraints.
- Discard invalid hints.
- Keep source segment items as truth.

## Final Recommendation

Adopt the "B+" plan:

```text
unit context
+ ordered anchor segment targets
+ per-segment translation items as truth
+ deterministic projection display groups
+ V1/full translation fallback
+ spike-gated optional semantic group hints
```

This gives Claread the Notion-like document feel without turning Plate into truth, and gives Ask Claread source-grounded context without losing the visible reading experience.

## Grill Questions

1. Accepted: V2.0 is item-only domain truth; semantic group hints are reserved for a spike-gated V2.1.
2. Accepted: multi-variant translation is blocked until `translation_profile` is part of job identity, operation fingerprint, and layer/snapshot metadata.
3. Accepted: before Translation V2, Phase 1 must render source by Stable Document Block / paragraph flow, with anchor segments as inline anchors rather than standalone visual paragraphs.
4. Accepted: do not add durable View Model caching initially; use pure builder plus frontend memory caching and revisit only after benchmark evidence.
5. Accepted/deferred: ignore mini-program consumption for this version; mini-program will be reworked later and can reassess reuse then.
