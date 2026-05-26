"use client";

import { useEffect } from "react";
import { matchShortcut } from "@/lib/shortcuts";
import { useCommandPalette } from "./useCommandPalette";
import { CommandPaletteDialog } from "./CommandPaletteDialog";

export function CommandPaletteProvider() {
  const toggle = useCommandPalette((s) => s.toggle);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (matchShortcut(event, "Primary+K")) {
        event.preventDefault();
        toggle();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [toggle]);

  return <CommandPaletteDialog />;
}
