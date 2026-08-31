import { getReaderSourcePreviewFromWeb } from "@/services/bff/reader-plate";

export const dynamic = "force-dynamic";

type SourcePreviewRouteContext = {
  params: Promise<{ recordId: string }>;
};

export async function GET(
  request: Request,
  context: SourcePreviewRouteContext,
): Promise<Response> {
  const { recordId } = await context.params;
  return getReaderSourcePreviewFromWeb(request, recordId);
}
