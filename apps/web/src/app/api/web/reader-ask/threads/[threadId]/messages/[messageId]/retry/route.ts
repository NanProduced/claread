import { retryReaderAskMessageForWeb } from "@/services/bff/reader-ask";

export async function POST(
  _request: Request,
  context: { params: Promise<{ threadId: string; messageId: string }> },
) {
  const { threadId, messageId } = await context.params;
  return retryReaderAskMessageForWeb(threadId, messageId);
}
