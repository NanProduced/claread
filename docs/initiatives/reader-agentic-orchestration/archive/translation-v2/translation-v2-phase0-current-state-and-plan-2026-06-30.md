# TMP: Translation V2 Phase 0 Current State And Plan

Date: 2026-06-30

Status: direction review for Translation V2; no implementation yet

Scope:

- New `/app/reader-record/{recordId}` Reader Record surface.
- Translation output grain, source grouping, snapshot projection, and bilingual display strategy.
- Legacy `/app/reader/{recordId}` remains comparison-only.

This document supersedes parts of `translation-v2-review-synthesis-2026-06-27.md`.
The older synthesis is still useful for truth-boundary rules, but its V2.0
output-grain recommendation is too conservative for the current product goal.

## Executive Conclusion

Translation V2 should not ship as "per-anchor-segment translation items plus
frontend grouping" only.

The current product goal is not merely machine-readable alignment. It is a
good bilingual reading experience: source text must still read like an article,
and Chinese translation must be fluent, natural, and placed close enough to the
corresponding source to support bilingual reading.

Recommended V2 direction:

```text
Reading Unit context
+ ordered Anchor Segment targets
+ worker-produced contiguous translation groups
+ group-level natural Chinese translation
+ segment/hash coverage metadata
+ low-weight bilingual lane projection
```

In other words:

- Reading Unit remains the worker scheduling and context window.
- Anchor Segment remains the stable grounding/citation unit.
- Translation V2 output should contain contiguous groups of 1-3 anchor segments
  in the common case, not only individual segment translations.
- The worker should translate each group naturally with full unit context.
- The publisher must validate group continuity, segment ownership, hashes, and
  coverage fail-closed.
- Frontend must not split Chinese translation text by punctuation.
- Frontend must stop rendering every anchor segment as a standalone visual
  paragraph when the source should flow as one paragraph/document block.

## Why The Current And Old Versions Both Fail

### Current Reader Record Version

The current V1 Reader Record path outputs unit-level translation. The Chinese
translation is semantically natural, but it is projected only at the end of the
unit. For long units this creates a large source/translation distance, so the
bilingual reading experience is close to unusable.

Observed from current screenshot:

- Source paragraphs, grammar blocks, and sentence analysis appear before a
  single long translation block.
- The translation covers multiple source paragraphs/sentences but has no local
  placement.
- The user must manually remember which English source sentence each Chinese
  clause maps to.

### Old Reader Version

The old version translated per sentence and placed each translation directly
under the corresponding source sentence. That improves immediate alignment, but
creates two product issues:

- Source text becomes one sentence per visual paragraph, making articles look
  like chopped verse, especially when sentences are short.
- English sentence boundaries are not always the right Chinese translation
  boundaries. Translating isolated sentences often produces unnatural Chinese,
  because English-Chinese discourse and sentence compression/expansion differ.

Therefore the right unit is neither full Reading Unit nor mandatory sentence.
It should be a small semantic reading group grounded in stable segments.

## Current Code State

This is based on the current worktree on 2026-06-30.

### Backend V1 Translation Contract

`services/api/app/schemas/reader_orchestration.py`:

- `TranslationLayerOutput` is still V1.
- Shape: `schema_version: Literal[1]`, `target_language`,
  `translated_text`, `notes`, `confidence`.
- There are no item/group outputs, segment ids, or source hashes in the
  translation output schema.

`services/api/app/services/reader_orchestration/translation_worker.py`:

- `TranslationJobContext` still loads one Reading Unit as `source_text`.
- Prompt still starts with `Translate the following reading unit.`
- The worker now includes `reading_goal`, `reading_variant`,
  `strategy_hash`, and `layer_policy_hash` in the prompt section and validates
  those fields fail-closed from job input.

Important delta from the 2026-06-27 synthesis:

- The old statement "current repo boundary does not yet show a first-class
  persisted owner for reading_goal / variant as translation profile truth" is
  no longer accurate.
- Strategy metadata is now present in job identity/input and prompt context.
- What is still missing: V2 output/profile metadata, group-level output, and
  publisher/snapshot validation around groups.

`services/api/app/services/reader_orchestration/job_bootstrap.py`:

- Translation jobs still use `TRANSLATION_OPERATION_FINGERPRINT =
  "translation_unit_v1"`.
- Operation fingerprint is composed with the resolved strategy hash.
- Translation bootstrapping still checks/publishes by
  `layer_type='translation'`, `target_scope='unit'`, `target_key=unit_id`.

`services/api/app/services/reader_orchestration/layer_publisher.py`:

- `publish_unit_translation(...)` only accepts V1 `TranslationLayerOutput`.
- It writes one `enhancement_layers` row per unit with
  `target_scope='unit'`.
