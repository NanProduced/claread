import "server-only";

import { getRecordList } from "./records";
import type { CommandPaletteRecordItem } from "@/components/layout/command-palette/command-palette-types";

export async function getCommandPaletteRecords(
  query?: string,
  limit?: number,
): Promise<CommandPaletteRecordItem[]> {
  const fetchLimit = query ? 50 : (limit ?? 8);
  const result = await getRecordList({ limit: fetchLimit });

  if (result.status !== "ready") {
    return [];
  }

  let records = result.records;

  if (query?.trim()) {
    const q = query.toLowerCase();
    records = records.filter(
      (r) =>
        r.title.toLowerCase().includes(q) ||
        r.sourceText.toLowerCase().includes(q),
    );
  }

  return records.slice(0, limit ?? 8).map((r) => ({
    id: r.id,
    title: r.title,
    excerpt: r.sourceText.slice(0, 120),
    createdAt: r.createdAt,
  }));
}
