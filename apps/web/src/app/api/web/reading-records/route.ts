import { NextResponse } from "next/server";

import { getReadingRecordListFromWeb } from "@/services/bff/reading-records";
import type { ReadingRecordProductState } from "@/types/api/reading-records";

export async function GET(request: Request) {
  const url = new URL(request.url);
  const limitParam = url.searchParams.get("limit");
  const queryParam = url.searchParams.get("query");
  const productStateParam = url.searchParams.get("productState");
  const parsedLimit = limitParam ? Number(limitParam) : NaN;
  const productStates = productStateParam
    ? productStateParam
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean)
        .map((value) => value as ReadingRecordProductState)
    : undefined;
  const options = {
    ...(Number.isFinite(parsedLimit) && parsedLimit > 0
      ? { limit: Math.floor(parsedLimit) }
      : {}),
    ...(queryParam?.trim() ? { query: queryParam.trim() } : {}),
    ...(productStates && productStates.length > 0
      ? { productStates }
      : {}),
  };

  const result = await getReadingRecordListFromWeb(options);

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
