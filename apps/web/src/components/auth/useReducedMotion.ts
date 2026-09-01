"use client";

import { useEffect, useState } from "react";

const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

export function useReducedMotion() {
	const [reduced, setReduced] = useState(false);

	useEffect(() => {
		if (typeof window.matchMedia !== "function") {
			return;
		}

		const mediaQuery = window.matchMedia(REDUCED_MOTION_QUERY);
		setReduced(mediaQuery.matches);

		const handleChange = (event: MediaQueryListEvent) => setReduced(event.matches);
		mediaQuery.addEventListener("change", handleChange);
		return () => mediaQuery.removeEventListener("change", handleChange);
	}, []);

	return reduced;
}
