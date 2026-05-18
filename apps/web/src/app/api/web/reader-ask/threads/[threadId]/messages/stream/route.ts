import { createReaderAskStreamForWeb } from "@/services/bff/reader-ask";
import type { ReaderAskMessageStreamRequestDto } from "@/types/api/reader-ask";

export async function POST(
  request: Request,
  context: { params: Promise<{ threadId: string }> },
) {
  const { threadId } = await context.params;
  const body = (await request.json()) as ReaderAskMessageStreamRequestDto;
  return createReaderAskStreamForWeb(threadId, body);
}
