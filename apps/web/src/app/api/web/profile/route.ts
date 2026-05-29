import { NextResponse } from "next/server";

import { getCloudSettings, updateProfileNickname, updateProfileSettings } from "@/services/bff/profile";

export async function GET() {
  const result = await getCloudSettings();

  if (!result.ok) {
    return NextResponse.json(
      { ok: false, message: result.message },
      { status: result.httpStatus },
    );
  }

  return NextResponse.json({ ok: true, settings: result.settings }, { status: 200 });
}

export async function PATCH(request: Request) {
  const body = (await request.json().catch(() => ({}))) as {
    nickname?: unknown;
    settings?: unknown;
  };

  const hasNickname = typeof body.nickname === "string";
  const hasSettings = body.settings && typeof body.settings === "object";

  if (hasNickname) {
    const result = await updateProfileNickname(body.nickname as string);
    if (!result.ok) {
      return NextResponse.json(
        { ok: false, message: result.message },
        { status: result.httpStatus },
      );
    }
  }

  if (hasSettings) {
    const result = await updateProfileSettings(body.settings as Record<string, unknown>);
    if (!result.ok) {
      return NextResponse.json(
        { ok: false, message: result.message },
        { status: result.httpStatus },
      );
    }
  }

  if (!hasNickname && !hasSettings) {
    return NextResponse.json(
      { ok: false, message: "需要提供 nickname 或 settings 字段。" },
      { status: 400 },
    );
  }

  return NextResponse.json({ ok: true }, { status: 200 });
}
