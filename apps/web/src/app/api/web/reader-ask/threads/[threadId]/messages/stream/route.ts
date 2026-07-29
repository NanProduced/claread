import { createReaderAskStreamForWeb } from "@/services/bff/reader-ask";
import type { ReaderAskMessageStreamRequestDto } from "@/types/api/reader-ask";

export async function POST(
  request: Request,
  context: { params: Promise<{ threadId: string }> },
) {
  const { threadId } = await context.params;
  const { searchParams } = new URL(request.url);
  const recordId = searchParams.get("recordId") ?? searchParams.get("record_id");
  const recordScope = searchParams.get("record_scope");
  const body = (await request.json()) as ReaderAskMessageStreamRequestDto;
  // ASK-TURN-LIFECYCLE R1: forward the browser's AbortSignal to the
  // upstream fetch so a user stop / network abort / page navigation
  // cancels the upstream SSE connection. This triggers the FastAPI
  // generator's ``finally`` block which reconciles any still-streaming
  // turn_run / message row to ``cancelled``.
  return createReaderAskStreamForWeb(
    threadId,
    body,
    recordId,
    recordScope === "reading_record" ? "reading_record" : "analysis",
    request.signal,
  );
}
