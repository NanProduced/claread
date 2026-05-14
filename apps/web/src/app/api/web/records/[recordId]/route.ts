import { NextResponse } from "next/server";

import { deleteRecordFromWeb } from "@/services/bff/records";

type RouteContext = {
  params: Promise<{ recordId: string }>;
};

export async function DELETE(_request: Request, context: RouteContext) {
  const { recordId } = await context.params;
  const result = await deleteRecordFromWeb(recordId);

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
