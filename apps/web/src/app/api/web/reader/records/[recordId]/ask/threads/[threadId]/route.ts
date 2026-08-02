import { NextResponse } from "next/server";
import { getReaderAskThreadForWeb } from "@/services/bff/reader-ask";

export async function GET(
  _request: Request,
  context: { params: Promise<{ recordId: string; threadId: string }> },
) {
  const { recordId, threadId } = await context.params;
  const result = await getReaderAskThreadForWeb(recordId, threadId);
  return result instanceof Response ? result : NextResponse.json(result);
}
