import { NextResponse } from "next/server";

import { submitReaderPlainTextFromWeb } from "@/services/bff/reader-plate";

export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as {
    plainText?: unknown;
    title?: unknown;
    language?: unknown;
  };

  const result = await submitReaderPlainTextFromWeb(body);

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