- Existing `coverage_json` and `quality_json` columns are available, but not
  used for Translation V2 group coverage yet.

`services/api/app/services/reader_orchestration/snapshot.py`:

- Snapshot validation already allows translation layers with target scope
  `unit` or `anchor_segment`.
- Snapshot builder can group translation layers by target.
- But each translation node still validates as V1 `TranslationLayerOutput` and
  emits one `reader_translation` node containing `output.translated_text`.

### Web Projection State

`apps/web/src/types/api/reader-plate.ts`:

- `ReaderTranslationNodeDto.target_scope` permits `"unit" | "anchor_segment"`.
- The node still has a flat translation text child list, not group coverage.

`apps/web/src/lib/reader-plate/projection/reader-record-plate-document.ts`:

- `buildBlockquoteBlock(...)` returns `null` unless
  `node.target_scope === "unit"`.
- This means anchor-segment translation nodes would currently be dropped by the
  Reader Record Plate projection.
- Unit translation is converted to a `blockquote` block with
  `sourceRole: "unit_translation_text"`.

`apps/web/src/components/editor/plugins/reader-blocks-kit.tsx`:

- `ReaderBlockquoteComponent` renders unit translation as a low-weight
  blockquote/translation lane.
- This is acceptable as a V1 fallback, but the semantics are wrong for V2
  translation groups.

`docs/initiatives/reader-agentic-orchestration/modules/reader-record-plate-surface-ui.md`:

- The document already says Translation V2 should show a translation pair
  group and avoid mechanical chopping.
- Its schema is too minimal: it lacks source text hashes, coverage, quality,
  strategy/profile metadata, group validation, and group-level translation
  ownership.

## External Findings

These are not direct product requirements, but they validate the direction.

- Microsoft Azure Document Translation explicitly emphasizes preserving
  original document structure/data format and preserving source presentation
  layout/format during translation. Claread is not a file translator, but the
  same principle applies: bilingual translation should not destroy document
  structure. Source: <https://learn.microsoft.com/en-us/azure/ai-services/translator/document-translation/overview>
- Immersive Translate defaults to bilingual display, supports toggling original
  text, and has paragraph-hover translation. This reinforces that bilingual
  reading is usually anchored to page/document structure rather than detached
  monolithic translation. Source: <https://immersivetranslate.com/en/docs/usage/>
- Unicode UAX #29 treats sentence boundaries as programmatic text boundaries
  and notes that text alone cannot always decide boundaries unambiguously.
  Sentence boundary detection is useful infrastructure, not a guarantee of a
  good semantic translation unit. Source: <https://unicode.org/reports/tr29/>
- Context-aware/document-level MT research exists because isolated sentence
  translation misses discourse dependencies. Recent work also proposes more
  realistic paragraph-to-paragraph settings for document-level translation.
  Claread's unit-context + small translation-group design matches this
  direction better than sentence-only translation. Source:
  <https://arxiv.org/abs/2305.13751>

## Revised V2 Architecture

### Source Concepts

Keep the existing truth hierarchy:

```text
Stable Document Block
-> Canonical Text Layer
-> Reading Unit
-> Anchor Segment
```

Responsibilities:

- Stable Document Block: source document structure and visual paragraph/list/
  quote/table boundaries. It should guide source flow and prevent article text
  from becoming one sentence per visual paragraph.
- Canonical Text Layer: immutable text coordinate basis.
- Reading Unit: worker context, scheduling, cost control, progress, and layer
  publication target.
- Anchor Segment: stable span anchor/citation unit and minimum validation unit.
- Translation Group: enhancement-layer output over contiguous anchor segments.
  It is not source truth and not a Plate/DOM truth.

### Do Not Rebuild Units For Translation

Translation V2 should not re-split Stable Reading Base just to improve display.
Existing Reading Units and Anchor Segments are facts for the active generation.

If source flow remains visually choppy, fix projection/grouping:

- Build source paragraphs from Stable Document Block/paragraph flow where
  available.
- In the current pure-text transition, group adjacent anchor segments by
  existing source block/unit separator and paragraph metadata.
- Keep anchor segments as inline selectable spans inside those paragraphs.

Future Unit Boundary Refiner can improve long units, but it must only regroup
existing anchor segments for new records/generations and cannot mutate frozen
source facts.

## Proposed Backend Schema

Keep one published translation enhancement layer per Reading Unit:

```text
layer_type = "translation"
target_scope = "unit"
target_key = unit_id
schema_version = 2
output_json = TranslationLayerOutput schema_version=2
coverage_json = group/segment coverage diagnostics
quality_json = model/prompt/profile/confidence diagnostics
```

Recommended output shape:

