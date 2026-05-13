import { NextResponse } from "next/server";

import { submitAnalysisFromWeb } from "@/services/bff/analysis";

export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as {
    text?: unknown;
    readingGoal?: unknown;
  };
  const result = await submitAnalysisFromWeb(body);

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
