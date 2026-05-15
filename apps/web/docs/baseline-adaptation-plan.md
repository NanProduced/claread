# Web Baseline Adaptation Plan

> **Status**: `CURRENT` | **Last updated**: 2026-05-15

This document defines the first Web milestone: match the verified miniprogram MVP business baseline through the shared FastAPI backend, while giving Web a production-grade information architecture and interaction model.

## Goal

Web first needs a real, debuggable baseline, not a polished final redesign. The first version should:

- Use real FastAPI data by default in local development.
- Keep Web behind Next.js BFF / RSC, never direct browser-to-FastAPI DTO consumption.
- Match the miniprogram MVP capabilities before adding Web-only features.
- Replace user-visible mock data with authenticated, empty, loading, error, and unavailable states.
- Follow Claread's editorial reading language: paper surface, semantic marks, original-text canvas, permanent dictionary, quiet app shell.

## Miniprogram Baseline To Match

The current miniprogram MVP has these verified flows:

| Capability | Miniprogram entry | Backend dependency | Web baseline target |
| --- | --- | --- | --- |
| Input and analysis | `pages/input` -> result | `POST /analysis-tasks`, `GET /analysis-tasks/{id}`, `GET /records/{id}` | `/read` submits text, polls task, opens `/reader/[recordId]` |
| Reader result | `pages/result` | `render_scene_json` from records | `/reader/[recordId]` renders paragraphs, sentences, translations, marks, sentence entries, warnings |
| Dictionary lookup | `WordPopup` | `GET /dict`, `GET /dict/entry` | Reader mark/word click shows inline light preview and updates the permanent dictionary panel |
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
- Engineering diagnostics such as upstream source names or adapter states should not appear on user-facing surfaces.
- Empty, error, loading, unauthenticated, insufficient quota, active task conflict, and degraded analysis states must be explicit.
- Lists should be quiet asset lists, not dashboard card grids.
- Reader marks need semantic meaning and accessible interaction. Color is not the only signal.
- Desktop dictionary uses inline light preview plus a permanent left dictionary panel. Mobile can use a bottom sheet.
- Web should adapt miniprogram long-press/tap patterns to mouse click, hover, selection toolbar, keyboard focus, and URL state.

## Mock Removal Policy

User-visible mock data should be removed from the main product path in this order:

1. `/read`, `/library`, `/reader`: replace mock fallback with real empty/error/unauthenticated states. `DONE`
2. `/settings`: replace mock quota with `/me/quota` and session projection. `DONE`
3. `/vocabulary`: replace mock vocabulary with `/vocabulary`. `DONE`
4. `/review`: add real due queue and submit review. `DONE`
5. Remove fixture data rather than carrying a dev-only mock path. `DONE`

## Implementation Batches

### Batch 1: IA And P0 Data

- Add semantic routes and remove rejected route prefixes instead of adding compatibility redirects.
- Update Web BFF analysis `readerUrl` to `/reader/[cloudRecordId]`.
- Connect `/settings` to `/auth/session/me` and `/me/quota`.
- Add dictionary API/BFF and a basic Reader lookup interaction. `DONE`

### Batch 2: Vocabulary And Review

- Connect `/vocabulary` to `GET /vocabulary`. `DONE`
- Add Reader "save to vocabulary" for dictionary/inline marks. `DONE`
- Add `/review` using `GET /vocabulary/review/due` and `POST /vocabulary/{id}/review`. `DONE`
- Remove vocabulary mock from user-visible pages. `DONE`

### Batch 3: User Assets

- Add favorites for records. `DONE`
- Add record deletion in Library through Web BFF. `DONE`
- Add annotations with `text_range` support.
  - 2026-05-14 review: Web Reader can safely create sentence-anchored highlight/note records through `/user-annotations`; true Web text selection is still blocked because the current Reader does not project DOM selections back to canonical sentence offsets, occurrence indexes, and text hash validation. Do not send `text_range` from Web until that mapping exists.
- Add feedback entry points for settings. `DONE`
- Add Reader dictionary/analysis feedback entry points after the Reader interaction model is reviewed.
- Add excerpts/library enhancements only after Reader and vocabulary are stable.

## Follow-Up Review Items

- Daily Reader response fields `body` / `highlights` / `paragraph_notes` / `takeaways` are still wide `dict` payloads. Web should wait for structured Pydantic models before adding a production Daily Reader view.
- `GET /records` needs Web search/filter parameters before the Library is promoted beyond baseline validation.
- `GET /favorites` has no pagination/filter parameters; record favorite state currently requires scanning the user's full favorites list.
- Feedback scope/type constants are duplicated between FastAPI and Web. Move them to generated contracts or a shared package before adding more feedback surfaces.
- Web text selection annotations need a canonical mapping from DOM selection to sentence offsets, occurrence indexes, and text hash before enabling `text_range`.

### Batch 4: Web-Specific Enhancements

- Reader density controls and preferences.
- Grammar X-Ray schema proposal and high-fidelity view are future Web enhancement topics; the baseline adapts existing `grammar_note` and `sentence_analysis` only.
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
