# C5A Web Hygiene — Consumer Audit Manifest (Phase L)

- Worktree: `C:\tmp\claread-arch-opt-c5a-web-hygiene`
- Branch: `codex/arch-opt-c5a-web-hygiene`
- Base: `reader-agentic-orchestration @ a99e021763ce0c9cc96ad1293040767eeb2341b0`
- Ownership: `apps/web/**`, `pnpm-lock.yaml` (dependency deletions only)

Method: every item judged by consumer proof, never by name. Four-sided check
for dependencies (imports / package scripts / config / build); repo-wide
import graph for barrels. `knip` was run once as a lead generator; every knip
claim below was re-verified by hand (knip produced false positives, e.g.
`FavoriteButton.tsx` which is imported by `ReaderRecordPlateSurface.tsx`).

## DELETE (triple-proven, executed in the physical commit)

| Item | Proof |
|---|---|
| `msw` (devDependency) | Zero imports repo-wide (`from "msw"` / `require("msw")` = 0), no setup file, no package script, no config, no CI (`.github/` absent), no Playwright/Vitest reference. Sole occurrence in the repo is the `package.json` entry itself. |
| `knip` (devDependency) | No package script invokes it, no `knip.json`/config anywhere, no CI, not importable as a runtime dep. Its only consumption was the one-shot audit sweep of this round. |
| `@platejs/ai` (dependency) | Zero references anywhere in `apps/web` outside `package.json`/lockfile. Lockfile importers section: only `apps/web` imports it. Not referenced by any kit/plugin config. |
| `@platejs/suggestion` (dependency) | Same as above; additionally its only other lockfile edge is from `@platejs/ai` (itself being removed). |
| `src/lib/reader-plate/bridges/selection/**` (4 files, 666 LOC: `index.ts`, `read-plate-reader-selection.ts`, `selection-toolbar-rect.ts`, `read-plate-reader-selection.test.tsx`) | Exported symbols `readPlateReaderSelection` / `selectionToolbarRectForReaderSelection`: zero live consumers repo-wide. The ONLY external mention is the negative source guard in `ReaderRecordPlateSurface.test.tsx` (`expect(source).not.toMatch(/readPlateReaderSelection/)`). Sole reachability is the root barrel re-export line `export * from "./bridges/selection";` (removed with it). Its own test exercises the retired `ReaderMockVm` type. |
| `src/components/reader/dictionary/index.ts` | Zero consumers of the barrel path. All live dictionary usage imports direct files: `ReaderRecordPlateSurface.tsx` imports `../dictionary/ReaderQuickPeek`, `../dictionary/ReaderDictionaryRail`, `../dictionary/contracts`, `../dictionary/shared`. The only other `"../dictionary"` import in the repo resolves to `reader-plate/bridges/dictionary` (different module, alive). |

## KEEP (candidates proven alive)

