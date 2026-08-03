import { reconcileReaderAskSubmissionForWeb } from "@/services/bff/reader-ask";

export async function GET(
  _request: Request,
  context: {
    params: Promise<{
      recordId: string;
      threadId: string;
      clientSubmissionId: string;
    }>;
  },
) {
  const { recordId, threadId, clientSubmissionId } = await context.params;
  return reconcileReaderAskSubmissionForWeb(recordId, threadId, clientSubmissionId);
}
