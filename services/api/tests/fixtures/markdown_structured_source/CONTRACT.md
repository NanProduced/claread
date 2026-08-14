# Structured Source Contract — G0–G5 / Verification

**Status**: revalidation passed on 2026-08-01; `source_callout full support`
and G0–G5 are complete for the scope. The closure claim was invalidated
by the multi-region and fingerprint findings and has now been re-frozen after
the two-callout Chromium + API/PostgreSQL chain and focused quality gates
passed. Frozen on 2026-07-22, re-frozen on 2026-07-23 (M3
prerequisite: added `real_list_wrapper` fixture for list wrapper RAG eligibility
regression), re-frozen on 2026-07-25 (Markdown ecosystem refactor), and
re-frozen on 2026-07-28 (L1 Authoritative Normalization), and re-frozen on
2026-08-01 (R-G0G5-multi-region fingerprint fusion and display-icon boundary),
and re-frozen for R-G0G5-list fingerprint plus no-stub G5.
Any change requires
cross-owner review and must first update `tests/fixtures/markdown_structured_source/**`
fixtures, then re-freeze this contract.

**Previous-round fixture baseline (2026-08-01; not closure)**: Added `source_callout` and
`rich_html_aside` fixtures for the generic callout block tree, restricted
Notion HTML normalization, inline marks, safe links, and independent trailing
text. Added `task_list` and `definition_list` fixtures so unsupported
semantics remain visible and are explicitly routed as candidate/content-check
or adaptation-notice outcomes. Added `citation_reference`, `heading_levels`,
and `gfm_alert` fixtures to freeze reference semantics, all h1–h6 levels, and
the GFM alert wrapper. The callout/alert wrapper is structural; descendant
blocks carry canonical text and inherit the source-callout semantic policy.

**verification status (2026-08-01; complete)**:

- Full Notion dual-MIME `/app/read` uses a real `ClipboardItem` and a
  deterministic DOM/Plate local fusion. Rich HTML remains authoritative for
  headings, lists, links and tables; every high-confidence escaped-aside region
  is paired by document order with one plain aside and validated before any
  region is replaced. Ambiguous, escaped, fenced, inline or unclosed regions,
  count/order/structure/link mismatches, and unsafe-link ambiguity fail closed
  to sanitized HTML with `html_aside_fusion_declined`; the whole plain document
  is never selected as a fallback and partial fusion is forbidden.
- The HTML fingerprint adapter normalizes `<ul>`/`<ol>` to ordered or
  unordered list wrappers, each `<li>`'s consecutive text/inline children to
  one `list_item_content` (`lic`) child, and nested lists to structural
  children of the `list_item`. Markdown `ul`/`ol`/`li`/`lic` trees map
  to the same fingerprint; list wrappers remain structural-null for the Stable
  semantic-role contract while role-bearing descendants inherit
  `source_callout`.
- Confirmed Source is frozen with revision/hash evidence. The Stable wrapper
  keeps `payload_json.display_icon`, has no canonical range, and its children
  retain the parent chain, inline marks, links, ranges and `source_callout`
  role. The icon has zero Reading Unit, Anchor Segment, or automatic-job
  representation; callout body descendants remain T-only and remain eligible
  for `USER_EXPLICIT` translation.
- Reader projection consumes only wrapper payload metadata and falls back to
  the product default icon when the payload is absent or invalid. It never
  infers the icon from the first body child.
- G5 used a normal `from app.main import app` / Uvicorn startup with no
  Ask bootstrap symbol injection or monkeypatch. The deterministic fake
  namespace was applied only by the existing test-side enhancement runner.

**Scope**: This contract governs the structured Markdown source pipeline output — the parser adapter (`markdown_source_parser.py`) and the downstream freeze/candidate/RAG consumers. It does NOT modify the read path for legacy frozen documents or snapshot-only records (Clause 6).

