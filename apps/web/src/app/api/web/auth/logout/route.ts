import { NextResponse } from "next/server";

import { clearWebAuthCookies } from "@/services/bff/session";

export async function POST() {
  await clearWebAuthCookies();

  return NextResponse.json({ ok: true });
}
