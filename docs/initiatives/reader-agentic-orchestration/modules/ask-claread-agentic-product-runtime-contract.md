# Ask Claread Agentic Product and Runtime Contract

Status: accepted design specification, 2026-07-25; implementation tracker
verified 2026-07-26.

## Purpose

This specification defines the product and runtime boundaries for the new
`reader_record_ask` agentic path. It is the authority for Ask Claread intent
ownership, knowledge use, provenance, citation presentation, Web Search,
reasoning visibility, and answer streaming.

The code and tests remain authoritative for lower-level wire details. Temporary
research, review reports, and task trackers are not long-term contract sources.

## Product identity

Ask Claread is a general AI assistant centered on improving English ability,
especially reading comprehension, grammar, vocabulary, and expression.

The English-learning focus is a center of gravity, not a hard topic wall:

- answer ordinary questions helpfully, including relevant general knowledge;
- do not reject a question only because it is not about English;
- when a conversation moves substantially away from Claread's focus, answer
  proportionately and guide the user back gently;
- do not force an artificial English-learning angle into every unrelated reply;
- a future Claread manual knowledge base may add a product-help role without
  replacing the English-learning identity.

## Decision ownership

### The Agent owns semantic decisions

The same answer Agent interprets the user's natural-language request and decides:

- whether the current baseline evidence is sufficient;
- whether to call `expand_evidence` or `search_current_article`;
- whether an enabled Web Search capability is needed;
- the focused search query and legal tool-call sequence;
- whether the answer uses article knowledge, general knowledge, Web knowledge,
  or a valid mixture;
- how to honor natural-language requests such as “仅依据本文” or “请给出处”.

The application must not replace these decisions with keyword routing, exact
question matching, unconditional retrieval, or an implicit client classifier.

### The Host owns capability and truth

The Host decides and enforces:

- which tools and provider capabilities are available for the turn;
- the active model execution configuration;
- identity, document, generation, and authorization fences;
- tool-call limits, model-view budgets, provider quotas, and timeouts;
- whether a tool actually executed and which typed outcome it returned;
- whether evidence belongs to the current turn and envelope;
- whether article coverage supports the declared article scope;
- whether a Web result has a verifiable public source;
- which validated citations may enter public DTOs and navigation.

The model proposes actions and provenance. It never grants itself capability or
declares evidence valid.

## Knowledge and answer contract

The current article is the answer foundation, not the knowledge ceiling.

An ordinary answer may contain:

- `article` content supported by current-turn article evidence;
- `general` content based on stable model knowledge;
- `web` content supported by verified Web evidence when Search is enabled;
- a valid mixture of these bases.

The model output remains minimal. Do not add a separate
`interpreted_intent`, evaluation-only state, or client-derived intent policy.
Each semantic answer block declares only the provenance needed to validate the
answer:

```text
AnswerBlockDraft
  text
  basis: article | general | web
  article_scope: null | selection_bounded | evidence_bounded | article_overview | full_article
  evidence_handles: opaque internal handles
```

The existing response outcome may distinguish a normal answer, clarification,
or a safe unavailable/source-unavailable result. It must not carry extra model
text when the Host owns the user-visible limitation message.

### Provenance invariants

- An `article` block has at least one current-envelope article evidence handle.
- Its `article_scope` is validated against server-confirmed coverage.
- A `general` block has no article or Web handle and no article scope.
- A `web` block has at least one current-turn verified Web evidence handle.
- Article evidence and Web evidence cannot support each other's blocks.
- Unknown, fabricated, stale, cross-turn, or source-kind-mismatched handles fail
  closed.
- `knowledge_mode` is derived only after block validation. The model does not
  supply it.
- Citation presentation may be compact, but internal block-level provenance is
  never optional.

Public DTOs, SSE, history, and browser state never expose `evh_*`, raw evidence
handles, envelope fingerprints, private locators, provider payloads, or internal
identity fields. Public citation IDs are message-local projections resolved
again by the server under the current user and record fence.

## Citation presentation

Article and Web provenance use different visual languages.

### Article evidence

- Every validated article-backed passage receives a compact Inline Citation.
- Use the AI Elements `InlineCitation` component.
- Hover or activation shows the relevant article excerpt.
- Article citations may navigate to the verified article position.
- Do not repeat article citations in a large answer-end Sources list.

### Web evidence

- Web-backed answers use the prompt.kit `Source` component.
- The answer-end Sources area is reserved for verified Web sources.
- General-knowledge blocks do not display a citation or a “general knowledge”
  badge.
- A provider-generated URL or citation marker is not sufficient. The Host must
  first normalize and validate the source.

## Web Search

The composer exposes a visible Search control.

- Search off: the turn has no Web Search capability.
- Search on: the selected model's verified Web Search adapter enters the Agent's
  action space.
- Enabling Search authorizes the capability; it does not force a search call.
- The Agent normally uses native knowledge first and searches when freshness or
  verification is needed.
