export default function LoginPage() {
  return (
    <main className="mx-auto grid min-h-screen max-w-md content-center px-6">
      <section className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-6">
        <h1 className="text-2xl font-semibold tracking-normal">Sign in</h1>
        <p className="mt-3 text-sm leading-6 text-[var(--muted)]">
          Email magic link auth will be added through the Web auth adapter.
        </p>
      </section>
    </main>
  );
}
