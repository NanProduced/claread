# Requirements Document

## Introduction

本需求定义 Claread Reader 标注体系重构的正式产品要求。

本轮重构目标不是修补现有“用户学习资产”链路，而是彻底收口 Reader 内的文本交互模型，删除文本收藏与资产聚合产品面，重建为：

- 文章收藏
- Reader 高亮
- Reader 笔记
- Ask Claread 围绕当前文章和显式外部稳定引用工作的读写能力

本轮允许重置测试数据，不以兼容未上线旧数据为目标。
本轮正式范围仅覆盖 analysis record Reader。Daily Reader 虽与文章收藏共享部分基础能力，但不在本轮重构范围内。

## UI Design Direction

### Purpose Statement

Reader 需要把用户自己的阅读痕迹收口回解析页主场，而不是通过二次聚合页让用户重新寻找已经读过的内容。新的 UI 需要让高亮、笔记、Ask Claread 三者在同一阅读表面上并存，但层次清楚，不互相争抢视觉注意力。

### Aesthetic Direction

Editorial / annotation-workbench

### Color Palette

- Ink Black `#1D1D1B`
- Paper Ivory `#F7F1E8`
- Warm Yellow Focus `#F6D74F`
- Mist Blue Highlight `#BFD8FF`
- Sage Highlight `#C8E2C8`
- Clay Highlight `#F2D3A1`

### Typography

- English reading body: existing Reader serif baseline
- Chinese UI / note rail body: existing product Chinese text stack
- No new decorative font family introduced in this phase

### Layout Strategy

- Reader 正文继续作为主画布
- Web 笔记使用句侧可折叠 comment rail
- 小程序不复制 Web rail，而使用句子展开层 / bottom sheet 查看笔记
- 不再使用独立“学习资产页”承接正文痕迹

## Requirements

### Requirement 1 - Remove Text Favorites

**User Story:** As a reader, I want a simpler interaction model so that I do not have to understand why text favorite, highlight, and note are separate but overlapping text actions.

#### Acceptance Criteria

1. While the Reader provides text interaction actions, when the user opens a selection toolbar, the Claread system shall not present a text favorite action.
2. While the backend persists favorites, when this refactor is complete, the Claread system shall only treat article-level favorites as a supported favorite type.
3. While Web and mini-program clients load favorite state, when they request favorite data, the Claread system shall return only article-level favorite semantics for Reader product flows.
4. While old text favorite code paths exist, when the refactor is implemented, the Claread system shall remove those code paths rather than keeping compatibility branches.

### Requirement 2 - Remove User Learning Assets Product Surface

**User Story:** As a reader, I want all meaningful reading traces to stay in the Reader page so that I do not need a second page to re-find things already visible in the parsed article.

#### Acceptance Criteria

1. While Web exposes library routes, when the refactor is complete, the Claread system shall remove `/library/assets` as a user-facing page.
2. While the mini-program exposes reading-related package pages, when the refactor is complete, the Claread system shall remove `packageA/excerpts` as a user-facing page.
3. While the backend exposes excerpt-asset aggregation APIs, when the refactor is complete, the Claread system shall remove `/excerpt-assets` and its supporting contracts, services, and client adapters.
4. While Reader deep-link and jump logic exists, when the refactor is complete, the Claread system shall not depend on asset-page-oriented `targetKey` recovery flows as a primary user navigation model.
5. While product and architecture documents describe current capabilities, when the refactor is complete, the Claread system documentation shall stop describing “用户学习资产 / 摘录资产” as a current product surface.

### Requirement 3 - Split Highlight And Note Into Two Models

**User Story:** As a reader, I want highlighting and note-taking to feel related but not entangled so that each action behaves predictably.

#### Acceptance Criteria

