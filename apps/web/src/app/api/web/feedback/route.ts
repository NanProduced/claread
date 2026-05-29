import { NextResponse } from "next/server";

import { listFeedbackFromWeb, submitFeedbackFromWeb } from "@/services/bff/feedback";
import type { WebFeedbackSubmitInput } from "@/services/bff/feedback";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const cursor = searchParams.get("cursor") || undefined;
  const limit = searchParams.get("limit") ? parseInt(searchParams.get("limit")!, 10) : undefined;
  const feedbackScope = searchParams.get("feedback_scope") || undefined;
  const clientPlatform = searchParams.get("client_platform") || undefined;
  const clientSurface = searchParams.get("client_surface") || undefined;
  const status = searchParams.get("status") || undefined;
  const result = await listFeedbackFromWeb(
    cursor,
    limit,
    feedbackScope,
    clientPlatform,
    clientSurface,
    status,
  );
  return NextResponse.json(result, { status: result.ok ? 200 : result.status });
}

export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as WebFeedbackSubmitInput;
  const result = await submitFeedbackFromWeb(body);

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
