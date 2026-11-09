import { createRequire } from 'node:module';
import { mkdir } from 'node:fs/promises';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE);
const browser = await chromium.launch({ headless: true });
const directory = '.artifacts/probes';
await mkdir(directory, { recursive: true });
for (const width of [1440, 390]) {
  const page = await browser.newPage({ viewport: { width, height: 1000 } });
  await page.route('**/api/**', (route) => {
    const url = route.request().url();
    if (url.includes('/cmdb/tree')) return route.fulfill({ json: { businesses: [] } });
    if (url.includes('/probes/policy')) return route.fulfill({ json: { allowed_cidrs: [] } });
    if (url.includes('/probes')) return route.fulfill({ json: { items: [], total: 0 } });
    return route.fulfill({ status: 503, json: { detail: 'Visual fixture' } });
  });
  await page.route('**/api/v1/auth/me', (route) => route.fulfill({ json: { id: 'visual-fixture', username: 'fixture', display_name: '界面检查', role: 'auditor', enabled: true, must_change_password: false, created_at: '', last_login_at: null } }));
  await page.goto('http://localhost:8088');
  if (width === 390) await page.getByRole('button', { name: '打开导航' }).click();
  const nav = width === 390 ? page.getByRole('dialog') : page.locator('.desktop-sider');
  await nav.getByText('运维中心', { exact: true }).click();
  await nav.getByText('服务/API 拨测', { exact: true }).click();
  await page.getByRole('heading', { name: '服务/API 拨测' }).waitFor();
  if (width === 390) await page.locator('.mobile-navigation').waitFor({ state: 'hidden' });
  await page.waitForTimeout(500);
  if (!await page.getByRole('button', { name: '新增目标' }).isDisabled()) throw new Error('Auditor create control must be disabled');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2);
  if (overflow) throw new Error(`Page overflow at ${width}`);
  await page.screenshot({ path: `${directory}/${width}.png`, fullPage: true });
  await page.close();
}
await browser.close();
console.log('Probe page desktop/mobile: visible, auditor controls disabled, no page overflow');
