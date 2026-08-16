import { NextResponse } from "next/server";

import { submitReaderAnalysisSectionRequestFromWeb } from "@/services/bff/reader-plate";

interface AnalysisSectionRequestRouteContext {
  params: Promise<{ recordId: string }>;
}

interface AnalysisSectionRequestBody {
  scope?: unknown;
  sectionId?: unknown;
}

export async function POST(
  request: Request,
  context: AnalysisSectionRequestRouteContext,
) {
  const { recordId } = await context.params;
  const parsedBody: unknown = await request.json().catch(() => null);
  const body: AnalysisSectionRequestBody =
    parsedBody !== null &&
    typeof parsedBody === "object" &&
    !Array.isArray(parsedBody)
      ? parsedBody
      : {};

  const result = await submitReaderAnalysisSectionRequestFromWeb(recordId, {
    scope: body.scope,
    sectionId: body.sectionId,
  });

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
