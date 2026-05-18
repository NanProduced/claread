import { AppShell } from "@/components/layout";

export default function AppShellLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <AppShell>{children}</AppShell>;
}
