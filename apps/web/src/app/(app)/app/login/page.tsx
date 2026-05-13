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
          Web 首期将通过手机号短信验证码登录。浏览器只持有 httpOnly cookie，Next.js BFF 负责与 Claread API 建立内部 session。
        </p>
        <div className="mt-6 space-y-3">
          <input
            className="w-full rounded-md border border-hairline bg-surface-warm px-4 py-3 text-sm text-ink outline-none placeholder:text-subtle"
            placeholder="输入手机号"
            disabled
          />
          <button
            className="w-full rounded-pill bg-ink px-4 py-3 text-sm font-semibold text-surface opacity-60"
            disabled
          >
            发送验证码
          </button>
        </div>
      </section>
    </main>
  );
}
