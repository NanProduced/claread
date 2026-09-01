"use client";

import { motion } from "motion/react";

import { useReducedMotion } from "./useReducedMotion";

type FloatingPathsProps = {
	position: 1 | -1;
};

/**
 * Decorative ink-line animation adapted from the Efferd `auth-5` block
 * (registry: https://efferd.com/r/new-york/auth-5.json). Durations are
 * deterministic instead of Math.random() so renders stay pure, and the
 * whole effect collapses to static lines under prefers-reduced-motion.
 */
export function FloatingPaths({ position }: FloatingPathsProps) {
	const reducedMotion = useReducedMotion();
	const paths = Array.from({ length: 36 }, (_, index) => ({
		id: index,
		d: `M-${380 - index * 5 * position} -${189 + index * 6}C-${
			380 - index * 5 * position
		} -${189 + index * 6} -${312 - index * 5 * position} ${216 - index * 6} ${
			152 - index * 5 * position
		} ${343 - index * 6}C${616 - index * 5 * position} ${470 - index * 6} ${
			684 - index * 5 * position
		} ${875 - index * 6} ${684 - index * 5 * position} ${875 - index * 6}`,
		opacity: 0.1 + index * 0.02,
		width: 0.5 + index * 0.03,
		duration: 20 + ((index * 7) % 10),
	}));

	return (
		<div aria-hidden="true" className="pointer-events-none absolute inset-0">
			<svg className="h-full w-full text-ink" fill="none" viewBox="0 0 696 316">
				<title>Background Paths</title>
				{paths.map((path) =>
					reducedMotion ? (
						<path
							key={path.id}
							d={path.d}
							stroke="currentColor"
							strokeOpacity={path.opacity * 0.6}
							strokeWidth={path.width}
						/>
					) : (
						<motion.path
							key={path.id}
							animate={{
								pathLength: 1,
								opacity: [0.3, 0.6, 0.3],
								pathOffset: [0, 1, 0],
							}}
							d={path.d}
							initial={{ pathLength: 0.3, opacity: 0.6 }}
							stroke="currentColor"
							strokeOpacity={path.opacity}
							strokeWidth={path.width}
							transition={{
								duration: path.duration,
								repeat: Number.POSITIVE_INFINITY,
								ease: "linear",
							}}
						/>
					),
				)}
			</svg>
		</div>
	);
}
