import { NextResponse } from "next/server";
import { getReaderAskThreadForWeb } from "@/services/bff/reader-ask";

export async function GET(
  _request: Request,
  context: { params: Promise<{ threadId: string }> },
) {
  const { threadId } = await context.params;
  const result = await getReaderAskThreadForWeb(threadId);
  return result instanceof Response ? result : NextResponse.json(result);
}
