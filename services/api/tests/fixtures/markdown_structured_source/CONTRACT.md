# Structured Source Contract — G0 Frozen

**Status**: G0 frozen artifact. Frozen on 2026-07-22, re-frozen on 2026-07-23 (M3 prerequisite: added `real_list_wrapper` fixture for list wrapper RAG eligibility regression), re-frozen on 2026-07-25 (Markdown ecosystem refactor D2 / A1: `code_block` and the `table` / `table_row` / `table_cell` hierarchy now default to `main_reading`; `table` / `table_row` wrappers stay `rag_eligible=false` — RAG targets the `table_cell` leaves; Clause 4 table and `gfm_table` / `code_mermaid` / `r14_complex` fixtures updated), re-frozen on 2026-07-28 (L1 Authoritative Normalization: three-level classification `silent` / `adaptation_notice` / `content_check` on every diagnostic; safe HTML / unsafe links / safe `<aside>` / non-HTML placeholders like `vector<T>` now continue as stable with `adaptation_notice` instead of routing to candidate; deterministic GFM tables freeze as stable with table payload metadata; code language and per-cell table header/alignment are projected into the per-unit Reader snapshot DTO as `codeLanguage` / `tableIsHeader` / `tableAlignment`; wrapper-only table metadata remains in Stable Document payload and is not exposed on a per-unit DTO; new `safe_html_adaptation` / `table_structure_uncertain` fixtures; Clause 3.5 / 5.1 / 5.4 / 7 updated). Any change requires cross-owner review and must first update `tests/fixtures/markdown_structured_source/**` fixtures, then re-freeze this contract.

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

**Diagnostic (L1)**: Every unsafe-protocol strip emits an `unsafe_link_protocol` warning classified `adaptation_notice`; the document continues as stable (it no longer routes to candidate).

**Raw HTML (L1)**: `html_block` and `html_inline` tokens are deterministically sanitized — executable structure (script / iframe / event-handler attributes / unknown markup) never survives into `text_content`; the remaining text is preserved as plain paragraphs (`extracted_from: html_block` / `html_inline`) and the document continues as stable with a `raw_html_block` / `inline_html` warning classified `adaptation_notice`. Raw HTML is not preserved as a first-class block type.

**Non-HTML placeholders (L1)**: A bare inline tag whose name is not a known HTML element and that carries no attributes (`vector<T>`, `<name>`, `x<y>`) is NOT HTML — it is preserved verbatim in `text_content` and produces no diagnostic. Known tags, tags with attributes, comments and self-closing tags stay on the strip-and-flag path (fail-safe).

### 3.6 Table structure determinism (L1)

- `table` block `payload_json`: `{"alignments": list[str], "column_count": int, "header_rows": int, "structure_uncertain": bool (only when true)}`.
- `table_row` block `payload_json`: `{"is_header": bool, "row_index": int}`.
- `table_cell` block `payload_json`: `{"column_index": int, "alignment": "left" | "center" | "right" | "default", "is_header": bool}`.
- A table is **deterministic** (freezes as stable) iff it has exactly one header row and every row's raw cell count equals `column_count`. markdown-it silently pads missing cells and drops extra cells, so any mismatch is a content/boundary change: the table payload is stamped `structure_uncertain: true`, a `table_structure_uncertain` warning (`content_check`) is emitted, and the document routes to candidate review.

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
      "blocks_freeze": false,
      "classification": "silent | adaptation_notice | content_check"
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

### 5.1 Warning codes (closed set)

Every warning carries a `classification` from the closed three-level set
(`silent` / `adaptation_notice` / `content_check`, Clause 5.4). The
parser outcome is classification-driven: any `content_check` warning
forces `candidate_document_required`; `silent` and `adaptation_notice`
warnings never do.

| code | message | blocks_freeze | classification | outcome |
|------|---------|---------------|----------------|---------|
| `raw_html_block` | Raw HTML block detected; executable structure removed, text preserved as a plain paragraph. | `false` | `adaptation_notice` | `stable_document_ready` |
| `inline_html` | Inline HTML tag stripped from paragraph text. | `false` | `adaptation_notice` | `stable_document_ready` |
| `unsafe_link_protocol` | Links with unsafe protocols (javascript/data/vbscript) were stripped from paragraph text; link text preserved. | `false` | `adaptation_notice` | `stable_document_ready` |
| `mermaid_static_only` | Mermaid diagram captured as static code block; dynamic rendering deferred. | `false` | `adaptation_notice` | `stable_document_ready` |
| `strikethrough_extension` | Strikethrough syntax captured as plain text; rendering is preserved. | `false` | `silent` | `stable_document_ready` |
| `has_unclosed_fence` | Fenced code block is missing its closing fence; captured as code_block but requires candidate review for boundary correctness. | `false` | `content_check` | `candidate_document_required` |
| `footnote_reference` | Footnote reference encountered; the reference marker is dropped from body text while the definition is captured as a footnote block. | `false` | `content_check` | `candidate_document_required` |
| `table_structure_uncertain` | Table row/column structure does not match the header definition; cells would be dropped or padded during deterministic normalization. | `false` | `content_check` | `candidate_document_required` |
| `missing_source_range` | Parser token missing source range; requires candidate review for boundary correctness. | `false` | `content_check` | `candidate_document_required` |
| `code_dominant` | Input is code-dominant with no narrative blocks; rejected from stable document freeze, action required. | `false` | `content_check` | `input_rejected_or_action_required` |

