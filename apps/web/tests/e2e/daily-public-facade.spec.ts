/**
 * 每日精读公开门面验收（详情页可读性、列表空态降级、页底主行动、SEO）。
 * 依赖真实本地 FastAPI 数据（当前有已发布文章、今日为空）。
 */
import { expect, test, type Locator, type Page } from "@playwright/test";

const DETAIL_PATH = "/daily/daily_2026_08_15_001";

/** 页面内：解析 rgb/rgba/color(srgb)，沿祖先合成不透明背景，返回前景/背景对比。 */
function installColorProbe(page: Page) {
  return page.evaluate(() => {
    (window as unknown as { __c3Probe: unknown }).__c3Probe = () => {
      const parse = (raw: string): [number, number, number, number] | null => {
        if (!raw || raw === "transparent" || raw === "rgba(0, 0, 0, 0)") return null;
        let m = raw.match(/rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,\s/]+([\d.]+))?\s*\)/);
        if (m) return [Number(m[1]), Number(m[2]), Number(m[3]), m[4] === undefined ? 1 : Number(m[4])];
        m = raw.match(/color\(\s*srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)(?:\s*\/\s*([\d.]+))?\s*\)/);
        if (m) {
          return [
            Number(m[1]) * 255,
            Number(m[2]) * 255,
            Number(m[3]) * 255,
            m[4] === undefined ? 1 : Number(m[4]),
          ];
        }
        return null;
      };

      const effectiveBg = (el: Element): string => {
        const stack: [number, number, number, number][] = [];
        let node: Element | null = el;
        while (node) {
          const c = parse(getComputedStyle(node).backgroundColor);
          if (c && c[3] > 0) stack.push(c);
          if (c && c[3] >= 1) break;
          node = node.parentElement;
        }
        let [r, g, b] = [255, 255, 255];
        for (let i = stack.length - 1; i >= 0; i--) {
          const [cr, cg, cb, a] = stack[i];
          r = cr * a + r * (1 - a);
          g = cg * a + g * (1 - a);
          b = cb * a + b * (1 - a);
        }
        return `rgb(${Math.round(r)}, ${Math.round(g)}, ${Math.round(b)})`;
      };

      const probe = (el: Element) => ({
        color: getComputedStyle(el).color,
        bg: effectiveBg(el),
      });

      const rgbOf = (raw: string): [number, number, number] | null => {
        const m = raw.match(/rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/);
        return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
      };

      const sameColor = (a: string, b: string, tolerance = 8): boolean => {
        const ca = rgbOf(a);
        const cb = rgbOf(b);
        if (!ca || !cb) return false;
        return (
          Math.abs(ca[0] - cb[0]) <= tolerance &&
          Math.abs(ca[1] - cb[1]) <= tolerance &&
          Math.abs(ca[2] - cb[2]) <= tolerance
        );
      };

      return {
        bodyColor: getComputedStyle(document.body).color,
        probe,
        sameColor,
        scanAnchors: () =>
          [...document.querySelectorAll("a")]
            .filter((a) => {
              const rect = a.getBoundingClientRect();
              return rect.width > 0 && rect.height > 0;
            })
            .map((a) => {
              const info = probe(a);
              return {
                text: (a.textContent ?? "").trim().slice(0, 40),
                color: info.color,
                bg: info.bg,
                invisible: sameColor(info.color, info.bg),
              };
            })
            .filter((entry) => entry.invisible),
      };
    };
  });
}

async function probeLocator(page: Page, locator: Locator) {
  return locator.evaluate((el) => {
    const probe = (window as unknown as { __c3Probe: () => { probe: (el: Element) => { color: string; bg: string }; sameColor: (a: string, b: string, t?: number) => boolean } }).__c3Probe();
    return probe.probe(el);
  });
}

