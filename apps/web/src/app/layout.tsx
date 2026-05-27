import type { Metadata, Viewport } from "next";
import { AppearanceProvider } from "@/components/providers/appearance-provider";
import { ClareadToaster } from "@/components/primitives/toast";
import { TooltipProvider } from "@/components/primitives/tooltip";
import { clareadFontVariables } from "./claread-fonts";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "Claread",
    template: "%s | Claread",
  },
  description: "Claread is a multi-client English reading assistant.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#f7f6f2",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body className={clareadFontVariables}>
        <AppearanceProvider>
          <TooltipProvider>
            {children}
            <ClareadToaster />
          </TooltipProvider>
        </AppearanceProvider>
      </body>
    </html>
  );
}