1. While the Reader persists highlight data, when the user creates or edits a highlight, the Claread system shall use a dedicated highlight annotation model.
2. While the Reader persists note data, when the user creates or edits a note, the Claread system shall use a dedicated reader-note model rather than storing note semantics inside highlight annotations.
3. While a user references the same quote with both highlight and note, when both objects exist, the Claread system shall allow them to coexist without requiring one object to own the other.
4. While the codebase contains old mixed note/highlight logic, when the refactor is implemented, the Claread system shall remove mixed-model logic instead of preserving both old and new semantics.

### Requirement 4 - Highlight Behavior In Reader

**User Story:** As a reader, I want highlighting to be visually quiet but precise so that I can mark text without confusing it with workflow-generated marks.

#### Acceptance Criteria

1. While the Reader renders user highlights, when a highlight is shown, the Claread system shall render exactly one user-highlight style with three user-facing colors that are visually distinct from workflow vocabulary highlights.
2. While the Reader renders user highlights, when a highlight is displayed, the Claread system shall keep text color black rather than recoloring text with the highlight color.
3. While the user hovers or focuses a highlight on Web, when the highlight becomes active, the Claread system shall deepen the background emphasis without changing the highlight into a workflow-style lexical mark.
4. While a user selects text, when the selection corresponds to a whole sentence, a single-sentence range, or a multi-sentence range, the Claread system shall allow highlight creation for each of those three range shapes.
5. While a user creates a highlight that exactly matches an existing highlight, when the action is confirmed, the Claread system shall edit the existing highlight instead of creating a duplicate.
6. While a user creates a highlight that is a strict subrange of an existing highlight, when the action is confirmed, the Claread system shall treat the action as operating on the existing highlight rather than creating a second overlapping highlight.
7. While a user creates a highlight that is a strict superrange of an existing highlight, when the action is confirmed, the Claread system shall extend the existing highlight according to the highlight conflict rules.
8. While highlight conflict rules are evaluated, when note data exists on overlapping text, the Claread system shall not use note objects to decide highlight merge behavior.

### Requirement 5 - Reader Note Identity And Authoring

**User Story:** As a reader, I want notes to reopen for the exact same quote and to remain separate for different quotes so that note-taking feels deterministic.

#### Acceptance Criteria

1. While the user creates a note from a whole sentence quote, when the note is persisted, the Claread system shall identify that note by the sentence-level quote identity.
2. While the user creates a note from a single-sentence text range, when the note is persisted, the Claread system shall identify that note by exact text index range rather than by string matching.
3. While the user creates a note from a multi-sentence quote, when the note is persisted, the Claread system shall identify that note by the full ordered quote-segment list.
4. While the user selects a quote that exactly matches an existing note, when the user chooses note authoring, the Claread system shall open the existing note in edit mode instead of creating a new note.
5. While the user selects a quote that overlaps but does not exactly match an existing note, when the user chooses note authoring, the Claread system shall allow a separate note to be created.
6. While a note exists, when the user edits the note text only, the Claread system shall keep the original quote identity stable.

### Requirement 6 - Reader Note Organization And Display

**User Story:** As a reader, I want notes to be organized beside the text in a stable way so that multiple notes remain readable even when ranges overlap.

#### Acceptance Criteria

1. While the Web Reader displays notes, when a sentence has notes, the Claread system shall organize them as a note list anchored to that sentence’s side rail.
2. While a note references a whole sentence, when the note is shown in the note rail, the Claread system shall display the note without a visible quote preview by default.
3. While a note references a single-sentence partial quote, when the note is shown in the note rail, the Claread system shall display the quoted text and the note content together.
4. While a note references a multi-sentence quote, when the note is shown in the note rail, the Claread system shall display a compressed quote preview and the note content together.
5. While a multi-sentence note is organized structurally, when the note is persisted, the Claread system shall anchor the note list position to the first quoted sentence without changing the full quote semantics.
6. While a sentence contains multiple notes, when the note rail is shown, the Claread system shall support folding, expansion, and scrolling so that note density does not block the reading surface.
7. While notes are ordered in a sentence note list, when the list is rendered, the Claread system shall sort by quote position in the text before using creation time as a fallback.
8. While a whole-sentence note identity exists for a sentence, when the user targets that same whole sentence again, the Claread system shall treat it as the same note identity rather than creating a second whole-sentence note for that sentence.
9. While a text-range or multi-range note identity exists, when the user targets the exact same quote again, the Claread system shall treat it as the same note identity rather than creating a duplicate note.

