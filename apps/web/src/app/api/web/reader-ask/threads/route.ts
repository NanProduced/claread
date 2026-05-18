import { NextResponse } from "next/server";
import {
  createReaderAskThreadForWeb,
  listReaderAskThreadsForWeb,
} from "@/services/bff/reader-ask";
import type { ReaderAskThreadCreateRequestDto } from "@/types/api/reader-ask";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const recordId = searchParams.get("recordId") ?? searchParams.get("record_id") ?? "";
  if (!recordId.trim()) {
    return NextResponse.json({ message: "Missing recordId." }, { status: 400 });
  }

  const result = await listReaderAskThreadsForWeb(recordId);
  return result instanceof Response ? result : NextResponse.json(result);
}

export async function POST(request: Request) {
  const body = (await request.json()) as ReaderAskThreadCreateRequestDto;
  const result = await createReaderAskThreadForWeb(body);
  return result instanceof Response ? result : NextResponse.json(result);
}
