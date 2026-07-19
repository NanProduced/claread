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
    <div className="space-y-9">
      <section aria-labelledby="settings-appearance-heading">
        <h3 id="settings-appearance-heading" className="text-base font-semibold text-ink">
          外观
        </h3>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">
          选择 Claread 在此设备上的显示方式。
        </p>
        <div className="mt-5">
          <ThemePreferencesSection />
        </div>
      </section>

      <section
        className="border-t border-hairline pt-8"
        aria-labelledby="settings-reading-defaults-heading"
      >
        <h3 id="settings-reading-defaults-heading" className="text-base font-semibold text-ink">
          新阅读默认值
        </h3>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">
          新建阅读时先带入这些设置，单篇文章仍可单独调整。
        </p>
        <div className="mt-5">
          <ReadingDefaultsSection
            readingGoal={readingGoal}
            readingVariant={readingVariant}
            canEdit={canEdit}
          />
        </div>
      </section>
    </div>
  );
}