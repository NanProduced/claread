import Link from "next/link";
import type { Route } from "next";

const links: Array<{ href: Route; label: string }> = [
  { href: "/read", label: "Read" },
  { href: "/help", label: "Help" },
  { href: "/about", label: "About" },
];

export default function HomePage() {
  return (
    <main className="min-h-screen px-6 py-6">
      <nav className="mx-auto flex max-w-6xl items-center justify-between">
        <Link href="/" className="text-lg font-semibold tracking-normal">
          Claread
        </Link>
        <div className="flex items-center gap-2 text-sm text-[var(--muted)]">
          {links.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="rounded-md px-3 py-2 hover:bg-[var(--surface)]"
            >
              {link.label}
            </Link>
          ))}
        </div>
      </nav>

      <section className="mx-auto grid min-h-[70vh] max-w-6xl content-center gap-8 py-20">
        <div className="max-w-3xl">
          <h1 className="text-5xl font-semibold leading-tight tracking-normal text-[var(--foreground)]">
            Claread Web
          </h1>
          <p className="mt-5 max-w-2xl text-lg leading-8 text-[var(--muted)]">
            A placeholder entry for the future public site. Current web work
            focuses on the reading app, reader annotations, history, dictionary,
            and share pages.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link
              href="/read"
              className="rounded-md bg-[var(--accent)] px-5 py-3 text-sm font-medium text-[var(--accent-foreground)]"
            >
              Start reading
            </Link>
            <Link
              href="/share/demo"
              className="rounded-md border border-[var(--border)] px-5 py-3 text-sm font-medium"
            >
              Share page placeholder
            </Link>
          </div>
        </div>
      </section>
    </main>
  );
}