### Requirement 7 - Note Focus Projection

**User Story:** As a reader, I want to know exactly which original text a selected note refers to so that note review feels grounded in the article.

#### Acceptance Criteria

1. While a note is focused in Web or mini-program, when the Reader projects the note back onto the article, the Claread system shall render the quoted text with bright yellow emphasis and underline.
2. While note focus emphasis is displayed, when the note is not focused anymore, the Claread system shall remove that emphasis from the article surface.
3. While highlight and note reference the same quote, when a note is focused, the Claread system shall add note focus emphasis without mutating the persisted highlight object.
4. While the Web Reader displays multiple note cards, when one note is selected for reading or editing, the Claread system shall treat that note as the single active focused note in the UI.
5. While one note is already focused in the Web Reader UI, when another note is selected, the Claread system shall move focus to the newly selected note instead of keeping multiple notes simultaneously focused.

### Requirement 8 - Selection Toolbar Behavior

**User Story:** As a reader, I want the selection toolbar to expose only meaningful actions so that each action has a clear result.

#### Acceptance Criteria

1. While a user selects text in Web Reader, when the selection toolbar opens, the Claread system shall expose Ask Claread, highlight color, note, lookup, and select-current-sentence actions.
2. While the user opens the selection toolbar, when the refactor is complete, the Claread system shall not expose text favorite.
3. While the current selection already has a highlight, when the toolbar opens, the Claread system shall reflect the current highlight state.
4. While the current selection already has a note, when the user chooses the note action, the Claread system shall open the existing note instead of creating a duplicate.
5. While the current selection has a highlight but no note, when the user chooses the note action, the Claread system shall allow note creation from that same selection without conflict.
6. While the current selection exactly matches an existing note quote, when the user invokes the note action from the toolbar, the Claread system shall route to existing-note editing instead of note creation.

### Requirement 9 - Mini-Program Phase 1 Scope

**User Story:** As a mobile reader, I want mini-program behavior to remain stable during the refactor so that unsupported selection authoring does not appear half-finished.

#### Acceptance Criteria

1. While the mini-program result page is updated for this refactor, when Phase 1 is delivered, the Claread system shall prioritize note and highlight display/view flows over new complex selection authoring flows.
2. While mini-program currently contains selection-writing UI, when Phase 1 is delivered, the Claread system shall remove UI and state paths that imply unsupported complex note-writing behavior.
3. While mini-program still supports article-level favorite writing, when Phase 1 is delivered, the Claread system shall preserve article favorite behavior.
4. While mini-program displays notes in Reader, when the user inspects an existing note, the Claread system shall project focused quote emphasis in the article according to the shared note model.
5. While mini-program API clients include note-writing mutation paths, when Phase 1 is delivered, the Claread system shall remove or disable note-writing mutation flows and keep note behavior read-only.

### Requirement 10 - Ask Claread Product Boundary

**User Story:** As a reader using Ask Claread, I want Ask to work from the current article and explicit external stable references so that the assistant remains grounded and predictable.

#### Acceptance Criteria

1. While Ask Claread is invoked from Reader, when the request contract is built, the Claread system shall describe Ask as current-article-rooted rather than asset-center-rooted.
2. While Ask Claread accepts attachments, when the refactor is complete, the Claread system shall support current article sentence/range/multi-range context, highlight references, note references, explicit external records, and explicit stable external analysis/supplement assets.
3. While Ask Claread processes a request, when no explicit external stable reference is provided, the Claread system shall not use historical excerpt-asset lookup as a default capability.
4. While Ask Claread exposes trace or context UI, when the refactor is complete, the Claread system shall not present history-asset or record-excerpt-asset capabilities as current product semantics.
5. While Ask Claread performs disambiguation for external stable assets, when multiple stable candidates exist, the Claread system shall preserve asset-level disambiguation behavior.

