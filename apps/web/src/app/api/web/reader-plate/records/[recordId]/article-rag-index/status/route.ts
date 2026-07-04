import { NextResponse } from "next/server";

import { getReaderArticleRagIndexStatusFromWeb } from "@/services/bff/reader-plate";

interface ArticleRagIndexStatusRouteContext {
  params: Promise<{ recordId: string }>;
}

export async function GET(
  _request: Request,
  context: ArticleRagIndexStatusRouteContext,
) {
  const { recordId } = await context.params;

  const result = await getReaderArticleRagIndexStatusFromWeb(recordId);

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}