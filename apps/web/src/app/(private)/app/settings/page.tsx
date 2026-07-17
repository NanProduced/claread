import { ScrollArea } from "@/components/primitives/scroll-area";
import { readReadingDefaultsFromSettings } from "@/lib/reading-defaults";
import { getProfileSettings } from "@/services/bff/profile";
import { SettingsSectionContent } from "./sections/SettingsSectionContent";

export default async function SettingsPage() {
  const settings = await getProfileSettings();
  const quota = settings.quota;
  const displayName = settings.profile?.nickname || settings.session.phone || "Web User";
  const realNickname = settings.profile?.nickname || "";
  const avatarText = displayName.trim().slice(0, 1).toUpperCase() || "U";
  const readingDefaults = readReadingDefaultsFromSettings(settings.profile?.settings);
  const canEditSharedDefaults = settings.status === "ready";

  const quotaLimit = quota ? (quota.dailyFreePoints ?? quota.quotaLimit) : 0;
  const quotaUsed = quota ? (quota.dailyUsedPoints ?? quota.quotaUsed) : 0;
  const quotaPercentage = quotaLimit > 0 ? Math.min(100, Math.max(0, (quotaUsed / quotaLimit) * 100)) : 0;

  return (
    <ScrollArea className="h-dvh bg-surface-canvas text-ink">
      <main className="flex flex-col px-6 py-16 sm:px-12 lg:px-24 xl:px-32 mx-auto w-full max-w-[1200px]">
        <div className="mx-auto w-full max-w-[880px] pb-32">
          {/* Title */}
          <div className="mb-12">
            <h1 className="font-display text-[3.5rem] font-semibold leading-[1.05] tracking-tight text-ink md:text-[4.5rem]">
              Preferences.
            </h1>
          </div>

          <div className="divide-y divide-hairline">
            <SettingsSectionContent
              mode="fallback"
              accountData={{
                nickname: realNickname,
                displayFallback: displayName,
                phone: settings.session.phone,
                status: settings.status,
                avatarText,
              }}
              preferencesData={{
                readingGoal: readingDefaults.readingGoal,
                readingVariant: readingDefaults.readingVariant,
                canEdit: canEditSharedDefaults,
              }}
              usageData={{
                quota,
                quotaUsed,
                quotaLimit,
                quotaPercentage,
              }}
            />
          </div>
        </div>
      </main>
    </ScrollArea>
  );
}
