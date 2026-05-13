import { NextResponse } from "next/server";

import { getWebSession, projectSession } from "@/services/bff/session";

export async function GET() {
  const session = await getWebSession();

  return NextResponse.json(projectSession(session));
}