**Reference**:
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
| `profile` | adapter config name | First phase: `"commonmark_gfm_v1"` (CommonMark + GFM table/strikethrough + footnote plugin) |

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
| `list` | `bullet_list_open` / `ordered_list_open` | Yes (structural wrapper; narrative is in child `list_item` blocks) |
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

**Raw HTML (L1)**: `html_block` and `html_inline` tokens are deterministically sanitized — executable structure (script / iframe / event-handler attributes / unknown markup) never survives into `text_content`. Plain raw HTML degrades to visible text; paired rich `<aside>` content is normalized through the same Markdown adapter into a structural `blockquote` wrapper plus paragraph/list descendants. The document continues as stable with a `raw_html_block` / `inline_html` warning classified `adaptation_notice`; raw HTML itself is not preserved as a first-class block type.

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

### 4.1 Semantic role and translation prompt profile

Semantic role, automatic-layer policy, and translation prompt profile are
separate persisted decisions. The deterministic semantic resolver does not use
an LLM classifier. Under
`reader_translation_prompt_profile_v1`, admitted translation units resolve to
one of `prose`, `heading`, `quotation`, `citation_reference`,
`source_callout`, or `explicit_section`. `USER_EXPLICIT` section translation
uses `explicit_section` independently of automatic-layer policy. Missing,
legacy, unknown, or future semantic metadata falls back to `prose` (fail-open).
Mixed batches carry a profile per unit and MUST NOT silently apply one unit's
profile to another unit.

### 4.2 Notion dual-MIME callout fusion

When a clipboard item contains both `text/html` and `text/plain`, negotiation
MUST NOT choose one complete document globally when the HTML contains an
escaped Notion callout. The implementation has one local-fusion seam and one
all-or-nothing plan for the whole ClipboardItem:

1. sanitize and parse the HTML DOM; retain its rich headings, lists, links and
   tables;
2. discover N same-parent, block-level, explicitly paired escaped
   `&lt;aside&gt;` / `&lt;/aside&gt;` regions in HTML. A candidate must have no
   nested elements in the marker paragraphs and no `pre`/`code`/`script`/
   `style` ancestry. Inline examples, escaped `\\&lt;aside&gt;`, fenced code,
   actual `<aside>` markup, and unclosed markers are not candidates;
3. discover N high-confidence, paired block asides in plain Markdown and parse
   each slice through the existing Markdown parser;
4. build a `CalloutFusionFingerprint` for every pair in document order. The
   fingerprint contains `{boundary: {open: "aside", close: "aside", block:
   true}, documentOrder, visibleText, blocks, links, linkCount,
   unsafeLinkCount}`. Each `blocks` entry preserves the normalized block type
    (`paragraph`, `list`, `list_item`, heading, etc.), exact visible text,
    ordered marks and child structure. Each ordered `links` entry is
    `{visibleText, sanitizedHref}`; link count and order are part of equality.
   The HTML DOM adapter uses this list normalization table:

   | HTML DOM | Fingerprint / Plate Markdown tree | Contract |
   |----------|-----------------------------------|----------|
   | `<ul>` | `list:unordered` / `ul` | Orderedness is preserved |
   | `<ol>` | `list:ordered` / `ol` | Orderedness is preserved |
   | `<li>` consecutive text/inline children | `list_item` → `list_item_content` / `li` → `lic` | Text order, marks and links are preserved |
   | nested `<ul>`/`<ol>` | child list of the containing `list_item` | Parent chain and list kind are preserved |
5. normalize every URL through the shared clipboard sanitizer before comparing:
   trim whitespace; allow relative/fragment references and `http`, `https`, or
   `mailto` schemes with a lower-cased scheme; reject `javascript`, `data`,
   `vbscript`, empty, and unsupported schemes. An unsafe or missing href raises
   `unsafeLinkCount` and prevents fusion. No URL, underscore, backtick, or
   punctuation is deleted from visible text to obtain a match;
