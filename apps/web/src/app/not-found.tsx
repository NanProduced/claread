import Link from "next/link";

export default function NotFound() {
  return (
    <main className="grid min-h-screen content-center px-6 text-center">
      <h1 className="text-3xl font-semibold tracking-normal">Page not found</h1>
      <Link href="/" className="mt-5 text-sm text-[var(--accent)]">
        Back to Claread
      </Link>
    </main>
  );
}
