import { reconcileReaderAskSubmissionForWeb } from "@/services/bff/reader-ask";

/**
 * ASK-RETRY-CONTRACT-R4 — Browser → BFF submission reconcile.
 * GET /api/web/reader-ask/threads/{threadId}/submissions/{clientSubmissionId}
 */
export async function GET(
  request: Request,
  context: {
    params: Promise<{ threadId: string; clientSubmissionId: string }>;
  },
) {
  const { threadId, clientSubmissionId } = await context.params;
  const { searchParams } = new URL(request.url);
  const recordId = searchParams.get("recordId") ?? searchParams.get("record_id");
  const recordScope = searchParams.get("record_scope");
  return reconcileReaderAskSubmissionForWeb(
    threadId,
    clientSubmissionId,
    recordId,
    recordScope === "reading_record" ? "reading_record" : "analysis",
  );
}
