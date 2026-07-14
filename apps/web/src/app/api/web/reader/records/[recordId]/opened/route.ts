import { NextResponse } from "next/server";

import { getWebSession } from "@/services/bff/session";
import { markReaderRecordOpened } from "@/services/api/reading-records";

type OpenedRouteContext = {
  params: Promise<{ recordId: string }>;
};

export async function POST(_request: Request, context: OpenedRouteContext) {
  const { recordId } = await context.params;
  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return NextResponse.json(
      {
        ok: false,
        status: 401,
        code: "auth_required",
        message: "请先登录。",
      },
      { status: 401 },
    );
  }

  const upstream = await markReaderRecordOpened(session.sessionToken, recordId);

  if (!upstream.ok && (upstream.status === 0 || upstream.status >= 500)) {
    return NextResponse.json(
      {
        ok: false,
        status: 503,
        code: "upstream_unavailable",
        message: "透读服务暂时不可用，请稍后重试。",
      },
      { status: 503 },
    );
  }

  if (!upstream.ok) {
    return NextResponse.json(
      {
        ok: false,
        status: upstream.status,
        code: "upstream_error",
        message: upstream.message,
      },
      { status: upstream.status },
    );
  }

  return NextResponse.json(
    { ok: true, ...upstream.data },
    { status: 200 },
  );
}
