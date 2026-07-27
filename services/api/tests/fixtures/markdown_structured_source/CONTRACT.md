# Structured Source Contract — G0 Frozen

**Status**: G0 frozen artifact. Frozen on 2026-07-22, re-frozen on 2026-07-23 (M3 prerequisite: added `real_list_wrapper` fixture for list wrapper RAG eligibility regression), re-frozen on 2026-07-25 (Markdown ecosystem refactor D2 / A1: `code_block` and the `table` / `table_row` / `table_cell` hierarchy now default to `main_reading`; `table` / `table_row` wrappers stay `rag_eligible=false` — RAG targets the `table_cell` leaves; Clause 4 table and `gfm_table` / `code_mermaid` / `r14_complex` fixtures updated). Any change requires cross-owner review and must first update `tests/fixtures/markdown_structured_source/**` fixtures, then re-freeze this contract.

**Scope**: This contract governs the structured Markdown source pipeline output — the parser adapter (`markdown_source_parser.py`) and the downstream freeze/candidate/RAG consumers. It does NOT modify the read path for legacy frozen documents or snapshot-only records (Clause 6).

**Reference**:
- Plan: `docs/tmp/TMP-reader-markdown-rich-input-refactor-plan-2026-07-22.md` §4
- Schema: `app/schemas/reader_documents.py` (`StableDocumentBlock`, `StableDocumentBlockType`, `_DEFAULT_POLICY_BY_BLOCK_TYPE`, `default_interpretation_policy_for`)
- Existing normalizer: `app/services/reader_orchestration/input_document_normalizer.py` (current `NORMALIZER_VERSION = "d6_i3b_structured_source_v1"`; legacy `"d6_i3b_plain_text_markdown_v1"` referenced only in Clause 6 for the legacy read path)
- Suitability gate: `app/services/reader_orchestration/input_suitability_gate.py`

---

## Clause 1 — Identity

Every parser invocation MUST produce a stable identity envelope:

| Field | Source | Notes |
|-------|--------|-------|
| `parser_name` | adapter constant | First phase: `"markdown_it_py"` |
| `parser_version` | adapter constant (semver-ish string, not Python package version) | First phase: `"v1"` (parser-internal contract version; library upgrade bumps only when token tree shape changes) |
| `profile` | adapter config name | First phase: `"commonmark_gfm_v1"` (CommonMark + GFM table/strikethrough; footnote plugin disabled in first phase) |

These three fields REPLACE the legacy `normalizer_version` semantics (`"d6_i3b_plain_text_markdown_v1"`) for newly produced documents. Legacy frozen documents keep their existing `normalizer_version` on read (Clause 6).

**Block identity**:

| Field | Type | Rule |
|-------|------|------|
| `block_id` | `str` (non-empty) | Adapter-assigned stable id; format `b{order_index+1}` in first phase (e.g. `b1`, `b2`). Globally unique within one document. |
| `parent_block_id` | `str \| None` | `None` for top-level blocks. For `list_item` inside `list`: parent is the `list` block. For `table_row` inside `table`: parent is the `table` block. For `table_cell` inside `table_row`: parent is the `table_row`. For nested `list` inside `list_item`: parent is the `list_item`. |
| `order_index` | `int >= 0` | Document-global order index, assigned by parser traversal (pre-order DFS). |

**Document identity**: `parser_name + parser_version + profile` MUST be written into the frozen document metadata (replacing/upgrading `normalizer_version` semantics). The exact persistence column is owned by M1 backend; this contract only fixes that the triple is present and queryable.

---

## Clause 2 — Source Range

**Primary unit**: UTF-16 code unit offset (consistent with `reading_bases.text` / anchor_segments / canonical text layer).

**Secondary unit**: 1-based line number (parser diagnostic only; NOT used as citation truth).

| Field | Type | Notes |
|-------|------|-------|
| `source_range.line_start` | `int >= 1` | 1-based start line in normalized source. |
| `source_range.line_end` | `int >= 1` | 1-based end line (inclusive). |
| `source_range.utf16_start` | `int \| None` | First phase: `None` (M1 fills when canonical text layer integration lands). |
| `source_range.utf16_end` | `int \| None` | First phase: `None`. |

