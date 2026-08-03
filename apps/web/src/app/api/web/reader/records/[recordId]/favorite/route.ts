import { NextResponse } from "next/server";

import {
  favoriteRecord,
  getRecordFavoriteState,
  unfavoriteRecord,
} from "@/services/bff/favorites";

type RouteContext = {
  params: Promise<{ recordId: string }>;
};

function response(result: Awaited<ReturnType<typeof getRecordFavoriteState>>) {
  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}

export async function GET(_request: Request, context: RouteContext) {
  const { recordId } = await context.params;
  return response(await getRecordFavoriteState(recordId));
}

export async function POST(_request: Request, context: RouteContext) {
  const { recordId } = await context.params;
  return response(await favoriteRecord(recordId));
}

export async function DELETE(_request: Request, context: RouteContext) {
  const { recordId } = await context.params;
  return response(await unfavoriteRecord(recordId));
}
