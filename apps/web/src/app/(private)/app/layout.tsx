import { AppShell } from "@/components/layout";
import { RecentReadingProvider } from "@/components/layout/recent-reading-context";
import { CloudPreferencesSync } from "@/components/providers/CloudPreferencesSync";
import { SettingsDialogProvider } from "@/components/settings/SettingsDialogProvider";
import { getReadingRecordListFromWeb } from "@/services/bff/reading-records";

export default async function AppShellLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const recentResult = await getReadingRecordListFromWeb({
    limit: 10,
    recentOnly: true,
  });
  const recentRecords = recentResult.ok ? recentResult.items : [];

  return (
    <RecentReadingProvider initialItems={recentRecords}>
      <SettingsDialogProvider>
        <AppShell userName="Claread" userPlanLabel="Free">
          <CloudPreferencesSync />
          {children}
        </AppShell>
      </SettingsDialogProvider>
    </RecentReadingProvider>
  );
}