**Newline normalization**: `\r\n` and `\r` MUST be normalized to `\n` BEFORE parsing. The normalization happens at the adapter entry, before markdown-it-py sees the text. Source ranges are computed against the normalized text. This is fail-closed: a document with mixed/unknown newlines must be normalized, not rejected.

**Parse error / missing range**: If a token has no `map` (markdown-it-py edge case), the adapter MUST fail-closed — emit a `missing_source_range` warning and route to `candidate_document_required`. Silent `line_start=None` is forbidden.

---

## Clause 3 — Block Expression

### 3.1 Block type enumeration

The adapter emits block types from this closed set (mirrors `StableDocumentBlockType` in `reader_documents.py`):

| Adapter-emitted type | markdown-it-py token | First-phase support |
|----------------------|---------------------|---------------------|
| `heading` | `heading_open` ... `inline` | Yes |
| `paragraph` | `paragraph_open` ... `inline` | Yes |
| `list_item` | `list_item_open` ... | Yes (flattened items; ordered/unordered metadata in payload) |
| `blockquote` | `blockquote_open` ... | Yes |
| `table` | `table_open` | Yes (GFM plugin) |
| `table_row` | `tr_open` | Yes |
| `table_cell` | `td_open` / `th_open` | Yes |
| `code_block` | `fence` (with info string → language) or `code_block` (indented) | Yes |
| `thematic_break` | `hr` | Yes (routes to `metadata_only`) |
| `footnote` | `footnote_ref` / `footnote_open` | **First phase: captured as block but flagged `footnote_reference` warning; full footnote plugin semantics deferred.** |

Block types NOT emitted by the adapter in the first phase: `image`, `image_ocr`, `caption`, `unknown`. These remain the domain of OCR / image pipeline / Candidate Document confirm flow.

### 3.2 Table parent-child + order

- `table` (parent=None) → `table_row` (parent=table) → `table_cell` (parent=table_row).
- `order_index` is document-global, assigned in pre-order DFS.
- A table with M rows × N cols produces `1 + M + M*N` blocks.

### 3.3 List parent-child + order

- Top-level `list` block → `list_item` (parent=list) → nested `list` (parent=list_item) → nested `list_item` (parent=nested list).
- `list` block `payload_json`: `{"ordered": bool, "depth": int, "start": int}`.
- `list_item` block `payload_json`: `{"marker": str}` (e.g. `"-"`, `"*"`, `"1."`).

### 3.4 Inline marks (emphasis / strong / strikethrough / inline_code)

- Inline marks are NOT separate blocks. They are flattened into the parent block's `text_content`.
- `text_content` is the plain-text rendering of inline tokens (markdown-it-py `Token.content` on the inline token, or a flatten of child tokens).
- Strikethrough (GFM) is captured in text but flagged with a `strikethrough_extension` warning (informative; does not block freeze).

### 3.5 Link safety — protocol whitelist

**Whitelist**: `http`, `https`, `mailto`.

**Stripping rule**: Any link whose `href` uses a non-whitelisted protocol (e.g. `javascript:`, `data:`, `vbscript:`) MUST be stripped — the link text is preserved in `text_content`, but the `href` is removed and recorded in `payload_json.stripped_links` with `{"text": ..., "href": ..., "reason": "unsafe_protocol"}`.

**Safe links**: Preserved in `payload_json.links` as `{"text": ..., "href": ...}`.

**Diagnostic**: Every unsafe-protocol strip emits an `unsafe_link_protocol` warning and routes the document to `candidate_document_required`.

**Raw HTML**: `html_block` and `html_inline` tokens are fail-closed in the first phase. Text is extracted (paragraph-level), but the document routes to `candidate_document_required` with a `raw_html_block` / `inline_html` warning. Raw HTML is not preserved as a first-class block type.

---

## Clause 4 — Policy (default_route per block type)

Mirrors `_DEFAULT_POLICY_BY_BLOCK_TYPE` in `app/schemas/reader_documents.py`. The adapter does NOT override these defaults; Candidate Document confirm flow may override per-block.

