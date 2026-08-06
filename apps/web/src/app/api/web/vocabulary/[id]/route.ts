import { NextResponse } from "next/server";

import { deleteVocabularyFromWeb, updateVocabularyFromWeb } from "@/services/bff/vocabulary";
import type { VocabularyMasteryStatusDto } from "@/types/api/vocabulary";

type RouteContext = {
  params: Promise<{ id: string }>;
};

export async function PATCH(request: Request, context: RouteContext) {
  const { id } = await context.params;
  const body = (await request.json().catch(() => ({}))) as {
    mastery_status?: VocabularyMasteryStatusDto;
  };
  const result = await updateVocabularyFromWeb(id, body);

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}

export async function DELETE(_request: Request, context: RouteContext) {
  const { id } = await context.params;
  const result = await deleteVocabularyFromWeb(id);

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
