import { NextResponse } from "next/server";

import { addVocabularyFromWeb } from "@/services/bff/vocabulary";

export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as unknown;
  const result = await addVocabularyFromWeb(body);

  return NextResponse.json(result, {
    status: result.status,
  });
}
