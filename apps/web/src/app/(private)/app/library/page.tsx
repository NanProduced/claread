import { getReadingRecordListFromWeb } from "@/services/bff/reading-records";
import { LibraryClient } from "./LibraryClient";

export default async function HistoryPage() {
  const readingRecordsResult = await getReadingRecordListFromWeb({ limit: 100 });

  return (
    <main className="flex h-dvh flex-col overflow-hidden bg-reader-paper px-4 py-6 text-ink sm:px-8 lg:px-16 xl:px-24">
      <div className="mx-auto flex min-h-0 w-full max-w-[1300px] flex-1 flex-col">
        <LibraryClient
          readingRecords={readingRecordsResult.ok ? readingRecordsResult.items : []}
          readingRecordsStatus={readingRecordsResult.ok ? "ready" : readingRecordsResult.code}
          readingRecordsMessage={readingRecordsResult.ok ? undefined : readingRecordsResult.message}
        />
      </div>
    </main>
  );
}
