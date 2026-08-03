import { NextResponse } from "next/server";

import { completeReaderSourceArtifactUploadFromWeb } from "@/services/bff/reader-plate";

interface CompleteUploadRequestBody {
  contentType?: unknown;
  byteSize?: unknown;
  contentSha256?: unknown;
}

interface CompleteUploadRouteContext {
  params: Promise<{ artifactId: string }>;
}

export async function POST(request: Request, context: CompleteUploadRouteContext) {
  const { artifactId } = await context.params;
  const body = (await request.json().catch(() => ({}))) as CompleteUploadRequestBody;

  const result = await completeReaderSourceArtifactUploadFromWeb(artifactId, {
    contentType: body.contentType,
    byteSize: body.byteSize,
    contentSha256: body.contentSha256,
  });

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
