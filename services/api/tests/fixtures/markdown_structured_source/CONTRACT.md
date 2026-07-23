# Structured Source Contract — G0 Frozen

**Status**: G0 frozen artifact. Frozen on 2026-07-22. Any change requires cross-owner review and must first update `tests/fixtures/markdown_structured_source/**` fixtures, then re-freeze this contract.

**Scope**: This contract governs the structured Markdown source pipeline output — the parser adapter (`markdown_source_parser.py`) and the downstream freeze/candidate/RAG consumers. It does NOT modify the read path for legacy frozen documents or snapshot-only records (Clause 6).

**Reference**:
- Plan: `docs/tmp/TMP-reader-markdown-rich-input-refactor-plan-2026-07-22.md` §4
- Schema: `app/schemas/reader_documents.py` (`StableDocumentBlock`, `StableDocumentBlockType`, `_DEFAULT_POLICY_BY_BLOCK_TYPE`, `default_interpretation_policy_for`)
- Existing normalizer: `app/services/reader_orchestration/input_document_normalizer.py` (`NORMALIZER_VERSION = "d6_i3b_plain_text_markdown_v1"`)
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
| `table` | `metadata_only` | `false` | `["table_cell"]` |
| `table_row` | `metadata_only` | `false` | `["table_cell"]` |
| `table_cell` | `rag_ask_only` | `true` | `["table_cell"]` |
| `image` | `metadata_only` | `false` | `["image_ocr"]` |
| `image_ocr` | `rag_ask_only` | `true` | `["image_ocr"]` |
| `footnote` | `rag_ask_only` | `true` | `["footnote"]` |
| `code_block` | `rag_ask_only` | `true` | `["code_block"]` |
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

The 10 G0 fixtures under `tests/fixtures/markdown_structured_source/` are the executable acceptance criteria for this contract. Each fixture declares:

- `input.md` — raw Markdown input.
- `expected_blocks.json` — expected block tree (block_id, block_type, text_content, payload_json, parent_block_id, order_index, source_range).
- `expected_policy.json` — expected per-block policy (default_route, rag_eligible, allowed_source_scope).
- `expected_diagnostics.json` — expected warnings, unsupported, outcome.

**G0 gate**: Fixtures may be marked `xfail`/`skip` against the current regex normalizer (which cannot produce table/code/footnote blocks). The G1 gate (M1 completion) requires all 10 fixtures to pass against the new parser adapter.

**Fixture inventory**:

| Fixture | Covers | Expected outcome |
|---------|--------|------------------|
| `simple_paragraph` | Baseline single paragraph | `stable_document_ready` |
| `r14_complex` | Full complex article (heading/list/table/code/blockquote/emphasis/link) | `stable_document_ready` |
| `nested_list` | 3-level nested ordered+unordered list | `stable_document_ready` |
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
