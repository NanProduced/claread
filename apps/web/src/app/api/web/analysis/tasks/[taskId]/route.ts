import { NextResponse } from "next/server";

import { getAnalysisTaskStatusFromWeb } from "@/services/bff/analysis";

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ taskId: string }> },
) {
  const { taskId } = await params;
  const result = await getAnalysisTaskStatusFromWeb(taskId);

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
