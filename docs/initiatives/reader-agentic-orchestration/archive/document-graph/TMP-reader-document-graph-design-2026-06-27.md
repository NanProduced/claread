# TMP: Reader Document View Model Design

Date: 2026-06-27

Status: review-revised draft; 2026-07-01 UI closeout amended

Related formal modules:

- `docs/initiatives/reader-agentic-orchestration/modules/reading-base-and-units.md`
- `docs/initiatives/reader-agentic-orchestration/modules/plate-reader-projection.md`
- `docs/initiatives/reader-agentic-orchestration/modules/enhancement-layers-and-parsed.md`
- `docs/initiatives/reader-agentic-orchestration/modules/reader-record-plate-surface-ui.md`
- `docs/initiatives/reader-agentic-orchestration/modules/reader-plate-component-integration.md`

Review inputs:

- `docs/tmp/reader-orchestration/review/reader-document-graph-design-review-{1..6}.md`
- `docs/tmp/reader-orchestration/review/TMP-reader-document-graph-review-worklog-2026-06-27.md`

## Summary

The previous draft introduced `ReaderDocumentGraph` as a product semantic layer between Stable Source Truth and Plate.js. The direction was correct, but the review found that it overlapped with existing contracts:

- `ReaderPlateSnapshot`
- `ReaderRecordPlateDocument`
- `UserEditorialAssetAnchor`
- existing Translation V2 draft

This revision keeps the core idea but changes the architecture stance:

```text
Stable Source Truth
-> ReaderPlateSnapshot public contract
-> Reader Document View Model / Snapshot Value Builder
-> Plate.js read-only document + Ask Claread context
```

`ReaderDocumentGraph` should not become a new backend truth or a second public API contract in the next implementation slice. The safer term is **Reader Document View Model** or **Snapshot Value Builder**.

It is a rebuildable projection model used to make Plate rendering and Ask context resolve from the same visible-document structure.

2026-07-01 closeout: the earlier per-segment Translation V2 recommendation in this TMP has been superseded by the implemented group-native translation contract. The current backend layer persists `TranslationLayerOutput.groups[]`; the worker decides semantic groups within a Reading Unit, and Web projection renders `source group paragraph -> translation blockquote -> annotations`. This TMP should no longer be used to ask agents for per-segment translation items or unit-level fallback display.

## Core Decision

Keep these facts stable:

- `reading_bases.text` / Canonical Text remains the offset baseline.
- Stable Document Blocks preserve document structure.
- Reading Units and Anchor Segments preserve orchestration and grounding boundaries.
- Enhancement Layers remain typed domain outputs.
- User assets remain user-owned facts anchored through `UserEditorialAssetAnchor`.
- `ReaderPlateSnapshot` remains the public BFF/API snapshot contract.

Add or refine these projection responsibilities:

- A renderer-neutral **Reader Document View Model** is built inside snapshot/projection assembly.
- Plate.js renders from that view model, but does not become the business truth.
- Ask Claread resolves context from the same view model shape or from an equivalent server-side resolver.
- Translation V2 separates domain translation items from display grouping.
- Projection anchors are read-capable across visible scopes, but V1 user writes remain stable-source-only.

### Confirmed Product Direction

Use **Anchor-backed Plate Document** as the Reader Record surface model:

- backend truth remains Stable Reading Document + Enhancement Layers + User Assets + source-grounded anchors;
- Plate.js is the real interactive document surface, not a cosmetic wrapper around custom HTML;
- persisted user notes/highlights are user assets with source-grounded anchor ranges;
- Plate comment/highlight marks are projection and interaction primitives, not the durable business database;
- Ask Claread resolves from visible selection plus source-grounded `anchor_set` and full related context.

Confirmed UI decisions:

- Only two reader modes exist: `intensive` and `immersive`.
- `intensive` shows source, translation lane/groups, grammar note callouts, sentence analysis structure blocks, vocabulary marks, user highlights, and user notes.
- `immersive` hides translation, grammar explanation blocks, and sentence analysis blocks; it keeps source text, lightweight lexical marks, user highlights, user notes, selection, Ask, and note/highlight affordances.
- Translation is a Plate-selectable, quote-like auxiliary lane in intensive mode; it is hidden in immersive mode.
- `grammar_note` is the existing span-bound grammar explanation. It must render as a Plate/Notion-like callout and must not be renamed to Grammar X-Ray.
- `sentence_analysis` is the existing long-sentence / sentence-structure analysis block. It remains a Claread-specific structure block, not `grammar_note` and not future Grammar X-Ray.
- Grammar X-Ray is a future planned capability and must not occupy the current `grammar_note` / `sentence_analysis` naming or visual weight.
- Annotation content uses a fixed annotation/UI sans stack. Source text remains user-font-switchable. Translation is an auxiliary reading lane and should keep low-voice source alignment rather than becoming a loud analysis block.
- AI explanation blocks may use a weak two-layer header: a small type label such as `语法解析` / `长句拆析`, then the specific title. The header is navigation, not the main content, and must stay visually weaker than the source text.
- User highlight uses three semantic colors only: yellow, blue, rose. The exact inline annotation visual system is still under review; see "Deferred Annotation Visual Matrix" below.
- Clicking `vocab_highlight` opens dictionary quick peek and may call dictionary lookup. Clicking `phrase_gloss` / `context_gloss` opens existing explanation. Ordinary source-text single click does not trigger lookup by default.
- Global selection may start from source, translation, grammar note, sentence analysis, supplement, user note quote, or mixed content. Durable assets still map to source anchors; impossible-to-ground UI text can only use degraded Ask.

## Non-Goals