6. only after all N fingerprints match exactly does the Plate fragment seam
   replace all N HTML regions with the corresponding plain callout fragments.
   The pairing is one-to-one by document order, so duplicate callouts remain
   deterministic and cannot cross-wire icons, children, or trailing content.

If correspondence is absent or any count, order, boundary, block structure,
visible text, mark, link count/order, sanitized URL, or safety fingerprint is
ambiguous, the entire plan is rejected with the stable reason
`html_aside_fusion_declined`. The result keeps sanitized HTML rich structure;
it MUST NOT partially fuse, silently switch to the whole plain document, drop
content, or rewrite a trusted HTML link with a different plain URL. At the
input surface the escaped marker remains visible or enters the existing
Content Check/adaptation notice path. A successful canonical submission has
exactly N paired `<aside>...</aside>` regions, no raw HTML attributes, and no
visible `<aside>`, `</aside>`, or `[!NOTE]` marker.

### 4.3 Source-callout display icon boundary

Confirmed Source may retain the original emoji. During parsing, only a direct
leading paragraph whose content is exactly one safe emoji grapheme inside a
recognized `html_aside`/`gfm_alert` wrapper is promoted to the wrapper's
`payload_json.display_icon`. The paragraph is removed before canonical range
assignment; retained block IDs and `parent_block_id` links are compacted
together. Therefore the icon has no canonical range, Stable tree leaf,
Reading Unit, Anchor Segment, annotation target, translation profile entry, or
automatic job target. The wrapper itself has no canonical range; all visible
callout text remains in its descendants with `content_role=source_callout` and
the T-only automatic policy. Reader projection reads `display_icon` from the
wrapper payload and uses the product default icon when it is absent or invalid.

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
| `task_list_unsupported` | GFM task-list checkbox state is preserved as visible text but task-list semantics are not supported; candidate review is required. | `false` | `content_check` | `candidate_document_required` |
| `definition_list_degraded` | Definition-list syntax is preserved as plain text; definition-list structure is not supported in the first phase. | `false` | `adaptation_notice` | `stable_document_ready` |
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
| `task_list` | Task-list checked-state semantics are not supported in the first phase; the visible marker is retained for candidate review. |
| `definition_list` | Definition-list structure is not supported in the first phase; text is retained for safe review. |

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
`tests/test_table_code_metadata_reload.py` against real PostgreSQL).

---

## Clause 6 — Fallback (Legacy Read Path Unchanged)

**Legacy frozen documents**: Documents frozen under the regex normalizer (`NORMALIZER_VERSION = "d6_i3b_plain_text_markdown_v1"`) are read back with their existing `normalizer_version` and block structure. The new contract does NOT re-parse them.

**Snapshot-only records**: Records that have a Plate snapshot but no frozen stable document continue to use the snapshot-only read path. The new contract does NOT add a stable-document requirement to them.

**New contract applies only to**: newly parsed Markdown input (pasted text, uploaded `.md`/`.markdown`, candidate draft generation) after the M1 adapter lands. Existing data is untouched.

**Migration**: There is no batch re-parse migration. If a legacy document needs structured-source features (table blocks, code blocks, source ranges), the user re-submits or re-freezes via the Candidate Document flow.

---

## Fixture Compliance

The 20 fixtures under `tests/fixtures/markdown_structured_source/` are the executable acceptance criteria for this contract. Each fixture declares:

- `input.md` — raw Markdown input.
- `expected_blocks.json` — expected block tree (block_id, block_type, text_content, payload_json, parent_block_id, order_index, source_range).
- `expected_policy.json` — expected per-block policy (default_route, rag_eligible, allowed_source_scope).
- `expected_diagnostics.json` — expected warnings (code + classification), unsupported, outcome.

**G0/G1 gate**: The 20 fixtures pass against the parser adapter. Unsupported or
candidate-routed content is still required to remain visible and carry its
explicit diagnostic/outcome; it is not a silent drop.

