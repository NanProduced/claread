import { NextResponse } from "next/server";
import {
  createReaderAskThreadForWeb,
  listReaderAskThreadsForWeb,
} from "@/services/bff/reader-ask";
import type { ReaderAskThreadCreateRequestDto } from "@/types/api/reader-ask";

export async function GET(
  _request: Request,
  context: { params: Promise<{ recordId: string }> },
) {
  const { recordId } = await context.params;
  const result = await listReaderAskThreadsForWeb(recordId);
  return result instanceof Response ? result : NextResponse.json(result);
}

export async function POST(
  request: Request,
  context: { params: Promise<{ recordId: string }> },
) {
  const { recordId } = await context.params;
  const body = (await request.json().catch(() => ({}))) as ReaderAskThreadCreateRequestDto;
  const result = await createReaderAskThreadForWeb(recordId, body);
  return result instanceof Response ? result : NextResponse.json(result);
}
