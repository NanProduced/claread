import { NextResponse } from "next/server";

import { getReaderPlateSnapshotFromWeb } from "@/services/bff/reader-plate";

type SnapshotRouteContext = {
  params: Promise<{ recordId: string }>;
};

export async function GET(_request: Request, context: SnapshotRouteContext) {
  const { recordId } = await context.params;
  const result = await getReaderPlateSnapshotFromWeb(recordId);

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
