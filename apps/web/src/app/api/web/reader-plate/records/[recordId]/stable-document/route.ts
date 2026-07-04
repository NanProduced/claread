import { NextResponse } from "next/server";

import { getReaderStableDocumentFromWeb } from "@/services/bff/reader-plate";

interface StableDocumentRouteContext {
  params: Promise<{ recordId: string }>;
}

export async function GET(
  _request: Request,
  context: StableDocumentRouteContext,
) {
  const { recordId } = await context.params;

  const result = await getReaderStableDocumentFromWeb(recordId);

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}