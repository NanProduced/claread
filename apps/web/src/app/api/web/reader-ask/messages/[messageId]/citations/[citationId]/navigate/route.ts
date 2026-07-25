import { navigateReadingRecordAskCitationForWeb } from "@/services/bff/reader-ask";

/**
 * Secure citation navigation BFF.
 *
 * Client supplies only recordId (query) + messageId + citationId (path).
 * Server re-resolves fence and restricted evidence; never accepts client
 * base/generation/stable-document overrides.
 */
export async function POST(
  request: Request,
  context: { params: Promise<{ messageId: string; citationId: string }> },
) {
  const { messageId, citationId } = await context.params;
  const { searchParams } = new URL(request.url);
  const recordId = searchParams.get("recordId") ?? searchParams.get("record_id");
  if (!recordId?.trim()) {
    return new Response(JSON.stringify({ message: "Missing reading record id." }), {
      status: 400,
      headers: { "content-type": "application/json" },
    });
  }
  const result = await navigateReadingRecordAskCitationForWeb(
    recordId,
    messageId,
    citationId,
  );
  if (result instanceof Response) {
    return result;
  }
  return Response.json(result);
}
