import { AppShell } from "@/components/layout";
import { CloudPreferencesSync } from "@/components/providers/CloudPreferencesSync";

export default function AppShellLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AppShell>
      <CloudPreferencesSync />
      {children}
    </AppShell>
  );
}
