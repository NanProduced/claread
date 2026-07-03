import { describe, expect, it } from "vitest";

import {
  ARTICLE_RAG_ENSURE_STATUS_FALLBACK,
  ARTICLE_RAG_INDEX_STATUS_FALLBACK,
  ASK_ARTICLE_RAG_STATUS_FALLBACK,
  PIPELINE_NEXT_ACTION_FALLBACK,
  PIPELINE_OUTCOME_FALLBACK,
  mapArticleRagIndexEnsure,
  mapArticleRagIndexStatus,
  mapArtifactPipelineStatus,
  mapAskArticleRagSidecar,
  normalizeArticleRagEnsureStatus,
  normalizeArticleRagIndexStatus,
  normalizeAskArticleRagStatus,
  normalizePipelineNextAction,
  normalizePipelineOutcome,
} from "./status-mapper";
import type {
  ReaderArticleRagIndexEnsureResponseDto,
  ReaderArticleRagIndexStatusResponseDto,
  ReaderArtifactPipelineStatusResponseDto,
} from "@/types/api/reader-plate";
import type { ReaderAskArticleRagSidecarDto } from "@/types/api/reader-ask";

// ---------------------------------------------------------------------------
// Pipeline outcome / next_action normalizers
// ---------------------------------------------------------------------------

describe("normalizePipelineOutcome", () => {
  it.each([
    "upload_pending",
    "upload_available_not_submitted",
    "extraction_queued",
    "extraction_running",
    "extraction_retry_later",
    "extraction_failed",
    "materialization_queued",
    "materialization_running",
    "materialization_retry_later",
    "materialization_failed",
    "stable_document_ready",
    "candidate_document_required",
    "input_rejected_or_action_required",
  ] as const)("passes known outcome %s through unchanged", (outcome) => {
    expect(normalizePipelineOutcome(outcome)).toBe(outcome);
  });

  it.each([
    "unknown_value",
    "",
    "EXTRACTION_FAILED",
    null,
    undefined,
    42,
    {},
  ])("coerces unknown outcome %p to extraction_failed fallback", (value) => {
    expect(normalizePipelineOutcome(value)).toBe(PIPELINE_OUTCOME_FALLBACK);
    expect(PIPELINE_OUTCOME_FALLBACK).toBe("extraction_failed");
  });
});

describe("normalizePipelineNextAction", () => {
  it.each([
    "complete_upload",
    "submit_input",
    "wait_for_worker",
    "retry_later",
    "show_error",
    "open_reader",
    "confirm_candidate_document",
    "revise_input",
  ] as const)("passes known next_action %s through unchanged", (action) => {
    expect(normalizePipelineNextAction(action)).toBe(action);
  });

  it.each(["unknown_action", "", "SHOW_ERROR", null, undefined, 0])(
    "coerces unknown next_action %p to show_error fallback",
    (value) => {
      expect(normalizePipelineNextAction(value)).toBe(PIPELINE_NEXT_ACTION_FALLBACK);
      expect(PIPELINE_NEXT_ACTION_FALLBACK).toBe("show_error");
    },
  );
});

// ---------------------------------------------------------------------------
// Article RAG index status / ensure status normalizers
// ---------------------------------------------------------------------------

describe("normalizeArticleRagIndexStatus", () => {
  it.each([
    "not_ready",
    "not_indexed",
    "queued",
    "indexing",
    "indexed",
    "failed",
    "superseded_or_stale",
    "unavailable",
  ] as const)("passes known status %s through unchanged", (status) => {
    expect(normalizeArticleRagIndexStatus(status)).toBe(status);
  });

  it.each(["unknown_status", "", "INDEXED", null, undefined, 1])(
    "coerces unknown status %p to unavailable fallback",
    (value) => {
      expect(normalizeArticleRagIndexStatus(value)).toBe(
        ARTICLE_RAG_INDEX_STATUS_FALLBACK,
      );
      expect(ARTICLE_RAG_INDEX_STATUS_FALLBACK).toBe("unavailable");
    },
  );
});

