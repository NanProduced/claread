import { NextResponse } from "next/server";

import { recoverReaderRecordFromWeb } from "@/services/bff/reading-records";

type RecoveryRouteContext = {
  params: Promise<{ recordId: string }>;
};

/**
 * POST /api/web/reader/records/{recordId}/recovery
 *
 * No request body: identity comes from the web session and the recovery
 * trigger is fixed to manual upstream. Success returns the approved
 * recovery DTO; failures return the sanitized BFF error envelope.
 */
export async function POST(_request: Request, context: RecoveryRouteContext) {
  const { recordId } = await context.params;
  const result = await recoverReaderRecordFromWeb(recordId);

  if (!result.ok) {
    return NextResponse.json(
      {
        ok: false,
        status: result.status,
        code: result.code,
        message: result.message,
      },
      { status: result.status },
    );
  }

  return NextResponse.json({ ok: true, ...result.data }, { status: 200 });
}
