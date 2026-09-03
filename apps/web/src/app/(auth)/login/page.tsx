import { Suspense } from "react";

import { EmailAuthScreen } from "@/components/auth/EmailAuthScreen";

import { EmailAuthFlow } from "./EmailAuthFlow";

export default function LoginPage() {
	return (
		<Suspense fallback={<EmailAuthScreen mode="email" intent="login" />}>
			<EmailAuthFlow initialIntent="login" />
		</Suspense>
	);
}
