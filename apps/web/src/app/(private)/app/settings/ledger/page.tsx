import { redirect } from "next/navigation";

import { appReadRoute } from "@/lib/routes";

/**
 * `/app/settings/ledger` is a retired route — the standalone credit-ledger
 * page no longer exists. The current Settings Dialog "用量与积分" section is
 * a placeholder pending the Agentic orchestration adaptation and does NOT
 * host the ledger UI yet. Any direct navigation to this legacy path is
 * redirected to `/app/read`; users can reopen Settings via the AppShell
 * user menu or Command Palette.
 *
 * Note: do NOT claim the ledger has migrated into the Usage section. The
 * migration target will be wired up once Agentic orchestration lands.
 */
export default function LedgerRedirectPage(): never {
  redirect(appReadRoute);
}
