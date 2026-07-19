import { AppShell } from "@/components/layout";
import { RecentReadingProvider } from "@/components/layout/recent-reading-context";
import { CloudPreferencesSync } from "@/components/providers/CloudPreferencesSync";
import { SettingsDialogProvider } from "@/components/settings/SettingsDialogProvider";
import { getReadingRecordListFromWeb } from "@/services/bff/reading-records";
import { getProjectedWebSession } from "@/services/bff/session";

export default async function AppShellLayout({
  children,
  settings,
}: {
  children: React.ReactNode;
  settings?: React.ReactNode;
}) {
  const [session, recentResult] = await Promise.all([
    getProjectedWebSession(),
    getReadingRecordListFromWeb({ limit: 10 }),
  ]);
  const recentRecords = recentResult.ok ? recentResult.items : [];
  const userContact = session.phone;

  return (
    <RecentReadingProvider initialItems={recentRecords}>
      <SettingsDialogProvider>
        <AppShell userName="Claread" userContact={userContact} userPlanLabel="Free">
          <CloudPreferencesSync />
          {children}
          {settings}
        </AppShell>
      </SettingsDialogProvider>
    </RecentReadingProvider>
  );
}