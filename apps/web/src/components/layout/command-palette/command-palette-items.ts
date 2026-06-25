import { Library, Plus, Settings, BookMarked } from "lucide-react";
import {
  appReadRoute,
  appLibraryRoute,
  appVocabularyRoute,
  appSettingsRoute,
} from "@/lib/routes";
import type { CommandPaletteCommand } from "./command-palette-types";

export function getPageCommands(
  navigate: (href: string) => void,
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
      onSelect: () => navigate(appSettingsRoute),
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
