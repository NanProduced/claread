"use client";

import { useEffect, useState } from "react";
import { motion } from "motion/react";

import { useReducedMotion } from "./useReducedMotion";

export type AuthQuote = {
	text: string;
	author: string;
};

type QuoteRotatorProps = {
	quotes: AuthQuote[];
	intervalMs?: number;
};

/**
 * Decorative rotation of reading quotes for the auth brand panel. The whole
 * region is aria-hidden; under prefers-reduced-motion it never rotates and
 * never animates.
 */
export function QuoteRotator({ quotes, intervalMs = 6000 }: QuoteRotatorProps) {
	const reducedMotion = useReducedMotion();
	const [index, setIndex] = useState(0);

	useEffect(() => {
		if (reducedMotion || quotes.length < 2) {
			return;
		}

		const timer = setInterval(() => {
			setIndex((current) => (current + 1) % quotes.length);
		}, intervalMs);
		return () => clearInterval(timer);
	}, [reducedMotion, quotes.length, intervalMs]);

	const quote = quotes[index] ?? quotes[0];
	if (!quote) {
		return null;
	}

	return (
		<div aria-hidden="true">
			<motion.blockquote
				key={index}
				animate={{ opacity: 1 }}
				className="space-y-3"
				initial={{ opacity: 0 }}
				transition={{ duration: reducedMotion ? 0 : 0.6, ease: "easeOut" }}
			>
				<p className="font-reading text-xl leading-8 text-ink">&ldquo;{quote.text}&rdquo;</p>
				<footer className="text-sm font-medium text-muted-foreground">{quote.author}</footer>
			</motion.blockquote>
		</div>
	);
}
