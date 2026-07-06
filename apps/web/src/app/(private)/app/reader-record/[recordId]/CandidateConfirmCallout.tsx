"use client";

import { useEffect, useState } from "react";
import { CandidateConfirmDialog } from "../../read/CandidateConfirmDialog";
import {
  readPendingCandidate,
  type PendingCandidate,
} from "../../read/pending-candidate";

interface CandidateConfirmCalloutProps {
  recordId: string;
}

type FallbackState =
  | { kind: "idle" }
  | { kind: "ready"; candidate: PendingCandidate; open: boolean };

function isMatchingCandidate(
  candidate: PendingCandidate | null,
  recordId: string,
): boolean {
  if (!candidate) return false;
  return candidate.readingRecordId === recordId;
}

// Avoid `next/navigation` here: the reader-record tests stub window.location,
// and the fallback only needs runtime-native refresh / navigation behavior.
function refreshPage() {
  if (typeof window !== "undefined") {
    window.location.reload();
  }
}

function pushTo(path: string) {
  if (typeof window !== "undefined") {
    window.location.assign(path);
  }
}

export function CandidateConfirmCallout({ recordId }: CandidateConfirmCalloutProps) {
  const [state, setState] = useState<FallbackState>({ kind: "idle" });

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const pending = readPendingCandidate();
      if (pending && isMatchingCandidate(pending, recordId)) {
        setState({ kind: "ready", candidate: pending, open: true });
      } else {
        setState({ kind: "idle" });
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [recordId]);

  if (state.kind === "idle") return null;

  return (
    <CandidateConfirmDialog
      candidate={state.candidate}
      open={state.open}
      onOpenChange={(open) => {
        setState({ kind: "ready", candidate: state.candidate, open });
      }}
      onConfirmed={refreshPage}
      onRestart={() => pushTo("/app/read")}
      onRefresh={refreshPage}
    />
  );
}
