import { NextResponse } from "next/server";
import { resetReaderAskThreadForWeb } from "@/services/bff/reader-ask";

export async function POST(
  _request: Request,
  context: { params: Promise<{ threadId: string }> },
) {
  const { threadId } = await context.params;
  const result = await resetReaderAskThreadForWeb(threadId);
  return result instanceof Response ? result : NextResponse.json(result);
}
