import { NextResponse } from "next/server";
import { deleteReaderAskSupplementForWeb } from "@/services/bff/reader-ask";

export async function DELETE(
  _request: Request,
  context: { params: Promise<{ supplementId: string }> },
) {
  const { supplementId } = await context.params;
  const result = await deleteReaderAskSupplementForWeb(supplementId);
  return result instanceof Response ? result : NextResponse.json(result);
}
