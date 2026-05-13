import "server-only";

import { adaptRecordToReaderRecord, type ReaderRecordVm } from "@/adapters/records.adapter";
import { mockReaderVm } from "@/lib/mock-data";
import {
  getUpstreamRecordByClientId,
  getUpstreamRecordById,
} from "@/services/api/records";
import { getWebSession, type WebSession } from "@/services/bff/session";
import type { RecordResponseDto } from "@/types/api/records";

export type ReaderDataSource =
  | "upstream-render-scene"
  | "upstream-source-text"
  | "mock-fallback";

export interface ReaderBffResult {
  record: ReaderRecordVm;
  dataSource: ReaderDataSource;
  session: WebSession;
  fallbackReason?: string;
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function mockReaderRecord(recordId: string): ReaderRecordVm {
  return {
    id: recordId,
    title: "The Silent Spring of AI Regulation",
    createdAt: "2026-05-12T08:30:00Z",
    sourceText: mockReaderVm.article.sentences.map((sentence) => sentence.text).join(" "),
    readingGoal: mockReaderVm.request.readingGoal,
    readingVariant: mockReaderVm.request.readingVariant,
    analysisStatus: "mock",
    reader: mockReaderVm,
  };
}

function resolveRecordId(recordId: string): string {
  if (recordId === "demo-record" && process.env.CLAREAD_WEB_DEMO_RECORD_ID) {
    return process.env.CLAREAD_WEB_DEMO_RECORD_ID;
  }

  return recordId;
}

function hasRenderableScene(record: RecordResponseDto): boolean {
  const scene = record.render_scene_json;
  return Boolean(
    scene &&
      typeof scene === "object" &&
      "article" in scene &&
      (Array.isArray((scene as { translations?: unknown }).translations) ||
        Array.isArray((scene as { inline_marks?: unknown }).inline_marks)),
  );
}

export async function getReaderRecord(recordId: string): Promise<ReaderBffResult> {
  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return {
      record: mockReaderRecord(recordId),
      dataSource: "mock-fallback",
      session,
      fallbackReason:
        session.kind === "mock_phone"
          ? "Local mock phone session is active, but no FastAPI debug session token is configured."
          : "No Web session cookie or dev debug session is configured.",
    };
  }

  const upstreamRecordId = resolveRecordId(recordId);
  const upstreamResult = UUID_RE.test(upstreamRecordId)
    ? await getUpstreamRecordById(upstreamRecordId, session.sessionToken)
    : await getUpstreamRecordByClientId(upstreamRecordId, session.sessionToken);

  if (!upstreamResult.ok) {
    return {
      record: mockReaderRecord(recordId),
      dataSource: "mock-fallback",
      session,
      fallbackReason: `FastAPI records/detail failed (${upstreamResult.status}): ${upstreamResult.message}`,
    };
  }

  const record = adaptRecordToReaderRecord(upstreamResult.data);

  return {
    record,
    dataSource: hasRenderableScene(upstreamResult.data)
      ? "upstream-render-scene"
      : "upstream-source-text",
    session,
    fallbackReason: hasRenderableScene(upstreamResult.data)
      ? undefined
      : "FastAPI record exists but render_scene_json is missing or incomplete; rendering source_text only.",
  };
}