```ts
type TranslationLayerOutputV2 = {
  schema_version: 2;
  target_language: string;
  source_language: string;
  profile: {
    reading_goal: "daily_reading" | "exam";
    reading_variant: string;
    strategy_version: string;
    strategy_hash: string;
    layer_policy_hash: string;
  };
  groups: Array<{
    group_id: string;
    anchor_segment_ids: string[];
    source_text: string;
    source_text_hash: string;
    segment_sources: Array<{
      anchor_segment_id: string;
      source_text: string;
      source_text_hash: string;
      segment_type: string;
      boundary_quality: string;
    }>;
    translated_text: string;
    confidence: "low" | "normal" | "high";
    reason:
      | "single_segment"
      | "semantic_unit"
      | "short_segment_merge"
      | "quote_flow"
      | "contrast_pair"
      | "low_boundary_isolation"
      | "other";
  }>;
  full_translation?: string;
  notes: string[];
  diagnostics: string[];
};
```

Rationale:

- `groups[].translated_text` is the text shown in the bilingual lane.
- `segment_sources[]` preserves stable grounding and hash checks.
- `source_text_hash` prevents the LLM from translating stale or invented
  source text.
- `profile` makes strategy identity visible in the durable output and future
  snapshot/Ask context.
- `full_translation` is fallback/reference only, not the default display.

Do not make group ids source truth. They can be deterministic output ids, for
example:

```text
translation_group:{layer_id}:{first_anchor_segment_id}:{last_anchor_segment_id}
```

### Worker Prompt Direction

Worker should still receive the whole unit for context, but the target task
should be group-aware:

- Input: unit text, ordered target segments, segment hashes, segment types,
  boundary qualities, strategy profile.
- Instruction: produce contiguous translation groups that preserve reading
  flow and natural Chinese.
- Constraint: groups may merge adjacent short/connected segments, but may not
  cross unit boundary and should not cross Stable Document Block / quote/list
  boundary once that metadata is available.
- Constraint: do not produce per-word or UI placement instructions.

### Publisher Validation

Publisher must fail closed on:

- unknown `anchor_segment_id`;
- segment does not belong to target unit;
- `segment_sources[].source_text_hash` mismatch;
- group `source_text_hash` mismatch;
- empty translated text;
- duplicated segment coverage;
- non-contiguous segment ids in one group;
- overlapping groups;
- group crossing disallowed structure boundary when block metadata is present;
- V2 profile metadata mismatching job input/strategy context.

Missing segment coverage should be explicit in `coverage_json`, not silent.
Default policy should be:

- publish complete V2 groups when all required segment coverage is present;
- if some groups fail validation, reject the V2 output and retry/fail the job;
- fall back to V1 unit translation display only when there is no V2 layer for
  that unit or a deliberate compatibility path marks V2 unavailable.

### Snapshot Shape

Snapshot should not flatten a V2 group back into a V1 `reader_translation`
blockquote.

Recommended new node:

```ts
type ReaderTranslationGroupNodeDto = {
  type: "reader_translation_group";
  owner: "system_ai";
  layer_id: string;
  layer_version: number;
  base_id: string;
  unit_id: string;
  group_id: string;
  target_language: string;
  covered_anchor_segment_ids: string[];
  source_text_hash: string;
  confidence: "low" | "normal" | "high";
  reason: string;
  children: Array<{ text: string }>;
};
```

The snapshot can keep existing V1 `reader_translation` for compatibility. Web
projection should prefer V2 `reader_translation_group` when present and show V1
unit translation only as fallback.

## Proposed Web Projection And Display

### Source Flow

The page should stop treating every anchor segment as an independent visual
paragraph in intensive mode.

Desired projection:

```text
source paragraph group
  inline anchor segment spans
translation group lane for covered segments
grammar/sentence analysis blocks where needed
next source paragraph group
```

If a translation group covers two adjacent source sentences inside one
paragraph, the source should still look like one paragraph, not two standalone
paragraph cards.

### Translation Lane

Use a dedicated `reader_translation_group` / `reader_record_translation_group`
element rather than generic `blockquote` semantics.

Visual rules:

- source remains primary;
- translation uses smaller sans text, muted color, and a left rule/quiet lane;
- spacing is tight enough to preserve source/translation association;
- no visible "本段译文" label by default;
- no card-heavy boxes;
- intensive mode shows translation groups by default;
- immersive mode hides translation groups and analysis blocks.

### Selection / Ask / Feedback

Translation selection can remain copy/Ask capable, but source-grounded context
must map back to covered source segments:

```text
translation selection
-> translation group node
-> layer_id + group_id
-> covered_anchor_segment_ids
-> unit source context
```

Highlight/note writes should remain disabled on AI translation text unless a
future user-asset contract explicitly supports translation-owned anchors.

## Docs Impact

### `translation-v2-review-synthesis-2026-06-27.md`

Keep as historical evidence, but mark as superseded or delete after accepted
conclusions are compressed into official docs.