- Do not persist raw Plate/Slate JSON as source truth.
- Do not let LLM workers output raw Plate nodes, Plate paths, or Slate operations.
- Do not replace `ReaderPlateSnapshot` with a new public `ReaderDocumentGraph` contract in V1.
- Do not add a backend `reader_document_graph_nodes` table in V1.
- Do not allow persistent V1 notes/highlights on regenerable AI text.
- Do not make Plate path, DOM selection, or node id a durable anchor target.

## Terms

### Stable Source Truth

The stable reading source after input preview/confirmation:

- Stable Document Blocks: document structure such as paragraph, heading, list item, blockquote, table, code block, etc.
- Canonical Text: plain text derived from main reading blocks, used for UTF-16 offsets and hashes.
- Reading Units: orchestration windows for AI workers.
- Anchor Segments: stable source spans, usually sentence-like, used for grounding.

### ReaderPlateSnapshot

The current public snapshot contract. It remains the BFF/API boundary for Web.

It includes source metadata, navigation, enhancement layers, ask supplements, user assets, parsed decisions, and a Plate-consumable `value`.

This revision does not rename or replace it.

### Reader Document View Model

A rebuildable projection model created by snapshot/projection assembly.

It answers:

- What visible document nodes should the user see?
- Which source anchors or layer items do these visible nodes come from?
- Which nodes can Ask reference?
- Which visible text can be selected, and which selections can be persisted as user assets?

It is not a storage model. It can be implemented as:

- a server/BFF-side snapshot builder,
- a frontend pure view function over `ReaderPlateSnapshot`,
- or both during migration, as long as the public contract remains stable.

### Translation Items

Historical term from the earlier V2 draft. Current implementation does not persist one
translation item per Anchor Segment. The durable translation layer is group-native:
`TranslationLayerOutput.groups[]` carries `group_id`, `anchor_segment_ids`,
`source_text_hash`, and `translated_text`.

### Translation Display Groups

Current implementation: translation groups are no longer a purely derived projection artifact.
The LLM outputs semantic group handles and translated text; the server hydrates deterministic
`group_id` and source hash, then snapshot emits `reader_translation_group`.

Projection still owns display placement:

- source group paragraph construction,
- translation blockquote placement,
- annotation ordering after translation,
- Stable Document Block boundaries,
- Anchor Segment order,
- grammar/sentence-analysis presence,
- display mode and projection policy.

`group_id` remains a render/coverage key, not a stable source anchor or user-asset target.

### Projection Anchor

A visible selection reference used by Plate selection and Ask.

It is broader than V1 stable-source anchors for reading/Ask purposes, but it must not become a competing durable user asset anchor contract.

For persistence, V1 still writes `UserEditorialAssetAnchor` against stable source only.

## Target Data Flow

### Input To Stable Source

```text
User input
-> input adapter / normalization
-> preview document
-> user confirmation
-> Stable Document Blocks
-> Canonical Text
-> Reading Units
-> Anchor Segments
```

The input stage should preserve or create document structure that helps rendering:

- paragraph breaks,
- headings,
- lists,
- blockquotes,
- footnotes or references when detectable.

It should not put Markdown syntax into the canonical text offset baseline.

### Orchestration

```text
Stable Source Truth
-> unit/segment-based orchestration
-> Translation / Vocabulary / Grammar / Sentence Analysis workers
-> Enhancement Layers
-> layer publisher validation
```

Workers may still run per Reading Unit for cost and context control.

Published layer outputs must remain grounded in stable source targets.

### Snapshot And Projection

```text
Stable Source Truth
+ Enhancement Layers
+ User Assets
+ Ask Supplements
-> ReaderPlateSnapshot assembly
-> Reader Document View Model / Snapshot Value Builder
-> ReaderPlateSnapshot.value
-> Plate.js read-only document
```

Ask Claread should resolve context from the same visible-document model:

```text
Plate selection / active cue / selected asset
-> Projection Anchor
-> Ask Context Resolver
-> source grounding + visible context + relevant layer facts
-> Ask Claread
```

## View Model Shape

The exact TypeScript/Python type can be finalized later. Conceptually, each visible node should carry:

```ts
type ReaderDocumentViewNode = {
  id: string;
  kind:
    | "source_block"
    | "source_segment"
    | "translation_group"
    | "vocabulary_mark"
    | "grammar_note"
    | "sentence_analysis"
    | "ask_supplement"
    | "user_asset_indicator";
  owner: "stable_source" | "system_ai" | "ask_supplement" | "user";
  origin: {
    block_id?: string;
    unit_id?: string;
    anchor_segment_ids?: string[];
    layer_id?: string;
    layer_item_id?: string;
    supplement_id?: string;
    asset_id?: string;
  };
  placement: ReaderDocumentPlacement;
  display: ReaderDocumentDisplayPolicy;
  anchors: ProjectionAnchor[];
  children?: ReaderDocumentViewNode[];
};
```

### Placement

Use typed placement, not arbitrary string ordering:

```ts
type ReaderDocumentPlacement = {
  block_id: string;
  after_anchor_segment_id?: string;
  before_anchor_segment_id?: string;
  lane:
    | "source"
    | "translation"
    | "inline_mark"
    | "system_cue"
    | "supplement_cue"
    | "user_cue";
  priority: number;
};
```

Rules:

- Source order comes from Stable Document Blocks and Anchor Segments.
- AI nodes never reorder source text.
- Translation groups are inserted after their covered source segment group.
- Grammar notes and sentence analysis appear as low-interruption cues by default.
- Cross-block insertion is disallowed in V1.

### Display Policy

Use typed display policy, not `Record<string, unknown>`:

```ts
type ReaderDocumentDisplayPolicy = {
  mode_visibility: {
    immersive: "hidden" | "cue" | "inline" | "block";
    intensive: "hidden" | "cue" | "inline" | "block";
  };
  default_expanded: boolean;
  user_expandable: boolean;
  density: "compact" | "normal" | "detailed";
};
```

