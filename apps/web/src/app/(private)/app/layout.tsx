import { AppShell } from "@/components/layout";
import { CloudPreferencesSync } from "@/components/providers/CloudPreferencesSync";
import { getProjectedWebSession } from "@/services/bff/session";

export default async function AppShellLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await getProjectedWebSession();
  const userContact = session.phone;

  return (
    <AppShell userName="Claread" userContact={userContact} userPlanLabel="Free">
      <CloudPreferencesSync />
      {children}
    </AppShell>
  );
}
