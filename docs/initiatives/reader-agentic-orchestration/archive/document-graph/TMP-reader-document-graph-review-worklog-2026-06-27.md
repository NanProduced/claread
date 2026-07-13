# TMP: ReaderDocumentGraph Review Worklog

Date: 2026-06-27

Scope:

- Source design: `docs/tmp/reader-orchestration/TMP-reader-document-graph-design-2026-06-27.md`
- Reviews: `docs/tmp/reader-orchestration/review/reader-document-graph-design-review-{1..6}.md`
- Purpose: record per-review judgement before producing synthesis.

## Review 1

Verdict: conditionally accept.

Valid findings:

- The strongest point is the contract boundary: `ReaderDocumentGraph` currently overlaps with `ReaderPlateSnapshot` and `ReaderRecordPlateDocument`. If kept, it must be upstream/internal to snapshot assembly or explicitly replace the snapshot contract. It cannot sit beside it.
- `TranslationDisplayGroup` should not automatically become persistent worker truth. A better split is worker output as per-segment domain translation plus optional grouping hints, while projection derives display groups.
- Non-source anchors have a real rebase problem. Translation, grammar, sentence analysis, and supplements are regenerable; persistent user assets should not depend on their volatile text ranges in V1.
- Vocabulary should be modeled as marks/resolutions on source spans, not as independent document-flow nodes.
- `order: string` is under-specified. A deterministic placement policy based on source block/segment order is safer than arbitrary fractional ordering in V1.

Possible overreach:

- It suggests Graph must be only an internal backend assembler. This is directionally safe, but may understate the product value of a renderer-neutral view model shared by Plate, mini-program, export, and Ask.
- It treats display groups as purely projection-derived. Some semantic hints from worker may still be useful, but they should not be durable layout truth.

Design changes to carry forward:

- Reframe Graph as `ReaderPlateSnapshot` builder/view model, not a new API truth.
- Move `display_groups` out of Translation Layer truth; allow `grouping_hints` at most.
- V1 write assets only to stable source. Ask can read non-source scopes.
- Add Graph node type to current Plate node/mark mapping table.

## Review 2

Verdict: conditionally accept, more conservative than review 1.

Valid findings:

- It correctly points out that the proposed Graph, `ProjectionAnchor`, and `TranslationDisplayGroup` duplicate existing concepts: `ReaderPlateSnapshot`, `UserEditorialAssetAnchor.scope`, and Translation V2 draft.
- It is right that `graph_version` plus `last_event_sequence` creates dual versioning. Runtime freshness should continue to key off `last_event_sequence`; a separate graph schema version can exist only as code/cache metadata.
- It correctly objects to `user_note` as an anchor scope. User note is an asset/owner concept, not a source text scope.
- It adds an important privacy boundary: Ask should not automatically read all user notes. User notes should be included only when explicitly selected, referenced, or permitted by a clear product policy.
- It correctly requires projection op alignment if any Graph nodes become part of the event/recovery path.

Possible overreach:

- Recommendation A, "do not introduce ReaderDocumentGraph as a top-level new term", may be too strict. The term is useful as a product/architecture mental model if we define it as a view model, not truth or wire DTO.
- It says front-end deterministic grouping is enough; this may be too mechanical for natural bilingual layout. A hybrid "domain items + deterministic projection + optional semantic hints" is better.

Design changes to carry forward:

- Do not rename the formal snapshot contract around Graph in Phase 0.
- Extend `UserEditorialAssetAnchor` carefully instead of creating a parallel durable anchor contract.
- Remove `user_note` from `ProjectionAnchor.scope`; model user note as asset with origin/source grounding.
- Explicit read/write split for Ask versus user asset persistence.

## Review 3

Verdict: conditionally accept.

Valid findings:

- It identifies a real ambiguity: `ReaderDocumentGraph` and `ReaderRecordPlateDocument` are both product semantic projections unless their mapping is explicit.
- It correctly asks for deterministic display group generation and quantified rules.
- It correctly argues non-source note persistence requires rebase/orphan behavior. Silent migration is unacceptable.
- It points out a useful modeling issue: `grammar_cue` versus `grammar_note`, and `sentence_analysis_cue` versus `sentence_analysis`, mix semantic type and display state.
- It calls out `display_policy: Record<string, unknown>` as too loose for a contract-worthy design.

