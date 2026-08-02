import { NextResponse } from "next/server";

import { submitReaderUnifiedInputFromWeb } from "@/services/bff/reader-plate";

interface ReaderPlateInputRequestBody {
  text?: unknown;
  sourceType?: unknown;
  filename?: unknown;
  language?: unknown;
  sourceMetadata?: unknown;
  clientRecordId?: unknown;
  reading_goal?: unknown;
  reading_variant?: unknown;
  readingGoal?: unknown;
  readingVariant?: unknown;
}

export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as ReaderPlateInputRequestBody;

  const result = await submitReaderUnifiedInputFromWeb({
    text: body.text,
    sourceType: body.sourceType,
    filename: body.filename,
    language: body.language,
    sourceMetadata: body.sourceMetadata,
    clientRecordId: body.clientRecordId,
    readingGoal: body.readingGoal ?? body.reading_goal,
    readingVariant: body.readingVariant ?? body.reading_variant,
  });

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
