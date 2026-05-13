import Link from "next/link";

export default function HistoryPage() {
  return (
    <main className="mx-auto max-w-6xl px-6 py-8">
      <header className="flex items-center justify-between">
        <div>
          <p className="text-sm text-[var(--muted)]">Workspace</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-normal">
            History
          </h1>
        </div>
        <Link href="/app" className="rounded-md border border-[var(--border)] px-4 py-2 text-sm">
          New analysis
        </Link>
      </header>

      <section className="mt-8 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-6">
        <p className="text-sm leading-6 text-[var(--muted)]">
          This page will read records from the shared Claread API and open a
          selected record in the Web Reader.
        </p>
      </section>
    </main>
  );
}
