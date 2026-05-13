import { NextResponse } from "next/server";

import { lookupDictEntryForWeb, parseEntrySearchParams } from "@/services/bff/dict";

export async function GET(request: Request) {
  const entryId = parseEntrySearchParams(new URL(request.url).searchParams);

  if (typeof entryId !== "number") {
    return NextResponse.json(entryId, { status: entryId.status });
  }

  const result = await lookupDictEntryForWeb(entryId);

  return NextResponse.json(result, {
    status: result.kind === "error" ? result.status : 200,
  });
}
