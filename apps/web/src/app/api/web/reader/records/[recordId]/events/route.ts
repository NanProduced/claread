import { NextResponse } from "next/server";

import { pollReaderEventsFromWeb } from "@/services/bff/reader-plate";

type EventsRouteContext = {
  params: Promise<{ recordId: string }>;
};

export async function GET(request: Request, context: EventsRouteContext) {
  const { recordId } = await context.params;
  const url = new URL(request.url);
  const afterSequenceParam = url.searchParams.get("after_sequence");
  const limitParam = url.searchParams.get("limit");

  const afterSequence =
    afterSequenceParam !== null && /^\d+$/.test(afterSequenceParam)
      ? Number(afterSequenceParam)
      : undefined;
  const limit =
    limitParam !== null && /^\d+$/.test(limitParam)
      ? Number(limitParam)
      : undefined;

  const result = await pollReaderEventsFromWeb(recordId, {
    afterSequence,
    limit,
  });

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
