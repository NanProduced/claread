import { NextResponse } from "next/server";

import { addVocabularyFromWeb, getVocabularyLookupMatch } from "@/services/bff/vocabulary";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const dictEntryIdParam = searchParams.get("dict_entry_id");
  const parsedDictEntryId = dictEntryIdParam ? Number(dictEntryIdParam) : null;
  // Use safe-integer semantics consistent with the BFF layer.
  // Number.isFinite allows non-integer values like 123.45 which the BFF
  // would silently treat as null.
  const dictEntryId =
    parsedDictEntryId !== null && Number.isSafeInteger(parsedDictEntryId) && parsedDictEntryId > 0
      ? parsedDictEntryId
      : null;
  const lemma = searchParams.get("lemma");
  const form = searchParams.get("form");

  const result = await getVocabularyLookupMatch({
    dictEntryId,
    lemma,
    form,
  });

  return NextResponse.json(result, {
    status: result.status,
  });
}

export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as unknown;
  const result = await addVocabularyFromWeb(body);

  return NextResponse.json(result, {
    status: result.status,
  });
}
