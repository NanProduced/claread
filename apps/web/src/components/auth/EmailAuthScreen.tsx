"use client";

import Link from "next/link";
import { ChevronLeft } from "lucide-react";

import { BrandLockup } from "@/components/brand/BrandMarks";
import { Button } from "@/components/ui/button";
import { homeRoute } from "@/lib/routes";

import { AuthBrandPanel } from "./AuthBrandPanel";
import { EmailAuthCard, type EmailAuthCardProps } from "./EmailAuthCard";

/*
 * Direction contract — email auth shell.
 * THESIS: a quiet split sign-in. Left reads like a calm reading room (Claread
 * mark, one English slogan, rotating public-domain reading quotes, ink line
 * animation); right stays a Vercel-plain email-first form. It refuses the
 * category's gradient hero + marketing-copy auth page.
 * OWN-WORLD: Claread tokens only — surface / ink / hairline / primary /
 * feedback-error, Source Serif 4 for the quotes, Inter UI stack, BrandLockup,
 * shadcn Button + InputGroup. No gradients, no glass, no raw HEX, no heavy
 * shadows.
 * STORY: the visitor signs in or creates an account with email; Google stays
 * visible but is honestly marked unavailable; the quotes signal a reading
 * product, not a generic SaaS.
 * FIRST VIEWPORT: desktop — brand panel left (logo top-left, slogan and
 * rotating quote at the bottom, FloatingPaths behind); right column a centered
 * max-w-sm form with a Home ghost top-left. Mobile — brand lockup above a
 * single-column form.
 * FORM: Efferd registry block auth-5 (https://efferd.com/r/new-york/auth-5.json),
 * adopted partially: split layout, divider, Google icon and FloatingPaths; the
 * testimonial slot becomes the quote rotator.
 */
export type EmailAuthScreenProps = EmailAuthCardProps;

export function EmailAuthScreen(props: EmailAuthScreenProps) {
	return (
		<main className="relative min-h-dvh bg-surface text-ink lg:grid lg:h-screen lg:grid-cols-2 lg:overflow-hidden">
			<AuthBrandPanel />

			<div className="relative flex min-h-dvh flex-col justify-center px-6 py-10 sm:px-10 lg:min-h-0">
				<Button
					asChild
					variant="ghost"
					size="sm"
					className="absolute left-5 top-6 text-muted-foreground max-md:min-h-11"
				>
					<Link href={homeRoute}>
						<ChevronLeft aria-hidden="true" />
						首页
					</Link>
				</Button>

				<div className="mx-auto w-full max-w-sm">
					<div data-slot="auth-brand-mobile" className="mb-10 lg:hidden">
						<BrandLockup href={null} priority />
					</div>
					<EmailAuthCard {...props} />
				</div>
			</div>
		</main>
	);
}