| Block type | default_route | rag_eligible | allowed_source_scope |
|------------|---------------|--------------|----------------------|
| `paragraph` | `main_reading` | `true` | `["main_reading_text"]` |
| `heading` | `main_reading` | `true` | `["heading"]` |
| `list_item` | `main_reading` | `true` | `["main_reading_text"]` |
| `blockquote` | `main_reading` | `true` | `["main_reading_text"]` |
| `caption` | `main_reading` | `true` | `["main_reading_text"]` |
| `table` | `main_reading` | `false` | `["table_cell"]` |
| `table_row` | `main_reading` | `false` | `["table_cell"]` |
| `table_cell` | `main_reading` | `true` | `["table_cell"]` |
| `image` | `metadata_only` | `false` | `["image_ocr"]` |
| `image_ocr` | `rag_ask_only` | `true` | `["image_ocr"]` |
| `footnote` | `rag_ask_only` | `true` | `["footnote"]` |
| `code_block` | `main_reading` | `true` | `["code_block"]` |
| `thematic_break` | `metadata_only` | `false` | `["published_layer"]` |
| `unknown` | `metadata_only` | `false` | `["published_layer"]` |

**Invariant**: `default_route == "ignored"` requires `rag_eligible == false` (enforced by `StableDocumentInterpretationPolicy` model validator).

---

## Clause 5 — Diagnostic Carrying

Diagnostics are structured; free-text-only diagnostics are forbidden.

```json
{
  "fixture_name": "<name>",
  "warnings": [
    {
      "code": "<snake_case_code>",
      "message": "<human-readable English message>",
      "blocks_freeze": false
    }
  ],
  "unsupported": [
    {
      "code": "<snake_case_code>",
      "message": "<human-readable English message>"
    }
  ],
  "outcome": "<outcome>"
}
```

### 5.1 Warning codes (closed set, first phase)

| code | message | blocks_freeze | outcome |
|------|---------|---------------|--------|
| `raw_html_block` | Raw HTML block detected; stored as text but requires candidate review. | `false` | `candidate_document_required` |
| `inline_html` | Inline HTML tag stripped from paragraph text. | `false` | `candidate_document_required` |
| `has_unclosed_fence` | Fenced code block is missing its closing fence; captured as code_block but requires candidate review. | `false` | `candidate_document_required` |
| `unsafe_link_protocol` | Links with unsafe protocols (javascript/data/vbscript) were stripped from paragraph text; link text preserved. | `false` | `candidate_document_required` |
| `footnote_reference` | Footnote reference encountered; footnote plugin not enabled in first phase. | `false` | `candidate_document_required` |
| `strikethrough_extension` | GFM strikethrough extension captured in text; rendering preserved, no freeze block. | `false` | `stable_document_ready` |
| `mermaid_static_only` | Mermaid diagram captured as static code block; dynamic rendering deferred. | `false` | `stable_document_ready` |
| `code_dominant` | Input is code-dominant with no narrative blocks; rejected from stable document freeze, action required. | `false` | `input_rejected_or_action_required` |
| `missing_source_range` | Parser token missing source range; requires candidate review for boundary correctness. | `false` | `candidate_document_required` |

### 5.2 Unsupported codes (closed set, first phase)

| code | message |
|------|---------|
| `raw_html` | Raw HTML is not a first-class block type in the first phase; text is extracted but structure is not preserved. |
| `unsafe_link_sanitization` | Unsafe-protocol link sanitization is a first-phase safety measure; full link audit requires candidate review. |
| `footnote_full_semantics` | Footnote definition is captured as a block but full footnote semantics (multi-ref, backref) are not supported in first phase. |

### 5.3 Outcome values (closed set)

| outcome | meaning |
|---------|---------|
| `stable_document_ready` | No blocking warnings; document can freeze as stable. |
| `candidate_document_required` | Warnings present; document must route to Candidate Document review before freeze. |
| `input_rejected_or_action_required` | Input is unsuitable for structured source (e.g. code-dominant, empty); rejected from freeze, action required. |

---

## Clause 6 — Fallback (Legacy Read Path Unchanged)

