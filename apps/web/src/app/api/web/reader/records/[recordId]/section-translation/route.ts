import { NextResponse } from "next/server";

import { submitReaderSectionTranslationFromWeb } from "@/services/bff/reader-plate";

interface SectionTranslationRouteContext {
  params: Promise<{ recordId: string }>;
}

interface SectionTranslationRequestBody {
  startUnitId?: unknown;
  endUnitId?: unknown;
  startAnchorSegmentId?: unknown;
  endAnchorSegmentId?: unknown;
  nodeId?: unknown;
  outlineRevision?: unknown;
}

export async function POST(
  request: Request,
  context: SectionTranslationRouteContext,
) {
  const { recordId } = await context.params;
  const body = (await request.json().catch(() => ({}))) as SectionTranslationRequestBody;

  const result = await submitReaderSectionTranslationFromWeb(recordId, {
    startUnitId: body.startUnitId,
    endUnitId: body.endUnitId,
    startAnchorSegmentId: body.startAnchorSegmentId,
    endAnchorSegmentId: body.endAnchorSegmentId,
    nodeId: body.nodeId,
    outlineRevision: body.outlineRevision,
  });

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
