import { FeedbackForm } from "../FeedbackForm";

export function SupportSection() {
  // FeedbackForm already renders MyFeedbackList internally (with refreshKey
  // linkage after each submit), so we don't render a second MyFeedbackList here.
  return <FeedbackForm />;
}
