import { NextResponse } from "next/server";

import { submitReaderSourceArtifactInputFromWeb } from "@/services/bff/reader-plate";

interface SubmitInputRequestBody {
  title?: unknown;
  language?: unknown;
  readingGoal?: unknown;
  readingVariant?: unknown;
  reading_goal?: unknown;
  reading_variant?: unknown;
}

interface SubmitInputRouteContext {
  params: Promise<{ artifactId: string }>;
}

export async function POST(request: Request, context: SubmitInputRouteContext) {
  const { artifactId } = await context.params;
  const body = (await request.json().catch(() => ({}))) as SubmitInputRequestBody;

  const result = await submitReaderSourceArtifactInputFromWeb(artifactId, {
    title: body.title,
    language: body.language,
    readingGoal: body.readingGoal ?? body.reading_goal,
    readingVariant: body.readingVariant ?? body.reading_variant,
  });

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