describe("normalizeArticleRagEnsureStatus", () => {
  it.each([
    "enqueued",
    "idempotent_noop",
    "not_ready",
    "no_active_base",
    "generation_mismatch",
    "record_not_found",
    "plan_hash_mismatch",
    "bootstrap_inconsistent",
    "error",
  ] as const)("passes known ensure status %s through unchanged", (status) => {
    expect(normalizeArticleRagEnsureStatus(status)).toBe(status);
  });

  it.each(["unknown_ensure", "", "ENQUEUED", null, undefined])(
    "coerces unknown ensure status %p to error fallback",
    (value) => {
      expect(normalizeArticleRagEnsureStatus(value)).toBe(
        ARTICLE_RAG_ENSURE_STATUS_FALLBACK,
      );
      expect(ARTICLE_RAG_ENSURE_STATUS_FALLBACK).toBe("error");
    },
  );
});

// ---------------------------------------------------------------------------
// Ask article_rag sidecar status normalizer
// ---------------------------------------------------------------------------

describe("normalizeAskArticleRagStatus", () => {
  it.each([
    "available",
    "empty",
    "not_indexed_or_unavailable",
    "composer_rejected",
    "disabled",
    "stale_due_to_repair",
  ] as const)("passes known sidecar status %s through unchanged", (status) => {
    expect(normalizeAskArticleRagStatus(status)).toBe(status);
  });

  it.each(["unknown_sidecar", "", "AVAILABLE", null, undefined, true])(
    "coerces unknown sidecar status %p to not_indexed_or_unavailable fallback",
    (value) => {
      expect(normalizeAskArticleRagStatus(value)).toBe(
        ASK_ARTICLE_RAG_STATUS_FALLBACK,
      );
      expect(ASK_ARTICLE_RAG_STATUS_FALLBACK).toBe("not_indexed_or_unavailable");
    },
  );
});

// ---------------------------------------------------------------------------
// mapArtifactPipelineStatus — debug field stripping + enum coercion
// ---------------------------------------------------------------------------

function makePipelineJobRaw() {
  return {
    job_id: "job_1",
    status: "running",
    attempt_count: 1,
    max_attempts: 3,
    failure_class: "TransienceError",
    failure_code: "upstream_timeout",
    rationale_code: "retry_later_policy",
    available_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:01:00Z",
  };
}

function makePipelineStatusRaw(): ReaderArtifactPipelineStatusResponseDto {
  return {
    artifact: {
      artifact_id: "art_1",
      status: "extraction_running",
      artifact_kind: "original_upload",
      storage_provider: "oss",
      bucket: "claread",
      endpoint: "https://oss.example.com",
      object_key: "artifacts/art_1.bin",
      content_type: "application/pdf",
      byte_size: 1024,
      content_sha256: "abc",
      source_filename: "doc.pdf",
      reading_record_id: "rec_1",
      original_input_id: "inp_1",
    },
    record: {
      reading_record_id: "rec_1",
      generation: 1,
      product_state: "processing",
      readiness_state: "submitted",
      active_base_id: null,
      source_type: "pdf_text",
      title: null,
      language: null,
    },
    original_input: {
      original_input_id: "inp_1",
      input_type: "original_upload",
      content_sha256: "abc",
      has_source_text: false,
      extraction_status: "running",
      metadata: {},
    },
    extraction_job: makePipelineJobRaw(),
    materialization_job: null,
    candidate_document: null,
    stable_document: null,
    outcome: "extraction_running",
    next_action: "wait_for_worker",
  };
}