Cue versus expanded content is display state. It should not always require separate semantic node types.

## Translation V2

### Domain Output

Superseded draft note: this section originally recommended per-segment items plus derived
display groups. That direction was intentionally replaced during implementation because it
made the LLM behave like it was filling a row-by-row table and hurt natural Chinese
bilingual reading. Current truth is group-native:

```ts
type TranslationLayerOutput = {
  groups: Array<{
    group_id: string;
    anchor_segment_ids: string[];
    source_text_hash: string;
    translated_text: string;
  }>;
};
```

Validation:

- worker still runs per Reading Unit for context and cost control;
- LLM only outputs `groups[].anchor_segment_ids` and `groups[].translated_text`;
- server hydrates deterministic `group_id` and `source_text_hash`;
- publisher validates unknown anchors, contiguous order, no overlap, complete coverage,
  stable source slice hash, fingerprint base, and non-empty translation text;
- source text, source language, target language, confidence, reason, notes, diagnostics,
  raw Plate JSON and UI instructions are not part of the translation output contract.

### Display Groups

Display placement is projection policy, not a second translation truth.

Grouping rules:

- Do not cross Stable Document Block boundaries.
- Do not set numeric group size thresholds.
- Do not mechanically create one group per anchor segment.
- Let the LLM decide semantic reading groups within a unit.
- Do not split Chinese translation text after publication.
- If grammar_note / sentence_analysis requires visual interruption inside a long group,
  handle that as display-only layout around the source/annotation nodes; do not create new
  translation facts.
- Keep quote/list/table boundaries intact.

Decision status: superseded by implemented group-native contract:

- keep Reading Unit as the worker context window;
- pass ordered Anchor Segment targets into the translation worker;
- persist server-hydrated translation groups as layer truth;
- do not persist per-segment source echo or full unit translation fallback;
- projection renders each valid group as one source paragraph plus one translation blockquote;
- annotation blocks appear after the translation for the group they explain.

Grill Q1 decision: revised by implementation. Group-native translation is the accepted current contract.

Grill Q2 decision: accepted. Multi-variant translation is blocked until `translation_profile` is part of job identity, operation fingerprint, and layer/snapshot metadata.

Grill Q3 decision: closed for current source rendering. Web projection now renders source by
translation group span, with anchor segments preserved as leaf metadata rather than standalone
visual paragraphs.

Grill Q4 decision: accepted. Do not add durable View Model caching initially; use pure builder plus frontend memory caching and revisit only after benchmark evidence.

Grill Q5 decision: accepted/deferred. Do not consider mini-program consumption in this version; mini-program is expected to be reworked later and can reassess reuse then.

See `docs/tmp/reader-orchestration/review/translation-v2-review-synthesis-2026-06-27.md`.

## Grammar, Sentence Analysis, And Vocabulary

### Grammar

`grammar_note` remains a system AI layer item.

Default projection:

- keep a subtle underline/cue on the relevant source span,
- render the explanation as a visible Plate-native callout / annotation block after the relevant source/translation group,
- do not hide grammar notes behind hover by default,
- allow explicit reader modes to reduce density later, but the default intensive reading experience should feel like handwritten notes in a study text.

Grammar callouts may receive Markdown-formatted content. The projection path should be:

```text
grammar_note.note markdown
-> Markdown deserialize
-> Plate children
-> Plate callout element
```

The callout component must render Plate children or Plate-static children. It should not use isolated custom HTML recursion for selectable content.

### Sentence Analysis

`sentence_analysis` is a structure layer, not a generic callout and not a default-collapsed toggle.

Default projection:

- render an always-open `reader_sentence_analysis_block`,
- visually borrow the hierarchy and indentation of a document-native structure section,
- show structured `chunks` as first-class sentence-component rows,
- render `analysis` Markdown as Plate children below the structured rows,
- keep the block compact enough to read inline with the article.

This replaces the current "analysis as normal callout card" approach. The goal is a document-native structure note, not a business card and not a collapsed disclosure.

Current code still projects sentence analysis as document-flow callout. This is UI projection debt and should be fixed before or during the next UI slice.

### Vocabulary

Vocabulary is primarily marks/resolutions on stable source spans, not independent document-flow nodes.

The view model may expose vocabulary mark metadata for:

- hover/peek,
- dictionary rail,
- mark conflict resolution,
- Ask context grounding.

It should not insert vocabulary cards into the reading flow by default.

## Projection Anchor Capability

Separate read capability from write capability.

Selection capability is broader than persistence capability:

- Visible document text should be selectable through Plate wherever practical.
- Lookup, Ask, and Copy can operate on any selectable visible text.
- Persistent V1 note/highlight writes remain stable-source-only.

### Read / Ask Capability

Ask can reference visible scopes:

- stable source selection,
- translation display group,
- grammar note,
- sentence analysis,
- ask supplement,
- selected user asset.

The resolver must always return source grounding when the selected node is not source text.

Example:

```text
User selects translated text
-> resolver returns selected translation
-> corresponding source segments
-> surrounding source context
-> translation layer id/item ids
```

### Persistent User Asset Write Capability

Target design writes user assets against source-grounded anchor ranges:

- highlight,
- user note.

Current V1c code may remain single-range first while the Reading Record write path is stabilized. That is an implementation stage, not the final product constraint. The target persistence contract should allow 1..N source ranges per asset and preserve visible-selection provenance.

Use existing `UserEditorialAssetAnchor` for the current single-range path. Use `UserEditorialAssetAnchorSet` or an equivalent `user_asset_anchor_ranges` contract for multi-range notes/highlights.

Canonical product term: **User Note / personal reading note**.

