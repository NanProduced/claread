/** @vitest-environment jsdom */
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FloatingPaths } from "./FloatingPaths";

afterEach(() => {
	cleanup();
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

describe("FloatingPaths", () => {
	it("renders 36 decorative animated paths", () => {
		const { container } = render(<FloatingPaths position={1} />);

		const wrapper = container.firstElementChild;
		expect(wrapper?.getAttribute("aria-hidden")).toBe("true");

		const paths = container.querySelectorAll("path");
		expect(paths.length).toBe(36);
		// Animated variant carries motion-driven animation state; static does not.
		expect(
			Array.from(paths).some(
				(path) => path.hasAttribute("pathLength") || Boolean(path.getAttribute("style")),
			),
		).toBe(true);
	});

	it("renders static paths under prefers-reduced-motion", () => {
		mockReducedMotion(true);
		const { container } = render(<FloatingPaths position={-1} />);

		const paths = container.querySelectorAll("path");
		expect(paths.length).toBe(36);
		expect(
			Array.from(paths).every(
				(path) => !path.hasAttribute("pathLength") && !path.getAttribute("style"),
			),
		).toBe(true);
	});
});
