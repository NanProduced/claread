import { NextResponse } from "next/server";

import {
  getReaderConfirmedSourceFromWeb,
  updateReaderConfirmedSourceFromWeb,
} from "@/services/bff/reader-plate";

interface ConfirmedSourceRouteContext {
  params: Promise<{ recordId: string }>;
}

interface ConfirmedSourceUpdateBody {
  expected_revision?: unknown;
  markdown_text?: unknown;
  edit_source?: unknown;
}

export async function GET(
  _request: Request,
  context: ConfirmedSourceRouteContext,
) {
  const { recordId } = await context.params;

  const result = await getReaderConfirmedSourceFromWeb(recordId);

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}

export async function PUT(
  request: Request,
  context: ConfirmedSourceRouteContext,
) {
  const { recordId } = await context.params;
  const body = (await request.json().catch(() => ({}))) as ConfirmedSourceUpdateBody;

  const result = await updateReaderConfirmedSourceFromWeb(recordId, {
    expectedRevision: body.expected_revision,
    markdownText: body.markdown_text,
    editSource: body.edit_source,
  });

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