- Claread does not model collaborative comments in the Reader Record surface.
- Use the official Plate Comment component/model for the note UI surface, but remove collaboration semantics from the product layer.
- Plate Comment UI is used only as a read-only document interaction/projection primitive: mark, indicator, active state, and note-like panel affordance.
- `reader_notes` / `UserEditorialAsset` remain the domain truth.
- Plate `comment_<noteId>` ids are Web projection keys derived from `reader_notes.id`; they are not business ids.
- V1 does not support collaborators, replies, mentions, resolved/archive state, or comment permissions.

Overlapping and substring notes:

- Domain persistence allows multiple independent User Notes whose anchors overlap, contain one another, or share the same source segment.
- The persistence layer should not merge overlapping notes. Each saved note remains a separate `reader_notes` row with its own stable source anchor.
- Projection must split source leaves at all user note anchor boundaries, then apply all covering note ids to each minimal leaf.
- Plate value should use dynamic Plate comment mark keys such as `comment_<noteId>` for each note, instead of a single `user_note: true` boolean with one `user_note_data` payload.
- Plate `getCommentCount(leaf)` / overlapping comment behavior should drive the visible overlapping annotation state.
- When several notes cover the clicked text, the note panel should list all covering User Notes and make the active one explicit.
- Creating a new note inside an existing note span is allowed; it creates a new independent nested/subrange note, not a reply.
- Clicking a note mark opens the note stack for the current source sentence/anchor segment, then scrolls or focuses the clicked User Note inside that stack.
- Each note item in the stack shows the quoted selected text for its own anchor before the note body.
- If multiple notes use exactly the same normalized anchor range, the visual mark is effectively the same text span. The stack should still show all matching notes, with newest or explicitly clicked note active.
- Confirmed: creating a note on an exact same range as an existing note is allowed only with duplicate-range UX. Warn that this text already has note(s), offer to view/edit existing notes, and allow "add another note" as an explicit secondary action.
- Creating a partially overlapping or substring note does not require warning; it is a normal nested/overlapping annotation.

Do not persist V1 notes/highlights on translation, grammar text, sentence-analysis text, or ask-supplement text.

If the user starts a note from non-source visible text in V1, the product should either:

- save it against the corresponding source anchor, with a note that it was created from AI text context, or
- keep it as an Ask/session action, not a durable document note.

Recommended V1 behavior:

- Selecting source text and saving a note/highlight writes directly to `UserEditorialAssetAnchor`.
- Confirmed: selecting translation, grammar explanation, sentence analysis, or supplement text can open the same note UI, but persistence maps to the corresponding source anchor.
- The saved note records non-authoritative provenance such as `created_from_visible_scope = "translation" | "grammar_note" | "sentence_analysis" | "ask_supplement"`.
- The saved note should also retain the selected visible AI text when it differs from the source text, so the user can recognize why the source-anchored note was created.
- UI can show that the note was created from visible AI context, while the durable anchor remains source-grounded.
- Highlight remains stable-source-only in V1. Selecting translation, grammar explanation, sentence analysis, or supplement text should not create a durable highlight on AI text; the UI may disable Highlight or offer an explicit "highlight source" action.

Non-source persistent notes can be revisited only after a rebase/orphan policy exists.

## Ask Context Resolver

The resolver should use a typed input:

```ts
type AskContextRequest = {
  visible_selected_text: string;
  created_from_visible_scope:
    | "source"
    | "translation"
    | "grammar_note"
    | "sentence_analysis"
    | "ask_supplement"
    | "user_asset"
    | "mixed";
  anchor_set: ProjectionAnchor[];
  intent?: "explain" | "translate" | "grammar" | "vocabulary" | "continue";
  scope_upgrade?: "selection" | "segment" | "unit" | "record";
  related_node_ids?: string[];
};
```

Ask Claread must know both what the user visibly selected and where that selection maps back into stable source. For AI-generated visible text, include the selected AI text plus corresponding source anchors and layer metadata. If a selection cannot be grounded to source, the request may run as degraded Ask, but it must not create durable user assets.

Context templates:

| Anchor scope | Resolver should include |
|---|---|
| stable source | selected text, anchor segment, neighboring segments, unit context |
| translation group | selected translation, source segments, neighboring source context, translation item ids |
| grammar note | note text, source span/segment, grammar layer item id, neighboring source context |
| sentence analysis | analysis summary/chunks, source segment, neighboring source context |
| ask supplement | supplement text, origin source grounding, related source/layer nodes |
| selected user asset | asset body only when explicitly selected or permitted, plus source grounding |

Ask should not read all user notes by default.

## Plate.js Rendering Strategy

Plate.js should render a read-only document that feels like a document, not like HTML with inserted cards.

Principle:

- Prefer official Plate.js plugins/components whenever they can express the document behavior.
- Claread-specific code should adapt domain facts into Plate-native nodes, not reimplement Plate-like UI with isolated React/HTML components.
- Custom components are acceptable only when the behavior is truly Claread-specific, and even then they should render Plate children and preserve Plate selection semantics.
- All visible reading content should participate in one Plate selection/action pipeline unless there is an explicit technical exception.
- Claread actions such as word lookup, Ask, note, highlight, and copy are actions on Plate selection or active Plate marks/nodes, not a separate DOM-selection system.

Required UI/projection changes:

- Source text, translation text, grammar explanations, sentence analysis details, and supplements must be selectable or explicitly anchorable.
- Custom block components must render Plate children or Plate-static children where selection should work.
- The old selection strip should be removed or downgraded in favor of Plate-compatible floating actions.
- Floating toolbar should show Claread actions: Lookup, Ask, Note, Highlight, Copy.
- Do not show rich-text formatting tools in read-only mode.
- Do not let Plate AI/suggestion write directly into stable source.
- Word lookup remains a first-class Claread action, but it should be triggered from the same Plate selection/mark pipeline used by Ask/Note/Highlight.