**Fixture inventory**:

| Fixture | Covers | Expected outcome |
|---------|--------|------------------|
| `simple_paragraph` | Baseline single paragraph | `stable_document_ready` |
| `r14_complex` | Full complex article (heading/list/table/code/blockquote/emphasis/link) | `stable_document_ready` |
| `nested_list` | 3-level nested ordered+unordered list | `stable_document_ready` |
| `real_list_wrapper` | Realistic article with heading + unordered list wrapper + ordered list wrapper + closing paragraph; focused on list wrapper (text_content=null) + list_item child structure for RAG eligibility regression | `stable_document_ready` |
| `gfm_table` | Standard GFM table with alignment (deterministic: freezes stable) | `stable_document_ready` |
| `code_mermaid` | ```mermaid and ```python code blocks | `stable_document_ready` |
| `definition_list` | Definition-list syntax retained as visible paragraph text with an explicit adaptation notice | `stable_document_ready` |
| `raw_html` | Raw HTML block + inline HTML (L1: sanitized, adaptation_notice) | `stable_document_ready` |
| `safe_html_adaptation` | L1: script/iframe/event handler/unsafe protocols stripped to safety; safe aside/links and `vector<T>` / `<name>` placeholders preserved | `stable_document_ready` |
| `citation_reference` | References heading with author/year, emphasis, DOI link, and independent entries | `stable_document_ready` |
| `heading_levels` | CommonMark heading levels h1–h6 preserved in `payload_json.level` | `stable_document_ready` |
| `gfm_alert` | GFM alert marker preserved as a structural blockquote hint with nested content and trailing prose | `stable_document_ready` |
| `source_callout` | Paired Markdown aside with paragraph/list/mark descendants and independent trailing text | `stable_document_ready` |
| `rich_html_aside` | Notion-style rich HTML aside normalized into the same paragraph/list/mark tree | `stable_document_ready` |
| `task_list` | GFM checkbox markers retained as visible list-item text; checked semantics require candidate review | `candidate_document_required` |
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

## Clause 7 — Capability Matrix (G0–G5 / Closure, 2026-08-01)

**Status**: This matrix reflects the **actual implementation state** after the
G0–G5/closure checks above. It replaces any prior claims — tracked or untracked
— that described planned behaviour as if it were implemented. Future work may
promote entries from `not_implemented` / `partial` to `supported`, but MUST
first update this matrix and re-freeze the contract.

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
  mentioning this feature describes future work (later roadmap).

