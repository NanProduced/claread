import { NextResponse } from "next/server";

import { favoriteRecord, getRecordFavoriteState } from "@/services/bff/favorites";

function readRecordId(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

export async function GET(request: Request) {
  const recordId = readRecordId(new URL(request.url).searchParams.get("recordId"));
  const result = await getRecordFavoriteState(recordId);

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}

export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as { recordId?: unknown };
  const recordId = readRecordId(body.recordId);
  const result = await favoriteRecord(recordId);

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
