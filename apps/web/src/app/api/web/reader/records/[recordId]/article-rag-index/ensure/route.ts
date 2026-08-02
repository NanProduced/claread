import { NextResponse } from "next/server";

import { ensureReaderArticleRagIndexFromWeb } from "@/services/bff/reader-plate";

interface ArticleRagIndexEnsureRouteContext {
  params: Promise<{ recordId: string }>;
}

interface ArticleRagIndexEnsureRequestBody {
  expectedGeneration?: unknown;
  indexVersion?: unknown;
}

export async function POST(
  request: Request,
  context: ArticleRagIndexEnsureRouteContext,
) {
  const { recordId } = await context.params;
  const body = (await request.json().catch(() => ({}))) as ArticleRagIndexEnsureRequestBody;

  const result = await ensureReaderArticleRagIndexFromWeb(recordId, {
    expectedGeneration: body.expectedGeneration,
    indexVersion: body.indexVersion,
  });

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
