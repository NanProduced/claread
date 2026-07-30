# Semantic Automatic Layer Policy

**Status**: implemented (2026-07-29 repair). Authoritative contract for
deterministic content-role classification and automatic T/V/G/S policy.

**Truth ownership**

| Fact | Owner | Notes |
|------|--------|--------|
| `content_role` + `contract_version` | `stable_document_blocks.payload_json.semantic` | Frozen with generation |
| `automatic_layer_policy` + `resolver_version` | `reading_units.metadata_json.semantic` | Derived cache only |
| Snapshot `contentRole` / `automaticLayerPolicy` | projection | Automatic policy only; not job/loading/error |
| Classification rules | `semantic_classifier.py` | Single seam |
| Policy matrix + fence | `automatic_layer_policy.py` | Single seam |

**Product matrix (`semantic_contract_v1` / `automatic_layer_policy_v1`)**

| Content | T | V | G | S |
|---------|:-:|:-:|:-:|:-:|
| prose / list prose | ✅ | ✅ | ✅ | ✅ |
| heading | ✅ | ❌ | ❌ | ❌ |
| citation_reference | ✅ | ❌ | ❌ | ❌ |
| quotation / Markdown blockquote | ✅ | ❌ | ❌ | ❌ |
| source_callout / Notion aside / GFM alert | ✅ | ❌ | ❌ | ❌ |
| code / table / table_cell / link_only | ❌ | ❌ | ❌ | ❌ |

Legacy (missing `contract_version`): fail-open, all automatic layers on.

**Aside / blockquote flow**

1. Parser: `<aside>…</aside>` → `block_type=blockquote` +
   `payload.source_semantic_hint=html_aside` (ordinary `<div>` never sets hint).
2. Classifier (only seam): hint / GFM `[!NOTE]` → `source_callout`; bare
   `>` → `quotation` (enforced, not shadow for structure).
3. Resolver: blockquote structure is always T-only even if a role is shadow-only.

**Rollout mode** (`Settings.reader_automatic_layer_policy_mode: Literal["off","shadow","enforce"]`)

| Mode | Bootstrap | Worker `allows(layer)` | Executor |
|------|-----------|------------------------|----------|
| `off` | keep all | never block | runs |
| `shadow` | keep all + would-skip logs | never block | runs |
| `enforce` | filter / typed-supersede | enforced | 0 calls if disallowed |

Mode is **frozen on the job** at creation (`input_json.semantic_policy_mode`)
and is part of the operation fingerprint (`sem:…:mode:{mode}`). Workers must
use the frozen job mode, never re-read live settings for an existing job.

Legacy jobs with fence but missing mode → treat as `enforce` (prior behaviour).

**Job fence**

Automatic, section, **and** Z+ (grammar_bundle window) jobs stamp:
`semantic_contract_version`, `automatic_layer_policy_resolver_version`,
`automatic_layer_name`, `semantic_policy_mode`.

All three topologies share **one** strict fence builder:
`generation_semantic_fence_from_targets()` (in `automatic_layer_policy.py`),
exposed to automatic/section bootstraps via the public seam
`build_semantic_fence_from_unit_maps()` (in `job_bootstrap.py`) and called
directly by `ZPlusBootstrapService._create_window_reader_job()`. There is
**no second fence rule**. The builder contract:

| Target mix | Behaviour |
|------------|-----------|
| Empty targets | Legacy fence (`contract=None, resolver="legacy_open"`) |
| All legacy (no contract) | Legacy fence |
| Uniform contract + resolver | Exact pair |
| Mixed contract versions | `SemanticFenceConstructionError` (fail closed) |
| Mixed resolver versions | `SemanticFenceConstructionError` (fail closed) |
| Legacy + semantic mix | `SemanticFenceConstructionError` (fail closed) |

`SectionTranslationBootstrapService.request_section_translation()` builds the
fence from real target unit metadata (`reading_units.metadata_json.semantic`)
at job-creation time and freezes `automatic_layer_name="translation"` (server
forces the translation family; clients cannot select vocabulary/grammar via
section identity). The shared builder raises
`SemanticFenceConstructionError` on mixed versions; the section bootstrap
catches it and returns `REJECT` with `reason=semantic_fence_inconsistent`
— no `reader_jobs` / `reader_runs` row is persisted.

Automatic bootstrap calls the same shared builder inside its transaction.
Mixed versions raise before any row is persisted, so the transaction rolls
back. No job that would be destined for worker supersede is ever created.

Z+ bootstrap (`ZPlusBootstrapService.bootstrap_grammar_window_plan()`) calls
`generation_semantic_fence_from_targets()` inside
`_create_window_reader_job()`, which runs inside the outer
`async with conn.transaction()` block. A mixed fence identity is a
**generation invariant violation**, not a user content rejection: the typed
`SemanticFenceConstructionError` propagates out of the transaction, and
PostgreSQL rolls back every row already INSERTed in that transaction —
`layer_analysis_plans`, `analysis_windows`, `reader_runs`, and
`reader_jobs`. No half-legitimate plan/window/run/job survives. This is
fail-closed invariant handling: Z+ does not catch and silently degrade,
does not sort-and-pick-one, and does not create a job that would be
superseded by the worker fence.

The four fields are written to both `input_json` and the run `envelope_json`.

Fingerprint token: `sem:{contract}:{resolver}:mode:{mode}` — identical format
for automatic, section, and Z+ jobs, composed via the shared
`_compose_operation_fingerprint` seam.

Workers call `validate_automatic_job_semantic_fence` before any executor/LLM.
Version mismatch always typed-supersedes. Layer disallow only under frozen
`enforce`. Executor calls = 0 on fence supersede.

**Job layer identity (fenced jobs)**

- Any fence key present ⇒ `automatic_layer_name` **required**
- Must equal worker expected layer exactly:
  `translation` | `vocabulary` | `grammar_note`
- `layers_any` only selects grammar_note/sentence_analysis **policy
  admission**; it does not relax job layer identity
- No `grammar_bundle` alias on the job layer fence
- Missing/mismatched layer ⇒ `semantic_policy_version_mismatch` before
  executor (mode still defaults to enforce when mode key missing)

**USER_EXPLICIT section translation** (allows=false exemption only):

Production bootstrap (`SectionTranslationBootstrapService`) stamps the full
fence at creation, so worker-side fence validation is effective on real
section jobs — not only on manually-constructed fenced test jobs.

Trusted claim requires **all** of:

- fingerprint base `translation_article_section_v1`
- `request_origin=section_v1`
- `section_identity` via `parse_section_identity_mapping`
- identity record/base/generation match trusted **DB job row**
- identity range + anchors match trusted DB **`target_key`**
  (`decode_section_target_key`)
- DB universe `expand_closed_unit_range(start,end)` equals both
  worker-loaded unit ids **and** `input_json.target_unit_ids` (order-exact)
- anchors both absent **or** both present; when present, DB
  `anchor_segments` prove ownership of start/end units on the same base

Exemption rules:

- Only skips `allows("translation")==false` under enforce
- **Never** skips contract/resolver/layer identity fence
- **Never** applies to vocabulary / grammar / sentence_analysis / Z+ window
- Incomplete section claim on the translation lane → fail closed
  (`semantic_policy_version_mismatch`) before any executor call

**Non-goals**: Ask/RAG, Web Search, UI, DDL, legacy backfill, real LLM in CI.
