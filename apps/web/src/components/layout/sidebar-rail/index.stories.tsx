import type { Meta } from "@ladle/react";
import { useState } from "react";
import { SidebarRail } from ".";

export default {
  title: "Layout/SidebarRail",
} satisfies Meta;

export const Default = () => {
  const [collapsed, setCollapsed] = useState(false);
  return (
    <div className="min-h-[740px] bg-web-canvas">
      <SidebarRail pathname="/library" collapsed={collapsed} onToggle={() => setCollapsed((value) => !value)} />
      <div className={collapsed ? "pl-[84px]" : "pl-[232px]"} />
    </div>
  );
};
