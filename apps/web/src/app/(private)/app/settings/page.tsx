import { ScrollArea } from "@/components/primitives/scroll-area";
import { loadSettingsData } from "./lib/loadSettingsData";
import { SettingsSectionContent } from "./sections/SettingsSectionContent";

export default async function SettingsPage() {
  const { accountData, preferencesData, usageData } = await loadSettingsData();

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
              accountData={accountData}
              preferencesData={preferencesData}
              usageData={usageData}
            />
          </div>
        </div>
      </main>
    </ScrollArea>
  );
}
