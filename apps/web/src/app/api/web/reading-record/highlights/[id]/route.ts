import { NextResponse } from "next/server";

import {
  deleteReadingRecordHighlight,
  updateReadingRecordHighlight,
} from "@/services/bff/reading-record-user-assets";

export async function DELETE(
  _request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const { id } = await context.params;
  const result = await deleteReadingRecordHighlight(id);

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.httpStatus,
  });
}

export async function PATCH(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const { id } = await context.params;
  const payload = (await request.json().catch(() => null)) as unknown;
  const result = await updateReadingRecordHighlight(id, payload);

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.httpStatus,
  });
}
