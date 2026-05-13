import Link from "next/link";
import type { Route } from "next";

const readRoute = "/read" as Route;
const libraryRoute = "/library" as Route;
const vocabularyRoute = "/vocabulary" as Route;
const settingsRoute = "/settings" as Route;

export default function AppShellLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-web-canvas text-ink flex flex-col">
      <header className="sticky top-0 z-10 bg-web-canvas/80 backdrop-blur-md border-b border-hairline">
        <div className="mx-auto max-w-4xl px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-6">
            <Link href={readRoute} className="font-display font-bold text-lg tracking-tight">
              Claread
            </Link>
            <nav className="hidden md:flex items-center gap-4 text-sm font-semibold text-muted">
              <Link href={readRoute} className="hover:text-ink transition-colors">
                解析
              </Link>
              <Link href={libraryRoute} className="hover:text-ink transition-colors">
                历史
              </Link>
              <Link href={vocabularyRoute} className="hover:text-ink transition-colors">
                生词本
              </Link>
              <Link href={settingsRoute} className="hover:text-ink transition-colors">
                设置
              </Link>
            </nav>
          </div>
          <div className="flex items-center gap-4">
            <Link
              href={settingsRoute}
              className="flex items-center justify-center w-8 h-8 rounded-full bg-surface-quiet border border-hairline text-sm font-semibold text-ink shadow-surface-quiet hover:border-muted transition-colors"
            >
              U
            </Link>
          </div>
        </div>
      </header>

      <div className="flex-1 flex flex-col">{children}</div>
    </div>
  );
}
