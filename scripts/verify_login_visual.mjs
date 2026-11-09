import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH });
const output = process.env.VISUAL_OUTPUT || '/tmp/sentinel-login-visual';
await mkdir(output, { recursive: true });
try {
  for (const [name, width, height] of [['desktop', 1440, 900], ['mobile', 390, 844], ['mobile-small', 320, 740]]) {
    const page = await browser.newPage({ viewport: { width, height } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(process.env.APP_URL || 'http://localhost:8088', { waitUntil: 'networkidle' });
    const companion = page.getByRole('img', { name: /登录助手/ });
    await companion.waitFor();
    assert.equal(await page.locator('.scene-character').count(), 4);
    assert.equal(await page.locator('.scene-grid').count(), 0);
    await page.mouse.move(width - 20, height / 2);
    await page.waitForTimeout(600);
    const checkFace = async () => {
      const intact = await page.locator('.character-sun').evaluate((node) => {
        const head = node.querySelector('.character-head');
        const bounds = head.getBoundingClientRect();
        return getComputedStyle(node).backgroundColor === 'rgba(0, 0, 0, 0)'
          && getComputedStyle(head).backgroundColor === 'rgb(240, 201, 91)'
          && [...node.querySelectorAll('.pet-eye, .pet-mouth')].every((part) => {
            const rect = part.getBoundingClientRect();
            return rect.left >= bounds.left && rect.right <= bounds.right
              && rect.top >= bounds.top && rect.bottom <= bounds.bottom;
          });
      });
      assert.ok(intact, 'sun face must remain inside its moving yellow head');
    };
    await checkFace();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    const composition = await page.locator('.access-intro-copy').evaluate((node) => {
      const scene = node.querySelector('.access-scene').getBoundingClientRect();
      const title = node.querySelector('h1').getBoundingClientRect();
      const copy = node.querySelector('.access-capabilities').getBoundingClientRect();
      return getComputedStyle(node).textAlign === 'left'
        && title.bottom + 24 <= scene.top
        && (innerWidth > 600 ? scene.right + 20 <= copy.left : scene.bottom + 20 <= copy.top);
    });
    assert.ok(composition, 'approved title, scene and capabilities must remain spaced without overlap');
    assert.equal(await page.locator('.access-capability').count(), 4);
    assert.ok(await page.locator('.access-terminal').innerText().then((text) => text.includes('运维场景示意')));
    if (width <= 600) {
      const button = await page.getByRole('button', { name: /登\s*录/ }).boundingBox();
      assert.ok(button && button.y >= 0 && button.y + button.height <= height);
    }
    await page.screenshot({ path: `${output}/${name}-idle.png`, fullPage: true });
    await page.getByLabel('用户名', { exact: true }).fill('visual-check');
    assert.equal(await companion.getAttribute('data-state'), 'input');
    await page.getByLabel('密码', { exact: true }).fill('Visual-Only-Password');
    assert.equal(await companion.getAttribute('data-state'), 'closed');
    await page.waitForTimeout(1000);
    await page.screenshot({ path: `${output}/${name}-closed.png`, fullPage: true });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    assert.equal(await page.locator('.character-pose').first().evaluate((node) => getComputedStyle(node).animationName), 'none');
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    let release;
    await page.route('**/api/v1/auth/login', async (route) => {
      await new Promise((resolve) => { release = resolve; });
      await route.fulfill({ status: 401, json: { detail: '登录失败' } });
    });
    await page.getByRole('button', { name: /登\s*录/ }).click();
    await page.waitForFunction(() => document.querySelector('.login-companion')?.dataset.state === 'loading');
    assert.ok(release);
    release();
    await page.waitForFunction(() => document.querySelector('.login-companion')?.dataset.state === 'failure');
    await page.waitForTimeout(1000);
    await checkFace();
    await page.screenshot({ path: `${output}/${name}-failure-right.png`, fullPage: true });
    await page.unroute('**/api/v1/auth/login');
    await page.route('**/api/v1/auth/login', (route) => route.fulfill({ status: 200, json: {
      id: 'visual-only', username: 'visual-check', display_name: 'Visual', role: 'auditor',
      enabled: true, must_change_password: false, created_at: '2026-09-16T00:00:00Z', last_login_at: null,
    } }));
    await page.getByRole('button', { name: /登\s*录/ }).click();
    await page.waitForFunction(() => document.querySelector('.login-companion')?.dataset.state === 'success');
    await page.screenshot({ path: `${output}/${name}-success.png`, fullPage: true });
    assert.equal(errors.length, 0, errors.join('; '));
    await page.close();
    console.log(`${name}: layout, four characters, focus, loading, failure, success, reduced motion passed`);
  }
} finally {
  await browser.close();
}
