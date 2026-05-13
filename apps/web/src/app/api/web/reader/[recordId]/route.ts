import { NextResponse } from "next/server";

import { getReaderRecord } from "@/services/bff/reader";

type ReaderRouteContext = {
  params: Promise<{ recordId: string }>;
};

export async function GET(_request: Request, context: ReaderRouteContext) {
  const { recordId } = await context.params;
  const result = await getReaderRecord(recordId);

  return NextResponse.json({
    record: result.record,
    dataSource: result.dataSource,
    session: {
      mode: result.session.kind,
      source: result.session.source,
    },
    fallbackReason: result.fallbackReason,
  });
}
