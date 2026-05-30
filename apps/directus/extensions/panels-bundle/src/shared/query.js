export function flattenQuery(prefix, value, target) {
  if (value == null) return;

  if (Array.isArray(value)) {
    for (const child of value) {
      flattenQuery(`${prefix}[]`, child, target);
    }
    return;
  }

  if (typeof value === "object") {
    for (const [key, child] of Object.entries(value)) {
      flattenQuery(prefix ? `${prefix}[${key}]` : key, child, target);
    }
    return;
  }

  target.push([prefix, String(value)]);
}

export function buildQueryString(query) {
  const entries = [];
  flattenQuery("", query, entries);
  const params = new URLSearchParams();

  for (const [key, value] of entries) {
    params.append(key, value);
  }

  return params.toString();
}

export async function fetchJson(path) {
  const response = await fetch(path, {
    credentials: "include",
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }

  const payload = await response.json();
  return payload?.data ?? payload ?? null;
}

export function buildItemsPath(collection, query) {
  return `/items/${collection}?${buildQueryString(query)}`;
}