- The Host does not silently switch models, providers, or search modes.

Web Search is a model execution capability whose wire contract may differ by
provider, model, and API transport. A provider adapter must normalize native
results into one Claread Web Evidence contract.

Only a path that returns reliable source metadata may produce `basis=web` or a
public Web Source. A native search path that returns only synthesized text is
not Web-provenance capable for Ask Claread.

Search failures are fail-soft:

- expose a typed, sanitized tool outcome to the Agent;
- permit a bounded legal retry or a general-knowledge answer;
- never produce a Web block without verified Web evidence;
- do not fail the whole Ask turn only because Web Search was empty or
  unavailable.

Provider integration must be designed from the official DeepSeek and Qwen
documentation for the exact selected model and transport. A single generic
`enable_search=true` assumption is not a valid cross-provider design.

## System prompt governance

The production system prompt contains only stable product and safety principles:

- Ask Claread's English-learning-centered identity;
- the article-as-foundation-not-boundary principle;
- tool autonomy and evidence honesty;
- untrusted-content and capability boundaries;
- the minimum structured-output instructions not already enforced by schema.

Dynamic server facts such as coverage, available tools, and Search state belong
in typed server-owned projections. Tool-use guidance belongs primarily in the
tool schema and description. Output structure belongs in Pydantic models and
validators.

The production prompt and product policy must not contain:

- exact user-question matching;
- rules created to pass a single record or regression sample;
- sample-specific distinctions such as a fixed city/province correction;
- fixed exercise counts or answer wording introduced only for an evaluation;
- duplicated schema, coverage, or tool-contract prose;
- a hidden client or Host interpretation of natural-language intent.

Evaluation does not shape the production output contract. Model quality,
including intent following and teaching quality, will be evaluated later through
an independent LLM-as-a-Judge dataset and harness.

## Reasoning contract

Thinking-capable Ask options expose the provider's actual reasoning stream.
DeepSeek V4 Flash and Pro enable thinking; Qwen options follow their selected
provider transport's official thinking contract.

User-visible reasoning:

- preserves the provider's wording, order, and semantics;
- is not summarized, rewritten, or regenerated by another LLM;
- passes only through deterministic minimum security redaction for internal
  handles, identity, system-instruction fragments, and authentication material;
- streams through SSE as the provider emits it;
- uses AI Elements `Reasoning`;
- remains collapsed by default and shows a low-weight shimmer while running;
- is persisted after successful completion;
- reloads as the same concatenated visible text and remains expandable.

Raw provider payloads, signatures, authentication data, and unredacted internal
fields never enter SSE, the database, history DTOs, browser state, or logs.

Do not display progress labels as fake reasoning. If the provider emits no
reasoning, render no fabricated reasoning content.

`ChainOfThought` is a future enhancement for real structured steps or
server-observed tool phases. It is not a visual wrapper for an unstructured
reasoning string.

## Answer streaming and finalization

Answer streaming uses an explicit provisional-draft model:

1. The UI may show answer text before final validation as a temporary draft.
2. The draft has no citations, Sources, copy action, or other affordance that
   presents it as final.
3. A model retry replaces or resets the current draft. Retry attempts are never
   concatenated into one answer.
4. The Host validates the final structured output, provenance, handles, scope,
   and citations.
5. On success, the canonical answer replaces the draft, validated citations are
   attached, the answer and visible reasoning are committed, and then
   `message.completed` is emitted.
6. Cancellation, validation failure, or persistence failure never leaves a
   provisional answer or citation in cold history.

The exact SSE event names and persistence schema are implementation details, but
hot and cold projections must be byte-equivalent for the committed user-visible
answer and reasoning text.

## Superseded design direction

The normal Ask flow must not wire client or Host fields such as
`article_only`, `citation_required`, or `requested_citation_scope` as a
pre-model interpretation of natural language.

The previously implemented pure policy and resolver work may be retained only
where it directly supports valid block provenance. Speculative intent policy,
snapshot, and resolver logic with no current product source must be removed or
refactored rather than connected to production.

`web_capability` is not user intent. It becomes a separate execution capability
derived from the visible Search control and the selected model adapter.

## Current implementation status

Status verified on 2026-07-26:

| Workstream | State | Current evidence |
|---|---|---|
| Article RAG evidence lifecycle | accepted | Search-to-server-minted-evidence positive path, fabricated-handle rejection, call-limit semantics, and persistence-failure non-leakage are covered. |
| Per-turn model execution configuration | accepted | Send and retry use one resolver; provider completion caps and host output limits are applied symmetrically without silently substituting a default model (`b28fbe0d`). |
| Prompt and semantic ownership | accepted | The Agent owns natural-language intent and tool decisions. The unused Host intent resolver, exact-question correctness rules, duplicate prompt assembly, and policy JSON block are removed (`9ee3c736`). |
| Internal block provenance | accepted | Article, general, and mixed blocks are mechanically validated; evidence identity and article coverage fail closed; the Host derives `knowledge_mode`. |
| Public citation projection | accepted | The finalizer consumes canonical validated blocks; `reader_record_ask_agentic_v2` exposes message-local citation IDs only; `source_unavailable` is replayable; hot SSE and cold history agree; public surfaces are no-`evh` (`66cd6635`). |
| Article citation UI | accepted with one deferred affordance | AI Elements Inline Citation supports hover preview and ordered multi-citation navigation. Article Sources are not repeated at answer end. The secure navigation API is present, but the visible jump action remains intentionally hidden until the Plate typed-location adapter exists. |
| Reasoning SSE and persistence | not implemented | Provider thinking parts can reach the internal transport observer, but production passes no projector. No reasoning delta is published or persisted. |
| Provisional answer streaming | not implemented | The agentic path still publishes only the validated final answer. |
| Web Search and Web Sources | not implemented | `basis=web` remains fail-closed. The visible Search control, provider adapters, verified Web evidence, and prompt.kit Source integration remain future work. |

### Verification snapshot

- The completed backend seams pass a combined focused gate of 265 tests.
- All 699 `reader_record_ask` tests collect after the citation-finalizer test
  migration; the focused Article RAG file passes 18 tests.
- The Ask workspace, Inline Citation, API/BFF, and SSE frontend seams pass 189
  focused tests. Citation browser acceptance passes the hover, carousel,
  no-answer-end-Sources, no-fake-jump, and no-raw-handle contract.
- The repository-wide aggregate is not a release gate yet: unrelated global
  state, current-working-directory, temp-directory permission, and parallel
  Plate/Reader changes still make broad suites order-dependent. Focused gates
  remain the attributable acceptance evidence.
- No bounded real-provider acceptance has yet verified that each configured
  model emits usable reasoning or follows the new provenance contract. That
  belongs to the later real-model and LLM-as-a-Judge gates, not to structural
  unit tests.

### Reasoning workstream entry conditions

The next workstream may start, with these current gaps treated as requirements:

1. `ask-main-deepseek-v4-flash` and its replan profile still set
   `thinking.type=disabled`; this conflicts with the accepted product contract
   and must be corrected and wire-tested.
2. `thinking_transport` receives provider reasoning, but production passes
   `thinking_observer=None`; no content reaches SSE, persistence, or history.
3. The agentic path currently maps safe analysis lifecycle events onto legacy
   `reasoning.started/completed`. The Web UI can therefore render an empty
   “model returned no displayable reasoning” placeholder even though no
   reasoning projection exists. New agentic content events must be distinct
   from those phase events, and the new UI must not fabricate empty content.
4. The AI Elements Reasoning wrapper currently auto-opens while streaming.
   Ask Claread requires `defaultOpen={false}` so the shimmer remains collapsed
   unless the user opens it.
5. Direct iteration of PydanticAI `AgentEventStream` is deprecated. The
   reasoning implementation must migrate to the context-managed stream API and
   preserve cancellation and multi-tool-round behavior.
6. Successful turns must persist the exact visible reasoning projection
   atomically with the final answer. Cancellation, validation failure, and
   persistence failure must not create cold-history reasoning.

After reasoning, the remaining implementation order is:

1. provisional answer streaming with retry replacement and atomic
   finalization;
2. Plate typed-location navigation for article Inline Citations;
3. visible Search capability, provider-specific Web Search adapters, verified
   Web evidence, and prompt.kit Web Sources;
4. joint real-model and browser acceptance;
5. legacy agentic-v1 handlers, old workflow code, and obsolete-document
   cleanup after the new path is accepted.

## Implementation boundaries

The work should proceed as separate, reviewable lines:

1. provenance and prompt-policy realignment;
2. citation projection and UI acceptance;
3. reasoning SSE and persistence;
4. provisional answer streaming and atomic finalization;
5. provider-specific Web Search capability and Web evidence;
6. joint end-to-end acceptance;
7. legacy-path and obsolete-document cleanup.

Reasoning, streaming, Web Search, and citation UI must not be bundled into one
unreviewable cross-stack change.

## Primary references

- [PydanticAI tools](https://pydantic.dev/docs/ai/tools-toolsets/tools/)
- [PydanticAI output validation](https://pydantic.dev/docs/ai/core-concepts/output/)
- [DeepSeek thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/)
- [DeepSeek tool calls](https://api-docs.deepseek.com/guides/tool_calls/)
- [Qwen Web Search](https://help.aliyun.com/en/model-studio/web-search)
- [AI Elements Inline Citation](https://elements.ai-sdk.dev/components/inline-citation)
- [AI Elements Reasoning](https://elements.ai-sdk.dev/components/reasoning)
- [prompt.kit Source](https://www.prompt-kit.com/docs/source)