test.describe("daily public facade", () => {
  test("CTA 与来源链接在详情页可读，anchor 无同色于背景", async ({ page }) => {
    await page.goto(DETAIL_PATH);
    await installColorProbe(page);

    // 主 CTA：文字色不得等于（或接近）背景色。
    const cta = page.getByRole("link", { name: "加入我的阅读记录" });
    await expect(cta).toBeVisible();
    const ctaColors = await probeLocator(page, cta);
    expect(
      await page.evaluate(([color, bg]) => {
        return (window as unknown as { __c3Probe: () => { sameColor: (a: string, b: string, t?: number) => boolean } })
          .__c3Probe()
          .sameColor(color, bg);
      }, [ctaColors.color, ctaColors.bg] as const),
    ).toBe(false);

    // 来源链接保持 Daily 品牌色，不被 inherit 污染成正文墨色。
    const sourceLink = page.getByRole("heading", { name: "来源" }).locator("..").getByRole("link");
    await expect(sourceLink).toBeVisible();
    const sourceColors = await probeLocator(page, sourceLink);
    const bodyColor = await page.evaluate(
      () => (window as unknown as { __c3Probe: () => { bodyColor: string } }).__c3Probe().bodyColor,
    );
    const dailyAccent = await page.locator("main.daily-reader-surface").evaluate(
      (element) => getComputedStyle(element).getPropertyValue("--dr-accent").trim(),
    );
    expect(dailyAccent).toBe("#1f5eff");
    expect(
      await page.evaluate(([color, body]) => {
        return (window as unknown as { __c3Probe: () => { sameColor: (a: string, b: string, t?: number) => boolean } })
          .__c3Probe()
          .sameColor(color, body, 4);
      }, [sourceColors.color, bodyColor] as const),
    ).toBe(false);
  });

  test("C-2: 桌面正文使用中文学习控件与段落锚定边注", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(DETAIL_PATH);

    await expect(page.getByRole("heading", { name: "读前导引 · BEFORE YOU READ" })).toBeVisible();
    const guide = page.getByRole("button", { name: /展开第 1 段导读/ });
    const translation = page.getByRole("button", { name: /显示第 1 段译文/ });
    await expect(guide).toBeVisible();
    await expect(translation).toBeVisible();
    expect((await guide.boundingBox())?.height).toBeGreaterThanOrEqual(44);

    const annotation = page.locator('button[aria-label^="查看“"]').first();
    await annotation.click();
    const note = page.getByRole("complementary");
    await expect(note).toBeVisible();
    expect(await annotation.getAttribute("aria-expanded")).toBe("true");
    expect(await annotation.getAttribute("aria-controls")).toBe(await note.getAttribute("id"));
    expect((await note.boundingBox())?.x).toBeGreaterThan(1060);
  });

  test("C-2: 390px 无横向溢出，注释回落到正文内", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(DETAIL_PATH);

    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      ),
    ).toBe(0);

    await page.locator('button[aria-label^="查看“"]').first().click();
    const box = await page.getByRole("complementary").boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(20);
    expect(box!.x + box!.width).toBeLessThanOrEqual(370);

    await page.setViewportSize({ width: 640, height: 900 });
    const paragraphNumber = page.locator("section.group > span.dr-font-mono").first();
    await expect(paragraphNumber).toBeVisible();
    expect((await paragraphNumber.boundingBox())?.x).toBeGreaterThanOrEqual(0);
  });

  test("列表页今日为空时降级出头条且往期可见，无开发术语", async ({ page }) => {
    await page.goto("/daily");
    await installColorProbe(page);

    // 空态开发术语必须消失。
    await expect(page.getByText("今日精读暂未发布")).toHaveCount(0);
    await expect(page.getByText(/数据源|上游/)).toHaveCount(0);

    // 头条存在（今日有文章或最新已发布文章降级）。
    const heroKicker = page.getByText(/今日精读 ·|往期精选 ·/);
    await expect(heroKicker.first()).toBeVisible();

    // archive 侧栏独立可见且至少一篇文章。
    const archive = page.locator("aside#archive");
    await expect(archive).toBeVisible();
    await expect(archive.getByText("往期精选")).toBeVisible();
    expect(await archive.locator("a[href^='/daily/']").count()).toBeGreaterThan(0);

    // 全页 anchor 无同色于背景的实例（含侧栏 bg-ink CTA）。
    const victims = await page.evaluate(() => {
      return (window as unknown as { __c3Probe: () => { scanAnchors: () => unknown[] } }).__c3Probe().scanAnchors();
    });
    expect(victims).toEqual([]);
  });

  test("页底单一主行动；分享按钮有反馈", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.goto(DETAIL_PATH);

    // 页底只保留「加入我的阅读记录」，不再有同 href 的「收藏」文字链接。
    await expect(page.getByRole("link", { name: "加入我的阅读记录" })).toHaveCount(1);
    await expect(page.locator("a", { hasText: "收藏" })).toHaveCount(0);

    // 分享：桌面 Chromium 无 navigator.share，走复制链接 + toast 反馈。
    await page.getByRole("button", { name: "分享" }).click();
    await expect(page.locator("[data-sonner-toast]").first()).toBeVisible({ timeout: 10_000 });
  });

  test("C-4: 学习模式批量展开导读与译文并在刷新后保留", async ({ page }) => {
    await page.goto(DETAIL_PATH);

    const modeSwitch = page.getByRole("switch", { name: "学习模式" });
    await expect(modeSwitch).toHaveAttribute("aria-checked", "false");
    await modeSwitch.click();

    await expect(modeSwitch).toHaveAttribute("aria-checked", "true");
    await expect(page.getByRole("button", { name: "收起第 1 段导读" })).toBeVisible();
    await expect(page.getByRole("button", { name: "收起第 1 段译文" })).toBeVisible();

    await page.reload();
    await expect(page.getByRole("switch", { name: "学习模式" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  test("C-4: 登录态在原页加入阅读记录并获得成功反馈", async ({ page, context }) => {
    await context.addCookies([
      { name: "claread_web_session", value: "e2e-session", domain: "127.0.0.1", path: "/" },
    ]);
    let favorited = false;
    await page.route("**/api/web/daily-reader/*/favorite", async (route) => {
      if (route.request().method() === "POST") favorited = true;
      if (route.request().method() === "DELETE") favorited = false;
      await route.fulfill({ json: { ok: true, favorited } });
    });

    await page.goto(`${DETAIL_PATH}?intent=save`);

    await expect(page.getByRole("link", { name: "进入 Claread" })).toBeVisible();
    await expect(page.getByRole("button", { name: "已加入阅读记录" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(page).toHaveURL(new RegExp(`${DETAIL_PATH.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`));
    await expect(page.getByText("已加入阅读记录", { exact: true }).last()).toBeVisible();
  });

  test("SEO: 详情页 OG/Twitter/JSON-LD 与列表页基础 meta", async ({ page }) => {
    await page.goto(DETAIL_PATH);
    await expect(page.locator('meta[property="og:title"]')).toHaveAttribute("content", /.+/);
    await expect(page.locator('meta[property="og:description"]')).toHaveAttribute("content", /.+/);
    await expect(page.locator('meta[property="og:image"]')).toHaveAttribute("content", /^https?:\/\//);
    await expect(page.locator('meta[name="twitter:card"]')).toHaveAttribute(
      "content",
      "summary_large_image",
    );
    const jsonLd = await page.locator('script[type="application/ld+json"]').first().textContent();
    expect(jsonLd).toBeTruthy();
    const parsed = JSON.parse(jsonLd ?? "{}") as { "@type": string; headline: string };
    expect(parsed["@type"]).toBe("Article");
    expect(parsed.headline).toBeTruthy();

    await page.goto("/daily");
    await expect(page.locator('meta[property="og:title"]')).toHaveAttribute("content", /.+/);
    await expect(page.locator('meta[name="description"]')).toHaveAttribute("content", /.+/);
  });

  test("回归: 其余公开页 anchor 无同色于背景", async ({ page }) => {
    for (const route of ["/", "/about", "/help", "/blog"]) {
      await page.goto(route);
      await installColorProbe(page);
      const victims = await page.evaluate(() => {
        return (window as unknown as { __c3Probe: () => { scanAnchors: () => unknown[] } })
          .__c3Probe()
          .scanAnchors();
      });
      expect(victims, `route ${route}`).toEqual([]);
    }
  });
});
