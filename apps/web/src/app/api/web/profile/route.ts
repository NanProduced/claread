import { NextResponse } from "next/server";

import { updateProfileNickname } from "@/services/bff/profile";

export async function PATCH(request: Request) {
  const body = (await request.json().catch(() => ({}))) as { nickname?: unknown };
  const nickname = typeof body.nickname === "string" ? body.nickname : "";

  const result = await updateProfileNickname(nickname);

  if (result.ok) {
    return NextResponse.json({ ok: true }, { status: 200 });
  }

  return NextResponse.json(
    { ok: false, message: result.message },
    { status: result.httpStatus },
  );
}
