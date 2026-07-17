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
    <div className="space-y-8">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-[6rem_1fr]">
        <span className="pt-2 text-xs text-muted-foreground">外观</span>
        <ThemePreferencesSection />
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-[6rem_1fr]">
        <span className="pt-2 text-xs text-muted-foreground">新阅读默认值</span>
        <ReadingDefaultsSection
          readingGoal={readingGoal}
          readingVariant={readingVariant}
          canEdit={canEdit}
        />
      </div>
    </div>
  );
}
