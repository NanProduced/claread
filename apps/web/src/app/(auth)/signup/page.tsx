import { Suspense } from "react";

import { EmailAuthScreen } from "@/components/auth/EmailAuthScreen";

import { EmailAuthFlow } from "../login/EmailAuthFlow";

export default function SignupPage() {
	return (
		<Suspense fallback={<EmailAuthScreen mode="email" intent="register" />}>
			<EmailAuthFlow initialIntent="register" />
		</Suspense>
	);
}
