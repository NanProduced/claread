import { NextResponse } from "next/server";
import { confirmReaderAskActionForWeb } from "@/services/bff/reader-ask";
import type { ReaderAskActionConfirmRequestDto } from "@/types/api/reader-ask";

export async function POST(
  request: Request,
  context: { params: Promise<{ threadId: string; actionId: string }> },
) {
  const { threadId, actionId } = await context.params;
  const body = (await request.json()) as ReaderAskActionConfirmRequestDto;
  const result = await confirmReaderAskActionForWeb(threadId, actionId, body);
  return result instanceof Response ? result : NextResponse.json(result);
}
