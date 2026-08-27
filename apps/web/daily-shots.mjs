import { chromium } from '@playwright/test';
const base = 'http://127.0.0.1:3001';
const targets = [
  ['article-001', '/daily/daily_2026_08_26_001'],
  ['article-002', '/daily/daily_2026_08_26_002'],
  ['list', '/daily'],
];
const viewports = [
  ['desktop', { width: 1440, height: 960 }],
  ['mobile', { width: 390, height: 844 }],
];
const browser = await chromium.launch();
for (const [vpName, vp] of viewports) {
  const page = await browser.newPage({ viewport: vp });
  for (const [name, path] of targets) {
    await page.goto(base + path, { waitUntil: 'networkidle', timeout: 60000 });
    await page.screenshot({ path: `/tmp/shots/${vpName}-${name}.png`, fullPage: true });
    console.log('shot', vpName, name, await page.title());
  }
  await page.close();
}
await browser.close();
