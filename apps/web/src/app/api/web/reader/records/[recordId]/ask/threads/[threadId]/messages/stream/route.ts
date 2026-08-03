import { createReaderAskStreamForWeb } from "@/services/bff/reader-ask";
import type { ReaderAskMessageStreamRequestDto } from "@/types/api/reader-ask";

export async function POST(
  request: Request,
  context: { params: Promise<{ recordId: string; threadId: string }> },
) {
  const { recordId, threadId } = await context.params;
  const body = (await request.json()) as ReaderAskMessageStreamRequestDto;
  return createReaderAskStreamForWeb(
    recordId,
    threadId,
    body,
    request.signal,
  );
}
