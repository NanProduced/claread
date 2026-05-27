import { NextResponse } from "next/server";
import { listReaderAskContextRecordsForWeb } from "@/services/bff/reader-ask";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const query = searchParams.get("query") ?? "";
  const excludeRecordId = searchParams.get("excludeRecordId") ?? searchParams.get("exclude_record_id");
  if (!query.trim()) {
    return NextResponse.json({ items: [] });
  }

  const result = await listReaderAskContextRecordsForWeb(query, excludeRecordId);
  return result instanceof Response ? result : NextResponse.json(result);
}
