import { NextResponse } from "next/server";

import { getCurrentAnalysisTaskFromWeb } from "@/services/bff/analysis";

export async function GET() {
  const result = await getCurrentAnalysisTaskFromWeb();

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
