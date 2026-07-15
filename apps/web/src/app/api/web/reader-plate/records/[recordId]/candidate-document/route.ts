import { NextResponse } from "next/server";

import { getReaderCandidateDocumentFromWeb } from "@/services/bff/reader-plate";

interface CandidateDocumentRouteContext {
  params: Promise<{ recordId: string }>;
}

export async function GET(
  _request: Request,
  context: CandidateDocumentRouteContext,
) {
  const { recordId } = await context.params;

  const result = await getReaderCandidateDocumentFromWeb(recordId);

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}