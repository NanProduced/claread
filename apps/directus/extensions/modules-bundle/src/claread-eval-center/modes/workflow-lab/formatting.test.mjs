import assert from "node:assert/strict";
import test from "node:test";

import { extractLLMConfigSnapshot } from "./composables/workflowLabFormatting.js";
import { extractLLMConfigSnapshot as extractLLMConfigSnapshotAnchor } from "./composables/anchorDebugFormatting.js";

// ── workflowLabFormatting.extractLLMConfigSnapshot ─────────────

test("extractLLMConfigSnapshot returns null for null artifact", () => {
  assert.equal(extractLLMConfigSnapshot(null), null);
});

test("extractLLMConfigSnapshot returns null for missing snapshot", () => {
  assert.equal(extractLLMConfigSnapshot({}), null);
});

test("extractLLMConfigSnapshot returns null for non-object snapshot", () => {
  assert.equal(extractLLMConfigSnapshot({ llm_config_snapshot: "bad" }), null);
});

test("extractLLMConfigSnapshot extracts all fields from full snapshot", () => {
  const artifact = {
    llm_config_snapshot: {
      profile_name: "workflow-qwen36-flash-tool-required",
      provider: "dashscope",
      adapter: "openai_compatible",
      model_name: "qwen3.6-flash-2026-04-16",
      fallback_profiles: [],
      model_settings: {
        temperature: 0.3,
        extra_body: { enable_thinking: false },
      },
      openai_profile: {
        openai_supports_tool_choice_required: true,
      },
      structured_output: {
        default_structured_output_mode: "tool",
        supports_json_schema_output: false,
        supports_json_object_output: false,
        openai_supports_tool_choice_required: true,
        expected_tool_choice: "required",
        expected_response_format: null,
      },
      parallel_tool_calls: false,
      thinking_enabled: false,
      structured_output_runtime: [
        {
          agent_name: "vocabulary",
          profile_name: "workflow-qwen36-flash-tool-required",
          provider: "dashscope",
          model_name: "qwen3.6-flash-2026-04-16",
          resolved_default_structured_output_mode: "tool",
          resolved_openai_supports_tool_choice_required: true,
          inferred_expected_tool_choice: "required",
          inferred_expected_response_format: null,
          resolved_parallel_tool_calls: false,
          resolved_thinking_enabled: false,
          observed_usage: null,
          observed_retry_count: null,
          observed_request_count: null,
        },
      ],
    },
  };

  const result = extractLLMConfigSnapshot(artifact);
  assert.deepEqual(result, {
    profile: "workflow-qwen36-flash-tool-required",
    provider: "dashscope",
    adapter: "openai_compatible",
    model: "qwen3.6-flash-2026-04-16",
    openai_supports_tool_choice_required: true,
    expected_tool_choice: "required",
    supports_json_schema_output: false,
    supports_json_object_output: false,
    default_structured_output_mode: "tool",
    expected_response_format: null,
    thinking_enabled: false,
    parallel_tool_calls: false,
    structured_output_runtime: [
      {
        agent_name: "vocabulary",
        profile_name: "workflow-qwen36-flash-tool-required",
        provider: "dashscope",
        model_name: "qwen3.6-flash-2026-04-16",
        resolved_default_structured_output_mode: "tool",
        resolved_openai_supports_tool_choice_required: true,
        inferred_expected_tool_choice: "required",
        inferred_expected_response_format: null,
        resolved_parallel_tool_calls: false,
        resolved_thinking_enabled: false,
        observed_usage: null,
        observed_retry_count: null,
        observed_request_count: null,
      },
    ],
  });
});

test("extractLLMConfigSnapshot detects thinking via enable_thinking=true", () => {
  const artifact = {
    llm_config_snapshot: {
      profile_name: "test",
      provider: "p",
      adapter: "a",
      model_name: "m",
      model_settings: { extra_body: { enable_thinking: true } },
      structured_output: {},
    },
  };
  const result = extractLLMConfigSnapshot(artifact);
  assert.equal(result.thinking_enabled, true);
});

test("extractLLMConfigSnapshot detects thinking via thinking.type=enabled", () => {
  const artifact = {
    llm_config_snapshot: {
      profile_name: "test",
      provider: "p",
      adapter: "a",
      model_name: "m",
      model_settings: { extra_body: { thinking: { type: "enabled" } } },
      structured_output: {},
    },
  };
  const result = extractLLMConfigSnapshot(artifact);
  assert.equal(result.thinking_enabled, true);
});

test("extractLLMConfigSnapshot returns expected_tool_choice from structured_output", () => {
  const artifact = {
    llm_config_snapshot: {
      profile_name: "eval-profile",
      provider: "eval-provider",
      adapter: "openai_compatible",
      model_name: "eval-model",
      model_settings: {},
      structured_output: {
        openai_supports_tool_choice_required: false,
        expected_tool_choice: "auto",
      },
    },
  };
  const result = extractLLMConfigSnapshot(artifact);
  assert.equal(result.expected_tool_choice, "auto");
  assert.equal(result.openai_supports_tool_choice_required, false);
});

test("extractLLMConfigSnapshot handles missing structured_output gracefully", () => {
  const artifact = {
    llm_config_snapshot: {
      profile_name: "test",
      provider: "p",
      adapter: "a",
      model_name: "m",
    },
  };
  const result = extractLLMConfigSnapshot(artifact);
  assert.equal(result.profile, "test");
  assert.equal(result.expected_tool_choice, null);
  assert.equal(result.thinking_enabled, false);
  assert.equal(result.parallel_tool_calls, null);
  assert.equal(result.expected_response_format, null);
  assert.deepEqual(result.structured_output_runtime, []);
});

test("extractLLMConfigSnapshot defaults structured_output_runtime to empty array", () => {
  const artifact = {
    llm_config_snapshot: {
      profile_name: "test",
      provider: "p",
      adapter: "a",
      model_name: "m",
      structured_output: {},
    },
  };
  const result = extractLLMConfigSnapshot(artifact);
  assert.ok(Array.isArray(result.structured_output_runtime));
  assert.equal(result.structured_output_runtime.length, 0);
});

// ── anchorDebugFormatting.extractLLMConfigSnapshot ─────────────

test("anchorDebug extractLLMConfigSnapshot returns null for null artifact", () => {
  assert.equal(extractLLMConfigSnapshotAnchor(null), null);
});

test("anchorDebug extractLLMConfigSnapshot extracts same fields", () => {
  const artifact = {
    llm_config_snapshot: {
      profile_name: "workflow-qwen36-flash",
      provider: "dashscope",
      adapter: "openai_compatible",
      model_name: "qwen3.6-flash-2026-04-16",
      model_settings: {},
      structured_output: {
        default_structured_output_mode: "tool",
        openai_supports_tool_choice_required: false,
        expected_tool_choice: "auto",
      },
      parallel_tool_calls: null,
      thinking_enabled: false,
      structured_output_runtime: [],
    },
  };
  const result = extractLLMConfigSnapshotAnchor(artifact);
  assert.equal(result.profile, "workflow-qwen36-flash");
  assert.equal(result.expected_tool_choice, "auto");
  assert.equal(result.thinking_enabled, false);
  assert.equal(result.parallel_tool_calls, null);
  assert.equal(result.expected_response_format, null);
  assert.deepEqual(result.structured_output_runtime, []);
});