### Claread Reader Typography Ramp v0.2

Status: implemented for the new Reader Record Plate surface only. This does not change the legacy `/app/reader/{recordId}` page, RAG, or input preprocessing. Translation grouping has since moved to the group-native backend contract and Web projection path.

The Reader Record surface now uses a Claread-specific typography ramp rather than generic reader text classes. The source article remains user-configurable, while all annotation content uses fixed sans typography so explanation density stays stable when the user switches source font.

| Layer | Sans | Editorial | Book | Notes |
|---|---|---|---|---|
| Source, `md` | `1.04rem / 1.74`, `46rem` column | `1.08rem / 1.82`, `44rem` column | `1.07rem / 1.84`, `44rem` column | Sans is the new default for intensive mode; saved user font preferences are still respected. |
| Source, `sm` | about 6% smaller than `md` | about 6% smaller than `md` | about 6% smaller than `md` | Dense reading option. |
| Source, `lg` | about 10% larger than `md` | about 10% larger than `md` | about 10% larger than `md` | Accessibility option. |
| Translation lane | fixed sans `0.90rem / 1.68` | fixed sans `0.90rem / 1.68` | fixed sans `0.90rem / 1.68` | Current path uses backend translation groups, rendered after their source group paragraph. |
| Grammar note body | fixed sans `0.89rem / 1.64` | fixed sans `0.89rem / 1.64` | fixed sans `0.89rem / 1.64` | Annotation text should not follow source font. |
| Sentence analysis body | fixed sans `0.89rem / 1.64` | fixed sans `0.89rem / 1.64` | fixed sans `0.89rem / 1.64` | Structure rows and prose share annotation rhythm. |
| Labels | fixed sans `0.72rem / 1.25` | fixed sans `0.72rem / 1.25` | fixed sans `0.72rem / 1.25` | Navigation only; labels should not compete with explanation titles. |
| Inline code | `0.84-0.86em` | `0.84-0.86em` | `0.84-0.86em` | Prevent grammar formula chips from inflating line height. |

Class contract:

- source family: `reader-record-plate-font-sans | reader-record-plate-font-editorial | reader-record-plate-font-book`
- source scale: `reader-record-plate-type-sm | reader-record-plate-type-md | reader-record-plate-type-lg`
- density: `reader-record-plate-density-intensive | reader-record-plate-density-immersive`

Translation grouping is no longer deferred: current Web projection consumes backend `reader_translation_group` nodes. Typography v0.2 still keeps translation visually secondary to source text.

### 2026-07-01 Visual Baseline Closeout

Current accepted document rhythm for the Reader Record Plate surface:

```text
source group paragraph
-> translation blockquote
-> grammar_note callout(s)
-> sentence_analysis structure block(s)
-> ask supplement callout(s), when present
```

Baseline expectations:

- source text is not rendered one sentence per visual paragraph when backend translation group spans multiple anchors;
- separator leaves remain in the source paragraph but do not carry anchor metadata;
- translation is selectable auxiliary text, not an inline mark and not a loud analysis card;
- intensive mode shows translation and analysis blocks; immersive mode hides them while keeping source and lightweight marks;
- active anchor / lookup context for non-primary anchors in a grouped paragraph resolves by leaf metadata, not by the paragraph primary anchor;
- visual screenshots should verify desktop and mobile readability before starting the Annotation Visual Matrix.

### Deferred Annotation Visual Matrix

Status: deferred design matrix. Do not implement or lock these combinations until visual review on real Reader Record content passes.

The current product decision is only the interaction/data split:

- AI vocabulary / grammar / sentence-analysis marks are system annotations.
- User highlights and notes are user assets.
- Plate selection is transient interaction state.

The exact visual treatment must be tested because multiple marks can overlap on the same source span. Plate.js mark styling dimensions available to Claread include text color, underline style/color, highlight/background color, font weight, font style, hover state, active state, and popover/quick-peek affordances.

