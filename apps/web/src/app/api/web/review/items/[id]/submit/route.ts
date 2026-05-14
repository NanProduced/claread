import { NextResponse } from "next/server";

import { submitReviewItem } from "@/services/bff/review";
import type { ReviewSubmitResultDto } from "@/types/api/review";

type RouteContext = {
  params: Promise<{ id: string }>;
};

function isReviewResult(value: unknown): value is ReviewSubmitResultDto {
  return value === "known" || value === "unfamiliar";
}

export async function POST(request: Request, context: RouteContext) {
  const { id } = await context.params;
  const body = (await request.json().catch(() => ({}))) as { result?: unknown };

  if (!isReviewResult(body.result)) {
    return NextResponse.json(
      {
        ok: false,
        status: 400,
        code: "bad_request",
        message: "Invalid review result. Use known or unfamiliar.",
      },
      { status: 400 },
    );
  }

  const result = await submitReviewItem(id, body.result);

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
