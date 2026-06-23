import { NextResponse } from "next/server";

import { getReadingRecordListFromWeb } from "@/services/bff/reading-records";

export async function GET(request: Request) {
  const url = new URL(request.url);
  const limitParam = url.searchParams.get("limit");
  const parsedLimit = limitParam ? Number(limitParam) : NaN;
  const options =
    Number.isFinite(parsedLimit) && parsedLimit > 0
      ? { limit: Math.floor(parsedLimit) }
      : {};

  const result = await getReadingRecordListFromWeb(options);

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