### 5.2 Unsupported codes (closed set, first phase)

| code | message |
|------|---------|
| `raw_html` | Raw HTML is not a first-class block type in the first phase; text is extracted but structure is not preserved. |
| `unsafe_link_sanitization` | Unsafe-protocol link sanitization is a first-phase safety measure; full link audit requires candidate review. |
| `footnote_full_semantics` | Footnote definition is captured as a block but full footnote semantics (multi-ref, backref) are not supported in first phase. |

### 5.3 Outcome values (closed set)

| outcome | meaning |
|---------|---------|
| `stable_document_ready` | No content-check warnings; document can freeze as stable (silent / adaptation_notice records may be present). |
| `candidate_document_required` | At least one content-check warning; document must route to Candidate Document review before freeze. |
| `input_rejected_or_action_required` | Input is unsuitable for structured source (e.g. code-dominant, empty); rejected from freeze, action required. |

### 5.4 Three-level adaptation classification (L1)

Every normalization event is classified into exactly one level; the
backend parser + gate are the single classification authority (the
frontend only renders the records):

| classification | semantics | routing effect |
|----------------|-----------|----------------|
| `silent` | Deterministic, meaning-preserving normalization (newline normalization, strikethrough capture). Invisible to the user. | none |
| `adaptation_notice` | Content was cleaned or safely downgraded (raw HTML removed, unsafe link protocol stripped, mermaid rendered static) and the document continues. Surfaced as a non-blocking notice. | none (document stays stable) |
| `content_check` | Content, boundaries or meaning may change (unclosed fence, footnote reference loss, table structure uncertainty, missing source range, image, math, OCR uncertainty, code dominance). Requires human review. | `candidate_document_required` |

**Field contract (exact paths)**:

- Parser: `MarkdownParseResult.warnings[i].classification` (`DiagnosticWarning.classification`).
- Gate: `InputSuitabilityResult.adaptations[i]` = `{code, message, classification}` (`AdaptationRecord` in `app/schemas/reader_input_adapter.py`). Parser warnings flow through with their classification; gate-only signals (`image_ocr_uncertain`, `document_block_degraded` (math), `ocr_low_confidence`, `layout_order_uncertain`, `code_dominant`, `too_long_requires_envelope`, `source_type_review_default`) are recorded as `content_check`.
- Normalizer: `NormalizedInputDocument.adaptations` mirrors the suitability records.
- Candidate persistence: `candidate_reading_documents.quality_json.suitability.adaptations`.
- Stable-ready persistence: `stable_reading_documents.source_profile_json.suitability.adaptations`.

### 5.5 Snapshot DTO metadata projection (L1)

The Reader snapshot `reader_source_block` payload projects (all keys
present whenever `stableBlockType` is set, `null` when not applicable):

| DTO key | source | emitted for |
|---------|--------|-------------|
| `codeLanguage` | `payload_json.language` (empty → `null`) | `code_block` |
| `tableIsHeader` | `payload_json.is_header` | `table_row` / `table_cell` |
| `tableAlignment` | `payload_json.alignment` | `table_cell` |

The projection is implemented once in `base_builder.py` (build path) and
reused in `repository.py` (DB reload path); both paths MUST produce
structurally equivalent snapshots (proven by
`tests/test_l1_table_code_metadata_reload.py` against real PostgreSQL).

---

## Clause 6 — Fallback (Legacy Read Path Unchanged)

**Legacy frozen documents**: Documents frozen under the regex normalizer (`NORMALIZER_VERSION = "d6_i3b_plain_text_markdown_v1"`) are read back with their existing `normalizer_version` and block structure. The new contract does NOT re-parse them.

**Snapshot-only records**: Records that have a Plate snapshot but no frozen stable document continue to use the snapshot-only read path. The new contract does NOT add a stable-document requirement to them.

