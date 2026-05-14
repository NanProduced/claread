import { NextResponse } from "next/server";

import { getReaderRecord } from "@/services/bff/reader";

type ReaderRouteContext = {
  params: Promise<{ recordId: string }>;
};

export async function GET(_request: Request, context: ReaderRouteContext) {
  const { recordId } = await context.params;
  const result = await getReaderRecord(recordId);

  if (!result.ok) {
    return NextResponse.json(result, { status: result.status });
  }

  return NextResponse.json(result);
}
