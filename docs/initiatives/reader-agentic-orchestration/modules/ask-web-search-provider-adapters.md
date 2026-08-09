# Ask Claread Web Search Provider Adapters

Status: approved for implementation
Date: 2026-07-28

## Goal

Connect the existing Ask Claread Web Search foundation to real Qwen and
DeepSeek provider capabilities without replacing the current reasoning,
provenance, citation, SSE, persistence, or history contracts.

The user-visible Search control grants Web Search permission for the turn.
The Ask Claread agent still decides whether the question actually needs a
search. Enabling Search must not force a call, and disabling it must make the
tool unavailable.

## Frozen architecture

Keep the existing provider-neutral `search_web` function tool and
`WebSearchBackend` port. The main Ask model decides whether to call the tool.
The backend selected for that turn invokes the matching provider-native Web
Search facility and returns normalized `WebSearchResult` hits.

This is intentionally not a direct provider-native tool injection into the
main PydanticAI run. Direct injection would require provider-specific changes
to the current model transports, reasoning event stream, tool lifecycle, and
citation extraction. The existing host function-tool seam already supplies
agent autonomy while preserving one provenance and persistence path.

Do not add a Tavily, Serper, Brave, or other third-party fallback in this
workstream.

## Provider bindings

### Qwen

For an Ask execution resolved to a supported DashScope Qwen model, construct a
DashScope Responses adapter from the same server-owned
`ResolvedModelConfig`: provider, adapter, model name, base URL, and API key.

The adapter calls the Responses API with the native
`{"type": "web_search"}` tool. It extracts source URLs only from
`web_search_call.action.sources`, canonicalizes and deduplicates them, limits
the normalized result to the requested `max_results`, and uses the display
domain as the title when the provider supplies no title.

Qwen's answer text, reasoning, raw tool events, provider request IDs, query
metadata, and usage payload must not become public citations.

Official protocol references:

- <https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses>
- <https://help.aliyun.com/en/model-studio/web-extractor>

### DeepSeek

For an Ask execution resolved to `deepseek-v4-flash` or
`deepseek-v4-pro`, construct an Anthropic-compatible adapter from the same
server-owned `ResolvedModelConfig`. It uses
`https://api.deepseek.com/anthropic` and the provider's server-side Web Search
tool contract.

Normalize sources only from provider Web Search result blocks. Never infer a
URL from generated answer text and never manufacture a citation when the
provider omits source data.

The DeepSeek compatibility documentation declares support for
`server_tool_use` and `web_search_tool_result`, but does not fully document the
standalone request wire. Therefore the adapter may be implemented and tested
offline, but it must remain unavailable in production until the bounded real
probe below confirms the exact request shape and returned source fields.

Official protocol references:

- <https://api-docs.deepseek.com/guides/anthropic_api/>
- <https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code/>
- <https://api-docs.deepseek.com/guides/thinking_mode>

## Capability and construction

Capability is derived from the selected option's `ResolvedModelConfig` and the
production adapter registry. There is no global Web Search provider setting,
environment selector, compatibility alias, or deprecated fallback.

A model option is `available` only when all of the following are true:

1. Its resolved provider, adapter, and model name have a registered Web Search
   adapter.
2. The adapter can be constructed from the resolved base URL and credential.
3. The exact provider/model wire has passed its bounded real probe.

The same resolution must produce both the immutable
`ResolvedWebSearchCapability` and the executable `WebSearchBackend`; do not
allow separate decisions to drift. `agentic.run_started.web_search_mode` is
`allowed` only when both exist.

Send and Retry continue to use the persisted user-selected mode. Retry may
reconstruct the backend from the persisted model option, but must never use
the current UI state or silently switch provider. Persisted `allowed` with an
adapter that is no longer constructible fails before streaming with the
existing typed unavailable response.

The model-option API exposes `web_search_capability="available"` using the
same readiness resolver. Do not hard-code availability by option label or
match user question text.

If capability is no longer executable between model-options projection and
send, the POST correctly fails closed before streaming with the stable code
`web_search_unavailable`. The browser owns the friendly copy and may offer a
same-submission retry with Web Search disabled; it must not reinterpret that
legal GET/POST timing difference as resolver drift.

## Outcomes and public boundary

Provider adapters map results to the existing outcomes:

- `completed`: at least one valid normalized source.
- `no_results`: provider completed the search with no usable source.
- `unavailable`: missing credential, unsupported model, disabled provider
  capability, rate limit, or temporary provider unavailability.
- `failed`: malformed provider response or non-recoverable request failure.

No provider exception, raw body, API key, request header, query payload,
provider result reference, handle, fingerprint, rank, score, or provider
reasoning may enter SSE, public DTOs, logs, or cold history.

The existing block-level provenance remains authoritative:

- `basis="web"` requires a current-turn registered Web evidence handle.
- Article and Web handles cannot substitute for each other.
- The finalizer mints message-local citation IDs.
- Public Web citations contain only `citation_id`, `source_kind="web"`,
  canonical `url`, non-empty `title`, and optional `description`.
- Article citations continue to use Inline Citation; Web citations continue
  to use Prompt Kit Sources.

## Bounded real verification

After offline tests pass, run at most one fixed real request per provider, with
no retry and a bounded output budget. Existing server-side credentials may be
used but must never be printed or persisted in test artifacts.

Each probe must verify:

1. The actual model identity and endpoint.
2. The exact native Web Search request shape.
3. The provider really performs a search for a fixed time-sensitive query.
4. At least one canonical source URL is returned in the documented result
   structure.
5. Thinking/tool behavior remains compatible.
6. Raw provider data and credentials are absent from public/logged surfaces.

Passing only fixtures, mocked HTTP, or a manually authored response is not
activation evidence. If a provider probe fails or its sources cannot be
reliably extracted, keep that provider unavailable and report the observed
wire without weakening provenance.

## Acceptance gates

Implementation is complete when:

1. Provider adapters have deterministic mocked-wire tests for success,
   no-results, rate-limit/unavailable, malformed response, timeout, URL
   canonicalization, deduplication, and result limits.
2. Capability tests prove model-specific readiness and fail-closed behavior.
3. The existing FunctionModel vertical slice still proves
   `search_web -> evidence -> validated web block -> PublicCitation -> SSE ->
   persistence -> cold history` with no internal leakage.
4. Send/Retry symmetry and pre-stream failure behavior pass.
5. Qwen and DeepSeek are activated independently only after their respective
   real probe passes.
6. Browser verification proves: the Search control appears only for an
   activated option, sending preserves the selected mode, the agent may call
   search autonomously, `searching_web` progress appears, and Prompt Kit
   Sources render the returned public Web citations.
7. Existing reasoning streaming, article Inline Citation, Markdown rendering,
   cancellation, and failure paths remain green.
