"use client";

import { BrandLockup } from "@/components/brand/BrandMarks";

import { FloatingPaths } from "./FloatingPaths";
import { QuoteRotator, type AuthQuote } from "./QuoteRotator";

export const AUTH_QUOTES: AuthQuote[] = [
	{
		text: "Reading maketh a full man; conference a ready man; and writing an exact man.",
		author: "Francis Bacon",
	},
	{
		text: "Reading is to the mind what exercise is to the body.",
		author: "Joseph Addison",
	},
	{
		text: "The reading of all good books is like a conversation with the finest minds of past centuries.",
		author: "René Descartes",
	},
	{
		text: "A room without books is like a body without a soul.",
		author: "Marcus Tullius Cicero",
	},
];

/**
 * Desktop-only decorative column of the auth shell: Claread mark on top,
 * reading slogan plus rotating public-domain quotes at the bottom, and the
 * Efferd auth-5 line animation behind, quieted to ink hairlines.
 */
export function AuthBrandPanel() {
	return (
		<div
			data-slot="auth-brand-panel"
			className="relative hidden flex-col overflow-hidden border-r border-hairline bg-surface-raised p-10 lg:flex"
		>
			<div className="absolute inset-0">
				<FloatingPaths position={1} />
				<FloatingPaths position={-1} />
			</div>

			<BrandLockup href={null} className="relative" />

			<div className="relative z-10 mt-auto space-y-6">
				<p className="text-sm font-medium text-ink">Read deeply, understand clearly.</p>
				<QuoteRotator quotes={AUTH_QUOTES} />
			</div>
		</div>
	);
}
