import { PhoneLoginForm } from "./PhoneLoginForm";

export default function LoginPage() {
  return (
    <main className="mx-auto grid min-h-[calc(100vh-56px)] max-w-md content-center px-6">
      <section className="rounded-note border border-hairline bg-surface p-6 shadow-surface-quiet">
        <p className="text-xs font-semibold uppercase tracking-[0.12em] text-lens-blue">
          Claread Web
        </p>
        <h1 className="mt-3 font-headline text-3xl font-semibold tracking-normal text-ink">
          手机号登录
        </h1>
        <p className="mt-3 text-sm leading-6 text-muted">
          Web 首期将通过手机号短信验证码登录。生产预留阿里云 Dypnsapi；本地调试先用验证码 888888 跑通 Web 会话。
        </p>
        <PhoneLoginForm />
      </section>
    </main>
  );
}
