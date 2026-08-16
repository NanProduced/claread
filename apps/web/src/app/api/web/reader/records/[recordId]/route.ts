import { NextResponse } from "next/server";

import { deleteReaderRecordFromWeb } from "@/services/bff/reading-records";

type RecordRouteContext = {
  params: Promise<{ recordId: string }>;
};

export async function DELETE(_request: Request, context: RecordRouteContext) {
  const { recordId } = await context.params;
  const result = await deleteReaderRecordFromWeb(recordId);

  if (!result.ok) {
    return NextResponse.json(
      {
        ok: false,
        status: result.status,
        code: result.code,
        message: result.message,
      },
      { status: result.status },
    );
  }

  return NextResponse.json({ ok: true, ...result.data }, { status: 200 });
}
