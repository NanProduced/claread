import type { GlobalProvider } from "@ladle/react";
import "@claread/design-tokens/web/tokens.css";
import "@/app/globals.css";
import { TooltipProvider } from "@/components/primitives/tooltip";
import { ClareadToaster } from "@/components/primitives/toast";

export const Provider: GlobalProvider = ({ children }) => {
  return (
    <TooltipProvider>
      {children}
      <ClareadToaster />
    </TooltipProvider>
  );
};
