import { NextResponse } from "next/server";
import { confirmReaderAskActionForWeb } from "@/services/bff/reader-ask";
import type { ReaderAskActionConfirmRequestDto } from "@/types/api/reader-ask";

export async function POST(
  request: Request,
  context: { params: Promise<{ threadId: string; actionId: string }> },
) {
  const { threadId, actionId } = await context.params;
  const { searchParams } = new URL(request.url);
  const recordId = searchParams.get("recordId") ?? searchParams.get("record_id");
  const recordScope = searchParams.get("record_scope");
  const body = (await request.json()) as ReaderAskActionConfirmRequestDto;
  const result = await confirmReaderAskActionForWeb(
    threadId,
    actionId,
    body,
    recordId,
    recordScope === "reading_record" ? "reading_record" : "analysis",
  );
  return result instanceof Response ? result : NextResponse.json(result);
}
