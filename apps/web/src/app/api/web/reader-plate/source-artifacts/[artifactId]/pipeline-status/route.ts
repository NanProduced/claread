import { NextResponse } from "next/server";

import { getReaderArtifactPipelineStatusFromWeb } from "@/services/bff/reader-plate";

interface PipelineStatusRouteContext {
  params: Promise<{ artifactId: string }>;
}

export async function GET(_request: Request, context: PipelineStatusRouteContext) {
  const { artifactId } = await context.params;

  const result = await getReaderArtifactPipelineStatusFromWeb(artifactId);

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
