// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";

import {
  HISTORY_MARKER_KEY,
  type SettingsDialogMarker,
  closeSettingsDialogHistory,
  isOwnedBySettingsDialog,
  openSettingsDialogHistory,
  parseSettingsSection,
  readSettingsDialogMarker,
  replaceSettingsDialogSection,
} from "./settings-dialog-history";

const SECTIONS = ["account", "preferences", "usage", "support"] as const;

function setCurrentHistoryState(state: unknown): void {
  // Replace current entry with the given state object so tests can stage
  // pre-existing host/Next state before each scenario.
  window.history.replaceState(state as Record<string, unknown>, "", location.href);
}

function snapshotUrl(): { pathname: string; search: string; hash: string } {
  return {
    pathname: window.location.pathname,
    search: window.location.search,
    hash: window.location.hash,
  };
}

describe("parseSettingsSection", () => {
  it("returns the section for valid values", () => {
    expect(parseSettingsSection("account")).toBe("account");
    expect(parseSettingsSection("preferences")).toBe("preferences");
    expect(parseSettingsSection("usage")).toBe("usage");
    expect(parseSettingsSection("support")).toBe("support");
  });

  it("falls back to 'preferences' for null / undefined / invalid", () => {
    expect(parseSettingsSection(null)).toBe("preferences");
    expect(parseSettingsSection(undefined)).toBe("preferences");
    expect(parseSettingsSection("")).toBe("preferences");
    expect(parseSettingsSection("invalid")).toBe("preferences");
    expect(parseSettingsSection("ACCOUNT")).toBe("preferences");
    expect(parseSettingsSection({ section: "account" } as unknown as string)).toBe(
      "preferences",
    );
  });
});

describe("readSettingsDialogMarker", () => {
  it("returns null when history.state is null", () => {
    window.history.replaceState(null, "", location.href);
    expect(readSettingsDialogMarker()).toBeNull();
  });

  it("returns null when state has no namespaced key", () => {
    setCurrentHistoryState({ otherKey: "host-value" });
    expect(readSettingsDialogMarker()).toBeNull();
  });

  it("returns null when marker is not an object", () => {
    setCurrentHistoryState({ [HISTORY_MARKER_KEY]: "not-an-object" });
    expect(readSettingsDialogMarker()).toBeNull();
  });

  it("returns null when marker version is missing or wrong", () => {
    setCurrentHistoryState({
      [HISTORY_MARKER_KEY]: { section: "account" },
    });
    expect(readSettingsDialogMarker()).toBeNull();

    setCurrentHistoryState({
      [HISTORY_MARKER_KEY]: { version: 2, section: "account" },
    });
    expect(readSettingsDialogMarker()).toBeNull();
  });

  it("returns null when marker section is invalid", () => {
    setCurrentHistoryState({
      [HISTORY_MARKER_KEY]: { version: 1, section: "invalid" },
    });
    expect(readSettingsDialogMarker()).toBeNull();
  });

  it("returns parsed marker for valid version + section", () => {
    setCurrentHistoryState({
      [HISTORY_MARKER_KEY]: { version: 1, section: "account" },
    });
    expect(readSettingsDialogMarker()).toEqual({
      version: 1,
      section: "account",
    });
  });
});

describe("isOwnedBySettingsDialog", () => {
  it("returns false when no marker is present", () => {
    window.history.replaceState(null, "", location.href);
    expect(isOwnedBySettingsDialog()).toBe(false);

    setCurrentHistoryState({ otherKey: "host-value" });
    expect(isOwnedBySettingsDialog()).toBe(false);
  });

  it("returns true when the current entry was created by openSettingsDialogHistory", () => {
    const beforeUrl = snapshotUrl();
    openSettingsDialogHistory("account");
    expect(isOwnedBySettingsDialog()).toBe(true);
    expect(snapshotUrl()).toEqual(beforeUrl);
  });
});

