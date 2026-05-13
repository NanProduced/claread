import Link from "next/link";

export default function AppHomePage() {
  return (
    <main className="min-h-screen px-6 py-8">
      <div className="mx-auto flex max-w-6xl flex-col gap-8">
        <header className="flex items-center justify-between">
          <div>
            <p className="text-sm text-[var(--muted)]">Claread Web App</p>
            <h1 className="mt-2 text-3xl font-semibold tracking-normal">
              Reading workspace
            </h1>
          </div>
          <Link
            href="/app/history"
            className="rounded-md border border-[var(--border)] px-4 py-2 text-sm"
          >
            History
          </Link>
        </header>

        <section className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-6">
          <h2 className="text-xl font-semibold tracking-normal">
            Analysis input placeholder
          </h2>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-[var(--muted)]">
            The first functional page will submit text to the shared Claread API,
            poll analysis tasks, and open the Web Reader when a render scene is
            ready.
          </p>
          <div className="mt-5 flex gap-3">
            <Link
              href="/app/reader/demo-record"
              className="rounded-md bg-[var(--accent)] px-4 py-2 text-sm font-medium text-[var(--accent-foreground)]"
            >
              Open reader placeholder
            </Link>
            <Link
              href="/app/login"
              className="rounded-md border border-[var(--border)] px-4 py-2 text-sm"
            >
              Login placeholder
            </Link>
          </div>
        </section>
      </div>
    </main>
  );
}
