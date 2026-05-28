import { NextResponse } from "next/server";

import { getCreditLedger } from "@/services/bff/credit-ledger";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const cursor = searchParams.get("cursor") || undefined;
  const limitParam = searchParams.get("limit");
  const limit = limitParam ? Number(limitParam) : undefined;

  const result = await getCreditLedger({
    cursor,
    limit: limit !== undefined && Number.isSafeInteger(limit) && limit > 0 ? limit : undefined,
  });

  if (result.status === "ready") {
    return NextResponse.json(result.data, { status: 200 });
  }

  return NextResponse.json(
    { status: result.status, message: result.message },
    { status: result.httpStatus },
  );
}
