import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output = process.env.VISUAL_OUTPUT || '/tmp/sentinel-login-responsive';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH, args: ['--no-sandbox'] });
const sizes = [
  ['wide', 2560, 1440], ['desktop', 1920, 1080], ['zoom110', 1745, 900],
  ['laptop', 1440, 900], ['short', 1366, 768], ['compact', 1280, 720],
  ['zoom150', 1280, 640], ['tablet-landscape', 1024, 768],
  ['tablet', 768, 1024], ['mobile', 390, 844], ['small', 320, 568],
  ['keyboard', 390, 420], ['landscape', 844, 390],
  ['wide-short', 1920, 768], ['ultrawide-short', 2560, 720],
  ['break1000', 1000, 800], ['break1001', 1001, 801],
  ['break600', 600, 800], ['break601', 601, 801],
  ['break1250', 1250, 800], ['break1251', 1251, 801],
];
try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  // Authentication is stubbed: layout checks must never create real login attempts.
  await page.route('**/api/v1/auth/login', (route) => route.fulfill({
    status: 401, json: { detail: 'Visual validation only' },
  }));
  await page.goto(process.env.APP_URL || 'http://localhost:8088', { waitUntil: 'networkidle' });
  for (const [name, width, height] of sizes) {
    await page.setViewportSize({ width, height });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(200);
    const geometry = await page.evaluate(() => {
      const rect = (selector) => document.querySelector(selector).getBoundingClientRect();
      const panel = rect('.access-panel');
      const form = rect('.access-panel form');
      const intro = rect('.access-intro');
      const title = rect('.access-intro h1');
      const scene = rect('.access-scene');
      const copy = rect('.access-capabilities');
      const button = rect('.access-submit');
      return {
        overflow: document.documentElement.scrollWidth > innerWidth,
        fullHeight: rect('.access-layout').height >= innerHeight,
        formWidth: form.width, formInside: form.left >= panel.left && form.right <= panel.right,
        titleInside: title.left >= intro.left && title.right <= intro.right,
        separate: scene.width > 0 && (scene.right + 20 <= copy.left || scene.bottom + 20 <= copy.top),
        buttonVisible: button.top >= 0 && button.bottom <= innerHeight,
        formFirst: panel.top === 0 && intro.top >= panel.bottom,
      };
    });
    assert.equal(geometry.overflow, false, name + ': horizontal overflow');
    assert.ok(geometry.fullHeight, name + ': must fill browser');
    assert.ok(geometry.formWidth <= 440 && geometry.formInside, name + ': form bounds');
    assert.ok(geometry.titleInside && geometry.separate, name + ': composition overlap');
    if (height >= 568) assert.ok(geometry.buttonVisible, name + ': login below first viewport');
    if (width <= 1000) assert.ok(geometry.formFirst, name + ': small screens must prioritize login');
    for (const state of ['idle', 'input', 'closed', 'loading', 'failure', 'success', 'welcome', 'wave']) {
      await page.locator('.login-companion').evaluate((node, next) => {
        node.className = 'login-companion pet-' + next;
        node.dataset.state = next;
      }, state);
      await page.mouse.move(width - 10, height / 2);
      for (const delay of [350, 500, 650]) {
        await page.waitForTimeout(delay);
        assert.ok(await page.locator('.access-scene').evaluate((node) => {
          const box = node.getBoundingClientRect();
          const terminal = node.querySelector('.access-terminal').getBoundingClientRect();
          return [...node.querySelectorAll('.character-head, .character-body, .character-hand')].every((part) => {
            const bounds = part.getBoundingClientRect();
            return bounds.left >= box.left - 1 && bounds.right <= box.right + 1 && bounds.top >= terminal.bottom + 8;
          });
        }), name + ': animated character outside scene in ' + state);
      }
    }
    await page.locator('.login-companion').evaluate((node) => {
      node.className = 'login-companion pet-idle';
      node.dataset.state = 'idle';
    });
    await page.screenshot({ path: output + '/' + name + '.png', fullPage: true });
    await page.getByLabel('密码', { exact: true }).fill('visual-only');
    await page.getByRole('button', { name: /登\s*录/ }).scrollIntoViewIfNeeded();
    assert.ok(await page.getByRole('button', { name: /登\s*录/ }).isVisible());
    console.log(name + ': responsive layout and animation bounds passed');
  }
  assert.deepEqual(errors, []);
} finally {
  await browser.close();
}
