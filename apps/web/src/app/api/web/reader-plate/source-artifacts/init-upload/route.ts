import { NextResponse } from "next/server";

import { initReaderSourceArtifactUploadFromWeb } from "@/services/bff/reader-plate";

interface InitUploadRequestBody {
  artifactKind?: unknown;
  sourceFilename?: unknown;
  contentType?: unknown;
  byteSize?: unknown;
  contentSha256?: unknown;
  readingRecordId?: unknown;
  originalInputId?: unknown;
  sourceRefs?: unknown;
  metadata?: unknown;
  quality?: unknown;
}

export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as InitUploadRequestBody;

  const result = await initReaderSourceArtifactUploadFromWeb({
    artifactKind: body.artifactKind,
    sourceFilename: body.sourceFilename,
    contentType: body.contentType,
    byteSize: body.byteSize,
    contentSha256: body.contentSha256,
    readingRecordId: body.readingRecordId,
    originalInputId: body.originalInputId,
    sourceRefs: body.sourceRefs,
    metadata: body.metadata,
    quality: body.quality,
  });

  return NextResponse.json(result, {
    status: result.ok ? 200 : result.status,
  });
}
