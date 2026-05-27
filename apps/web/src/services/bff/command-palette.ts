import "server-only";

import { getRecordList } from "./records";
import type { CommandPaletteRecordItem } from "@/components/layout/command-palette/command-palette-types";

export async function getCommandPaletteRecords(
  query?: string,
  limit?: number,
): Promise<CommandPaletteRecordItem[]> {
  const requestedLimit = limit ?? 8;
  const fetchLimit = query ? 50 : requestedLimit;
  const result = await getRecordList({ limit: fetchLimit });

  if (result.status !== "ready") {
    return [];
  }

  if (query?.trim()) {
    const q = query.toLowerCase();
    const matches = result.records.filter(
      (record) =>
        record.title.toLowerCase().includes(q) ||
        record.sourceText.toLowerCase().includes(q),
    );

    let page = result.page + 1;
    while (matches.length < requestedLimit && (page - 1) * fetchLimit < result.total) {
      const nextPage = await getRecordList({ page, limit: fetchLimit });
      if (nextPage.status !== "ready" || nextPage.records.length === 0) {
        break;
      }
      matches.push(
        ...nextPage.records.filter(
          (record) =>
            record.title.toLowerCase().includes(q) ||
            record.sourceText.toLowerCase().includes(q),
        ),
      );
      page += 1;
    }

    return matches.slice(0, requestedLimit).map((record) => ({
      id: record.id,
      title: record.title,
      excerpt: record.sourceText.slice(0, 120),
      createdAt: record.createdAt,
    }));
  }

  return result.records.slice(0, requestedLimit).map((r) => ({
    id: r.id,
    title: r.title,
    excerpt: r.sourceText.slice(0, 120),
    createdAt: r.createdAt,
  }));
}
