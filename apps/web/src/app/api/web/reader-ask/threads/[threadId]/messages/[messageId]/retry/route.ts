import { retryReaderAskMessageForWeb } from "@/services/bff/reader-ask";
import type { ReaderAskMessageRetryRequestDto } from "@/types/api/reader-ask";

export async function POST(
  request: Request,
  context: { params: Promise<{ threadId: string; messageId: string }> },
) {
  const { threadId, messageId } = await context.params;
  const body = (await request.json().catch(() => ({}))) as ReaderAskMessageRetryRequestDto;
  return retryReaderAskMessageForWeb(threadId, messageId, body);
}