describe("mapArtifactPipelineStatus", () => {
  it("strips failure_class / failure_code / rationale_code from extraction_job", () => {
    const mapped = mapArtifactPipelineStatus(makePipelineStatusRaw());

    expect(mapped.extraction_job).not.toBeNull();
    expect(mapped.extraction_job).not.toHaveProperty("failure_class");
    expect(mapped.extraction_job).not.toHaveProperty("failure_code");
    expect(mapped.extraction_job).not.toHaveProperty("rationale_code");
    expect(mapped.extraction_job).toMatchObject({
      job_id: "job_1",
      status: "running",
      attempt_count: 1,
      max_attempts: 3,
    });
  });

  it("strips debug fields from materialization_job too", () => {
    const raw = makePipelineStatusRaw();
    raw.materialization_job = makePipelineJobRaw();
    raw.materialization_job.job_id = "job_2";
    raw.extraction_job = null;

    const mapped = mapArtifactPipelineStatus(raw);

    expect(mapped.materialization_job).not.toBeNull();
    expect(mapped.materialization_job).not.toHaveProperty("failure_class");
    expect(mapped.materialization_job).not.toHaveProperty("failure_code");
    expect(mapped.materialization_job).not.toHaveProperty("rationale_code");
    expect(mapped.extraction_job).toBeNull();
  });

  it("preserves known outcome / next_action unchanged", () => {
    const mapped = mapArtifactPipelineStatus(makePipelineStatusRaw());

    expect(mapped.outcome).toBe("extraction_running");
    expect(mapped.next_action).toBe("wait_for_worker");
  });

  it("coerces unknown outcome to extraction_failed and unknown next_action to show_error", () => {
    const raw = makePipelineStatusRaw();
    // Bypass TS to simulate schema drift / hostile fake.
    (raw as { outcome: unknown }).outcome = "totally_unknown_outcome";
    (raw as { next_action: unknown }).next_action = "totally_unknown_action";

    const mapped = mapArtifactPipelineStatus(raw);

    expect(mapped.outcome).toBe("extraction_failed");
    expect(mapped.next_action).toBe("show_error");
  });

  it("preserves artifact / record / candidate / stable summary fields", () => {
    const raw = makePipelineStatusRaw();
    raw.candidate_document = {
      candidate_document_id: "cand_1",
      record_generation: 1,
      canonical_text_preview: "preview",
    };
    raw.stable_document = {
      stable_document_id: "sd_1",
      base_id: "base_1",
      record_generation: 1,
      content_sha256: "abc",
      canonical_text_sha256: "def",
    };

    const mapped = mapArtifactPipelineStatus(raw);

    expect(mapped.artifact.artifact_id).toBe("art_1");
    expect(mapped.record?.reading_record_id).toBe("rec_1");
    expect(mapped.candidate_document?.candidate_document_id).toBe("cand_1");
    expect(mapped.stable_document?.stable_document_id).toBe("sd_1");
  });
});

// ---------------------------------------------------------------------------
// mapArticleRagIndexStatus — reason_code stripping + status coercion
// ---------------------------------------------------------------------------

function makeRagStatusRaw(): ReaderArticleRagIndexStatusResponseDto {
  return {
    reading_record_id: "rec_1",
    status: "indexed",
    stable_document_id: "sd_1",
    base_id: "base_1",
    record_generation: 1,
    index_run_id: "run_1",
    index_version: "v1",
    plan_content_sha256: "abc",
    chunk_count: 42,
    reason_code: "debug_internal_reason",
  };
}

