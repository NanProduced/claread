import { getVocabularyList } from "@/services/bff/vocabulary";
import { VocabularyClient } from "./VocabularyClient";

export default async function VocabularyPage() {
  const vocabulary = await getVocabularyList();

  return (
    <main className="flex h-dvh flex-col overflow-hidden bg-reader-paper px-4 py-6 text-ink sm:px-8 lg:px-16 xl:px-24">
      <div className="mx-auto flex min-h-0 w-full max-w-[1300px] flex-1 flex-col">
        <VocabularyClient
          items={vocabulary.items}
          status={vocabulary.status}
          message={vocabulary.message}
        />
      </div>
    </main>
  );
}