**Legacy frozen documents**: Documents frozen under the regex normalizer (`NORMALIZER_VERSION = "d6_i3b_plain_text_markdown_v1"`) are read back with their existing `normalizer_version` and block structure. The new contract does NOT re-parse them.

**Snapshot-only records**: Records that have a Plate snapshot but no frozen stable document continue to use the snapshot-only read path. The new contract does NOT add a stable-document requirement to them.

**New contract applies only to**: newly parsed Markdown input (pasted text, uploaded `.md`/`.markdown`, candidate draft generation) after the M1 adapter lands. Existing data is untouched.

**Migration**: There is no batch re-parse migration. If a legacy document needs structured-source features (table blocks, code blocks, source ranges), the user re-submits or re-freezes via the Candidate Document flow.

---

## Fixture Compliance

The 11 G0 fixtures under `tests/fixtures/markdown_structured_source/` are the executable acceptance criteria for this contract. Each fixture declares:

- `input.md` — raw Markdown input.
- `expected_blocks.json` — expected block tree (block_id, block_type, text_content, payload_json, parent_block_id, order_index, source_range).
- `expected_policy.json` — expected per-block policy (default_route, rag_eligible, allowed_source_scope).
- `expected_diagnostics.json` — expected warnings, unsupported, outcome.

**G0 gate**: Fixtures may be marked `xfail`/`skip` against the current regex normalizer (which cannot produce table/code/footnote blocks). The G1 gate (M1 completion) requires all 11 fixtures to pass against the new parser adapter.

**Fixture inventory**:

| Fixture | Covers | Expected outcome |
|---------|--------|------------------|
| `simple_paragraph` | Baseline single paragraph | `stable_document_ready` |
| `r14_complex` | Full complex article (heading/list/table/code/blockquote/emphasis/link) | `stable_document_ready` |
| `nested_list` | 3-level nested ordered+unordered list | `stable_document_ready` |
| `real_list_wrapper` | Realistic article with heading + unordered list wrapper + ordered list wrapper + closing paragraph; focused on list wrapper (text_content=null) + list_item child structure for RAG eligibility regression | `stable_document_ready` |
| `gfm_table` | Standard GFM table with alignment | `stable_document_ready` |
| `code_mermaid` | ```mermaid and ```python code blocks | `stable_document_ready` |
| `raw_html` | Raw HTML block + inline HTML | `candidate_document_required` |
| `footnote` | Footnote reference + definition | `candidate_document_required` |
| `unsafe_link` | javascript/data/vbscript links stripped | `candidate_document_required` |
| `unclosed_fence` | Missing closing fence | `candidate_document_required` |
| `reject_empty` | Code-dominant content, no narrative | `input_rejected_or_action_required` |

---

## Deviation Protocol

Any deviation from this contract in M1/M2/M3 implementation MUST:

1. First update the affected fixture(s) under `tests/fixtures/markdown_structured_source/`.
2. Re-freeze this CONTRACT.md with the deviation documented.
3. Cross-owner review (M1 backend + M2 web + M3 RAG/Ask) before merge.

Silent deviations (implementation drift from contract without fixture/contract update) are a G1/G2/G3 gate failure.

---

## Clause 7 — Capability Matrix (R2R Frozen, 2026-07-27)

**Status**: This matrix reflects the **actual implementation state** as of R2R
(HEAD `951bb3b9` + R2R unstaged worktree). It replaces any prior claims —
tracked or untracked — that described planned behaviour as if it were
implemented. Future R3 work may promote entries from `not_implemented` /
`partial` to `supported`, but MUST first update this matrix and re-freeze
the contract.

**Authority**: This is the single source of truth for "what the Markdown
long-document pipeline actually does end-to-end" across parser → DB →
Snapshot → Reader projection. Untracked notes (e.g.
`docs/initiatives/reader-agentic-orchestration/modules/markdown-adaptation-state.md`)
MUST NOT override this matrix when they conflict.

**Legend**:
- `supported` — full end-to-end contract with tests at each layer boundary.
- `partial` — exists at one or more layers, but a documented gap remains;
  see the "Gap" column. Consumers MUST NOT assume the missing layer is
  implicit.
- `not_implemented` — does not exist in production code. Any plan/roadmap
  mentioning this feature describes future work (R3 or later).