| Capability | Parser | DB payload | Snapshot / Reader projection | Tests | Status | Gap |
|------------|--------|------------|------------------------------|-------|--------|-----|
| `paragraph` | supported | supported | supported (ReaderParagraph) | supported | supported | — |
| `heading` (h2–h6) | supported | supported (`heading_level`) | supported (ReaderHeading) | supported | supported | — |
| `heading` (h1) | supported | supported | rendered as-is (no demotion) | n/a | partial | **H1 demotion is NOT implemented.** Reader renders h1 verbatim; outline nav includes it. Demoting h1 → h2 (to keep semantic outline sane when the input uses h1 as a document title) is future work scope. |
| `list` (ordered / unordered) | supported | supported (`ordered`, `depth`, `start`) | supported (generic stable tree preserves recursive wrappers and metadata) | parser/reload fixtures; projection tests; G3 browser suite | supported | — |
| `list_item` | supported | supported (`marker`) | supported (ReaderListItem) | supported | supported | — |
| nested list (≥3 levels) | supported | supported (`parent_block_id` chain) | supported (recursive generic stable tree) | `test_nested_list_parent_chain_survives_reload`; projection tests; G3/G5 browser suites | supported | — |
| `blockquote` | supported | supported | supported (ReaderBlockquote / ReaderMarkdownBlockquote) | supported | supported | — |
| `source_callout` (paired Markdown/GFM/rich HTML) | supported (aside hint / GFM alert) | supported (wrapper-only `display_icon`; descendants own canonical text) | supported (payload-owned icon, default fallback, recursive children, selection/manual operations) | `test_source_callout_display_icon.py`, `test_source_callout_and_reference_reload.py`, `test_reader_snapshot_stable_block_reload.py`, Web projection/round-trip, 13 Chromium aside cases, 1 two-callout real `/app/read` case | supported | N paired escaped Notion regions use all-or-nothing fingerprint fusion by document order; arbitrary raw HTML remains sanitized/degraded by contract. |
| inline marks (em / strong / strikethrough / inline_code) | supported | supported (flattened into `text_content`) | supported (leaf components) | supported | supported | Strikethrough is captured as plain text with `strikethrough_extension` warning; visual rendering uses CSS `line-through`. |
| `link` (safe protocol) | supported | supported (`payload_json.links`) | supported (ReaderLink) | supported | supported | — |
| `link` (unsafe protocol) | deterministic strip (L1) | supported (`payload_json.stripped_links`) | n/a (stripped before projection) | supported | supported | L1: `adaptation_notice`; the document continues as `stable_document_ready` (no longer routes to candidate). |
| `code_block` (fenced, with language) | supported | supported (`payload_json.language`, `fenced`, `closed`) | supported (L1: snapshot `reader_source_block.codeLanguage`) | `test_code_block_survives_reload`; `test_table_code_metadata_reload.py` (real PostgreSQL) | supported | L1: language is projected into the snapshot DTO on both the build and DB-reload paths. Rendering the badge/highlight is the Web projection's follow-up. |
| `code_block` (indented, no language) | supported | supported (`language` empty → DTO `null`) | supported (no badge) | supported | supported | — |
| `code_block` (unclosed fence) | fail-closed (content_check) | supported (`closed: false`) | supported (`data-closed="false"`) | `unclosed_fence` fixture | partial | Captured as `code_block` but document routes to `candidate_document_required` with `has_unclosed_fence` warning (`content_check`). |
| `code_line` (Plate internal) | n/a (parser emits `text_content`) | n/a | Web deserialize-only plugin (`ReaderMarkdownCodeLineComponent`) | deserialize tests | partial | Only used by the Web MarkdownTextInput / callout deserialize path. The Stable Document path stores `text_content` as text nodes, not `code_line` elements. |
| `thematic_break` | supported | supported (`metadata_only` route) | NOT rendered as a Reader reading unit | `test_thematic_break_routes_to_metadata_only_no_unit` | partial | **Stable Document keeps the block as `metadata_only`**, but Reader does not emit a reading unit for it. The `<hr>` is invisible in the Reader projection by design; may add a metadata-only divider affordance. |
| `table` (GFM, deterministic) | supported | supported (`table` / `table_row` / `table_cell` hierarchy; `alignments` / `column_count` / `header_rows`) | supported (L1: leaf-cell `tableIsHeader` / `tableAlignment`; wrapper fields when a unit matches) | `gfm_table` fixture; `test_table_code_metadata_reload.py` (real PostgreSQL) | supported | L1: tables with one header row and consistent raw cell counts freeze as `stable_document_ready` (no candidate). |
| `table` (structure-uncertain) | fail-closed (content_check) | supported (`structure_uncertain: true`) | n/a (candidate path) | `table_structure_uncertain` fixture | supported | L1: row/column mismatch (cells would be padded/dropped) or missing header row routes to `candidate_document_required`. |
| `table_row` / `table_cell` | supported | supported (`is_header`, `alignment`) | supported (L1: snapshot `tableIsHeader` / `tableAlignment`) | parser fixtures; `test_table_code_metadata_reload.py` | supported | L1: source header-row semantics and per-cell alignment survive the Reading Record Snapshot reload. |
| `footnote` | supported (mdit-py-plugins `footnote_plugin` enabled) | supported (`footnote` block type, `footnote_id`, `footnote_anchor`) | NOT rendered as a Reader reading unit | `footnote` fixture | partial | **Parser produces degraded/candidate semantics**, not "no parser support". Footnote reference produces `footnote_reference` warning and routes document to `candidate_document_required`. Full footnote rendering (multi-ref / backref / inline footnote) is future work. |
| `image` | detected by suitability gate (`has_image`) | n/a (not frozen as a first-class block in the first phase) | n/a | `has_image` flag in `input_suitability_gate.py` | partial | **Backend suitability gate routes image-containing inputs to candidate review**, NOT "纯文本 + 暂不支持提示". Frontend renders no image block in the Reader. image block schema/renderer is not implemented. |
| `raw_html` (block / inline) | deterministic sanitize (L1) | text extracted, HTML not preserved; paired rich `<aside>` becomes `source_callout` | visible safe text; paired `<aside>` uses the generic callout tree | `raw_html` / `safe_html_adaptation` / `rich_html_aside`; G5 browser suite | partial | Arbitrary raw HTML is intentionally not a first-class block. Sanitization and adaptation notices are supported; paired rich `<aside>` is covered as `source_callout`. |
| `definition_list` | deterministic plain-text degradation | paragraph payload only | visible paragraph text | `definition_list` fixture | partial | No definition-list block type exists; the syntax remains visible and carries `definition_list_degraded` as an adaptation notice. |
| non-HTML placeholders (`vector<T>` / `<name>`) | supported (L1: preserved verbatim, no diagnostic) | supported | supported (plain text) | `safe_html_adaptation` fixture; `test_markdown_safe_normalization.py` | supported | Bare unknown tags without attributes are literal text, not HTML. |
| `task_list` (GFM checkbox) | visible marker + fail-closed diagnostic | list/list_item payload only; no `checked` field | visible marker text; no checkbox semantics | `task_list` fixture; parser clause test | partial | No `task_list` block type or `checked` payload exists. `task_list_unsupported` is `content_check`, so the source cannot freeze silently and routes to candidate review. |
| `image_ocr` | n/a (OCR pipeline) | n/a | n/a | n/a | not_implemented | Image OCR pipeline is the domain of the Candidate Document confirm flow; not in scope for the Markdown structured source pipeline. |
| `caption` | not_implemented | not_implemented | not_implemented | none | not_implemented | — |
| `math` / `mermaid` | `mermaid_static_only` warning; math routes to candidate review | supported (as `code_block` with `language: "mermaid"`) | partial (Reading Record loses language; direct structured-source DTO can render a badge) | `code_mermaid` fixture | partial | Mermaid is stored as static code. The Reading Record Snapshot path does not yet carry `language`, so it cannot distinguish Mermaid after reload. |
| Markdown lint (input safety) | n/a (Web-only) | n/a | Web `lintMarkdownInput` (raw HTML / unsafe link / unclosed fence) | `markdown-lint.test.ts`; `AnalyzeSubmitForm.test.tsx`; G5 browser suite | supported | Lint findings are visible and non-blocking; backend parser classification remains authoritative and the submit path remains recoverable. |
| Submit safety (button + Ctrl/Cmd+Enter) | n/a | n/a | `handleSubmit` flush + non-blocking lint notice | `AnalyzeSubmitForm.test.tsx` R2R Issue C | supported | Both entry points share the same recoverable path; backend content-check outcomes still route to candidate review. |
| Paste fidelity (raw paste submit) | n/a | n/a | `getSubmitText()` returns raw paste text when `!dirty` | `MarkdownTextInput.test.tsx` | supported | Edit-after-paste flips `dirty` and switches to serialize output. |
| Serialize scheduling (debounce) | n/a | n/a | `handleEditorChange` light/heavy split | lifecycle tests + code review; interactive browser gate pending | partial | Production code defers non-boundary serialization by 150 ms and flush returns one submit snapshot. Test-only component instrumentation was removed; a real browser performance/E2E harness remains follow-up work. |
| Strict Mode safety | n/a | n/a | `onDegraded` ref guard | `MarkdownTextInput.test.tsx` R2R | supported | Mount notification fires exactly once under `<StrictMode>`. |

