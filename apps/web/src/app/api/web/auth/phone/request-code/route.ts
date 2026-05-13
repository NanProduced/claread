import { NextResponse } from "next/server";

import { requestPhoneCode } from "@/services/bff/phone-auth";

export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as { phone?: unknown };
  const result = await requestPhoneCode(body.phone);

  return NextResponse.json(result, { status: result.ok ? 200 : 400 });
}
