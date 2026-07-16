import { Suspense } from "react";
import { getVocabularyList } from "@/services/bff/vocabulary";
import { VocabularyClient } from "./VocabularyClient";

export default async function VocabularyPage() {
  const vocabulary = await getVocabularyList();

  const learningCount = vocabulary.items.filter((i) => !i.mastered).length;
  const masteredCount = vocabulary.items.filter((i) => i.mastered).length;

  const recentItems = [...vocabulary.items]
    .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime())
    .slice(0, 2);

  const multiContextItems = vocabulary.items
    .filter((i) => i.totalSourceCount > 1)
    .sort((a, b) => b.totalSourceCount - a.totalSourceCount)
    .slice(0, 2);

  return (
    <main className="flex h-dvh flex-col overflow-hidden bg-surface-canvas px-4 py-6 text-ink sm:px-8 lg:px-16 xl:px-24">
      <div className="mx-auto flex min-h-0 w-full max-w-[1300px] flex-1 flex-col">
        <Suspense>
          <VocabularyClient
            items={vocabulary.items}
            status={vocabulary.status}
            message={vocabulary.message}
            dueCount={vocabulary.dueCount}
            learningCount={learningCount}
            masteredCount={masteredCount}
            recentItems={recentItems}
            multiContextItems={multiContextItems}
          />
        </Suspense>
      </div>
    </main>
  );
}