| Capability | Parser | DB payload | Snapshot / Reader projection | Tests | Status | Gap |
|------------|--------|------------|------------------------------|-------|--------|-----|
| `paragraph` | supported | supported | supported (ReaderParagraph) | supported | supported | — |
| `heading` (h2–h6) | supported | supported (`heading_level`) | supported (ReaderHeading) | supported | supported | — |
| `heading` (h1) | supported | supported | rendered as-is (no demotion) | n/a | partial | **H1 demotion is NOT implemented.** Reader renders h1 verbatim; outline nav includes it. Demoting h1 → h2 (to keep semantic outline sane when the input uses h1 as a document title) is R3 scope. |
| `list` (ordered / unordered) | supported | supported (`ordered`, `depth`, `start`) | partial (Reader reconstructs wrappers from leaf units) | parser/reload fixtures | partial | The Reading Record Snapshot does not project wrapper `ordered`, `depth`, or `start`; Web currently reconstructs every wrapper with `ordered: false`. |
| `list_item` | supported | supported (`marker`) | supported (ReaderListItem) | supported | supported | — |
| nested list (≥3 levels) | supported | supported (`parent_block_id` chain) | partial (grouped leaf lists, not recursive nesting) | `test_nested_list_parent_chain_survives_reload` | partial | The DB parent chain survives reload, but the Reading Record projection groups consecutive items by parent id into sibling wrappers and clears the wrapper parent. Recursive nesting is not reconstructed yet. |
| `blockquote` | supported | supported | supported (ReaderBlockquote / ReaderMarkdownBlockquote) | supported | supported | — |
| inline marks (em / strong / strikethrough / inline_code) | supported | supported (flattened into `text_content`) | supported (leaf components) | supported | supported | Strikethrough is captured as plain text with `strikethrough_extension` warning; visual rendering uses CSS `line-through`. |
| `link` (safe protocol) | supported | supported (`payload_json.links`) | supported (ReaderLink) | supported | supported | — |
| `link` (unsafe protocol) | fail-closed strip | supported (`payload_json.stripped_links`) | n/a (stripped before projection) | supported | supported | Routes document to `candidate_document_required`. |
| `code_block` (fenced, with language) | supported | supported (`payload_json.language`, `fenced`, `closed`) | partial (Reading Record Snapshot omits language) | `test_code_block_survives_reload` | partial | DB reload preserves the language payload, but `ReaderSourceBlockNodeDto` has no language field and the Reading Record projection currently sets `language: null`. `structured-source-renderer` language-badge fixtures exercise a separate direct DTO path and are not Snapshot reload proof. |
| `code_block` (indented, no language) | supported | supported (`language: null`) | supported (no badge) | supported | supported | — |
| `code_block` (unclosed fence) | fail-closed | supported (`closed: false`) | supported (`data-closed="false"`) | `unclosed_fence` fixture | partial | Captured as `code_block` but document routes to `candidate_document_required` with `has_unclosed_fence` warning. |
| `code_line` (Plate internal) | n/a (parser emits `text_content`) | n/a | Web deserialize-only plugin (`ReaderMarkdownCodeLineComponent`) | deserialize tests | partial | Only used by the Web MarkdownTextInput / callout deserialize path. The Stable Document path stores `text_content` as text nodes, not `code_line` elements. Does NOT project `language`. |
| `thematic_break` | supported | supported (`metadata_only` route) | NOT rendered as a Reader reading unit | `test_thematic_break_routes_to_metadata_only_no_unit` | partial | **Stable Document keeps the block as `metadata_only`**, but Reader does not emit a reading unit for it. The `<hr>` is invisible in the Reader projection by design; R3 may add a metadata-only divider affordance. |
| `table` (GFM) | supported | supported (`table` / `table_row` / `table_cell` hierarchy) | partial (leaf-cell reconstruction) | `gfm_table` parser fixture; Web projection tests | partial | Inputs route to `candidate_document_required`. Reading Record reconstructs contiguous cells into rows/tables, but Snapshot omits wrapper identity and header metadata. |
| `table_row` / `table_cell` | supported | supported | partial | parser fixtures; Web projection tests | partial | Web forces reconstructed rows to `isHeader: false`; source header-row semantics are not preserved through Reading Record Snapshot. |
| `footnote` | supported (mdit-py-plugins `footnote_plugin` enabled) | supported (`footnote` block type, `footnote_id`, `footnote_anchor`) | NOT rendered as a Reader reading unit | `footnote` fixture | partial | **Parser produces degraded/candidate semantics**, not "no parser support". Footnote reference produces `footnote_reference` warning and routes document to `candidate_document_required`. Full footnote rendering (multi-ref / backref / inline footnote) is R3. |
| `image` | detected by suitability gate (`has_image`) | n/a (not frozen as a first-class block in the first phase) | n/a | `has_image` flag in `input_suitability_gate.py` | partial | **Backend suitability gate routes image-containing inputs to candidate review**, NOT "纯文本 + 暂不支持提示". Frontend renders no image block in the Reader. R3 image block schema/renderer is not implemented. |
| `raw_html` (block / inline) | fail-closed | text extracted, HTML not preserved | n/a | `raw_html` fixture | partial | **Backend fail-closes to `candidate_document_required`** with `raw_html_block` / `inline_html` warning. Frontend Markdown lint (`lintMarkdownInput`) also flags raw HTML as `hasDangerousContent` and the submit gate blocks fetch. R3 may add a sanitized HTML candidate review path. |
| `task_list` (GFM checkbox) | NOT implemented | NOT implemented | NOT implemented | none | not_implemented | **No `task_list` block type, no `checked` payload, no DTO/Reader consumer.** Cannot be declared as supported at any layer. R3 may add a `task_list` block type with `checked` state preservation. |
| `image_ocr` | n/a (OCR pipeline) | n/a | n/a | n/a | not_implemented | Image OCR pipeline is the domain of the Candidate Document confirm flow; not in scope for the Markdown structured source pipeline. |
| `caption` | not_implemented | not_implemented | not_implemented | none | not_implemented | — |
| `math` / `mermaid` | `mermaid_static_only` warning; math routes to candidate review | supported (as `code_block` with `language: "mermaid"`) | partial (Reading Record loses language; direct structured-source DTO can render a badge) | `code_mermaid` fixture | partial | Mermaid is stored as static code. The Reading Record Snapshot path does not yet carry `language`, so it cannot distinguish Mermaid after reload. |
| Markdown lint (input safety) | n/a (Web-only) | n/a | Web `lintMarkdownInput` (raw HTML / unsafe link / unclosed fence) | `AnalyzeSubmitForm.test.tsx` R2R Issue C | supported | Submit gate calls `flush()` + `lintMarkdownInput(submitText)` synchronously and fail-closes on `hasDangerousContent`. |
| Submit safety (button + Ctrl/Cmd+Enter) | n/a | n/a | `handleSubmit` flush + lint gate | `AnalyzeSubmitForm.test.tsx` R2R Issue C | supported | Both entry points share the same fail-closed path. |
| Paste fidelity (raw paste submit) | n/a | n/a | `getSubmitText()` returns raw paste text when `!dirty` | `MarkdownTextInput.test.tsx` | supported | Edit-after-paste flips `dirty` and switches to serialize output. |
| Serialize scheduling (debounce) | n/a | n/a | `handleEditorChange` light/heavy split | lifecycle tests + code review; interactive browser gate pending | partial | Production code defers non-boundary serialization by 150 ms and flush returns one submit snapshot. Test-only component instrumentation was removed; a real browser performance/E2E harness remains follow-up work. |
| Strict Mode safety | n/a | n/a | `onDegraded` ref guard | `MarkdownTextInput.test.tsx` R2R Phase 3 | supported | Mount notification fires exactly once under `<StrictMode>`. |

