import { HelpCircle } from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/primitives/tooltip";
import type { ReadingDefaultState } from "@/lib/reading-defaults";
import { ReadingDefaultsSection } from "../ReadingDefaultsSection";
import { ThemePreferencesSection } from "../ThemePreferencesSection";

interface PreferencesSectionProps extends ReadingDefaultState {
  canEdit: boolean;
}

export function PreferencesSection({
  readingGoal,
  readingVariant,
  canEdit,
}: PreferencesSectionProps) {
  return (
    <div className="space-y-10">
      <div>
        <div className="mb-6">
          <h3 className="font-headline text-2xl font-semibold text-ink">主题偏好</h3>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            当前主题会同时影响全站外壳、功能页和阅读器视图。
          </p>
        </div>
        <ThemePreferencesSection />
      </div>

      <div>
        <div className="mb-6">
          <div className="flex items-center gap-2">
            <h3 className="font-headline text-2xl font-semibold text-ink">默认透读设置</h3>
            <TooltipProvider>
              <Tooltip delayDuration={300}>
                <TooltipTrigger className="text-muted-foreground hover:text-ink focus:outline-none focus-visible:ring-1 focus-visible:ring-lens-blue rounded-full p-1 transition-colors cursor-help">
                  <HelpCircle className="h-5 w-5" />
                </TooltipTrigger>
                <TooltipContent side="right" className="max-w-[280px]">
                  <p>
                    这里的设置仅作为每次新建阅读时的初始默认值。在实际解析文章前，您依然可以针对单篇文章自由调整。
                  </p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            新建阅读任务时优先带入的目标与难度。
          </p>
        </div>
        <ReadingDefaultsSection
          readingGoal={readingGoal}
          readingVariant={readingVariant}
          canEdit={canEdit}
        />
      </div>
    </div>
  );
}