describe("openSettingsDialogHistory", () => {
  afterEach(() => {
    // Reset history stack between describe blocks so each scenario starts
    // from a clean single-entry state.
    window.history.replaceState(null, "", location.href);
  });

  it("pushes a new entry containing the namespaced marker", () => {
    const initialLength = window.history.length;
    const beforeUrl = snapshotUrl();

    openSettingsDialogHistory("account");

    const marker = readSettingsDialogMarker();
    expect(marker).not.toBeNull();
    expect(marker?.version).toBe(1);
    expect(marker?.section).toBe("account");

    // A push happened (length grows when there is room; jsdom may cap it,
    // but the URL must be unchanged either way).
    expect(window.history.length).toBeGreaterThanOrEqual(initialLength);
    expect(snapshotUrl()).toEqual(beforeUrl);
  });

  it("preserves other history.state fields (host / Next state)", () => {
    setCurrentHistoryState({
      __nextInternal: { key: "abc" },
      hostScroll: 200,
      [HISTORY_MARKER_KEY]: undefined,
    });

    openSettingsDialogHistory("preferences");

    const state = window.history.state as Record<string, unknown>;
    expect(state.__nextInternal).toEqual({ key: "abc" });
    expect(state.hostScroll).toBe(200);
    expect(state[HISTORY_MARKER_KEY]).toEqual({
      version: 1,
      section: "preferences",
    });
  });

  it("does not change pathname, search, or hash", () => {
    window.history.replaceState(null, "", "/app/reader/r123?hl=en#frag");
    const before = snapshotUrl();

    openSettingsDialogHistory("account");

    expect(snapshotUrl()).toEqual(before);
  });

  it("does not fire popstate (pushState/replaceState never do)", () => {
    let popstateCount = 0;
    const handler = () => {
      popstateCount += 1;
    };
    window.addEventListener("popstate", handler);

    try {
      openSettingsDialogHistory("account");
      expect(popstateCount).toBe(0);
    } finally {
      window.removeEventListener("popstate", handler);
    }
  });
});

describe("replaceSettingsDialogSection", () => {
  afterEach(() => {
    window.history.replaceState(null, "", location.href);
  });

  it("replaces the current marker section in place (no new history entry)", () => {
    openSettingsDialogHistory("account");
    const lengthAfterOpen = window.history.length;

    replaceSettingsDialogSection("usage");

    const marker = readSettingsDialogMarker();
    expect(marker?.section).toBe("usage");
    expect(window.history.length).toBe(lengthAfterOpen);
  });

  it("preserves other history.state fields when replacing", () => {
    setCurrentHistoryState({
      nextKey: "n1",
      [HISTORY_MARKER_KEY]: { version: 1, section: "account" },
    });

    replaceSettingsDialogSection("support");

    const state = window.history.state as Record<string, unknown>;
    expect(state.nextKey).toBe("n1");
    expect(state[HISTORY_MARKER_KEY]).toEqual({
      version: 1,
      section: "support",
    });
  });

  it("does not change pathname, search, or hash", () => {
    window.history.replaceState(null, "", "/app/library?tab=recent#top");
    const before = snapshotUrl();

    openSettingsDialogHistory("account");
    replaceSettingsDialogSection("preferences");

    expect(snapshotUrl()).toEqual(before);
  });

  it("is a no-op when no marker is present (safe degradation)", () => {
    setCurrentHistoryState({ otherKey: "host-value" });
    const beforeState = window.history.state;
    const beforeUrl = snapshotUrl();

    replaceSettingsDialogSection("account");

    expect(window.history.state).toEqual(beforeState);
    expect(snapshotUrl()).toEqual(beforeUrl);
  });
});

describe("closeSettingsDialogHistory", () => {
  afterEach(() => {
    window.history.replaceState(null, "", location.href);
  });

  it("returns 'owned-back' when the current entry has a marker (calls history.back)", () => {
    openSettingsDialogHistory("account");
    const result = closeSettingsDialogHistory();
    expect(result).toBe("owned-back");
  });

  it("returns 'local-only' when the current entry has no marker (safe degradation, no back)", () => {
    setCurrentHistoryState({ otherKey: "host-value" });
    const result = closeSettingsDialogHistory();
    expect(result).toBe("local-only");
  });

  it("does not change URL when marker is absent (safe degradation)", () => {
    window.history.replaceState(null, "", "/app/reader/r1?hl=en#frag");
    const before = snapshotUrl();

    const result = closeSettingsDialogHistory();

    expect(result).toBe("local-only");
    expect(snapshotUrl()).toEqual(before);
  });
});

describe("marker shape and namespace", () => {
  it("uses the namespaced key 'clareadSettingsDialog'", () => {
    expect(HISTORY_MARKER_KEY).toBe("clareadSettingsDialog");
  });

  it("always stamps version: 1", () => {
    openSettingsDialogHistory("account");
    const marker = readSettingsDialogMarker() as SettingsDialogMarker;
    expect(marker.version).toBe(1);
  });

  it("supports every section value round-trip", () => {
    for (const section of SECTIONS) {
      window.history.replaceState(null, "", location.href);
      openSettingsDialogHistory(section);
      const marker = readSettingsDialogMarker();
      expect(marker?.section).toBe(section);
    }
  });
});
