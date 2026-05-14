import { NextResponse } from "next/server";

import { submitFeedbackFromWeb } from "@/services/bff/feedback";
import type { WebFeedbackSubmitInput } from "@/services/bff/feedback";

export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as WebFeedbackSubmitInput;
  const result = await submitFeedbackFromWeb(body);

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
