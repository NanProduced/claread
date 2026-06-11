import type { Route } from "next";

export const homeRoute = "/" as Route;
export const aboutRoute = "/about" as Route;
export const helpRoute = "/help" as Route;
export const blogRoute = "/blog" as Route;
export const dailyRoute = "/daily" as Route;
export const shareDemoRoute = "/share/demo" as Route;

export const loginPath = "/login";
export const loginRouteBase = "/login" as Route;

export const appHomeRoute = "/app" as Route;
export const appReadRoute = "/app/read" as Route;
export const appLibraryRoute = "/app/library" as Route;
export const appVocabularyRoute = "/app/vocabulary" as Route;
export const appReviewRoute = "/app/review" as Route;
export const appSettingsRoute = "/app/settings" as Route;

export const protectedRoutePrefixes = ["/app"] as const;
export const nextAllowlistPrefixes = [
  "/",
  "/about",
  "/help",
  "/blog",
  "/daily",
  "/share",
  "/app",
] as const;
export const intentAllowlist = ["save"] as const;

export type LoginIntent = (typeof intentAllowlist)[number];

export function matchesRoutePrefix(pathname: string, prefixes: readonly string[]) {
  return prefixes.some((prefix) => {
    if (prefix === "/") {
      return pathname === "/";
    }

    return pathname === prefix || pathname.startsWith(`${prefix}/`);
  });
}

export function isAllowedNextPath(pathname: string) {
  return matchesRoutePrefix(pathname, nextAllowlistPrefixes);
}

export function isAllowedIntent(value: string | null): value is LoginIntent {
  return Boolean(value) && intentAllowlist.includes(value as LoginIntent);
}

export function dailyArticleRoute(articleId: string): Route {
  return `/daily/${encodeURIComponent(articleId)}` as Route;
}

export function appReaderRoute(recordId: string): Route {
  return `/app/reader/${encodeURIComponent(recordId)}` as Route;
}

export function loginRoute(nextPath?: string | null, intent?: string | null): Route {
  if (!nextPath && !intent) {
    return loginRouteBase;
  }

  const params = new URLSearchParams();

  if (nextPath && isAllowedNextPath(nextPath)) {
    params.set("next", nextPath);
  }

  if (intent && isAllowedIntent(intent)) {
    params.set("intent", intent);
  }

  return params.size > 0
    ? (`${loginPath}?${params.toString()}` as Route)
    : loginRouteBase;
}
