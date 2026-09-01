import { EmailAuthScreen } from "@/components/auth/EmailAuthScreen";

/**
 * Production entry renders only the initial email state. Every other state of
 * the shell is exercised through component tests and temporary acceptance
 * harnesses until the real email challenge API is wired.
 */
export default function LoginPage() {
	return <EmailAuthScreen mode="email" />;
}