### 7.1 Specific clarifications

- **H1 demotion**: NOT implemented. Any untracked document claiming h1 → h2
  demotion in the Reader projection is describing a plan, not current
  behaviour. The parser preserves h1 as-is; the Reader renders it verbatim
  with the same component family as h2–h6.
- **Code language**: The parser and DB preserve `payload_json.language`,
  and (L1) the Reading Record Snapshot DTO projects it as
  `reader_source_block.codeLanguage` on both the build and DB-reload
  paths (`tests/test_table_code_metadata_reload.py`). The Web Reading
  Record Plate builder's rendering of the badge/highlight is a separate
  follow-up; language projection ≠ syntax highlighting.
- **Footnote**: The backend parser has `footnote_plugin` enabled and
  produces `footnote` / `footnote_ref` / `footnote_anchor` semantics with
  `footnote_reference` warning and `candidate_document_required` outcome.
  The matrix MUST NOT describe this as "no parser support". Full footnote
  rendering (multi-ref / backref / inline footnote) is future work.
- **Image**: The backend suitability gate (`input_suitability_gate.py`)
  detects `has_image` and routes the input to `candidate_document_required`
  with `image_ocr_uncertain` flag. The matrix MUST NOT describe this as
  "纯文本 + 暂不支持提示" — it is a candidate review routing, not a silent
  text-only fallback.