Possible overreach:

- It assumes display groups should be fully deterministic and never worker-influenced. That is safest for layout stability, but semantic grouping hints may still help with "important transition" or quote boundaries.
- It raises RAG indexing early. Good to keep as a non-goal/constraint, but not needed for Phase 1 implementation.

Design changes to carry forward:

- Type `display_policy` and keep cue/expanded state as display policy where possible.
- Add explicit non-source scope lifecycle states: read-only, stale, orphaned, source-fallback.
- Define Ask scope priority when selection spans multiple visible scopes.

## Review 4

Verdict: conditionally accept.

Valid findings:

- It makes the clearest write/read distinction: persistent user assets should anchor to stable source; non-source scopes can be temporary Ask references.
- It correctly notes that `ProjectionAnchor.scope` currently mixes content type and ownership; `user_note` is especially wrong as a scope.
- It adds the necessary source hash/alignment failure policy for Translation V2.
- It points out long-document windowing needs a specific boundary and cache key before becoming design.
- It revalidates the current doc/code conflict: formal docs say sentence analysis cue-only, current code still projects callouts.

Possible overreach:

- It suggests `display_groups` should carry a group source hash. This may be useful for validation, but if groups are projection-derived, the stronger invariant is per-segment source hash; group hash can remain cache/debug metadata.
- It sketches a window API too early. We should document it as Phase 3+, not Phase 1.

Design changes to carry forward:

- V1 user note/highlight writes only stable source.
- Translation V2 must include source text/hash per item and fail closed or mark alignment failure when source grounding breaks.
- Ask can resolve non-source visible text but must always include source grounding.

## Review 5

Verdict: conditionally accept, implementation-oriented.

Valid findings:

- It asks who builds Graph: server, client, or BFF. Current design is vague. Best answer: Phase 1 should be snapshot/BFF/server-side builder, with front-end still receiving a stable DTO.
- It correctly asks for cache key and invalidation. `(record_id, base_id, generation, last_event_sequence, graph_schema_version)` is enough for disposable cache.
- It proposes context templates per scope for Ask. This should enter the design because Ask quality depends on context shape, not just anchor shape.
- It requires characterization tests for translation grouping, grammar breakpoints, sentence analysis, anchor validity, and Ask context.
- It highlights backward compatibility with current `ReaderPlateSnapshot`.

Possible overreach:

- It recommends materialized cache if Graph generation is over 100ms. We should not set this as an immediate rule without benchmark; prefer no persistent cache in Phase 1.
- It suggests stable string orders such as `block_001:source:0`. That is implementable, but we should avoid encoding display taxonomy into an ordering string if a typed placement tuple is clearer.

Design changes to carry forward:

- Phase 1: full snapshot/graph rebuild, no materialized backend table.
- Add benchmark/test target before considering cache.
- Add Ask context template matrix by scope.
- Add migration path: existing snapshot remains public contract while Graph/view model is introduced under it.

## Review 6

Verdict: accept direction, reject current shape.

Valid findings:

- It is right that several immediate visual problems do not require Graph: remove/replace old selection strip, make callout content real Plate/static Plate children, and move sentence analysis to cue-only.
- It correctly says Graph should not be introduced before these V1a/V1c UI contract issues are understood. Otherwise we risk adding architecture before fixing projection misuse.
- It strongly supports splitting Translation V2 into domain `items` and projection `display_groups`; this is consistent with reviews 1, 2, 3, and 4.
- It correctly rejects V1 persistent notes on AI text due to rebase risk.
- It frames Graph as a view function over snapshot, which is a safer starting point than a new backend read model.

Possible overreach:

- It may understate the need for a unified visible-document context model for Ask. Fixing UI projection alone will not solve "Ask sees different structure than user sees".
- It proposes pushing Graph to V2+. I think we should instead split the work: immediate UI projection fixes happen first, while the Graph/view model is specified now as the target architecture but implemented minimally.
- Its proposed float order string is not obviously better than deterministic typed placement. Use typed placement first.

