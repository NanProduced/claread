import type { Metadata } from "next";

type SharePageProps = {
  params: Promise<{ shareId: string }>;
};

export async function generateMetadata({
  params,
}: SharePageProps): Promise<Metadata> {
  const { shareId } = await params;

  return {
    title: `Shared Claread result ${shareId}`,
    description: "A placeholder for a public Claread reading result.",
    openGraph: {
      title: `Shared Claread result ${shareId}`,
      description: "A placeholder for a public Claread reading result.",
      type: "article",
    },
  };
}

export default async function SharePage({ params }: SharePageProps) {
  const { shareId } = await params;

  return (
    <main className="min-h-screen px-6 py-12">
      <article className="mx-auto max-w-3xl rounded-lg border border-[var(--border)] bg-[var(--surface)] p-8">
        <p className="text-sm text-[var(--muted)]">Share {shareId}</p>
        <h1 className="mt-4 text-4xl font-semibold tracking-normal">
          Shared reading result placeholder
        </h1>
        <p className="mt-5 text-lg leading-8 text-[var(--muted)]">
          This route is reserved for SSR share snapshots and dynamic metadata.
        </p>
      </article>
    </main>
  );
}
