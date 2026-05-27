import { NextResponse } from "next/server";

import {
  deleteWebReaderNote,
  updateWebReaderNote,
} from "@/services/bff/reader-notes";

interface RouteContext {
  params: Promise<{ noteId: string }>;
}

export async function PATCH(request: Request, context: RouteContext) {
  const { noteId } = await context.params;
  const payload = (await request.json()) as unknown;
  const result = await updateWebReaderNote(noteId, payload);

  if (!result.ok) {
    return NextResponse.json(result, { status: result.httpStatus });
  }

  return NextResponse.json(result, { status: 200 });
}

export async function DELETE(_request: Request, context: RouteContext) {
  const { noteId } = await context.params;
  const result = await deleteWebReaderNote(noteId);

  if (!result.ok) {
    return NextResponse.json(result, { status: result.httpStatus });
  }

  return NextResponse.json(result, { status: 200 });
}