Still valid:

- do not split V1 Chinese translation on the frontend;
- do not re-split Stable Reading Base for display;
- Reading Unit remains context window;
- Anchor Segment remains grounding/citation unit;
- Plate/DOM/Slate path are projection only;
- unit-level V1 translation is fallback only;
- source text should flow by document/paragraph rather than sentence blocks.

Needs update:

- V2.0 should not be item-only per segment. Current product requirement pushes
  group-level translation into V2.0.
- The old "profile owner missing" concern is stale. Strategy metadata now
  exists in job identity/input and worker prompt, though output/snapshot still
  need metadata.
- Projection grouping should consume worker-produced translation groups rather
  than inventing semantic groups entirely on the frontend.

### Official docs to update after design acceptance

- `docs/initiatives/reader-agentic-orchestration/modules/reader-record-plate-surface-ui.md`
  - replace simplified V2 schema with group-level schema and display rules;
  - document V1 fallback vs V2 group display.
- `docs/initiatives/reader-agentic-orchestration/modules/schema-and-domain-contract.md`
  - add TranslationLayerOutput V2 validation/fail-closed contract.
- `docs/initiatives/reader-agentic-orchestration/modules/reading-base-and-units.md`
  - clarify that Translation Groups are enhancement output over contiguous
    Anchor Segments and do not mutate Reading Units/Anchor Segments.
- `docs/initiatives/reader-agentic-orchestration/implementation-plan.md`
  - add Translation V2 phased implementation.

## Implementation Plan

### Phase 1: Backend V2 Contract

Files likely involved:

- `services/api/app/schemas/reader_orchestration.py`
- `services/api/app/services/reader_orchestration/translation_worker.py`
- `services/api/app/services/reader_orchestration/layer_publisher.py`
- `services/api/app/services/reader_orchestration/snapshot.py`
- focused backend tests around worker, publisher, snapshot.

Tasks:

1. Add V2 output schema beside V1.
2. Load ordered anchor segments into `TranslationJobContext`.
3. Update prompt to ask for contiguous translation groups.
4. Update operation fingerprint base to cover V2 schema/prompt version.
5. Add publisher validation for groups/hashes/coverage.
6. Publish V2 in existing enhancement layer storage.
7. Keep V1 output/display fallback.

### Phase 2: Snapshot / DTO / Web Projection

Files likely involved:

- `apps/web/src/types/api/reader-plate.ts`
- `apps/web/src/lib/reader-plate/projection/reader-record-plate-document.ts`
- `apps/web/src/lib/reader-plate/projection/reader-record-plate-to-plate-value.ts`
- `apps/web/src/components/editor/plugins/reader-blocks-kit.tsx`
- `apps/web/src/components/reader/plate/ReaderRecordPlateSurface.test.tsx`

Tasks:

1. Add `reader_translation_group` DTO/type.
2. Add ReaderRecordPlateDocument translation group block.
3. Prefer V2 groups; fall back to V1 unit blockquote.
4. Preserve source paragraph flow with inline anchor metadata.
5. Keep selection/Ask source context mapped to covered segments.
6. Add grouping/rendering tests, including multi-segment group and V1 fallback.

### Phase 3: Visual And UX QA

Tasks:

1. Create representative fixtures:
   - short sentences;
   - quote-heavy paragraph;
   - paragraph where two English sentences become one Chinese sentence;
   - long single paragraph;
   - grammar note interruption;
   - low-boundary-quality fallback segment.
2. Browser QA for intensive/immersive modes.
3. Verify the page no longer looks like one sentence per paragraph.
4. Verify translation is close enough to source and visually subordinate.

## Risks And Open Decisions

- Group size policy: default 1-3 anchor segments, but exact max should be
  validated with sample texts.
- Grammar/sentence-analysis interruption: if a note must appear immediately
  after a source span, projection may need to split a visual source/translation
  group while preserving source group truth.
- Stable Document Blocks availability: current transition still relies on
  `reading_bases.text` and heuristic paragraph metadata. V2 should work with
  that, but richer document blocks will improve boundary quality.
- Ask/RAG indexing: translation groups can be visible context, but citations
  must resolve to source segment ids, not group ids alone.
- Evaluation: need a small manual/eval set for English->Chinese naturalness and
  bilingual placement, not only schema tests.

## Recommended Next Agent Task

Do not start coding all phases at once.

Next task should be a backend-focused design spike:

1. Draft concrete Python/Pydantic V2 schema.
2. Draft publisher validation algorithm.
3. Draft worker input/prompt shape with ordered target segments.
4. Identify exact tests to add before implementation.
5. Confirm whether `target_scope='unit'` remains the durable layer target for
   V2 groups.

Only after this design spike is reviewed should implementation start.
