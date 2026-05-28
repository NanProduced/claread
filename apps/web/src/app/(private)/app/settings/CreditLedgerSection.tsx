"use client";

import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";

import { CreditLedgerPanel } from "./CreditLedgerPanel";

export function CreditLedgerSection() {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="mt-4 border-t border-hairline pt-4">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between text-sm font-semibold text-ink"
      >
        <span>积分明细</span>
        {expanded ? (
          <ChevronUp className="h-4 w-4 text-muted" />
        ) : (
          <ChevronDown className="h-4 w-4 text-muted" />
        )}
      </button>
      {expanded ? <CreditLedgerPanel /> : null}
    </div>
  );
}
