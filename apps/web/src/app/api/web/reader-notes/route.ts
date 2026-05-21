import { NextResponse } from "next/server";

import {
  createWebReaderNote,
  getReaderNotes,
} from "@/services/bff/reader-notes";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const recordId = searchParams.get("recordId");

  if (!recordId) {
    return NextResponse.json(
      { status: "invalid_request", items: [], message: "recordId is required." },
      { status: 400 },
    );
  }

  const result = await getReaderNotes(recordId);
  const status =
    result.status === "ready"
      ? 200
      : result.status === "upstream_unavailable"
        ? 503
        : result.status === "unauthenticated" || result.status === "mock_session"
          ? 401
          : 502;

  return NextResponse.json(result, { status });
}

export async function POST(request: Request) {
  const payload = (await request.json()) as unknown;
  const result = await createWebReaderNote(payload);

  if (!result.ok) {
    return NextResponse.json(result, { status: result.httpStatus });
  }

  return NextResponse.json(result, { status: 201 });
}
