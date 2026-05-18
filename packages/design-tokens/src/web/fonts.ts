export const clareadFontVariableNames = {
  uiEn: "--font-ui-en",
  readingEn: "--font-reading-en",
  uiZh: "--font-ui-zh",
  readingZh: "--font-reading-zh",
} as const;

export const clareadFontStacks = {
  uiEn: ["Inter", "system-ui", "sans-serif"],
  readingEn: ["Newsreader", "Georgia", "Times New Roman", "serif"],
  uiZh: ["PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", "sans-serif"],
  readingZh: ["Source Han Serif SC", "Songti SC", "STSong", "Noto Serif SC", "serif"],
} as const;
