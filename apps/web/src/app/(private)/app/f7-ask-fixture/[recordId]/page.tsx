import { notFound } from "next/navigation";

/**
 * F7 Ask Sidecar Fixture — a stable page-level test entry for F7 e2e 验收.
 *
 * This page renders ONLY the AiWorkspacePanel in isolation, without the Plate
 * editor / FloatingToolbar / ReaderRecordPlateSurface. It exists so that the
 * Ask article_rag sidecar integration (F6) can be verified at the page level
 * even while ReaderRecordPlateSurface is being refactored.
 *
 * Routing: /app/f7-ask-fixture/{recordId}
 *
 * Scope:
 *   - Renders the REAL AiWorkspacePanel (no fake / no fallback).
 *   - Uses the SAME BFF contracts as /app/reader-record/{recordId}
 *     (/api/web/reader-ask/*). Mocks live in tests/e2e.
 *   - Does NOT render ReaderRecordPlateSurface, Plate editor, or the
 *     selection-toolbar → AIMenu flow. Those are verified separately.
 *   - Clearly named "f7-ask-fixture" so it is not mistaken for a production
 *     route. Keep this route out of any navigation UI / sitemap.
 *
 * Test entry point for Scenario 5 in reader-orchestration-flow.spec.ts.
 */

import { F7AskSidecarFixtureClient } from "./F7AskSidecarFixtureClient";

type RouteParams = { recordId: string };
type RouteParamsInput = RouteParams | Promise<RouteParams>;

export default async function F7AskSidecarFixturePage({
  params,
}: {
  params: RouteParamsInput;
}) {
  if (process.env.NODE_ENV === "production") {
    notFound();
  }

  const { recordId } = await params;
  return <F7AskSidecarFixtureClient recordId={recordId} />;
}
