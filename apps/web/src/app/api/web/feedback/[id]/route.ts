import { NextResponse } from "next/server";

import { deleteFeedbackFromWeb } from "@/services/bff/feedback";

export async function DELETE(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const result = await deleteFeedbackFromWeb(id);
  if (result.ok) {
    return new NextResponse(null, { status: 204 });
  }
  return NextResponse.json(result, { status: result.status });
}
