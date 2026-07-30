import { Library, Newspaper, Plus, Settings, BookMarked } from "lucide-react";
import type { SettingsSection } from "@/components/settings/settings-dialog-history";
import {
  appReadRoute,
  appLibraryRoute,
  appVocabularyRoute,
  dailyRoute,
} from "@/lib/routes";
import type { CommandPaletteCommand } from "./command-palette-types";

export function getPageCommands(
  navigate: (href: string) => void,
  openSettings: (section?: SettingsSection) => void,
): CommandPaletteCommand[] {
  return [
    {
      id: "page-read",
      label: "新解读",
      icon: Plus,
      group: "pages",
      onSelect: () => navigate(appReadRoute),
    },
    {
      id: "page-daily",
      label: "每日精读",
      icon: Newspaper,
      group: "pages",
      onSelect: () => navigate(dailyRoute),
    },
    {
      id: "page-library",
      label: "阅读记录",
      icon: Library,
      group: "pages",
      onSelect: () => navigate(appLibraryRoute),
    },
    {
      id: "page-vocabulary",
      label: "生词本",
      icon: BookMarked,
      group: "pages",
      onSelect: () => navigate(appVocabularyRoute),
    },
    {
      id: "page-settings",
      label: "设置",
      icon: Settings,
      group: "pages",
      onSelect: () => openSettings("preferences"),
    },
  ];
}

export function getCommandCommands(
  navigate: (href: string) => void,
  lastReaderUrl?: string,
): CommandPaletteCommand[] {
  return [
    {
      id: "cmd-open-recent",
      label: "打开最近文章",
      group: "commands",
      disabled: !lastReaderUrl,
      onSelect: () => {
        if (lastReaderUrl) {
          navigate(lastReaderUrl);
        }
      },
    },
  ];
}
