import { NextResponse } from "next/server";

import {
  deleteReaderImageSourceOverrideFromWeb,
  upsertReaderImageSourceOverrideFromWeb,
} from "@/services/bff/reader-plate";

type ImageOverrideRouteContext = {
  params: Promise<{ recordId: string }>;
};

export async function PUT(request: Request, context: ImageOverrideRouteContext) {
  const { recordId } = await context.params;
  const body = (await request.json().catch(() => null)) as Record<string, unknown> | null;
  const result = await upsertReaderImageSourceOverrideFromWeb({
    recordId,
    stableDocumentId: body?.stable_document_id,
    blockId: body?.block_id,
    inlineOrdinal: body?.inline_ordinal,
    url: body?.url,
  });

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}

export async function DELETE(request: Request, context: ImageOverrideRouteContext) {
  const { recordId } = await context.params;
  const url = new URL(request.url);
  const stableDocumentId = url.searchParams.get("stable_document_id");
  const blockId = url.searchParams.get("block_id");
  const inlineOrdinalRaw = url.searchParams.get("inline_ordinal");

  const result = await deleteReaderImageSourceOverrideFromWeb({
    recordId,
    stableDocumentId: stableDocumentId ?? undefined,
    blockId: blockId ?? undefined,
    inlineOrdinal: inlineOrdinalRaw,
  });

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
