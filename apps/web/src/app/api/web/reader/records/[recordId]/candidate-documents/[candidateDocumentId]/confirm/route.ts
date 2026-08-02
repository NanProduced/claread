import { NextResponse } from "next/server";

import { confirmReaderCandidateDocumentFromWeb } from "@/services/bff/reader-plate";

interface ConfirmCandidateRequestBody {
  language?: unknown;
}

interface ConfirmCandidateRouteContext {
  params: Promise<{ recordId: string; candidateDocumentId: string }>;
}

export async function POST(
  request: Request,
  context: ConfirmCandidateRouteContext,
) {
  const { recordId, candidateDocumentId } = await context.params;
  const body = (await request.json().catch(() => ({}))) as ConfirmCandidateRequestBody;

  const result = await confirmReaderCandidateDocumentFromWeb(
    recordId,
    candidateDocumentId,
    {
      language: body.language,
    },
  );

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
