import Link from "next/link";
import { homeRoute } from "@/lib/routes";

export default function NotFound() {
  return (
    <main className="grid min-h-screen content-center px-6 text-center">
      <h1 className="text-3xl font-semibold tracking-normal">页面不存在</h1>
      <p className="mt-3 text-sm text-muted-foreground">
        你要找的页面不存在或已被移动。
      </p>
      <Link href={homeRoute} className="mt-5 text-sm text-[var(--accent)]">
        返回 Claread 首页
      </Link>
    </main>
  );
}
