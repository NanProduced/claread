import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { appSettingsRoute } from "@/lib/routes";
import { FeedbackForm } from "../FeedbackForm";

export default function FeedbackPage() {
  return (
    <main className="flex h-dvh flex-col overflow-y-auto bg-surface-canvas px-4 py-8 text-ink sm:px-8 lg:px-16 xl:px-24">
      <div className="mx-auto flex w-full max-w-[880px] flex-col pb-24 pt-4 lg:pt-12">
        <div className="mb-14 border-b border-hairline pb-8">
          <nav aria-label="Breadcrumb" className="mb-4 flex items-center gap-2 text-[0.68rem] font-bold tracking-[0.15em] text-muted-foreground">
            <Link href={appSettingsRoute} className="hover:text-ink hover:underline underline-offset-4 decoration-hairline transition-colors">
              Preferences
            </Link>
            <ChevronRight className="h-3 w-3 text-hairline" />
            <span className="text-ink">Feedback</span>
          </nav>
          <h1 className="font-headline text-[2.5rem] font-semibold leading-[1] tracking-tight text-ink md:text-[3.2rem]">
            Feedback.
          </h1>
        </div>

        <div className="min-h-0">
          <FeedbackForm />
        </div>
      </div>
    </main>
  );
}
