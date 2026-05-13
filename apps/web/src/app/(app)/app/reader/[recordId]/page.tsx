type ReaderPageProps = {
  params: Promise<{ recordId: string }>;
};

export default async function ReaderPage({ params }: ReaderPageProps) {
  const { recordId } = await params;

  return (
    <main className="min-h-screen bg-[var(--reader-paper)] px-6 py-8 text-[var(--reader-ink)]">
      <div className="mx-auto grid max-w-7xl gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <article className="reader-serif rounded-lg border border-[var(--border)] bg-[var(--surface)] p-8">
          <p className="text-sm font-sans text-[var(--muted)]">Record {recordId}</p>
          <h1 className="mt-4 text-4xl font-semibold leading-tight tracking-normal">
            Reader placeholder
          </h1>
          <p className="mt-6 text-xl leading-9">
            The Web Reader will render Claread&apos;s structured render scene,
            inline marks, sentence entries, translations, and user annotations.
          </p>
        </article>

        <aside className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-6">
          <h2 className="text-lg font-semibold tracking-normal">Context panel</h2>
          <p className="mt-3 text-sm leading-6 text-[var(--muted)]">
            Dictionary, annotations, and sentence detail panels will live here.
          </p>
        </aside>
      </div>
    </main>
  );
}