| Scene / annotation type | Text color | Underline / cue | Highlight / background | Bold | Italic | Hover / active behavior | Notes / review questions |
|---|---|---|---|---|---|---|---|
| Normal source text | Inherits source theme | None | None | No | No | Text selection only | Source font remains user-configurable; annotation typography does not affect source font choice. |
| `vocab_highlight` useful word | Candidate: inherit source text | Candidate: subtle amber underline | Candidate: none by default | Avoid by default | Avoid | Click opens dictionary quick peek; hover can lift underline contrast | Needs to explain why it is highlighted without looking like a user highlight. |
| `phrase_gloss` phrase / expression | Candidate: inherit source text | Candidate: purple underline, possibly solid | Candidate: none or very faint purple only on hover | Avoid | Avoid | Click opens structured phrase explanation; hover can show phrase type cue | Five phrase subtypes should not create five unrelated colors; subtype may appear in peek/header chip. |
| `context_gloss` contextual meaning | Candidate: inherit source text | Candidate: blue/cyan underline | Candidate: none or very faint blue only on hover | Avoid | Avoid | Click opens contextual explanation; active state links source span and peek | Needs stronger semantic distinction from phrase gloss without adding visual noise. |
| `grammar_note` source span | Candidate: inherit source text | Candidate: green dotted underline | Candidate: none by default | Avoid | Avoid | Hover/active highlights related grammar callout and source span | Must remain distinguishable when overlapping vocabulary marks. |
| `sentence_analysis` chunk source span | Candidate: inherit source text | Candidate: none by default, optional thin blue cue only when analysis block is nearby | Candidate: active-only pale blue when hovering chunk row | Avoid | Avoid | Hover/focus on chunk row highlights exact source chunk; source hover can activate chunk row when unambiguous | Default always-on decoration may be too noisy; active-only may be better. |
| Translation lane text | Muted annotation text | None | None; quote lane may use side rule / muted block rhythm | No | Optional for quoted style only if readability is better | Selectable; Copy/Ask only in V1 | Translation is visible content, not an inline mark. Grouping now comes from backend translation groups; display policy only controls placement/visibility. |
| `grammar_note` callout block | Annotation text color | None inside block except Markdown links/code | Candidate: Plate/Notion-like muted callout surface | Title may be 600; body normal | Markdown-driven only | Selecting text enables Copy/Ask; source-linked active state may tint border/surface | Block should be recognizable as a callout but not compete with source text. |
| `sentence_analysis` block | Annotation text color | None except chunk row affordance | Candidate: transparent or very light structure surface | Labels/title may be 600; body normal | Markdown-driven only | Chunk rows hover/focus source span; source hover may reverse-link | Keep as Claread-specific structure block, not `grammar_note` and not future Grammar X-Ray. |
| User highlight: yellow | Source text remains readable | Keep AI underline layers visible if possible | Warm yellow user asset fill | No forced bold | No | Click/hover may show asset affordance; active state can deepen fill | User asset should be visually stronger than AI marks but not obscure AI underlines. |
| User highlight: blue | Source text remains readable | Keep AI underline layers visible if possible | Soft blue user asset fill | No forced bold | No | Same as user highlight | Use for user semantic category, not AI annotation category. |
| User highlight: rose | Source text remains readable | Keep AI underline layers visible if possible | Soft rose user asset fill | No forced bold | No | Same as user highlight | Needs contrast testing on serif and sans source fonts. |
| User note / Plate comment mark | Source text remains readable | Candidate: comment-like underline or subtle note cue | Candidate: very faint note tint only on active/hover | No forced bold | No | Click opens personal note panel; active note mark is clearer | Should use Plate Comment behavior without collaboration semantics. |
| AI mark + user highlight overlap | Source text remains readable | AI underline should still be visible above/through fill | User highlight fill remains primary | Avoid | Avoid | Hover disambiguates available actions | Test if underline + filled background becomes muddy. |
| Vocabulary + grammar overlap | Source text remains readable | Need layered cue strategy: e.g. one solid underline + one dotted underline, or active disambiguation | Avoid default background | Avoid | Avoid | Hover/active should reveal both available annotations | This is the main unresolved source-mark styling risk. |
| User note + user highlight overlap | Source text remains readable | Note cue should not erase highlight meaning | Highlight fill remains primary; note active can add outline/cue | Avoid | Avoid | Click target should open note without blocking selection/highlight affordance | Needs Plate comment overlap behavior validation. |
| Plate selection | Browser/Plate selection color | Existing marks remain visible enough | Transient selection fill only | N/A | N/A | Floating toolbar appears; selection should not be cleared by actions | Selection color must be visually separate from user highlights. |

### Reader Modes

Default mode should be Study / Intensive:

- show source text,
- show translation display groups when available,
- show grammar callouts by default,
- show sentence-analysis structure blocks by default,
- keep Plate selection/actions available across visible content.

Keep Clean Reading / Immersive as an optional view mode:

- hide translation, grammar notes, and sentence-analysis blocks,
- keep user highlights and user notes visible,
- keep lightweight lexical marks, but render vocabulary/phrase/context cues much weaker than intensive mode,
- do not insert vocabulary chips, explanation text, grammar notes, sentence-analysis blocks, or translation into the document flow,
- keep selection, Ask, and explicit Lookup capability,
- keep the same Plate selection/action pipeline,
- do not change the underlying View Model or source/layer facts.

Reader mode is display policy only. It must not define backend truth, anchor semantics, or worker output shape.

There is no third "source/bilingual/intensive" mode in the Reader Record surface. Translation and analysis density are controlled by `intensive` versus `immersive`: intensive shows them, immersive hides them.

Immediate UI projection debt:

- Replace generic sentence-analysis callouts with always-open structure analysis blocks that borrow Plate-native document behavior.
- Confirmed sentence-analysis block layout: place it after the related source/translation group in intensive mode, show chunk rows first, render Markdown analysis underneath, do not use a default collapsed toggle, and only draw source chunk decorations when the source range can be matched unambiguously.
- Replace custom callout HTML recursion with official Plate callout-compatible rendering where possible.
- Add official Plate callout/annotation-related packages if needed; wrap them with Claread metadata adapters instead of duplicating their UI behavior.
- Make Markdown content render through Plate-compatible children instead of isolated custom HTML recursion.
- Replace native DOM selection flows with Plate selection flows for the Reader Record surface.
- Make translation, grammar callout text, sentence-analysis text, and supplement text either Plate-selectable or explicitly Plate-anchorable.
- Keep group-native translation display stable; only adjust display policy, typography and annotation placement around it.
- Unify mobile Dictionary, Ask, and Note into a single bottom `ReaderMobileActionSheet`; desktop may keep rail/popover surfaces.

## Mobile Action Surface

Current code is not unified:

- Dictionary detail already has a mobile compact bottom panel.
- Ask still uses `AiWorkspacePanel`.
- Note still uses `InlineCommentPanel` floating near the selection/comment mark.
- Quick Peek remains a lightweight floating preview.

Target mobile behavior:

- Plate toolbar / active mark actions open one shared bottom sheet for Dictionary, Ask, or Note.
- The sheet header keeps the current visible selection and source-grounded anchor summary visible.
- Switching sheet content must not clear the pinned Plate selection or anchor.
- Only one mobile action sheet should be open at a time.
- Closing the sheet returns focus to the document or trigger and may clear the transient selection overlay.
- Sheet height should be capped with internal scrolling so the reader can keep spatial context.