**New contract applies only to**: newly parsed Markdown input (pasted text, uploaded `.md`/`.markdown`, candidate draft generation) after the M1 adapter lands. Existing data is untouched.

**Migration**: There is no batch re-parse migration. If a legacy document needs structured-source features (table blocks, code blocks, source ranges), the user re-submits or re-freezes via the Candidate Document flow.

---

## Fixture Compliance

The 13 fixtures under `tests/fixtures/markdown_structured_source/` are the executable acceptance criteria for this contract. Each fixture declares:

- `input.md` — raw Markdown input.
- `expected_blocks.json` — expected block tree (block_id, block_type, text_content, payload_json, parent_block_id, order_index, source_range).
- `expected_policy.json` — expected per-block policy (default_route, rag_eligible, allowed_source_scope).
- `expected_diagnostics.json` — expected warnings (code + classification), unsupported, outcome.

**G0 gate**: Fixtures may be marked `xfail`/`skip` against the current regex normalizer (which cannot produce table/code/footnote blocks). The G1 gate (M1 completion) requires all fixtures to pass against the new parser adapter.

**Fixture inventory**:

| Fixture | Covers | Expected outcome |
|---------|--------|------------------|
| `simple_paragraph` | Baseline single paragraph | `stable_document_ready` |
| `r14_complex` | Full complex article (heading/list/table/code/blockquote/emphasis/link) | `stable_document_ready` |
| `nested_list` | 3-level nested ordered+unordered list | `stable_document_ready` |
| `real_list_wrapper` | Realistic article with heading + unordered list wrapper + ordered list wrapper + closing paragraph; focused on list wrapper (text_content=null) + list_item child structure for RAG eligibility regression | `stable_document_ready` |
| `gfm_table` | Standard GFM table with alignment (deterministic: freezes stable) | `stable_document_ready` |
| `code_mermaid` | ```mermaid and ```python code blocks | `stable_document_ready` |
| `raw_html` | Raw HTML block + inline HTML (L1: sanitized, adaptation_notice) | `stable_document_ready` |
| `safe_html_adaptation` | L1: script/iframe/event handler/unsafe protocols stripped to safety; safe aside/links and `vector<T>` / `<name>` placeholders preserved | `stable_document_ready` |
| `footnote` | Footnote reference + definition (ref dropped from body: content_check) | `candidate_document_required` |
| `unsafe_link` | javascript/data/vbscript links stripped (L1: adaptation_notice) | `stable_document_ready` |
| `unclosed_fence` | Missing closing fence (content_check) | `candidate_document_required` |
| `table_structure_uncertain` | L1: body row with extra raw cell (column mismatch; content_check) | `candidate_document_required` |
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
| `link` (unsafe protocol) | deterministic strip (L1) | supported (`payload_json.stripped_links`) | n/a (stripped before projection) | supported | supported | L1: `adaptation_notice`; the document continues as `stable_document_ready` (no longer routes to candidate). |
| `code_block` (fenced, with language) | supported | supported (`payload_json.language`, `fenced`, `closed`) | supported (L1: snapshot `reader_source_block.codeLanguage`) | `test_code_block_survives_reload`; `test_l1_table_code_metadata_reload.py` (real PostgreSQL) | supported | L1: language is projected into the snapshot DTO on both the build and DB-reload paths. Rendering the badge/highlight is the Web projection's follow-up. |
| `code_block` (indented, no language) | supported | supported (`language` empty → DTO `null`) | supported (no badge) | supported | supported | — |
| `code_block` (unclosed fence) | fail-closed (content_check) | supported (`closed: false`) | supported (`data-closed="false"`) | `unclosed_fence` fixture | partial | Captured as `code_block` but document routes to `candidate_document_required` with `has_unclosed_fence` warning (`content_check`). |
| `code_line` (Plate internal) | n/a (parser emits `text_content`) | n/a | Web deserialize-only plugin (`ReaderMarkdownCodeLineComponent`) | deserialize tests | partial | Only used by the Web MarkdownTextInput / callout deserialize path. The Stable Document path stores `text_content` as text nodes, not `code_line` elements. |
| `thematic_break` | supported | supported (`metadata_only` route) | NOT rendered as a Reader reading unit | `test_thematic_break_routes_to_metadata_only_no_unit` | partial | **Stable Document keeps the block as `metadata_only`**, but Reader does not emit a reading unit for it. The `<hr>` is invisible in the Reader projection by design; R3 may add a metadata-only divider affordance. |
| `table` (GFM, deterministic) | supported | supported (`table` / `table_row` / `table_cell` hierarchy; `alignments` / `column_count` / `header_rows`) | supported (L1: leaf-cell `tableIsHeader` / `tableAlignment`; wrapper fields when a unit matches) | `gfm_table` fixture; `test_l1_table_code_metadata_reload.py` (real PostgreSQL) | supported | L1: tables with one header row and consistent raw cell counts freeze as `stable_document_ready` (no candidate). |
| `table` (structure-uncertain) | fail-closed (content_check) | supported (`structure_uncertain: true`) | n/a (candidate path) | `table_structure_uncertain` fixture | supported | L1: row/column mismatch (cells would be padded/dropped) or missing header row routes to `candidate_document_required`. |
| `table_row` / `table_cell` | supported | supported (`is_header`, `alignment`) | supported (L1: snapshot `tableIsHeader` / `tableAlignment`) | parser fixtures; `test_l1_table_code_metadata_reload.py` | supported | L1: source header-row semantics and per-cell alignment survive the Reading Record Snapshot reload. |
| `footnote` | supported (mdit-py-plugins `footnote_plugin` enabled) | supported (`footnote` block type, `footnote_id`, `footnote_anchor`) | NOT rendered as a Reader reading unit | `footnote` fixture | partial | **Parser produces degraded/candidate semantics**, not "no parser support". Footnote reference produces `footnote_reference` warning and routes document to `candidate_document_required`. Full footnote rendering (multi-ref / backref / inline footnote) is R3. |
| `image` | detected by suitability gate (`has_image`) | n/a (not frozen as a first-class block in the first phase) | n/a | `has_image` flag in `input_suitability_gate.py` | partial | **Backend suitability gate routes image-containing inputs to candidate review**, NOT "纯文本 + 暂不支持提示". Frontend renders no image block in the Reader. R3 image block schema/renderer is not implemented. |
| `raw_html` (block / inline) | deterministic sanitize (L1) | text extracted, HTML not preserved | n/a | `raw_html` / `safe_html_adaptation` fixtures | partial | **L1: backend strips executable structure, preserves text, classifies `adaptation_notice` and continues as `stable_document_ready`.** Frontend Markdown lint (`lintMarkdownInput`) still flags raw HTML as `hasDangerousContent` and the submit gate blocks fetch; removing that fail-closed is the L1 frontend step that MUST land only after these server-side proofs. |
| non-HTML placeholders (`vector<T>` / `<name>`) | supported (L1: preserved verbatim, no diagnostic) | supported | supported (plain text) | `safe_html_adaptation` fixture; `test_markdown_safe_normalization.py` | supported | Bare unknown tags without attributes are literal text, not HTML. |
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
- **Code language**: The parser and DB preserve `payload_json.language`,
  and (L1) the Reading Record Snapshot DTO projects it as
  `reader_source_block.codeLanguage` on both the build and DB-reload
  paths (`tests/test_l1_table_code_metadata_reload.py`). The Web Reading
  Record Plate builder's rendering of the badge/highlight is a separate
  follow-up; language projection ≠ syntax highlighting.
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
- **Raw HTML**: L1 — the backend deterministically sanitizes raw HTML
  (executable structure removed, text preserved), classifies it
  `adaptation_notice` and continues as `stable_document_ready`.
  The matrix MUST NOT claim raw HTML is silently stripped
  without an adaptation record. Frontend `lintMarkdownInput` still flags
  raw HTML as dangerous and the submit gate blocks fetch until the L1
  frontend step removes that fail-closed (server proofs land first).
- **Thematic break**: Stable Document keeps the block as `metadata_only`
  (`default_route = metadata_only`, `rag_eligible = false`). Reader does
  NOT render a reading unit for it — there is no `<hr>` reading unit in
  the Reader projection. The matrix MUST NOT claim thematic break is
  rendered as a Reader reading unit.
- **Table**: L1 — deterministic GFM tables (one header row, consistent
  raw cell counts) freeze as `stable_document_ready` with first-class
  `table` / `table_row` / `table_cell` blocks and projected
  header/alignment metadata. Structure-uncertain tables (row/column
  mismatch or missing header) still route to `candidate_document_required`
  with the `table_structure_uncertain` warning. The matrix MUST NOT claim
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
| L1 safe normalization contract | `services/api/tests/test_markdown_safe_normalization.py` | script/iframe/event-handler/unsafe-protocol sanitization, safe aside/links, `vector<T>`/`<name>` placeholders, three-level classification, deterministic vs uncertain table routing |
| L1 table/code metadata reload | `services/api/tests/test_l1_table_code_metadata_reload.py` | deterministic table + code language stable-ready freeze, DB payload persistence, snapshot `codeLanguage`/`tableIsHeader`/`tableAlignment` build↔reload equivalence (real PostgreSQL) |
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
