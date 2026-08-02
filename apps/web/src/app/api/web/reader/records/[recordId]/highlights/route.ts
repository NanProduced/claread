import { NextResponse } from "next/server";

import { createReadingRecordHighlight } from "@/services/bff/reading-record-user-assets";

export async function POST(
  request: Request,
  context: { params: Promise<{ recordId: string }> },
) {
  const { recordId } = await context.params;
  const payload = (await request.json().catch(() => null)) as unknown;
  const result = await createReadingRecordHighlight(payload, recordId);

  return NextResponse.json(result, {
    status: result.ok ? 201 : result.httpStatus,
  });
}