## Lookup Interaction Model

Lookup must remain a first-class Claread capability, but it should be unified with Plate selection instead of using a separate DOM-selection system.

### Intent Priority

Use explicit user intent priority:

1. Existing vocabulary/phrase/context mark click.
2. Non-collapsed Plate selection.
3. Double-click word selection.
4. Manual dictionary search.

Do not automatically treat every drag selection as phrase lookup. Drag selection should open the Plate floating actions with `Lookup`, `Ask`, `Note`, `Highlight`, and `Copy`; the user chooses the action.

### Interaction Rules

- Clicking `vocab_highlight` opens dictionary quick peek and may call dictionary lookup.
- Clicking `phrase_gloss` or `context_gloss` opens the already published phrase/context explanation; it should not re-query dictionary by default.
- Clicking grammar marks opens or focuses grammar explanation, not dictionary lookup.
- Double-clicking an unmarked word may select the word and offer Lookup.
- Single-clicking unmarked source text should not trigger lookup on desktop; it is reserved for reading focus, cursor/selection start, or future active-node behavior.
- Drag-selecting text creates a selection intent; Lookup is one explicit action in the toolbar.
- Selecting multiple words changes the Lookup label to phrase/expression lookup.
- Selecting AI-visible text such as translation or grammar explanation can still Lookup/Ask/Copy, but persistent user assets map back to source when possible.
- Lookup should not clear or fight the Plate selection until the user dismisses the peek/panel or chooses another action.

### Dictionary And AI Capabilities

The current dictionary API supports:

- canonical lookup: `q/type/context_sentence/occurrence`,
- AI context explanation when a dictionary entry exists,
- AI-generated fallback when canonical dictionary has no entry.

The old Reader Workbench already contains useful dictionary rail, quick peek, AI context explanation, and AI fallback behavior. The new Reader Record surface should reuse the product behavior, but migrate the trigger and anchoring model into the Plate action pipeline.

The product should avoid over-relying on automatic phrase recognition from surrounding context. Use this order:

1. Exact selected text as the query.
2. Canonical dictionary lookup.
3. Existing published `phrase_gloss` / `context_gloss` marks when available.
4. AI context explanation for sense disambiguation.
5. AI-generated fallback only when canonical dictionary is missing or the user explicitly requests it.

AI-generated entries must be labeled as AI-generated/unverified. AI context explanations should be framed as "why this word/phrase means this here", not as replacement dictionary truth.

UI priority:

- Quick peek defaults to canonical dictionary results.
- If a canonical entry exists, AI context explanation appears as an explicit secondary action, not an automatic default call.
- If no canonical entry exists, show a not-found state with an explicit AI definition/fallback action.
- AI results appear in the dictionary rail or secondary peek section and must not overwrite canonical dictionary content.
- Avoid automatic AI calls on every selection because it adds cost, latency, and unstable behavior.

## Storage Strategy

V1:

- No new graph table.
- No raw Plate document table.
- Build the view model from existing facts during snapshot/projection.
- Full snapshot/view rebuild is acceptable until measured otherwise.

Possible cache:

```text
cache key =
record_id + base_id + generation + last_event_sequence + view_model_schema_version
```

Cache is disposable:

- cache miss rebuilds from truth,
- cache does not participate in anchor validation,
- cache does not write reader events,
- stale cache is never used for writes.

Only consider materialized backend read-model tables after benchmarks show rebuild is too slow and after the public contract is stable.

## Coordination With Input And RAG Work

Phase 1 is downstream UI/projection work. It should not require input pipeline or RAG schema changes if the existing source contracts remain stable.

Input-side alignment:

- The input pipeline owns Stable Document Blocks and Canonical Text.
- `reading_bases.text` remains the plain-text Canonical Text offset baseline during V1.
- Markdown, Plate value, Slate paths, DOM ranges, and display grouping must not be written into `reading_bases.text`.
- If input preprocessing creates headings, lists, blockquotes, tables, images, footnotes, or code blocks, those structures should be persisted as Stable Document Blocks or Candidate Document facts before freeze.
- Reading Units and Anchor Segments are derived from Canonical Text and remain the orchestration, enhancement, user-asset, Ask, and RAG grounding boundaries.

RAG-side alignment:

- RAG should index Stable Reading Document facts: Stable Document Blocks, Canonical Text, Reading Units, Anchor Segments, source scope, hashes, and published layer facts where appropriate.
- RAG should not index `ReaderPlateSnapshot.value`, Plate node paths, DOM selection payloads, or UI-only translation display groups as authoritative truth.
- Plate value can provide visible context for user experience, but citations must validate back to stable document/base/block/unit/anchor facts.
- AI-generated Markdown or Plate fragments must remain sanitized, allowlisted, length-capped, and source-grounded before they are exposed to RAG or Ask.
- Reader Document View Model / Snapshot Value Builder is rebuildable projection. It may help Ask resolve "what the user selected or saw", but it is not the RAG storage substrate.

Parallel-agent checklist:

- If the input/RAG agent is changing Candidate Document, Stable Document Blocks, `reading_bases`, `reading_units`, or `anchor_segments`, coordinate before UI implementation consumes new fields.
- If the input/RAG agent is only adding new adapters, extraction quality gates, or record-scoped RAG indexing over existing stable facts, this UI projection plan should not block that work.
- If a proposed RAG implementation depends on Plate JSON or visible UI node ids as the primary citation target, reject that direction and map it back to stable document/block/unit/anchor ids.
- If input preview wants a richer Markdown-like document, store structure as Candidate/Stable Document Blocks and derive plain Canonical Text from source text content; do not make Markdown syntax the durable offset baseline.

