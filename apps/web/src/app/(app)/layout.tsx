import { AppShellFrame } from "@/components/app-shell/AppShellFrame";

export default function AppShellLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <AppShellFrame>{children}</AppShellFrame>;
}
