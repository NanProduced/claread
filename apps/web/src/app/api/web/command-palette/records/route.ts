import { NextResponse } from "next/server";
import { getCommandPaletteRecords } from "@/services/bff/command-palette";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const query = searchParams.get("query") ?? undefined;
  const limitParam = searchParams.get("limit");
  const limit = limitParam ? parseInt(limitParam, 10) : undefined;

  const items = await getCommandPaletteRecords(query, limit);
  return NextResponse.json({ items });
}