### Requirement 11 - Ask Claread Write Actions

**User Story:** As a reader, I want Ask Claread to keep helping me write article-grounded highlights and notes so that AI actions remain useful after the model split.

#### Acceptance Criteria

1. While Ask Claread proposes or confirms a highlight-writing action, when the action executes, the Claread system shall write to the new highlight annotation model.
2. While Ask Claread proposes or confirms a note-writing action, when the action executes, the Claread system shall write to the new reader-note model.
3. While Ask Claread exposes write actions, when the refactor is complete, the Claread system shall not expose text favorite as an AI write action.
4. While Ask Claread contains legacy answer-to-note behavior, when the refactor is complete, the Claread system shall remove direct “save full assistant answer as note” behavior.

### Requirement 12 - Remove Legacy Asset-Center Semantics From Codebase

**User Story:** As a product and engineering team, we want the codebase to contain one coherent Reader interaction model so that future work does not inherit parallel logic branches.

#### Acceptance Criteria

1. While the refactor is implemented, when old and new flows differ, the Claread system shall remove legacy asset-center flows instead of preserving compatibility branches.
2. While shared contracts, BFFs, client adapters, and documentation still contain old asset-center semantics, when the refactor is complete, the Claread system shall delete or rewrite those semantics.
3. While tests still assert old text favorite, excerpt-asset, or mixed note/highlight behavior, when the refactor is complete, the Claread system shall delete or rewrite those tests to reflect the new model.

### Requirement 13 - Note Editing Boundaries

**User Story:** As a reader, I want note editing to preserve quote identity so that changing note text never silently changes what part of the article the note belongs to.

#### Acceptance Criteria

1. While a note already exists, when the user edits that note, the Claread system shall allow editing note content without changing quote identity.
2. While a note already exists, when the user wants to change the quoted text, the Claread system shall require deleting the old note and creating a new note instead of changing quote identity in place.
3. While a user selects text that exactly matches an existing note quote, when the user invokes note authoring, the Claread system shall enter existing-note editing rather than creating a new note.

### Requirement 14 - Web UI Refactor For New Reader Logic

**User Story:** As a reader, I want the Web Reader UI to reflect the new split note/highlight logic so that the interface itself does not imply old asset-center behavior.

#### Acceptance Criteria

1. While the Web Reader renders note-related UI, when note cards are shown, the Claread system shall present them in a sentence-side comment-style note rail adapted from the Plate-style comment pattern.
2. While the Web Reader renders selection actions, when the toolbar is shown, the Claread system shall update toolbar labels, button states, and note/highlight affordances to match the new split model.
3. While the Web Reader renders sentence action panels, note cards, or marker UI, when the refactor is complete, the Claread system shall remove UI semantics that imply text favorite or “user asset center” behavior.
4. While a note is focused or edited in Web Reader, when the UI updates, the Claread system shall synchronize note rail state, quote focus projection, and Ask entry affordances with the new note identity rules.

## Confirmed Scope Decisions

The following decisions are confirmed for Phase 1 requirements:

1. Web note focus is a UI interaction rule:
   - the Web Reader shall display quote-focus emphasis for only one actively focused note at a time
   - this does not limit how many note objects can exist in data storage
2. Note identity is stable and immutable:
   - note content may be edited
   - quote identity may not be edited in place
   - changing quote requires deleting the old note and creating a new note
3. Exact note-quote re-selection shall reopen the existing note in edit mode rather than creating a duplicate.
4. Mini-program note behavior is read-only in this phase.
5. This refactor formally covers analysis record Reader flows only.
6. Daily Reader is out of scope for this refactor, even though article favorite remains a shared model capability outside this phase.