| Item | Proof |
|---|---|
| `ftfy`, `langdetect` | Not JavaScript dependencies; owned by `services/api/pyproject.toml` (Python). Outside C5A ownership — no action. |
| `reader-plate/model/index.ts` | Live internal barrel consumers: `bridges/ask/adapters.ts`, `bridges/ask/types.ts` (`"../../model"`), `projection/render-scene-to-plate-document.ts` (`"../model"`). |
| `reader-plate/primitives/index.ts` | Heavy live internal consumers across `bridges/{ask,assets,dictionary,jump,selection}` (`"../../primitives"`). |
| `reader-plate/bridges/jump/index.ts` | Live internal consumers: `bridges/ask/adapters.ts`, `bridges/ask/types.ts` (`"../jump"`); ask bridge is externally consumed. |
| `reader-plate/bridges/{ask,assets,dictionary}/index.ts` | External consumers: `selection-slots.ts`, `ReaderRecordPlateSurface.tsx`, `HeroAppStage.tsx`, `hero-lookups.ts`. |
| `reader-plate/index.ts` (root barrel), `projection/index.ts` | Many external consumers (`@/lib/reader-plate`); `renderSceneToPlateDocument` used by `HeroAppStage.tsx`. |
| All component barrels (`composed`, `layout`, `primitives`, `reader/plate`, `plate-ui-adapter`, `reader/settings`, `shortcuts`, `command-palette`) | All have live path consumers. |
| `shiki` | Imported by `src/components/ai-elements/code-block.tsx`; that file sits in the deferred ai-elements cluster (see UNKNOWN), so `shiki` stays. |
| `@eslint/eslintrc` | See UNKNOWN — lint is not a C5A gate, removal risk not provable this round. |
| framer-motion / motion, radix-ui / @radix-ui/* | Both tracks alive; see dual-track facts. |
| Entire API↔BFF chain (`src/services/api/**`, `src/services/bff/**`, `src/app/api/web/**`) | See deletion-test conclusion. |

## UNKNOWN (deferred — evidence recorded, no action this round)

| Item | Evidence / reason deferred |
|---|---|
| `@eslint/eslintrc` (devDependency) | Not imported by `eslint.config.mjs`, not declared by `eslint-config-next`. But lint is not in the C5A gate set; removing an eslint-adjacent dep without a lint gate risks silent breakage. |
| `src/services/api/tasks.ts` + `src/types/api/tasks.ts` | Zero importers (knip + grep confirmed). Dead remnant of the retired analysis-task era (`analysis-tasks` is explicitly negative-guarded in `ReaderRecordPlateSurface.test.tsx`). Deferred because this round freezes the API↔BFF chain (audit-only). |
| ai-elements unused cluster: `code-block.tsx`, `tool.tsx`, `confirmation.tsx`, `plan.tsx`, `sources.tsx` | Zero external imports found (the `code-block` grep hits were `@platejs/code-block` and comments). Adjacent to KEEP-zone `AiWorkspacePanel`; dynamic-import risk not excluded. Deleting would also cascade to `shiki` and possibly `ui/alert.tsx`/`ui/card.tsx`/`primitives/alert.tsx`. |
| knip-flagged page/feature components: `EditorialTagList`, `ReaderAnnotations`, `CreditLedgerPanel`, `ProductAnnotations`, `HeroDeviceShowcase`, `ReaderContextPanel`, `ReaderGlobalFeedbackPrompt`, `ArticleRagCitationList`, `reader-anchors.ts` | No static path imports found, but dynamic/lazy usage not excluded; several sit in KEEP zones (marketing surface, Plate surface adjacency). |
| `src/lib/product-page/reader-demo-scenes.ts` | knip-flagged but inside the KEEP ProductReaderDemo/Hero marketing chain — not touched. |
| framer-motion → motion single-track | Would require editing KEEP marketing surfaces; zero-behavioral-diff not provable. |
| radix-ui ↔ @radix-ui/* single-track | Both tracks alive across `components/ui` and reader/editor internals; zero-diff not provable. |

## Dual-track facts (report-only)

- `framer-motion`: 3 files, all KEEP marketing surfaces (`GoalReaderCropPreview`, `ProductCoreFeatures`, `ProductReaderDemo`).
- `motion`: 6 files (`ai-elements/shimmer`, `HeroScrollScene`, `ProductAnnotations`, `ProductStickerWall`, `ui/highlighter`, `ui/macbook-scroll`).
- `radix-ui` (unified): 10 `components/ui/*` shadcn-style files.
- `@radix-ui/react-*` (scoped): 19 import sites (popover ×4, use-controllable-state ×3, slot ×2, scroll-area ×2, dialog ×2, tooltip/toolbar/tabs/switch/dropdown-menu/alert-dialog ×1).

## API↔BFF deletion-test conclusion (audit-only, chain frozen)

Hypothetical deletion/inline of the API↔BFF layer was evaluated against:

1. **Transport semantics** — `fastApiFetch` (`src/services/api/upstream.ts`, `server-only`) centralizes base-URL resolution (`CLAREAD_FASTAPI_BASE_URL` → `CLAREAD_API_BASE_URL` → `http://127.0.0.1:8000`), JSON accept/content-type defaults, `cache: "no-store"` default, Bearer injection, and a discriminated `UpstreamResult` envelope. The `payload` vs `body` split is deliberate: `body` carries the typed upstream error object consumed by BFF adapters (e.g. S4 candidate-recovery conflict resolution). Fan-in: 30 non-test files import `services/api/*`.
2. **Auth/session/cookie** — `getWebSession` (`services/bff/session.ts`) is the single cookie→session projection (`claread_web_session`, `claread_web_phone`, `claread_phone_login_challenge`) plus non-production debug-env and mock-phone paths. Tokens flow route → bff → api as Bearer; cookies never reach client layers.
3. **Error/domain mapping** — BFF adapters translate `UpstreamResult` into UI domain shapes; mappings are pinned by dedicated tests (`reader-ask.retry/reconcile`, `reading-records`, `reader-plate`, `reading-record-user-assets`, `profile`). Fan-in: 74 non-test files import `services/bff/*`.
4. **Cache/retry** — transport is `no-store` by default; retry lives in the ask layer (retry routes + `reader-ask.retry-path` tests), not in transport.

**Conclusion: KEEP the chain.** 40+ route handlers share the envelope/session/mapping behavior; inlining would duplicate it and is explicitly out of scope this round (no fan-in=1 inlining). Dead members inside the chain (`tasks.ts`, `types/api/tasks.ts`) are recorded as UNKNOWN, deferred to a chain-unfreeze round.
