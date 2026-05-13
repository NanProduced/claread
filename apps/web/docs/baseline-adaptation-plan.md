# Web Baseline Adaptation Plan

> **Status**: `CURRENT` | **Last updated**: 2026-05-14

This document defines the first Web milestone: match the verified miniprogram MVP business baseline through the shared FastAPI backend, while giving Web a production-grade information architecture and interaction model.

## Goal

Web first needs a real, debuggable baseline, not a polished final redesign. The first version should:

- Use real FastAPI data by default in local development.
- Keep Web behind Next.js BFF / RSC, never direct browser-to-FastAPI DTO consumption.
- Match the miniprogram MVP capabilities before adding Web-only features.
- Replace user-visible mock data with authenticated, empty, loading, error, and unavailable states.
- Follow Claread's editorial reading language: paper surface, semantic marks, light marginalia, quiet app shell.

## Miniprogram Baseline To Match

The current miniprogram MVP has these verified flows:

| Capability | Miniprogram entry | Backend dependency | Web baseline target |
| --- | --- | --- | --- |
| Input and analysis | `pages/input` -> result | `POST /analysis-tasks`, `GET /analysis-tasks/{id}`, `GET /records/{id}` | `/read` submits text, polls task, opens `/reader/[recordId]` |
| Reader result | `pages/result` | `render_scene_json` from records | `/reader/[recordId]` renders paragraphs, sentences, translations, marks, sentence entries, warnings |
| Dictionary lookup | `WordPopup` | `GET /dict`, `GET /dict/entry` | Reader mark/word click opens Web popover or mobile sheet |
| History | `packageA/history` | `GET /records`, favorites state | `/library` lists cloud records, opens Reader |
| Vocabulary | `packageA/vocab` | `GET/POST/PATCH/DELETE /vocabulary` | `/vocabulary` lists real words and source context |
| Review | `packageA/vocab-review` | `GET /vocabulary/review/due`, `POST /vocabulary/{id}/review` | `/review` runs a simple review queue |
| Profile and quota | `packageA/profile`, credit detail | `GET /auth/session/me`, `GET /me/quota`, `GET /me/credit/ledger` | `/settings` shows session, quota, and reading preferences |
| Favorites and excerpts | result actions, `packageA/excerpts` | `/favorites`, `/user-annotations` | Reader supports save/favorite/note after core read loop |
| Feedback | result/page feedback | `POST /feedback`, `GET /feedback` | Reader and settings expose feedback after core assets |

Daily Reader exists in the miniprogram, but Web baseline can treat it as a later slice after the user-input reading loop is stable.

## Product Routes

The Web product URLs should be semantic from the start. Because this is still a development baseline, discarded IA should be removed instead of kept as compatibility redirects.

| Product route | Purpose |
| --- | --- |
| `/read` | Paste/import text and submit analysis |
| `/reader/[recordId]` | Reading result |
| `/library` | Records/history library |
| `/vocabulary` | Vocabulary asset list |
| `/review` | Vocabulary review |
| `/settings` | Account, quota, preferences |
| `/login` | Phone login |
| `/share/[shareId]` | Public share |
| `/export/[recordId]` | Later artifact/export studio |

Implementation rule: route groups may still use `(app)`, but the URL and source tree should not preserve rejected product paths.

## Baseline UI Rules

This version does not need final visual polish, but it must not feel like a demo:

- The Reader must have a stable paper surface and 65-75ch line length.
- Engineering diagnostics like `mock-fallback` or `FastAPI Render Scene` should become low-priority debug text or disappear from user-facing surfaces.
- Empty, error, loading, unauthenticated, insufficient quota, active task conflict, and degraded analysis states must be explicit.
- Lists should be quiet asset lists, not dashboard card grids.
- Reader marks need semantic meaning and accessible interaction. Color is not the only signal.
- Desktop dictionary uses anchored popover or side detail. Mobile can use a bottom sheet.
- Web should adapt miniprogram long-press/tap patterns to mouse click, hover, selection toolbar, keyboard focus, and URL state.

## Mock Removal Policy

User-visible mock data should be removed from the main product path in this order:

1. `/read`, `/library`, `/reader`: replace mock fallback with real empty/error/unauthenticated states. Keep `/reader/demo-record` only as a development preview.
2. `/settings`: replace mock quota with `/me/quota` and session projection.
3. `/vocabulary`: replace mock vocabulary with `/vocabulary`.
4. `/review`: add real due queue and submit review.
5. Move remaining fixture data out of `src/lib/mock-data.ts` into a clearly dev-only fixture location if still needed.

## Implementation Batches

### Batch 1: IA And P0 Data

- Add semantic routes and remove rejected route prefixes instead of adding compatibility redirects.
- Update Web BFF analysis `readerUrl` to `/reader/[cloudRecordId]`.
- Connect `/settings` to `/auth/session/me` and `/me/quota`.
- Add dictionary API/BFF and a basic Reader lookup interaction.

### Batch 2: Vocabulary And Review

- Connect `/vocabulary` to `GET /vocabulary`.
- Add Reader "save to vocabulary" for dictionary/inline marks.
- Add `/review` using `GET /vocabulary/review/due` and `POST /vocabulary/{id}/review`.
- Remove vocabulary mock from user-visible pages.

### Batch 3: User Assets

- Add favorites for records/sentences/paragraphs.
- Add annotations with `text_range` support.
- Add feedback entry points for Reader and settings.
- Add excerpts/library enhancements only after Reader and vocabulary are stable.

### Batch 4: Web-Specific Enhancements

- Reader density controls and preferences.
- Grammar X-Ray schema proposal and high-fidelity view.
- Share page and export/artifact studio.
- Search/filter in library and vocabulary.

## Verification

Every batch must run:

```powershell
pnpm --filter @claread/web typecheck
pnpm --filter @claread/web lint
pnpm --filter @claread/web build
```

When a batch touches FastAPI contracts, also run targeted backend tests. Browser smoke should cover:

- `/read`
- Login with local mock code `888888`
- Submit text and open `/reader/[recordId]`
- `/library`
- `/settings`
- `/vocabulary` once connected
