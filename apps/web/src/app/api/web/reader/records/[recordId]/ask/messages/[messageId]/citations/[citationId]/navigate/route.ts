import { NextResponse } from "next/server";
import { navigateReadingRecordAskCitationForWeb } from "@/services/bff/reader-ask";

export async function POST(
  _request: Request,
  context: {
    params: Promise<{
      recordId: string;
      messageId: string;
      citationId: string;
    }>;
  },
) {
  const { recordId, messageId, citationId } = await context.params;
  const result = await navigateReadingRecordAskCitationForWeb(
    recordId,
    messageId,
    citationId,
  );
  return result instanceof Response ? result : NextResponse.json(result);
}
