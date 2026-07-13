# External Primary Sources For Long-Document Parsing / Orchestration

Date: 2026-07-09
Scope: external primary-source evidence only. This note is not a canonical Claread design document.

## Findings Summary

The strongest primary-source pattern is consistent across papers and official provider docs: long context availability is not the same thing as robust long-document understanding, so a production Reader should not default to naive whole-document passes for long inputs. The evidence favors deterministic routing, schema-bounded intermediate outputs, and outline-first or selective section processing for very long documents. Repeated grouped/windowed calls also have real provider-level cost levers when prompts are structured to maximize reusable prefixes.

## Sourced Findings

1. Naive whole-document long-context passes remain unreliable even when models nominally accept long inputs.

   `Lost in the Middle` finds that model performance degrades significantly when relevant information moves into the middle of long inputs, with a U-shaped curve favoring the beginning and end. The paper also reports that on a multi-document QA task, GPT-3.5-Turbo performed worse with the answer-bearing passage in the middle than in a closed-book setting, and that extended-context variants were not necessarily better at using their context. This is direct evidence against assuming that "fits in context" means "safe to process as one full-document pass."

   Sources:
   - https://arxiv.org/abs/2307.03172

2. Larger context windows alone did not solve long-dependency understanding in benchmarked long documents.

   `LooGLE` evaluates eight long-context LLMs on documents over 24,000 tokens and reports that models handled short-dependency tasks better than long-dependency tasks, that in-context learning and chain-of-thought brought only marginal gains, and that context-window extension strategies had limited impact on long-context understanding. The same abstract notes that retrieval-based techniques substantially helped short QA. This supports treating long-document quality as an orchestration problem, not just a context-window-sizing problem.

   Sources:
   - https://arxiv.org/abs/2311.04939

3. For production workflows, first-party guidance favors deterministic workflows over free-form agent loops for well-defined tasks, and explicitly recommends bounded control even when agents are used.

   Anthropic's official engineering guidance distinguishes predefined-code-path workflows from autonomous agents, recommends starting with the simplest solution possible, says workflows offer predictability and consistency for well-defined tasks, and notes that many applications are well served by optimized single calls plus retrieval and in-context examples. Where agents are used, Anthropic explicitly recommends stopping conditions such as a maximum iteration count to maintain control. This is strong first-party support for deterministic planners and bounded orchestration instead of open-ended loops.

   Sources:
   - https://www.anthropic.com/engineering/building-effective-agents

4. First-party structured-output mechanisms materially reduce production risk versus free-form text intermediates.

   OpenAI's Structured Outputs guide states that Structured Outputs ensure responses adhere to a supplied JSON Schema and recommends using Structured Outputs instead of JSON mode when possible, because JSON mode only guarantees valid JSON, not schema adherence. For production Reader orchestration, this is direct provider support for schema-bounded document profiles, section maps, plans, and worker outputs rather than free-form model text that later needs heuristic repair.

   Sources:
   - https://developers.openai.com/api/docs/guides/structured-outputs

5. Hierarchical outline/tree representations can outperform flat chunk retrieval on long-document reasoning tasks.

   `RAPTOR` introduces a recursive tree built from chunk embeddings, clustering, and summaries, then retrieves across different abstraction levels at inference time. The paper reports significant improvements over traditional retrieval-augmented baselines and an absolute 20-point gain on QuALITY when paired with GPT-4. This is direct primary-source evidence for outline-first / hierarchical section representations instead of only flat chunk-level processing.

   Sources:
   - https://arxiv.org/abs/2401.18059

6. Selective reading with compressed memory plus targeted lookups can outperform both raw full-context usage and simpler compression baselines on very long documents.

   `ReadAgent` proposes a human-inspired reading loop that stores memory episodes, compresses them into gist memories, and revisits source passages only when needed. The paper reports that this selective approach outperformed baselines using retrieval, original long contexts, or gist memories alone on QuALITY, NarrativeQA, and QMSum, while extending effective context length by 3.5x to 20x. This is strong support for section-lazy and targeted reread patterns over eager whole-document annotation.

   Sources:
   - https://arxiv.org/abs/2402.09727

7. Official OpenAI docs describe concrete prompt-caching levers that can materially change the economics of repeated grouped/windowed calls.

   OpenAI documents that prompt caching works automatically for prompts of 1024+ tokens, that cache hits require exact prefix matches, and that developers should place static instructions/examples at the beginning and variable content at the end. The docs also note large potential savings, a `prompt_cache_key` routing lever for common prefixes, and retention windows ranging from roughly 5-10 minutes of inactivity up to 24 hours depending on retention policy. For a Reader that repeatedly calls the same worker prompt over different windows, this is a first-party cost/latency lever with direct architectural consequences.

   Sources:
   - https://developers.openai.com/api/docs/guides/prompt-caching

8. Other first-party providers document similar prefix-reuse levers, which strengthens the case for cache-aware windowed orchestration rather than one-off whole-document calls.

   Anthropic documents prompt caching over the full prefix through a `cache_control` breakpoint, a default 5-minute lifetime, optional 1-hour TTL, and cache-read pricing at 0.1x base input price; it also recommends placing static content first. Google documents implicit Gemini caching enabled by default for newer models, minimum token thresholds, cost savings on cache hits, and the same prefix-first advice for large common content. The cross-provider pattern is clear: repeated calls can be made materially cheaper when prompt prefixes are stable and reusable.

   Sources:
   - https://platform.claude.com/docs/en/build-with-claude/prompt-caching
   - https://ai.google.dev/gemini-api/docs/caching

## Implications For Claread

- Do not let long-document Reader enhancement default to a single full-document pass just because the provider context window allows it. Reserve whole-article batch only for short or safely bounded cases.
- Keep planner ownership deterministic in backend code. If an LLM is used, constrain it to schema-bounded profiling or bounded group/section proposals; do not let it own budgets, release order, retries, or open-ended loop control.
- Prefer strict structured outputs for any intermediate artifact that crosses worker boundaries: document profile, section map, translation-group plan, outline, enhancement candidates, and publish envelopes.
- Separate medium/long and very-long modes. A reasonable external-evidence-aligned split is:
  - medium/long: grouped or windowed processing with target/context boundaries;
  - very long: outline-first, then current-section-first or user-selected-region-first lazy enhancement.
- Treat outline generation as a navigation/control primitive, not just a UI embellishment. Hierarchical section metadata can guide both user jumps and backend scheduling.
- Make repeated windowed calls cache-aware by construction:
  - keep policy text, output schema, and few-shot examples in a stable prefix;
  - append only the current window/section payload at the tail;
  - avoid injecting volatile per-request metadata before reusable blocks;
  - log cached-token / cache-hit metrics per provider.
- Add evaluation gates that specifically test middle-of-document quality, not just "can ingest N tokens." The external evidence shows position sensitivity is a real failure mode.
