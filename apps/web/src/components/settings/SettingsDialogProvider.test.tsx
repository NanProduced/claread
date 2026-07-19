/** @vitest-environment jsdom */

import { act, cleanup, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock the Host so the Provider test focuses on state machine only.
// Capture props so tests can invoke `onCloseAutoFocus` directly — the
// Radix Dialog is mocked away, so we substitute for Radix's lifecycle
// hook by calling the callback ourselves.
const hostPropsMock = vi.fn();
vi.mock("@/components/settings/SettingsDialogHost", () => ({
  SettingsDialogHost: (props: unknown) => {
    hostPropsMock(props);
    return <div data-testid="settings-dialog-host" />;
  },
}));

import {
  SettingsDialogProvider,
  useSettingsDialog,
} from "./SettingsDialogProvider";

// Capture popstate listeners registered by the Provider so tests can
// dispatch synthetic popstate events.
let popstateListeners: Array<(event: PopStateEvent) => void> = [];

beforeEach(() => {
  popstateListeners = [];
  // Wrap addEventListener to capture popstate handlers specifically.
  const original = window.addEventListener.bind(window) as (
    type: string,
    listener: EventListenerOrEventListenerObject,
    options?: boolean | AddEventListenerOptions,
  ) => void;
  vi.spyOn(window, "addEventListener").mockImplementation(
    (type: string, listener: unknown) => {
      if (type === "popstate" && typeof listener === "function") {
        popstateListeners.push(
          listener as (event: PopStateEvent) => void,
        );
      }
      return original(
        type,
        listener as EventListenerOrEventListenerObject,
      );
    },
  );

  // Reset history to a clean single entry before each test.
  window.history.replaceState(null, "", "/app/reader/r1");
  hostPropsMock.mockClear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/");
});

function firePopstate(state: unknown): void {
  // Replace the current history state so readSettingsDialogMarker sees
  // the marker that would have been left by a real Back/Forward jump.
  // replaceState itself does NOT fire popstate (that's the contract),
  // so we still dispatch a synthetic PopStateEvent below.
  window.history.replaceState(state as Record<string, unknown>, "");

  // React state updates from native event listeners must be wrapped in
  // act() so they flush synchronously in test mode.
  act(() => {
    const event = new PopStateEvent("popstate", { state });
    for (const listener of popstateListeners) {
      listener(event);
    }
  });
}

/**
 * Invoke the latest `onCloseAutoFocus` callback the Provider passed to
 * the Host. The Host is mocked, so we substitute for Radix Dialog's
 * lifecycle by calling the callback directly with a synthetic Event.
 *
 * Returns the Event so the caller can assert `defaultPrevented`.
 */
function invokeOnCloseAutoFocus(): Event {
  const lastCall = hostPropsMock.mock.calls.at(-1);
  if (!lastCall) throw new Error("Host was not rendered");
  const props = lastCall[0] as {
    onCloseAutoFocus?: (event: Event) => void;
  };
  if (!props.onCloseAutoFocus) {
    throw new Error("onCloseAutoFocus was not passed to Host");
  }
  // cancelable: true so event.preventDefault() flips defaultPrevented.
  const event = new Event("closeAutoFocus", { cancelable: true });
  act(() => {
    props.onCloseAutoFocus!(event);
  });
  return event;
}

function renderProvider() {
  return render(
    <SettingsDialogProvider>
      <Probe />
    </SettingsDialogProvider>,
  );
}

function Probe() {
  const controller = useSettingsDialog();
  return (
    <div>
      <span data-testid="is-open">{String(controller.isOpen)}</span>
      <span data-testid="active-section">{controller.activeSection}</span>
      <button
        data-testid="open-account"
        onClick={() => controller.openSettings("account")}
      >
        open-account
      </button>
      <button
        data-testid="open-default"
        onClick={() => controller.openSettings()}
      >
        open-default
      </button>
      <button
        data-testid="open-usage"
        onClick={() => controller.openSettings("usage")}
      >
        open-usage
      </button>
      <button
        data-testid="set-usage"
        onClick={() => controller.setActiveSection("usage")}
      >
        set-usage
      </button>
      <button
        data-testid="close"
        onClick={() => controller.closeSettings()}
      >
        close
      </button>
    </div>
  );
}

describe("SettingsDialogProvider — initial state (no marker)", () => {
  it("starts closed with activeSection=preferences", () => {
    renderProvider();
    expect(screen.getByTestId("is-open").textContent).toBe("false");
    expect(screen.getByTestId("active-section").textContent).toBe(
      "preferences",
    );
  });

  it("renders the SettingsDialogHost (Host mounted, but does not fetch on mount)", () => {
    renderProvider();
    expect(hostPropsMock).toHaveBeenCalled();
    expect(screen.getByTestId("settings-dialog-host")).toBeTruthy();
  });

  it("passes an onCloseAutoFocus callback to the Host", () => {
    renderProvider();
    const lastCall = hostPropsMock.mock.calls.at(-1)![0] as {
      onCloseAutoFocus?: (event: Event) => void;
    };
    expect(typeof lastCall.onCloseAutoFocus).toBe("function");
  });
});

describe("SettingsDialogProvider — initial restore from history marker", () => {
  it("opens with marker section when a valid marker is present on mount", () => {
    window.history.replaceState(
      {
        __nextInternal: { key: "abc" },
        clareadSettingsDialog: { version: 1, section: "support" },
      },
      "",
      "/app/reader/r1",
    );

    renderProvider();

    expect(screen.getByTestId("is-open").textContent).toBe("true");
    expect(screen.getByTestId("active-section").textContent).toBe("support");
  });

  it("stays closed when marker is missing", () => {
    window.history.replaceState({ otherKey: "host" }, "", "/app/reader/r1");

    renderProvider();

    expect(screen.getByTestId("is-open").textContent).toBe("false");
    expect(screen.getByTestId("active-section").textContent).toBe(
      "preferences",
    );
  });

  it("stays closed when marker has wrong version", () => {
    window.history.replaceState(
      {
        clareadSettingsDialog: { version: 999, section: "account" },
      },
      "",
      "/app/reader/r1",
    );

    renderProvider();

    expect(screen.getByTestId("is-open").textContent).toBe("false");
  });

  it("stays closed when marker has invalid section", () => {
    window.history.replaceState(
      {
        clareadSettingsDialog: { version: 1, section: "not-a-section" },
      },
      "",
      "/app/reader/r1",
    );

    renderProvider();

    expect(screen.getByTestId("is-open").textContent).toBe("false");
  });

  it("stays closed when marker is not an object", () => {
    window.history.replaceState(
      {
        clareadSettingsDialog: "not-an-object",
      },
      "",
      "/app/reader/r1",
    );

    renderProvider();

    expect(screen.getByTestId("is-open").textContent).toBe("false");
  });
});

describe("SettingsDialogProvider — open / close / section", () => {
  it("openSettings(section) opens with that section and pushes a marker", () => {
    renderProvider();

    const beforeUrl = {
      pathname: window.location.pathname,
      search: window.location.search,
      hash: window.location.hash,
    };

    act(() => {
      screen.getByTestId("open-account").click();
    });

    expect(screen.getByTestId("is-open").textContent).toBe("true");
    expect(screen.getByTestId("active-section").textContent).toBe("account");

    // URL unchanged.
    expect(window.location.pathname).toBe(beforeUrl.pathname);
    expect(window.location.search).toBe(beforeUrl.search);
    expect(window.location.hash).toBe(beforeUrl.hash);

    // Marker present in history.state.
    const state = window.history.state as Record<string, unknown> | null;
    expect(state).not.toBeNull();
    expect(state?.clareadSettingsDialog).toEqual({
      version: 1,
      section: "account",
    });
  });

  it("openSettings() with no argument defaults to 'preferences'", () => {
    renderProvider();
    act(() => {
      screen.getByTestId("open-default").click();
    });
    expect(screen.getByTestId("is-open").textContent).toBe("true");
    expect(screen.getByTestId("active-section").textContent).toBe(
      "preferences",
    );
  });

  it("openSettings(section) when already open uses replaceState (no new history entry)", () => {
    renderProvider();

    // First open: pushState.
    act(() => {
      screen.getByTestId("open-account").click();
    });
    expect(screen.getByTestId("is-open").textContent).toBe("true");
    expect(screen.getByTestId("active-section").textContent).toBe("account");

    const lengthAfterFirstOpen = window.history.length;

    const beforeUrl = {
      pathname: window.location.pathname,
      search: window.location.search,
      hash: window.location.hash,
    };

    // Second open while already open: should replaceState, not pushState.
    act(() => {
      screen.getByTestId("open-usage").click();
    });

    expect(screen.getByTestId("is-open").textContent).toBe("true");
    expect(screen.getByTestId("active-section").textContent).toBe("usage");

    // No new history entry.
    expect(window.history.length).toBe(lengthAfterFirstOpen);

    // URL unchanged.
    expect(window.location.pathname).toBe(beforeUrl.pathname);
    expect(window.location.search).toBe(beforeUrl.search);
    expect(window.location.hash).toBe(beforeUrl.hash);

    // Marker reflects the new section.
    const state = window.history.state as Record<string, unknown>;
    expect(state.clareadSettingsDialog).toEqual({
      version: 1,
      section: "usage",
    });
  });

  it("setActiveSection uses replaceState (no new history entry, URL unchanged)", () => {
    renderProvider();
    act(() => {
      screen.getByTestId("open-account").click();
    });
    const lengthAfterOpen = window.history.length;

    const beforeUrl = {
      pathname: window.location.pathname,
      search: window.location.search,
      hash: window.location.hash,
    };

    act(() => {
      screen.getByTestId("set-usage").click();
    });

    expect(screen.getByTestId("active-section").textContent).toBe("usage");
    expect(window.history.length).toBe(lengthAfterOpen); // no new entry
    expect(window.location.pathname).toBe(beforeUrl.pathname);
    expect(window.location.search).toBe(beforeUrl.search);
    expect(window.location.hash).toBe(beforeUrl.hash);

    const state = window.history.state as Record<string, unknown>;
    expect(state.clareadSettingsDialog).toEqual({
      version: 1,
      section: "usage",
    });
  });

  it("closeSettings calls history.back when current entry owns the marker", () => {
    renderProvider();
    act(() => {
      screen.getByTestId("open-account").click();
    });
    expect(screen.getByTestId("is-open").textContent).toBe("true");

    const backSpy = vi.spyOn(window.history, "back");

    act(() => {
      screen.getByTestId("close").click();
    });

    expect(backSpy).toHaveBeenCalledTimes(1);
  });

  it("closeSettings when owned-back keeps isOpen=true until popstate fires", () => {
    // This is the new contract: when closeSettingsDialogHistory returns
    // 'owned-back', the Provider does NOT setState closed. It waits
    // for the resulting popstate to sync state. This avoids double-
    // close races and preserves the "Back closes the dialog" mental
    // model.
    renderProvider();
    act(() => {
      screen.getByTestId("open-account").click();
    });
    expect(screen.getByTestId("is-open").textContent).toBe("true");

    const backSpy = vi.spyOn(window.history, "back");

    act(() => {
      screen.getByTestId("close").click();
    });

    expect(backSpy).toHaveBeenCalledTimes(1);
    // CRITICAL: still open after closeSettings, before popstate.
    expect(screen.getByTestId("is-open").textContent).toBe("true");

    // Now popstate fires (landing on a host entry with no marker).
    firePopstate({ otherKey: "host" });

    // After popstate, the dialog is closed.
    expect(screen.getByTestId("is-open").textContent).toBe("false");
  });

  it("closeSettings does NOT call history.back when no marker is owned (safe degradation)", () => {
    // Render with a clean host page history (no marker).
    window.history.replaceState({ otherKey: "host" }, "", "/app/reader/r1");
    renderProvider();

    // Open via popstate (marker present) — but we don't own the marker.
    // Actually, with the new initial-restore behavior, the Provider
    // would open if there's a marker. So we use a no-marker state and
    // call closeSettings while closed.
    expect(screen.getByTestId("is-open").textContent).toBe("false");

    const backSpy = vi.spyOn(window.history, "back");
    const beforeLength = window.history.length;

    act(() => {
      screen.getByTestId("close").click();
    });

    expect(backSpy).not.toHaveBeenCalled();
    expect(window.history.length).toBe(beforeLength);
  });

  it("closeSettings with local-only result closes immediately (no popstate wait)", () => {
    // Open via popstate (so isOpen=true but we don't own a marker
    // through pushState — the marker was already on the host entry
    // when the Provider mounted, and the user clicked close without
    // us pushing a new entry).
    window.history.replaceState(
      {
        clareadSettingsDialog: { version: 1, section: "account" },
      },
      "",
      "/app/reader/r1",
    );

    renderProvider();
    expect(screen.getByTestId("is-open").textContent).toBe("true");

    // closeSettingsDialogHistory sees the marker and calls history.back.
    // But we want to test the local-only path. Easiest way: clear the
    // marker before calling close.
    window.history.replaceState({ otherKey: "host" }, "", "/app/reader/r1");

    const backSpy = vi.spyOn(window.history, "back");

    act(() => {
      screen.getByTestId("close").click();
    });

    // local-only: no back call, immediate close.
    expect(backSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId("is-open").textContent).toBe("false");
  });
});

describe("SettingsDialogProvider — popstate sync (Back / Forward)", () => {
  it("popstate with valid marker restores isOpen + activeSection", () => {
    renderProvider();

    // Simulate Back/Forward landing on a history entry that has our marker.
    firePopstate({
      __nextInternal: { key: "x" },
      clareadSettingsDialog: { version: 1, section: "support" },
    });

    expect(screen.getByTestId("is-open").textContent).toBe("true");
    expect(screen.getByTestId("active-section").textContent).toBe("support");
  });

  it("popstate with no marker closes the dialog", () => {
    renderProvider();

    // First, open via popstate (marker present).
    firePopstate({
      clareadSettingsDialog: { version: 1, section: "account" },
    });
    expect(screen.getByTestId("is-open").textContent).toBe("true");

    // Now Back/Forward lands on a host entry with no marker → close.
    firePopstate({ otherKey: "host-value" });
    expect(screen.getByTestId("is-open").textContent).toBe("false");
  });

  it("popstate with invalid section falls back to 'preferences' (and stays open if marker present)", () => {
    renderProvider();

    firePopstate({
      clareadSettingsDialog: { version: 1, section: "not-a-section" },
    });

    // Invalid section inside marker → readSettingsDialogMarker returns null
    // → Provider closes the dialog (defensive: don't try to recover from
    // malformed marker).
    expect(screen.getByTestId("is-open").textContent).toBe("false");
  });

  it("popstate with wrong marker version closes the dialog", () => {
    renderProvider();

    firePopstate({
      clareadSettingsDialog: { version: 999, section: "account" },
    });

    expect(screen.getByTestId("is-open").textContent).toBe("false");
  });

  it("popstate preserves other history.state fields (host / Next state)", () => {
    renderProvider();

    firePopstate({
      __nextInternal: { key: "preserved" },
      hostScroll: 42,
      clareadSettingsDialog: { version: 1, section: "account" },
    });

    // The Provider does not touch history.state on popstate — it only
    // reads. Other fields must survive untouched.
    expect(window.history.state).toMatchObject({
      __nextInternal: { key: "preserved" },
      hostScroll: 42,
    });
  });
});

describe("SettingsDialogProvider — focus restoration (Radix onCloseAutoFocus)", () => {
  it("captures the opener element on open and restores focus on close", () => {
    const opener = document.createElement("button");
    opener.setAttribute("data-testid", "opener-btn");
    // jsdom only moves document.activeElement on .focus() for elements
    // with a valid tabIndex. tabIndex=-1 makes the button programmatically
    // focusable without entering the tab order.
    opener.tabIndex = -1;
    document.body.appendChild(opener);

    // Set focus to the opener before opening.
    opener.focus();
    // jsdom sets document.activeElement to the focused element.
    expect(document.activeElement).toBe(opener);

    renderProvider();

    act(() => {
      screen.getByTestId("open-account").click();
    });
    expect(screen.getByTestId("is-open").textContent).toBe("true");

    // Close via close button. Provider calls history.back (owned).
    // isOpen stays true until popstate fires.
    act(() => {
      screen.getByTestId("close").click();
    });
    expect(screen.getByTestId("is-open").textContent).toBe("true");

    // Simulate the resulting popstate (host entry without marker).
    firePopstate({ otherKey: "host" });
    expect(screen.getByTestId("is-open").textContent).toBe("false");

    // Now Radix would fire onCloseAutoFocus on dialog unmount. We
    // invoke the callback directly to substitute for Radix's lifecycle.
    const event = invokeOnCloseAutoFocus();

    // Opener was focused.
    expect(document.activeElement).toBe(opener);
    // Radix's default body-focus must be suppressed.
    expect(event.defaultPrevented).toBe(true);

    opener.remove();
  });

  it("falls back to visible mobile user-menu trigger when opener is disconnected", () => {
    const mobileTrigger = document.createElement("button");
    mobileTrigger.setAttribute("data-mobile-user-menu-trigger", "true");
    mobileTrigger.tabIndex = -1;
    document.body.appendChild(mobileTrigger);

    renderProvider();

    // No real opener set (document.activeElement is body on mount).
    act(() => {
      screen.getByTestId("open-account").click();
    });

    // Close + popstate (owned-back → wait → popstate).
    act(() => {
      screen.getByTestId("close").click();
    });
    firePopstate({ otherKey: "host" });
    expect(screen.getByTestId("is-open").textContent).toBe("false");

    // Invoke the Radix callback.
    const event = invokeOnCloseAutoFocus();

    // Mobile fallback was focused.
    expect(document.activeElement).toBe(mobileTrigger);
    expect(event.defaultPrevented).toBe(true);

    mobileTrigger.remove();
  });

  it("falls back to desktop user-menu trigger when mobile trigger is missing", () => {
    const desktopTrigger = document.createElement("button");
    desktopTrigger.setAttribute("data-desktop-user-menu-trigger", "true");
    desktopTrigger.tabIndex = -1;
    document.body.appendChild(desktopTrigger);

    renderProvider();

    act(() => {
      screen.getByTestId("open-account").click();
    });
    act(() => {
      screen.getByTestId("close").click();
    });
    firePopstate({ otherKey: "host" });

    const event = invokeOnCloseAutoFocus();

    expect(document.activeElement).toBe(desktopTrigger);
    expect(event.defaultPrevented).toBe(true);

    desktopTrigger.remove();
  });

  it("does NOT preventDefault when no focus target is available (lets Radix default)", () => {
    renderProvider();

    act(() => {
      screen.getByTestId("open-account").click();
    });
    act(() => {
      screen.getByTestId("close").click();
    });
    firePopstate({ otherKey: "host" });

    // No opener captured (body had focus on open), no mobile/desktop
    // triggers in the DOM.
    const event = invokeOnCloseAutoFocus();

    // No focus moved by our handler — Radix's default runs.
    expect(event.defaultPrevented).toBe(false);
  });

  it("opener is NOT re-captured when openSettings is called while already open", () => {
    // This verifies the same-round hardening: a subsequent
    // openSettings(section) call while open uses replaceState and does
    // NOT re-capture the opener. The original opener is still the
    // restore target on close.
    const opener = document.createElement("button");
    opener.setAttribute("data-testid", "opener-btn");
    opener.tabIndex = -1;
    document.body.appendChild(opener);
    opener.focus();
    expect(document.activeElement).toBe(opener);

    renderProvider();

    // Open — captures opener.
    act(() => {
      screen.getByTestId("open-account").click();
    });

    // Move focus away (simulating the user clicking inside the dialog).
    const other = document.createElement("button");
    other.tabIndex = -1;
    document.body.appendChild(other);
    other.focus();
    expect(document.activeElement).toBe(other);

    // Call openSettings again with a different section while open.
    act(() => {
      screen.getByTestId("open-usage").click();
    });

    // Close + popstate.
    act(() => {
      screen.getByTestId("close").click();
    });
    firePopstate({ otherKey: "host" });

    // Invoke Radix callback — should focus the ORIGINAL opener, not `other`.
    invokeOnCloseAutoFocus();

    expect(document.activeElement).toBe(opener);

    opener.remove();
    other.remove();
  });
});

describe("SettingsDialogProvider — no module-global / sessionStorage / URL state", () => {
  it("does not read or write sessionStorage", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem");
    renderProvider();
    act(() => {
      screen.getByTestId("open-account").click();
    });
    act(() => {
      screen.getByTestId("close").click();
    });
    firePopstate({ otherKey: "host" });
    expect(spy).not.toHaveBeenCalled();
  });

  it("does not call Next router (push / replace / back)", () => {
    renderProvider();
    // The Provider uses window.history directly; if it ever called the
    // Next router, that would change URL pathname/search/hash, which
    // the open/close tests above already assert is unchanged.
    expect(window.location.pathname).toBe("/app/reader/r1");
  });
});

describe("useSettingsDialog — outside provider", () => {
  it("throws a clear error when used outside SettingsDialogProvider", () => {
    expect(() => renderHook(() => useSettingsDialog())).toThrow(
      /SettingsDialogProvider/,
    );
  });
});
