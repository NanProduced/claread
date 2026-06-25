import { NextResponse } from "next/server";
import { deleteReaderAskSupplementForWeb } from "@/services/bff/reader-ask";

export async function DELETE(
  request: Request,
  context: { params: Promise<{ supplementId: string }> },
) {
  const { supplementId } = await context.params;
  const { searchParams } = new URL(request.url);
  const recordId = searchParams.get("recordId") ?? searchParams.get("record_id");
  const recordScope = searchParams.get("record_scope");
  const result = await deleteReaderAskSupplementForWeb(
    supplementId,
    recordId,
    recordScope === "reading_record" ? "reading_record" : "analysis",
  );
  return result instanceof Response ? result : NextResponse.json(result);
}
