import { NextResponse } from "next/server";

import { submitReadingRecordPlainTextFromWeb } from "@/services/bff/reader-plate";

export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as {
    plainText?: unknown;
    title?: unknown;
    language?: unknown;
    reading_goal?: unknown;
    reading_variant?: unknown;
    readingGoal?: unknown;
    readingVariant?: unknown;
  };

  const result = await submitReadingRecordPlainTextFromWeb({
    plainText: body.plainText,
    title: body.title,
    language: body.language,
    readingGoal: body.readingGoal ?? body.reading_goal,
    readingVariant: body.readingVariant ?? body.reading_variant,
  });

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
