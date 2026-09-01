/** @vitest-environment jsdom */
import { act } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QuoteRotator } from "./QuoteRotator";

const QUOTES = [
	{ text: "Alpha quote.", author: "Author A" },
	{ text: "Beta quote.", author: "Author B" },
];

afterEach(() => {
	cleanup();
	vi.useRealTimers();
	vi.unstubAllGlobals();
});

function mockReducedMotion(matches: boolean) {
	vi.stubGlobal("matchMedia", (query: string) => ({
		matches,
		media: query,
		onchange: null,
		addEventListener: vi.fn(),
		removeEventListener: vi.fn(),
		addListener: vi.fn(),
		removeListener: vi.fn(),
		dispatchEvent: () => false,
	}));
}

describe("QuoteRotator", () => {
	it("shows the first quote with its author and stays decorative", () => {
		const { container } = render(<QuoteRotator quotes={QUOTES} />);

		expect(container.textContent).toContain("Alpha quote.");
		expect(container.textContent).toContain("Author A");
		expect(container.querySelector("[aria-hidden='true']")).not.toBeNull();
	});

	it("rotates to the next quote after the interval and wraps around", () => {
		vi.useFakeTimers();
		const { container } = render(<QuoteRotator quotes={QUOTES} intervalMs={6000} />);

		expect(container.textContent).toContain("Alpha quote.");

		act(() => {
			vi.advanceTimersByTime(6000);
		});
		expect(container.textContent).toContain("Beta quote.");

		act(() => {
			vi.advanceTimersByTime(6000);
		});
		expect(container.textContent).toContain("Alpha quote.");
	});

	it("never rotates under prefers-reduced-motion", () => {
		mockReducedMotion(true);
		vi.useFakeTimers();
		const { container } = render(<QuoteRotator quotes={QUOTES} intervalMs={6000} />);

		act(() => {
			vi.advanceTimersByTime(18000);
		});
		expect(container.textContent).toContain("Alpha quote.");
		expect(container.textContent).not.toContain("Beta quote.");
	});
});
