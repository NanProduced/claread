import { listReaderAskModelOptionsForWeb } from "@/services/bff/reader-ask";

export async function GET(
  _request: Request,
  context: { params: Promise<{ recordId: string }> },
) {
  const { recordId } = await context.params;
  return listReaderAskModelOptionsForWeb(recordId);
}
