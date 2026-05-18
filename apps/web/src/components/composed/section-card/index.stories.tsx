import type { Meta } from "@ladle/react";
import { MessageSquare } from "lucide-react";
import { SectionCard } from ".";

export default {
  title: "Composed/SectionCard",
} satisfies Meta;

export const Default = () => (
  <SectionCard title="反馈" icon={MessageSquare} description="把不顺手的地方留在这里。">
    <div className="rounded-note border border-hairline bg-reader-paper px-4 py-5 text-sm text-muted">
      Form placeholder
    </div>
  </SectionCard>
);
