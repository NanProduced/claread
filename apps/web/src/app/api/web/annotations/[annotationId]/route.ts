import { NextResponse } from "next/server";

import {
  deleteReaderAnnotation,
  updateReaderAnnotation,
} from "@/services/bff/annotations";

interface RouteContext {
  params: Promise<{ annotationId: string }>;
}

export async function PATCH(request: Request, context: RouteContext) {
  const { annotationId } = await context.params;
  const payload = (await request.json()) as unknown;
  const result = await updateReaderAnnotation(annotationId, payload);

  if (!result.ok) {
    return NextResponse.json(result, { status: result.httpStatus });
  }

  return NextResponse.json(result, { status: 200 });
}

export async function DELETE(_request: Request, context: RouteContext) {
  const { annotationId } = await context.params;
  const result = await deleteReaderAnnotation(annotationId);

  if (!result.ok) {
    return NextResponse.json(result, { status: result.httpStatus });
  }

  return NextResponse.json(result, { status: 200 });
}