- **Raw HTML**: L1 — the backend deterministically sanitizes raw HTML
  (executable structure removed, text preserved), classifies it
  `adaptation_notice` and continues as `stable_document_ready`. Paired rich
  `<aside>` markup is normalized to the same wrapper/child block tree as
  Markdown aside content; attributes never enter canonical payloads.
  The matrix MUST NOT claim raw HTML is silently stripped without an
  adaptation record. Frontend `lintMarkdownInput` may show the warning badge,
  but the submit path remains non-blocking; the backend parser and its
  persisted adaptation record are authoritative.
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
  `checked` payload field, and no DTO/Reader checkbox consumer. The visible
  `[x]` / `[ ]` marker is retained, but `task_list_unsupported` is a
  `content_check` and routes the document to candidate review; checked state
  is not declared as supported.
- **Code highlighting**: See "Code language" above. Language projection ≠
  syntax highlighting.

### 7.2 Test coverage matrix (G0–G5 / final-gate snapshot)

| Layer | Test file | Covers |
|-------|-----------|--------|
| Parser + DB reload | `services/api/tests/test_reader_snapshot_stable_block_reload.py`; `services/api/tests/test_source_callout_and_reference_reload.py` | `code_block`, `thematic_break` metadata_only, nested-list parent chain, callout descendants, generation fence, mismatched block range, real PostgreSQL reload |
| L1 safe normalization contract | `services/api/tests/test_markdown_safe_normalization.py`; `services/api/tests/test_source_callout_and_reference_reload.py` | script/iframe/event-handler/unsafe-protocol sanitization, safe aside/links, rich HTML aside children/marks, `vector<T>`/`<name>` placeholders, three-level classification, deterministic vs uncertain table routing |
| G0 parser fixtures | `services/api/tests/test_markdown_source_parser.py` | 20 fixtures covering paragraph/heading h1–h6/marks/links/blockquote/citation-reference/callout/GFM alert/list/table/code/footnote/raw HTML/task list/definition list and explicit candidate/adaptation outcomes |
| L1 table/code metadata reload | `services/api/tests/test_table_code_metadata_reload.py` | deterministic table + code language stable-ready freeze, DB payload persistence, snapshot `codeLanguage`/`tableIsHeader`/`tableAlignment` build↔reload equivalence (real PostgreSQL) |
| Web deserialize | `apps/web/src/lib/reader-plate/markdown/deserialize.test.ts` | h1–h3, nested list, code fence language, blockquote (deserialize-only) |
| Web serialize round-trip | `apps/web/src/app/(private)/app/read/MarkdownTextInput.test.tsx` (`R2R/3: real serialize round-trip`) | Markdown → Plate → Markdown preserves h1–h3, nested list, code fence language, blockquote |
| Web scheduling | `apps/web/src/app/(private)/app/read/MarkdownTextInput.test.tsx` | public lifecycle, flush no-op/dedup, Strict Mode safety, long-document round-trip; real browser performance gate remains pending |
| Submit lint gate | `apps/web/src/app/(private)/app/read/AnalyzeSubmitForm.test.tsx` (`R2R Issue C: submit lint gate`) | raw HTML / unsafe link / unclosed fence block fetch on button + Ctrl/Cmd+Enter; safe content submits; attached file bypasses lint |
| Structured source renderer | `apps/web/src/lib/reader-plate/projection/__tests__/structured-source-renderer.test.tsx` | code_block language badge, mermaid badge, table, raw HTML routing, footnote routing |
| Reader selection/manual operations | `apps/web/tests/e2e/reader-selection-floating-toolbar.spec.ts` | 6 Chromium cases covering native selection, Copy, source_callout, 1280x720/390x844 viewports and dark mode |
| Translation prompt profiles | `services/api/tests/test_reader_translation_prompt_profiles.py`; `services/api/tests/test_reader_orchestration_translation_worker.py` | 16 current profile/golden/fake-executor tests plus the existing real-PostgreSQL worker suite; policy/profile separation and mixed-batch isolation |
| dual-MIME fingerprint/icon boundary | `apps/web/src/lib/clipboard/clipboard-source-negotiation.test.ts`; `apps/web/src/lib/clipboard/clipboard-source-fusion.ts`; `services/api/tests/test_source_callout_display_icon.py` | 29 Vitest negotiation tests plus API icon/freeze tests: list/lic direct text, ordered/nested lists, list marks/links, URL/structure/unsafe/text mismatch decline, two-callout all-or-nothing fusion, escaped/inline/fenced/unclosed negatives, wrapper payload icon, ordinary emoji negative, and no emoji Unit/Anchor/job target |
| Chromium aside safety | `apps/web/tests/e2e/source-callout-aside.spec.ts` | 13 passed: complete two-callout dual-MIME article with list/nested-list/link/marks, rich structure, trailing text, safe-URL mismatch visible decline, escaped/unclosed/dangerous HTML and no visible markers |
| G5 real product path | `apps/web/tests/e2e/reader-markdown-g5-real-product.spec.ts`; `services/api/tests/reader_markdown_g5_fake_runner.py` | 1 passed without Ask bootstrap/monkeypatch: normal `app.main:app` import/startup, real `ClipboardItem` `/app/read` → BFF/FastAPI/PostgreSQL, deterministic fake enhancement, list hierarchy/Stable Document/snapshot assertions, browser reload equivalence, trailing-text/no-duplicate checks |

### 7.3 Re-freeze protocol

When a later change promotes a capability from `partial` /
`not_implemented` to `supported`:

1. Update the corresponding row(s) in the matrix above.
2. Add or update the test entries in §7.2 to reference the new tests
   proving the promoted state.
3. Cross-owner review (M1 backend + M2 web + M3 RAG/Ask).
4. Bump the contract `Status` line at the top of this file with the
   re-freeze date and a one-line summary of the promotion.

Silent capability promotions (claiming a feature is supported without
updating this matrix and without adding tests) are a gate failure.
