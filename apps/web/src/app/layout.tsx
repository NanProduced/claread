import type { Metadata, Viewport } from "next";
import { clareadFontVariables } from "@claread/design-tokens/web/fonts";
import { ClareadToaster } from "@/components/primitives/toast";
import { TooltipProvider } from "@/components/primitives/tooltip";
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
  themeColor: "#fbfaf7",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body className={clareadFontVariables}>
        <TooltipProvider>
          {children}
          <ClareadToaster />
        </TooltipProvider>
      </body>
    </html>
  );
}
