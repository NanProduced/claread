import { redirect } from "next/navigation";
import { appReadRoute } from "@/lib/routes";

export default function AppIndexPage() {
  redirect(appReadRoute);
}
