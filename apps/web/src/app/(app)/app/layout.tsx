import Link from "next/link";

export default function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-web-canvas text-ink flex flex-col">
      {/* Lightweight App Shell Navigation (Top Bar) */}
      <header className="sticky top-0 z-10 bg-web-canvas/80 backdrop-blur-md border-b border-hairline">
        <div className="mx-auto max-w-4xl px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-6">
            <Link href="/app" className="font-display font-bold text-lg tracking-tight">
              Claread
            </Link>
            <nav className="hidden md:flex items-center gap-4 text-sm font-semibold text-muted">
              <Link href="/app" className="hover:text-ink transition-colors">解析</Link>
              <Link href="/app/history" className="hover:text-ink transition-colors">历史</Link>
              <Link href="/app/vocabulary" className="hover:text-ink transition-colors">生词本</Link>
            </nav>
          </div>
          <div className="flex items-center gap-4">
            <Link href="/app/profile" className="flex items-center justify-center w-8 h-8 rounded-full bg-surface-quiet border border-hairline text-sm font-semibold text-ink shadow-surface-quiet hover:border-muted transition-colors">
              U
            </Link>
          </div>
        </div>
      </header>

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col">
        {children}
      </div>
    </div>
  );
}