### 7.1 Specific clarifications

- **H1 demotion**: NOT implemented. Any untracked document claiming h1 → h2
  demotion in the Reader projection is describing an R3 plan, not current
  behaviour. The parser preserves h1 as-is; the Reader renders it verbatim
  with the same component family as h2–h6.
- **Code language**: The parser and DB preserve `payload_json.language`.
  The Reading Record Snapshot DTO does not currently project it, and the
  Reading Record Plate builder sets `language: null`. Direct
  `structured-source-renderer` fixtures that show `data-language` exercise a
  separate DTO path. Neither language badges nor syntax highlighting are an
  end-to-end Reading Record reload capability yet.
- **Footnote**: The backend parser has `footnote_plugin` enabled and
  produces `footnote` / `footnote_ref` / `footnote_anchor` semantics with
  `footnote_reference` warning and `candidate_document_required` outcome.
  The matrix MUST NOT describe this as "no parser support". Full footnote
  rendering (multi-ref / backref / inline footnote) is R3.
- **Image**: The backend suitability gate (`input_suitability_gate.py`)
  detects `has_image` and routes the input to `candidate_document_required`
  with `image_ocr_uncertain` flag. The matrix MUST NOT describe this as
  "纯文本 + 暂不支持提示" — it is a candidate review routing, not a silent
  text-only fallback.
