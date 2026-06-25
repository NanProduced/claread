import { NextResponse } from "next/server";
import {
  createReaderAskThreadForWeb,
  listReaderAskThreadsForWeb,
} from "@/services/bff/reader-ask";
import type { ReaderAskThreadCreateRequestDto } from "@/types/api/reader-ask";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const recordId = searchParams.get("recordId") ?? searchParams.get("record_id") ?? "";
  const recordScope = searchParams.get("record_scope") ?? undefined;
  if (!recordId.trim()) {
    return NextResponse.json({ message: "Missing recordId." }, { status: 400 });
  }

  const result = await listReaderAskThreadsForWeb(recordId, recordScope === "reading_record" ? "reading_record" : "analysis");
  return result instanceof Response ? result : NextResponse.json(result);
}

export async function POST(request: Request) {
  const body = (await request.json()) as ReaderAskThreadCreateRequestDto & {
    record_scope?: "analysis" | "reading_record" | null;
  };
  const result = await createReaderAskThreadForWeb(body);
  return result instanceof Response ? result : NextResponse.json(result);
}
