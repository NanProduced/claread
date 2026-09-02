# Web Auth Routing

> **Status**: `CURRENT` | **Last updated**: 2026-08-08

This document defines Claread Web's route boundary and login redirect contract.

## Route Classes

### Public Routes

These routes are publicly reachable and do not require a Web session.

| Route | Meaning |
| --- | --- |
| `/` | Claread public home |
| `/about` | Public product/about placeholder |
| `/help` | Public help placeholder |
| `/blog` | Public blog placeholder |
| `/daily` | Public Daily Reader index |
| `/daily/:articleId` | Public Daily Reader article |
| `/share/:shareId` | Public share page |

Public reading is allowed. Saving, adding vocabulary, writing personal notes, and other asset actions still require login.

### Auth Route

| Route | Meaning |
| --- | --- |
| `/login` | Explicit Web email login / registration entry |

`/login` is not part of the app shell. It is a dedicated auth boundary.

The first screen requires an explicit `login` or `register` intent. Login proceeds directly to email/password submission and never calls `/api/web/auth/email/start`; registration calls `start` to create an email OTP challenge. Password reset has its own request and completion path. The browser can enter set-password or reset only after OTP verification succeeds.

All browser auth calls stay on the same-origin Next.js BFF. Challenge IDs, tickets, purposes, and session tokens are held in HttpOnly cookies or server-only upstream calls; ordinary browser JSON and auth logs do not expose them. Terms and Privacy remain navigable from the login surface. A successful password reset stays on its confirmation screen until the user presses the fixed `/app/read` action.

### Private App Routes

These routes are the authenticated Claread workspace. They are intercepted by `proxy.ts` before page render when no Web session is present.

| Route | Meaning |
| --- | --- |
| `/app` | Private app entry, redirects to `/app/read` |
| `/app/read` | Analysis submission and recent reading entry |
| `/app/library` | User reading records |
| `/app/vocabulary` | User vocabulary assets |
| `/app/review` | Review queue entered from Vocabulary |
| `/app/settings` | Account, quota, feedback, and preferences |
| `/app/settings/feedback` | Feedback records |
| `/app/settings/ledger` | Credit ledger |
| `/app/reader/:recordId` | Private Reader route |

Protected pages must not render anonymous empty states. Missing sessions redirect to `/login?next=<path>`.

## Redirect Contract

`next` and `intent` are constrained inputs, not arbitrary navigation commands.

- `next` only accepts same-origin paths from an explicit allowlist: public Claread routes, share routes, and `/app/*`.
- `intent` only accepts known values. v1 uses `save`.
- External URLs, protocol-relative URLs, control characters, and unknown intents are ignored.

Examples:

```text
/app/read -> /login?next=/app/read
/app/reader/abc -> /login?next=/app/reader/abc
/daily/d-20260514 save action -> /login?next=/daily/d-20260514&intent=save
```

## Session Projection

The Web session contract exposes exactly three product states:

- `signed_out`
- `signed_in`
- `limited_debug`

`limited_debug` is a development-only constrained state created only by an explicitly injected `CLAREAD_WEB_DEBUG_SESSION_TOKEN`. It is independent of all login providers, is not treated as a fully signed-in personal account, and UI must describe it as limited. A `signed_out` request never falls back into this state.

## Backend Boundary

`proxy.ts` is only an early browser guard. It does not replace BFF or upstream authorization. Web BFF handlers must continue to validate session state before touching user records, vocabulary, feedback, annotations, favorites, review, or reader orchestration data.

`OWNER_DECISION_REQUIRED`: production HTTPS, same-origin BFF deployment, and trusted reverse-proxy/IP handling must be confirmed together before launch.
