# Web Auth Routing

> **Status**: `CURRENT` | **Last updated**: 2026-05-25

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
| `/examples/:slug` | Public annotated examples |
| `/share/:shareId` | Public share page |

Public reading is allowed. Saving, adding vocabulary, writing personal notes, and other asset actions still require login.

### Auth Route

| Route | Meaning |
| --- | --- |
| `/login` | Web phone login entry |

`/login` is not part of the app shell. It is a dedicated auth boundary.

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

`limited_debug` is a development-only constrained state. It is not treated as a fully signed-in personal account, and UI must describe it as limited.

## Backend Boundary

`proxy.ts` is only an early browser guard. It does not replace BFF or upstream authorization. Web BFF handlers must continue to validate session state before touching user records, vocabulary, feedback, annotations, favorites, review, or analysis tasks.
