import { getRecordList } from "@/services/bff/records";
import { LibraryClient } from "./LibraryClient";

export default async function HistoryPage() {
  const result = await getRecordList({ limit: 100 });
  const noteCount = result.records.reduce((sum, record) => sum + record.noteCount, 0);
  const vocabularyCount = result.records.reduce((sum, record) => sum + record.vocabularyCount, 0);

  return (
    <main className="flex h-dvh flex-col overflow-hidden bg-reader-paper px-4 py-6 text-ink sm:px-8 lg:px-16 xl:px-24">
      <div className="mx-auto flex min-h-0 w-full max-w-[1300px] flex-1 flex-col">
        <LibraryClient
          records={result.records}
          status={result.status}
          message={result.message}
          total={result.total}
          noteCount={noteCount}
          vocabularyCount={vocabularyCount}
        />
      </div>
    </main>
  );
}
