import { NextResponse } from "next/server";

import { getSettingsDialogProjection } from "@/services/bff/profile";

/**
 * GET /api/web/settings-dialog
 *
 * Returns the minimal, lazy-loaded Settings Dialog data projection:
 * `{ ok: true, data: SettingsDialogData }` on success.
 *
 * The projection reuses the existing session + `getUpstreamSessionMe`
 * upstream path and the same status semantics used by Settings, but
 * deliberately does NOT call `getUpstreamQuota`. See
 * `getSettingsDialogProjection` for the alignment guarantees with
 * `loadSettingsData()`.
 *
 * Error envelope follows the existing Web API convention
 * (`{ ok: false, status, code, message }`) and always uses fixed
 * Chinese fallback messages — raw upstream error details are never
 * surfaced.
 *
 * `SettingsDialogProjectionResult` is a strict discriminated union:
 * the `ok: true` arm carries a non-null `SettingsDialogData`, so the
 * success branch below accesses `result.data` directly without any
 * nullable tolerance.
 */
export async function GET() {
  const result = await getSettingsDialogProjection();

  if (result.ok) {
    // Success arm: `data` is `SettingsDialogData` (non-null by construction).
    return NextResponse.json(
      { ok: true, data: result.data },
      { status: 200 },
    );
  }

  // Error arm: `data` is null, `message` is a required string.
  const httpStatus = result.httpStatus;
  let code: string;
  if (httpStatus === 401) {
    code = "auth_required";
  } else if (httpStatus === 503) {
    code = "upstream_unavailable";
  } else {
    code = "upstream_error";
  }

  return NextResponse.json(
    {
      ok: false,
      status: httpStatus,
      code,
      message: result.message,
    },
    { status: httpStatus },
  );
}