## Migration Plan

### Phase 0: Terminology And Contract Alignment

- Keep `ReaderPlateSnapshot` as the public snapshot contract.
- Rename/reframe `ReaderDocumentGraph` as Reader Document View Model / Snapshot Value Builder.
- Document read vs write anchor capability.
- Keep group-native translation layer truth separate from Plate placement policy.
- Use a frontend pure reference builder first for Phase 1; do not introduce a backend/BFF view-model contract until the Plate UI behavior is stable.

### Phase 1: Current UI Projection Fixes

Goal: make the current Reader Record page feel like a Plate-native Notion-like study document without changing backend layer schemas.

In scope:

- Build a thin isolated Plate prototype/spike with fixed Reader Record mock data before touching the full Reader Record page.
- Spike, install, and truly adopt official Plate callout/annotation/document primitives where useful.
- Wrap official Plate behavior with Claread metadata adapters.
- Render source text by Stable Document Block / paragraph flow; anchor segments should remain inline anchors rather than standalone visual paragraphs.
- Stop rendering sentence analysis as a generic document-flow callout.
- Render sentence analysis as an always-open structure analysis block.
- Make grammar callout Markdown content Plate-compatible.
- Move Reader Record interactions to the Plate selection/action pipeline.
- Migrate Lookup triggers to Plate selection/mark actions while reusing old dictionary rail, quick peek, AI context explanation, and AI fallback behavior.
- Keep current group-native translation projection stable; Phase 1 UI work should not reopen backend translation schema.
- Build the Phase 1 Reader Document View Model as a frontend pure function over `ReaderPlateSnapshotDto`.

Out of scope:

- Translation V2 schema changes.
- Backend stable source schema changes.
- Ask resolver rewrite for non-source anchors.
- Persistent non-source notes/highlights.
- Backend graph/read-model persistence.
- Legacy `/app/reader/{recordId}` page changes.

Acceptance guardrail:

- Installing a Plate package is not sufficient.
- The Reader Record page must actually render through the corresponding Plate plugin/component path.
- Custom Claread wrappers must preserve Plate children, Plate selection, and Plate node semantics.
- Any remaining custom implementation must document why official Plate cannot be used and what the replacement path is.
- Tests or visual verification should prove that grammar callouts, sentence-analysis content, lookup selection, and floating actions are not bypassing Plate through isolated HTML/DOM selection code.
- Visual verification is required, but the Plate editor mock is a direction reference rather than a pixel-perfect target.
- Screenshots should judge whether the page feels like a native document with readable source text, translation, and annotations; final typography, spacing, density, colors, and annotation styling should be adjusted for the best reading experience.
- The isolated prototype is not a parallel demo product. It must produce the exact Plate primitives, wrapper approach, and projection shape that will be migrated into the real Reader Record surface.
- New work targets only the new `/app/reader-record/{recordId}` Reader Record surface. The legacy reader page is a comparison baseline and can remain unchanged until it is removed.
- Phase 1 acceptance is Plate-native interaction first: one Plate selection pipeline, real Plate component use, document-like typography/spacing, and non-jarring group-native translation display.

### Phase 2: Translation Group-Native Closeout

- Worker still runs at Reading Unit scope for context and cost control.
- Worker prompt receives complete unit source text plus ordered Anchor Segment handles.
- Worker outputs semantic translation groups; server hydrates `group_id` and `source_text_hash`.
- Publisher validates unknown segment ids, continuity, overlap, complete coverage, stable source slice hash, fingerprint base and non-empty translated text.
- Projection consumes `reader_translation_group` and never splits Chinese translation text.
- Add prompt-alignment and grouping characterization tests before broad rollout if future model changes regress into one-group-per-anchor behavior.
- Do not add worker-owned UI placement hints; grammar/sentence-analysis interruptions are display-only policy.
- Do not enable multiple translation variants until `translation_profile` participates in job identity, operation fingerprint, layer metadata, and snapshot/read-model metadata.

### Phase 3: Reader Document View Model Builder

- Introduce a pure builder that maps snapshot facts to renderer-neutral visible nodes.
- Generate Plate value from this builder.
- Use the same builder/resolver shape for Ask context.
- Evaluate whether the frontend reference builder should move to BFF/server-side projection after the UI behavior stabilizes.
- Do not introduce durable/server-side View Model caching initially; use pure builder plus frontend memory caching, then revisit only if long-article benchmarks exceed the accepted interaction threshold.
- Do not design current View Model rollout around mini-program consumption; mini-program will be reworked later and can reassess reuse of backend facts, grouping policy, or a future renderer-neutral BFF/read-model.
- Keep public API stable unless a later formal decision changes it.

### Phase 4: Projection Anchor Read Expansion

- Let Ask target translation groups, grammar notes, sentence analysis, and supplements.
- Always include source grounding.
- Do not persist non-source user assets yet.

### Phase 5: Optional Non-Source User Asset Writes

Only after defining:

- layer regenerate lifecycle,
- text hash/rebase policy,
- orphan/stale note UI,
- migration and persistence contract.

## Open Decisions For Grill

1. Accepted: V2.0 is item-only domain truth; optional worker `semantic_groups` are reserved for a spike-gated V2.1.
2. Accepted: multi-variant translation is blocked until `translation_profile` is part of job identity, operation fingerprint, and layer/snapshot metadata.
3. Closed: current Web projection renders source by translation group span / paragraph flow, with anchor segments preserved as leaf metadata rather than standalone visual paragraphs.
4. Accepted: do not add durable View Model caching initially; use pure builder plus frontend memory caching and revisit only after benchmark evidence.
5. Accepted/deferred: ignore mini-program consumption for this version; mini-program will be reworked later and can reassess reuse then.
