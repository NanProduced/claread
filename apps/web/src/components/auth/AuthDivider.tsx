import type { ComponentProps } from "react";

/**
 * Divider with a centered label, adapted from the Efferd `auth-divider`
 * registry item (https://efferd.com/r/new-york/auth-divider.json) and mapped
 * to Claread hairline + muted text tokens.
 */
export function AuthDivider({ children, ...props }: ComponentProps<"div">) {
	return (
		<div className="relative flex w-full items-center" {...props}>
			<div className="w-full border-t border-hairline" />
			<div className="flex w-max justify-center px-2 text-nowrap text-xs text-muted-foreground">
				{children}
			</div>
			<div className="w-full border-t border-hairline" />
		</div>
	);
}
