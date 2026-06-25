import { NextResponse } from "next/server";
import { resetReaderAskThreadForWeb } from "@/services/bff/reader-ask";

export async function POST(
  request: Request,
  context: { params: Promise<{ threadId: string }> },
) {
  const { threadId } = await context.params;
  const { searchParams } = new URL(request.url);
  const recordId = searchParams.get("recordId") ?? searchParams.get("record_id");
  const recordScope = searchParams.get("record_scope");
  const result = await resetReaderAskThreadForWeb(
    threadId,
    recordId,
    recordScope === "reading_record" ? "reading_record" : "analysis",
  );
  return result instanceof Response ? result : NextResponse.json(result);
}
