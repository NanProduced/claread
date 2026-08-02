import { retryReaderAskMessageForWeb } from "@/services/bff/reader-ask";
import type { ReaderAskMessageRetryRequestDto } from "@/types/api/reader-ask";

export async function POST(
  request: Request,
  context: { params: Promise<{ recordId: string; threadId: string; messageId: string }> },
) {
  const { recordId, threadId, messageId } = await context.params;
  const body = (await request.json().catch(() => ({}))) as ReaderAskMessageRetryRequestDto;
  return retryReaderAskMessageForWeb(
    recordId,
    threadId,
    messageId,
    body,
    request.signal,
  );
}
