import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { appSettingsRoute } from "@/lib/routes";
import { CreditLedgerPanel } from "../CreditLedgerPanel";

export default function LedgerPage() {
  return (
    <main className="flex h-dvh flex-col overflow-y-auto bg-reader-paper px-4 py-8 text-ink sm:px-8 lg:px-16 xl:px-24">
      <div className="mx-auto flex w-full max-w-[760px] flex-col pb-24 pt-4 lg:pt-12">
        {/* Typographic Breadcrumbs */}
        <div className="mb-14 border-b border-hairline pb-8">
          <nav aria-label="Breadcrumb" className="mb-4 flex items-center gap-2 text-[0.68rem] font-bold uppercase tracking-[0.15em] text-muted">
            <Link href={appSettingsRoute} className="hover:text-ink hover:underline underline-offset-4 decoration-hairline transition-colors">
              Preferences
            </Link>
            <ChevronRight className="h-3 w-3 text-hairline" />
            <span className="text-ink">Credit Ledger</span>
          </nav>
          <h1 className="font-headline text-[2.5rem] font-semibold leading-[1] tracking-tight text-ink md:text-[3.2rem]">
            Credit Ledger.
          </h1>
        </div>

        {/* Content */}
        <div className="min-h-0">
          <CreditLedgerPanel />
        </div>
      </div>
    </main>
  );
}