- **Raw HTML**: Backend fail-closes to `candidate_document_required` with
  `raw_html_block` / `inline_html` warning. Frontend `lintMarkdownInput`
  additionally flags raw HTML as dangerous and the submit gate blocks
  fetch. The matrix MUST NOT claim raw HTML is silently stripped without
  routing.
- **Thematic break**: Stable Document keeps the block as `metadata_only`
  (`default_route = metadata_only`, `rag_eligible = false`). Reader does
  NOT render a reading unit for it — there is no `<hr>` reading unit in
  the Reader projection. The matrix MUST NOT claim thematic break is
  rendered as a Reader reading unit.
- **Table**: Tables that trigger the suitability gate route to
  `candidate_document_required`. Tables in already-frozen stable documents
  reload through the normal Reader path, but the matrix MUST NOT claim
  that any random table input is equivalent to a paragraph reload without
  candidate review.
- **Task list**: No `task_list` block type exists in the schema, no
  `checked` payload field, no DTO/Reader consumer, and no test. The
  matrix MUST NOT declare checked-state preservation at any layer.
- **Code highlighting**: See "Code language" above. Language projection ≠
  syntax highlighting.

### 7.2 Test coverage matrix (R2R snapshot)

| Layer | Test file | Covers |
|-------|-----------|--------|
| Parser + DB reload | `services/api/tests/test_reader_snapshot_stable_block_reload.py` | `code_block`, `thematic_break` metadata_only, `nested list` parent chain, generation fence, mismatched block range |
| Web deserialize | `apps/web/src/lib/reader-plate/markdown/deserialize.test.ts` | h1–h3, nested list, code fence language, blockquote (deserialize-only) |
| Web serialize round-trip | `apps/web/src/app/(private)/app/read/MarkdownTextInput.test.tsx` (`R2R Phase 0/3: real serialize round-trip`) | Markdown → Plate → Markdown preserves h1–h3, nested list, code fence language, blockquote |
| Web scheduling | `apps/web/src/app/(private)/app/read/MarkdownTextInput.test.tsx` | public lifecycle, flush no-op/dedup, Strict Mode safety, long-document round-trip; real browser performance gate remains pending |
| Submit lint gate | `apps/web/src/app/(private)/app/read/AnalyzeSubmitForm.test.tsx` (`R2R Issue C: submit lint gate`) | raw HTML / unsafe link / unclosed fence block fetch on button + Ctrl/Cmd+Enter; safe content submits; attached file bypasses lint |
| Structured source renderer | `apps/web/src/lib/reader-plate/projection/__tests__/structured-source-renderer.test.tsx` | code_block language badge, mermaid badge, table, raw HTML routing, footnote routing |

### 7.3 Re-freeze protocol

When an R3 (or later) change promotes a capability from `partial` /
`not_implemented` to `supported`:

1. Update the corresponding row(s) in the matrix above.
2. Add or update the test entries in §7.2 to reference the new tests
   proving the promoted state.
3. Cross-owner review (M1 backend + M2 web + M3 RAG/Ask).
4. Bump the contract `Status` line at the top of this file with the
   re-freeze date and a one-line summary of the promotion.

Silent capability promotions (claiming a feature is supported without
updating this matrix and without adding tests) are a gate failure.
