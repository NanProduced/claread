import { NextResponse } from "next/server";

import { lookupDictAIForWeb, parseDictAIRequestBody } from "@/services/bff/dict";
import type { WebDictAIRequest } from "@/types/api/dict-ai";

export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as unknown;
  const parsed = parseDictAIRequestBody(body);

  if ("kind" in parsed && parsed.kind === "error") {
    return NextResponse.json(parsed, { status: parsed.status });
  }

  const result = await lookupDictAIForWeb(parsed as WebDictAIRequest);

  return NextResponse.json(result, {
    status: result.kind === "error" ? result.status : 200,
  });
}
