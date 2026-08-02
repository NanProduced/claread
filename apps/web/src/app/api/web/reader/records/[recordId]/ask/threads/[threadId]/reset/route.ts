import { NextResponse } from "next/server";
import { resetReaderAskThreadForWeb } from "@/services/bff/reader-ask";

export async function POST(
  _request: Request,
  context: { params: Promise<{ recordId: string; threadId: string }> },
) {
  const { recordId, threadId } = await context.params;
  const result = await resetReaderAskThreadForWeb(recordId, threadId);
  return result instanceof Response ? result : NextResponse.json(result);
}
