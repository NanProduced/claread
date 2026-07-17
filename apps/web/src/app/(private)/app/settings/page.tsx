import { ScrollArea } from "@/components/primitives/scroll-area";
import { loadSettingsData } from "./lib/loadSettingsData";
import { SettingsSectionContent } from "./sections/SettingsSectionContent";

export default async function SettingsPage() {
  const { accountData, preferencesData } = await loadSettingsData();

  return (
    <ScrollArea className="h-dvh bg-surface-canvas text-ink">
      <main className="mx-auto flex w-full max-w-4xl flex-col px-6 py-16 sm:px-12 lg:px-24">
        <div className="mx-auto w-full max-w-[880px] pb-32">
          {/* Title */}
          <div className="mb-8">
            <h1 className="text-2xl font-semibold text-ink">设置</h1>
          </div>

          <div className="divide-y divide-hairline">
            <SettingsSectionContent
              mode="fallback"
              accountData={accountData}
              preferencesData={preferencesData}
            />
          </div>
        </div>
      </main>
    </ScrollArea>
  );
}
