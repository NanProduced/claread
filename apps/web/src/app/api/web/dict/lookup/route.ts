import { NextResponse } from "next/server";

import { lookupDictForWeb, parseLookupSearchParams } from "@/services/bff/dict";

export async function GET(request: Request) {
  const params = parseLookupSearchParams(new URL(request.url).searchParams);

  if ("kind" in params && params.kind === "error") {
    return NextResponse.json(params, { status: params.status });
  }

  const result = await lookupDictForWeb(params);

  return NextResponse.json(result, {
    status: result.kind === "error" ? result.status : 200,
  });
}
