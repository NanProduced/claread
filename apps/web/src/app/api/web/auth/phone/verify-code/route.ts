import { NextResponse } from "next/server";

import { verifyPhoneCode } from "@/services/bff/phone-auth";

export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as {
    phone?: unknown;
    code?: unknown;
  };
  const result = await verifyPhoneCode(body.phone, body.code);

  return NextResponse.json(result, { status: result.ok ? 200 : 400 });
}
