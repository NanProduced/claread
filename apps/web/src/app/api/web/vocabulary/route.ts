import { NextResponse } from "next/server";

import { addVocabularyFromWeb, getVocabularyLookupMatch } from "@/services/bff/vocabulary";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const dictEntryIdParam = searchParams.get("dict_entry_id");
  const dictEntryId = dictEntryIdParam ? Number(dictEntryIdParam) : null;
  const lemma = searchParams.get("lemma");
  const form = searchParams.get("form");

  const result = await getVocabularyLookupMatch({
    dictEntryId: dictEntryId !== null && Number.isFinite(dictEntryId) ? dictEntryId : null,
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
