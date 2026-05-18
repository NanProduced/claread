import { Inter, Newsreader } from "next/font/google";

const clareadUiSans = Inter({
  subsets: ["latin"],
  variable: "--font-ui-en",
  display: "swap",
});

const clareadReadingSerif = Newsreader({
  subsets: ["latin"],
  variable: "--font-reading-en",
  display: "swap",
});

export const clareadFontVariables = [
  clareadUiSans.variable,
  clareadReadingSerif.variable,
].join(" ");
