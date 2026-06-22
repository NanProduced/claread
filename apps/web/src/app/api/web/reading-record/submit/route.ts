import { NextResponse } from "next/server";

import { submitReadingRecordPlainTextFromWeb } from "@/services/bff/reader-plate";

export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as {
    plainText?: unknown;
    title?: unknown;
    language?: unknown;
  };

  const result = await submitReadingRecordPlainTextFromWeb(body);

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
