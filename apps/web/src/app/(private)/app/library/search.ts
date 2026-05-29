import type { RecordListItemVm } from "@/types/view/RecordListItemVm";

export const readingGoalLabel: Record<string, string> = {
  daily_reading: "日常阅读",
  academic: "学术摘要",
  exam: "备考精读",
};

export const readingVariantLabel: Record<string, string> = {
  beginner_reading: "入门",
  intermediate_reading: "中级",
  intensive_reading: "精读",
  academic_general: "学术通用",
  gaokao: "高考",
  cet: "四六级",
  kaoyan: "考研",
  tem: "专四专八",
  ielts_toefl: "雅思托福",
};

export const sourceTypeLabel: Record<string, string> = {
  user_input: "手动粘贴",
  daily_article: "每日文章",
  imported: "导入",
  ocr: "OCR",
};

export const libraryGoalFilters = ["all", "daily_reading", "exam", "academic"] as const;
export const libraryFavoriteFilters = ["all", "favorited"] as const;
export const librarySortOptions = ["last_opened", "created_at"] as const;

export type LibraryGoalFilter = (typeof libraryGoalFilters)[number];
export type LibraryFavoriteFilter = (typeof libraryFavoriteFilters)[number];
export type LibrarySortOption = (typeof librarySortOptions)[number];

export function isLibraryGoalFilter(value: string | null | undefined): value is LibraryGoalFilter {
  return Boolean(value) && libraryGoalFilters.includes(value as LibraryGoalFilter);
}

export function isLibraryFavoriteFilter(
  value: string | null | undefined,
): value is LibraryFavoriteFilter {
  return Boolean(value) && libraryFavoriteFilters.includes(value as LibraryFavoriteFilter);
}

export function isLibrarySortOption(value: string | null | undefined): value is LibrarySortOption {
  return Boolean(value) && librarySortOptions.includes(value as LibrarySortOption);
}

export function normalizeLibraryQuery(value: string) {
  return value.trim().toLowerCase();
}

export function readingGoalName(value: string) {
  return readingGoalLabel[value] ?? "透读文章";
}

export function readingVariantName(value: string) {
  return readingVariantLabel[value] ?? value;
}

export function sourceTypeName(value: string) {
  return sourceTypeLabel[value] ?? "外部来源";
}

export function summarizeSourceExcerpt(record: RecordListItemVm) {
  const excerpt = record.sourceTextExcerpt.trim();
  if (excerpt) {
    return excerpt;
  }

  const firstLine = record.sourceText
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find(Boolean);

  if (!firstLine) {
    return "暂无原文片段";
  }

  return firstLine.length > 140 ? `${firstLine.slice(0, 140)}...` : firstLine;
}

function buildRecordSearchText(record: RecordListItemVm) {
  const excerptOrSource = record.sourceTextExcerpt.trim() || record.sourceText;

  return [
    record.title,
    excerptOrSource,
    readingGoalName(record.readingGoal),
    readingVariantName(record.readingVariant),
    sourceTypeName(record.sourceType),
    record.readingGoal,
    record.readingVariant,
    record.sourceType,
  ]
    .filter(Boolean)
    .join("\n")
    .toLowerCase();
}

export function filterRecordsByText(records: RecordListItemVm[], normalizedQuery: string) {
  if (!normalizedQuery) {
    return records;
  }

  return records.filter((record) => buildRecordSearchText(record).includes(normalizedQuery));
}

export function filterRecordsByGoal(records: RecordListItemVm[], goalFilter: LibraryGoalFilter) {
  if (goalFilter === "all") {
    return records;
  }

  return records.filter((record) => record.readingGoal === goalFilter);
}

export function filterRecordsByFavorite(
  records: RecordListItemVm[],
  favoriteFilter: LibraryFavoriteFilter,
) {
  if (favoriteFilter === "all") {
    return records;
  }

  return records.filter((record) => record.isFavorited);
}

export function sortLibraryRecords(records: RecordListItemVm[], sortOption: LibrarySortOption) {
  const sorted = [...records];

  sorted.sort((a, b) => {
    const dateA =
      sortOption === "created_at"
        ? new Date(a.createdAt).getTime()
        : new Date(a.lastOpenedAt || a.createdAt).getTime();
    const dateB =
      sortOption === "created_at"
        ? new Date(b.createdAt).getTime()
        : new Date(b.lastOpenedAt || b.createdAt).getTime();

    return dateB - dateA;
  });

  return sorted;
}

export function filterLibraryRecords(
  records: RecordListItemVm[],
  options: {
    normalizedQuery: string;
    favoriteFilter: LibraryFavoriteFilter;
    goalFilter: LibraryGoalFilter;
    sortOption: LibrarySortOption;
  },
) {
  return sortLibraryRecords(
    filterRecordsByGoal(
      filterRecordsByFavorite(
        filterRecordsByText(records, options.normalizedQuery),
        options.favoriteFilter,
      ),
      options.goalFilter,
    ),
    options.sortOption,
  );
}

export function countLibraryGoals(records: RecordListItemVm[]) {
  const counts: Record<LibraryGoalFilter, number> = {
    all: records.length,
    daily_reading: 0,
    exam: 0,
    academic: 0,
  };

  for (const record of records) {
    if (record.readingGoal === "daily_reading") {
      counts.daily_reading += 1;
    } else if (record.readingGoal === "exam") {
      counts.exam += 1;
    } else if (record.readingGoal === "academic") {
      counts.academic += 1;
    }
  }

  return counts;
}

export function findMostRecentRecord(records: RecordListItemVm[]) {
  if (records.length === 0) {
    return null;
  }

  return [...records].sort((a, b) => {
    const dateA = new Date(a.lastOpenedAt || a.createdAt).getTime();
    const dateB = new Date(b.lastOpenedAt || b.createdAt).getTime();
    return dateB - dateA;
  })[0] ?? null;
}
