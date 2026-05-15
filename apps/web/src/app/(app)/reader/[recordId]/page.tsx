import { notFound } from "next/navigation";

import { getReaderAnnotations } from "@/services/bff/annotations";
import { getReaderRecord } from "@/services/bff/reader";
import { ReaderWorkbench } from "./ReaderWorkbench";

type ReaderPageProps = {
  params: Promise<{ recordId: string }>;
};

export default async function ReaderPage({ params }: ReaderPageProps) {
  const { recordId } = await params;
  const result = await getReaderRecord(recordId);

  if (!result.ok) {
    if (result.status === 404) {
      notFound();
    }

    return (
      <main className="paper-grain min-h-screen px-5 py-8 text-ink">
        <section className="mx-auto max-w-2xl rounded-note border border-hairline bg-surface p-8 shadow-surface-quiet">
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-lens-blue">
            Reader
          </p>
          <h1 className="mt-3 font-headline text-2xl font-semibold text-ink">
            无法打开阅读记录
          </h1>
          <p className="mt-3 text-sm leading-6 text-muted">{result.message}</p>
        </section>
      </main>
    );
  }

  const { record, dataSource, message } = result;
  const annotationResult = await getReaderAnnotations(record.id);

  return (
    <ReaderWorkbench
      record={record}
      dataSource={dataSource}
      message={message}
      initialAnnotations={annotationResult.items}
    />
  );
}