Design changes to carry forward:

- Reorder phases: V1 UI projection fixes and Translation V2 domain/display split come before any durable Graph model.
- Keep Graph as terminology and view model, not a persisted read model.
- `ProjectionAnchor` is read-capable for Ask across scopes but write-capable only for stable source in V1.

## Cross-Review Synthesis

Strong consensus:

- Direction is accepted only conditionally. No review rejects the principle that Plate should remain renderer/projection and stable source remains truth.
- The current draft overstates `ReaderDocumentGraph` as a new layer. It must be reframed as a snapshot builder/view model unless we intentionally replace `ReaderPlateSnapshot`.
- `display_groups` should not be durable Translation Layer truth. Translation domain output should be per-anchor-segment items with source hash; grouping is projection policy, with optional hints at most.
- V1 persistent user assets should remain stable-source-only. Non-source anchors are acceptable for Ask/read context, not durable note/highlight writes.
- `ProjectionAnchor` should not introduce `user_note` scope, Plate/node path-like durable targets, or a competing version cursor.
- Sentence analysis should stop defaulting to document-flow callout; cue-only or cue + user-triggered compact expansion is the desired document style.
- Display policy, ordering, Ask context resolver, and cache invalidation need typed contracts before implementation.

Real conflicts between reviews:

- Graph timing: reviews 1/3/4/5 allow Phase 1 Graph as BFF/snapshot projection; review 6 wants Graph after V1a fixes. Recommended synthesis: specify Graph now, implement immediate UI projection fixes first, then add a minimal Graph/view-model builder without public contract churn.
- Display grouping owner: some reviews say fully deterministic projection; some allow worker hints. Recommended synthesis: persistent truth is deterministic/validated items; grouping output is projection-derived; worker may output non-authoritative `placement_hints` only after validation rules exist.
- Graph exposure: some want server/BFF Graph field; some want front-end view function only. Recommended synthesis: public API remains `ReaderPlateSnapshot`; Graph can be an internal builder or optional debug/view-model field during migration, not a required wire contract.

Likely mistaken or weaker claims:

- "Graph is unnecessary because UI can be fixed directly" is only partly true. It fixes visual card-ness, but not Ask/user-visible context consistency. The design should separate these goals instead of claiming Graph is the only UI fix.
- "display_groups must have stable persistent group_id" is weaker if groups are projection-derived. Stable ids are useful for rendering/citation but can be derived from `(layer_id, anchor_segment_ids, group_policy_version)`.
- "materialize Graph when >100ms" is premature. Use benchmark and cache only after measuring realistic long documents.

Recommended revised design stance:

1. Keep stable source truth exactly as is: Stable Document Blocks + Canonical Text + Reading Units + Anchor Segments.
2. Keep `ReaderPlateSnapshot` as the public BFF/API snapshot contract for now.
3. Rename/reframe `ReaderDocumentGraph` to "Reader Document View Model" or "Snapshot Value Builder" in the design. It is renderer-neutral and rebuildable, not truth.
4. Translation V2 domain output becomes per-segment translation items with source hash. Display groups become projection/read-model output.
5. Projection anchors split by capability:
   - Ask/read: can target stable source, translation display group, system layer item, ask supplement, selected user asset.
   - Write assets: V1 only stable source single range.
6. User notes are assets, not anchor scopes. Ask does not read all notes by default.
7. Add deterministic typed placement/order policy rather than free string order.
8. Add typed display policy; cue/expanded is display state, not always a separate semantic node type.
9. Phase order should be adjusted:
   - Phase 0: doc terminology and current UI projection debt alignment.
   - Phase 1: UI projection fixes on current Plate path.
   - Phase 2: Translation V2 domain/display split.
   - Phase 3: document view model/snapshot builder feeding Plate and Ask.
   - Phase 4: broader ProjectionAnchor read scopes for Ask.
   - Phase 5+: non-source persistent notes only with explicit rebase/orphan policy.
