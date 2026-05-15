import { NextResponse } from "next/server";

import {
  favoriteTarget,
  getFavoriteTargetState,
  unfavoriteTarget,
} from "@/services/bff/favorites";

function readString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function readRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const result = await getFavoriteTargetState(
    readString(searchParams.get("targetType")),
    readString(searchParams.get("targetKey")),
  );

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}

export async function POST(request: Request) {
  const body = readRecord(await request.json().catch(() => ({})));
  const result = await favoriteTarget({
    recordId: readString(body.recordId) || null,
    targetType: readString(body.targetType),
    targetKey: readString(body.targetKey),
    payloadJson: readRecord(body.payloadJson),
    note: readString(body.note) || null,
  });

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}

export async function DELETE(request: Request) {
  const { searchParams } = new URL(request.url);
  const result = await unfavoriteTarget(
    readString(searchParams.get("targetType")),
    readString(searchParams.get("targetKey")),
  );

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