describe("mapArticleRagIndexStatus", () => {
  it("strips reason_code from the safe DTO", () => {
    const mapped = mapArticleRagIndexStatus(makeRagStatusRaw());

    expect(mapped).not.toHaveProperty("reason_code");
    expect(mapped.status).toBe("indexed");
    expect(mapped.chunk_count).toBe(42);
  });

  it("coerces unknown status to unavailable", () => {
    const raw = makeRagStatusRaw();
    (raw as { status: unknown }).status = "totally_unknown_status";

    const mapped = mapArticleRagIndexStatus(raw);

    expect(mapped.status).toBe("unavailable");
  });

  it("preserves null / nullable fields untouched", () => {
    const raw = makeRagStatusRaw();
    raw.stable_document_id = null;
    raw.base_id = null;
    raw.record_generation = null;
    raw.index_run_id = null;
    raw.index_version = null;
    raw.plan_content_sha256 = null;
    raw.chunk_count = null;

    const mapped = mapArticleRagIndexStatus(raw);

    expect(mapped.stable_document_id).toBeNull();
    expect(mapped.chunk_count).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// mapArticleRagIndexEnsure — reason_code stripping + status coercion
// ---------------------------------------------------------------------------

function makeRagEnsureRaw(): ReaderArticleRagIndexEnsureResponseDto {
  return {
    reading_record_id: "rec_1",
    status: "enqueued",
    reason_code: "internal_debug",
    idempotent_noop: false,
    stable_document_id: "sd_1",
    base_id: "base_1",
    record_generation: 1,
    index_run_id: "run_1",
    job_id: "job_1",
    index_version: "v1",
    chunker_version: "chunker_v1",
  };
}

describe("mapArticleRagIndexEnsure", () => {
  it("strips reason_code from the safe DTO", () => {
    const mapped = mapArticleRagIndexEnsure(makeRagEnsureRaw());

    expect(mapped).not.toHaveProperty("reason_code");
    expect(mapped.status).toBe("enqueued");
    expect(mapped.idempotent_noop).toBe(false);
  });

  it("coerces unknown status to error", () => {
    const raw = makeRagEnsureRaw();
    (raw as { status: unknown }).status = "totally_unknown_ensure_status";

    const mapped = mapArticleRagIndexEnsure(raw);

    expect(mapped.status).toBe("error");
  });
});

// ---------------------------------------------------------------------------
// mapAskArticleRagSidecar — debug field stripping + status coercion + null safety
// ---------------------------------------------------------------------------

function makeSidecarRaw(): ReaderAskArticleRagSidecarDto {
  return {
    status: "available",
    failure_code: "internal_error",
    retryable: true,
    fallback_allowed: false,
    should_attach: true,
    context_ids: ["ctx_1", "ctx_2"],
    source_pack_hash: "pack_hash_secret",
    query_sha256: "query_hash_secret",
    citations: [
      {
        context_id: "ctx_1",
        chunk_id: "chunk_1",
        citation: {
          reading_record_id: "rec_1",
          stable_document_id: "sd_1",
          base_id: "base_1",
          record_generation: 1,
          block_ids: ["block_1"],
          unit_ids: ["unit_1"],
          anchor_segment_ids: ["anchor_1"],
          canonical_text_start_utf16: 0,
          canonical_text_end_utf16: 10,
        },
      },
    ],
  };
}

describe("mapAskArticleRagSidecar", () => {
  it("returns safe fallback shape when input is null", () => {
    const mapped = mapAskArticleRagSidecar(null);

    expect(mapped).toEqual({
      status: "not_indexed_or_unavailable",
      should_attach: false,
      context_ids: [],
      citations: [],
    });
  });

  it("returns safe fallback shape when input is undefined", () => {
    const mapped = mapAskArticleRagSidecar(undefined);

    expect(mapped.status).toBe("not_indexed_or_unavailable");
    expect(mapped.should_attach).toBe(false);
    expect(mapped.citations).toEqual([]);
  });

  it("strips failure_code / retryable / fallback_allowed / source_pack_hash / query_sha256", () => {
    const mapped = mapAskArticleRagSidecar(makeSidecarRaw());

    expect(mapped).not.toHaveProperty("failure_code");
    expect(mapped).not.toHaveProperty("retryable");
    expect(mapped).not.toHaveProperty("fallback_allowed");
    expect(mapped).not.toHaveProperty("source_pack_hash");
    expect(mapped).not.toHaveProperty("query_sha256");
  });

  it("preserves status / should_attach / context_ids / citations", () => {
    const mapped = mapAskArticleRagSidecar(makeSidecarRaw());

    expect(mapped.status).toBe("available");
    expect(mapped.should_attach).toBe(true);
    expect(mapped.context_ids).toEqual(["ctx_1", "ctx_2"]);
    expect(mapped.citations).toHaveLength(1);
    expect(mapped.citations[0]).toMatchObject({
      context_id: "ctx_1",
      chunk_id: "chunk_1",
      citation: {
        reading_record_id: "rec_1",
        stable_document_id: "sd_1",
        canonical_text_start_utf16: 0,
        canonical_text_end_utf16: 10,
      },
    });
  });

  it("coerces unknown sidecar status to not_indexed_or_unavailable", () => {
    const raw = makeSidecarRaw();
    (raw as { status: unknown }).status = "totally_unknown_sidecar_status";

    const mapped = mapAskArticleRagSidecar(raw);

    expect(mapped.status).toBe("not_indexed_or_unavailable");
  });

  it("coerces non-array context_ids / citations to empty arrays", () => {
    const raw = makeSidecarRaw();
    (raw as { context_ids: unknown }).context_ids = "not_an_array";
    (raw as { citations: unknown }).citations = null;

    const mapped = mapAskArticleRagSidecar(raw);

    expect(mapped.context_ids).toEqual([]);
    expect(mapped.citations).toEqual([]);
  });

  it("coerces non-boolean should_attach to false (strict === true check)", () => {
    // Truthy string "false" must NOT become true.
    const raw1 = makeSidecarRaw();
    (raw1 as { should_attach: unknown }).should_attach = "false";
    expect(mapAskArticleRagSidecar(raw1).should_attach).toBe(false);

    // Truthy string "truthy_string" must NOT become true.
    const raw2 = makeSidecarRaw();
    (raw2 as { should_attach: unknown }).should_attach = "truthy_string";
    expect(mapAskArticleRagSidecar(raw2).should_attach).toBe(false);

    // Number 1 must NOT become true.
    const raw3 = makeSidecarRaw();
    (raw3 as { should_attach: unknown }).should_attach = 1;
    expect(mapAskArticleRagSidecar(raw3).should_attach).toBe(false);

    // Only literal `true` is true.
    const raw4 = makeSidecarRaw();
    (raw4 as { should_attach: unknown }).should_attach = true;
    expect(mapAskArticleRagSidecar(raw4).should_attach).toBe(true);
  });

  it.each([
    "stale_due_to_repair",
    "disabled",
    "composer_rejected",
    "not_indexed_or_unavailable",
    "empty",
  ] as const)(
    "clears citations when status is %s (not available)",
    (status) => {
      const raw = makeSidecarRaw();
      (raw as { status: unknown }).status = status;

      const mapped = mapAskArticleRagSidecar(raw);

      expect(mapped.status).toBe(status);
      expect(mapped.citations).toEqual([]);
    },
  );

  it("clears citations when status is unknown / coerced to fallback", () => {
    const raw = makeSidecarRaw();
    (raw as { status: unknown }).status = "totally_unknown_status_value";

    const mapped = mapAskArticleRagSidecar(raw);

    expect(mapped.status).toBe("not_indexed_or_unavailable");
    expect(mapped.citations).toEqual([]);
  });

  it("preserves citations only when status is available", () => {
    const raw = makeSidecarRaw();
    // status is already "available" in makeSidecarRaw
    expect(mapAskArticleRagSidecar(raw).citations).toHaveLength(1);
  });

  it("does not mutate the input raw DTO", () => {
    const raw = makeSidecarRaw();
    const rawSnapshot = JSON.parse(JSON.stringify(raw));

    mapAskArticleRagSidecar(raw);

    expect(raw).toEqual(rawSnapshot);
  });
});
