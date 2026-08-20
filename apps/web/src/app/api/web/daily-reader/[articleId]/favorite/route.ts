import { NextResponse } from "next/server";

import {
  favoriteDailyReaderArticle,
  getDailyReaderArticleFavoriteState,
  unfavoriteDailyReaderArticle,
} from "@/services/bff/favorites";

type RouteContext = {
  params: Promise<{ articleId: string }>;
};

function response(result: Awaited<ReturnType<typeof getDailyReaderArticleFavoriteState>>) {
  return NextResponse.json(result, { status: result.ok ? 200 : result.status });
}

export async function GET(_request: Request, context: RouteContext) {
  const { articleId } = await context.params;
  return response(await getDailyReaderArticleFavoriteState(articleId));
}

export async function POST(_request: Request, context: RouteContext) {
  const { articleId } = await context.params;
  return response(await favoriteDailyReaderArticle(articleId));
}

export async function DELETE(_request: Request, context: RouteContext) {
  const { articleId } = await context.params;
  return response(await unfavoriteDailyReaderArticle(articleId));
}
