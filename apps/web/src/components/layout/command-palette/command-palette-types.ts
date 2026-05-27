import type { LucideIcon } from "lucide-react";

export type CommandGroup = "pages" | "recent" | "search" | "commands";

export interface CommandPaletteCommand {
  id: string;
  label: string;
  icon?: LucideIcon;
  shortcut?: string;
  onSelect: () => void;
  group: CommandGroup;
  disabled?: boolean;
}

export interface CommandPaletteRecordItem {
  id: string;
  title: string;
  excerpt: string;
  createdAt: string;
}
