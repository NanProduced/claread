import { Inter, Newsreader, Noto_Sans_SC, Noto_Serif_SC } from "next/font/google";

export const clareadUiSans = Inter({
  subsets: ["latin"],
  variable: "--font-ui-en",
  display: "swap",
});

export const clareadReadingSerif = Newsreader({
  subsets: ["latin"],
  variable: "--font-reading-en",
  display: "swap",
});

export const clareadUiZh = Noto_Sans_SC({
  variable: "--font-ui-zh",
  display: "swap",
  weight: ["400", "500", "600", "700"],
});

export const clareadReadingZh = Noto_Serif_SC({
  variable: "--font-reading-zh",
  display: "swap",
  weight: ["400", "500", "600", "700"],
});

export const clareadFontVariables = [
  clareadUiSans.variable,
  clareadReadingSerif.variable,
  clareadUiZh.variable,
  clareadReadingZh.variable,
].join(" ");
