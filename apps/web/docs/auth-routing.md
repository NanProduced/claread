# Web Auth Routing

> **Status**: `CURRENT` | **Last updated**: 2026-05-14

This document records the Web route boundary for Claread v1. It is a product and implementation contract, not a temporary tracker.

## Route Classes

### Protected Personal Routes

These routes represent a user's private reading workspace and assets. They must be intercepted by Next.js `proxy.ts` before page rendering when no Web session is present.

| Route | Meaning |
| --- | --- |
| `/read` | Authenticated article canvas and analysis submission |
| `/library` | User reading records |
| `/vocabulary` | User vocabulary assets |
| `/review` | Review queue entered from Vocabulary |
| `/settings` | Account, quota, feedback, and preferences |
| `/reader/:id` | Current Web private reader route |
| `/reader/r/:id` | Future private reader route |

Protected pages should not render anonymous empty states. If a session is missing, the user is redirected to `/login?next=<path>`.

### Public Content Routes

These routes are public product samples and editorial content. They do not require login and should be suitable for SSG or ISR once the content source is finalized.

| Route | Meaning |
| --- | --- |
| `/daily` | Public Daily Reader entry and archive |
| `/daily/:date` | Public Daily Reader article |
| `/examples/:slug` | Public long-lived examples |

Public reading is allowed, but asset actions such as saving, favoriting, adding vocabulary, or adding personal notes must require login.

### Token-Gated Share Routes

`/share/:token` is the only anonymous route for user-generated reading output. It is read-only and authenticated by the share token, not by the user's Web session.

`/reader/r/:id` is strictly private. Even if an ID is sent to another person, anonymous access must not work. Sharing must be generated through `/share/:token`.

## Redirect Contract

`next` and `intent` are constrained inputs, not arbitrary navigation commands.

- `next` only accepts same-origin paths from an explicit allowlist: protected app routes, public Daily Reader routes, public example routes, and token share routes.
- `intent` only accepts known values. v1 uses `save` for public content actions that should continue after login.
- External URLs, protocol-relative URLs, control characters, and unknown intents must be ignored.

Examples:

```text
/read -> /login?next=/read
/reader/r/abc -> /login?next=/reader/r/abc
/daily/2026-05-14 save action -> /login?next=/daily/2026-05-14&intent=save
```

## Backend Boundary

The proxy is an early route guard for the browser experience. It does not replace BFF or FastAPI authorization. BFF route handlers must continue to validate the session before accessing user records, vocabulary, feedback, annotations, favorites, review, or analysis tasks.

Daily Reader schema, public example content storage, and SSG/ISR publishing cadence are backend/architecture review items. UI work should not expand those decisions implicitly.
