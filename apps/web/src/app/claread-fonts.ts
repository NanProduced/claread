import { IBM_Plex_Mono, Inter, Newsreader, Source_Serif_4 } from "next/font/google";

const clareadUiSans = Inter({
  subsets: ["latin"],
  variable: "--font-ui-en",
  display: "swap",
});

const clareadReadingSerif = Source_Serif_4({
  subsets: ["latin"],
  variable: "--font-reading-en",
  display: "swap",
});

const clareadDailyReadingSerif = Newsreader({
  subsets: ["latin"],
  variable: "--font-daily-reading-en",
  display: "swap",
});

const clareadMono = IBM_Plex_Mono({
  weight: ["400", "500"],
  subsets: ["latin"],
  variable: "--font-mono-en",
  display: "swap",
});

export const clareadFontVariables = [
  clareadUiSans.variable,
  clareadReadingSerif.variable,
  clareadDailyReadingSerif.variable,
  clareadMono.variable,
].join(" ");
