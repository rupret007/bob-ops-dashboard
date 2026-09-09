'use strict';

// Only the two marked synthetic artifacts are served; no repository browsing.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require('playwright');

async function textIsUnclipped(locator) {
  return locator.evaluate(element => {
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    let count = 0;
    while (walker.nextNode()) {
      const text = walker.currentNode;
      if (!text.textContent.trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(text);
      for (const rect of range.getClientRects()) {
        if (!rect.width || !rect.height) continue;
        count++;
        if (rect.left < -1 || rect.right > innerWidth + 1) return false;
        for (let parent = text.parentElement; parent; parent = parent.parentElement) {
          const style = getComputedStyle(parent);
          if (style.visibility !== 'visible' || style.display === 'none') return false;
          if (Number.parseInt(style.webkitLineClamp, 10) > 0) return false;
          const box = parent.getBoundingClientRect();
          const left = box.left + parent.clientLeft, top = box.top + parent.clientTop;
          if (/^(hidden|clip|auto|scroll)$/.test(style.overflowX) &&
              (rect.left < left - 1 || rect.right > left + parent.clientWidth + 1)) return false;
          if (/^(hidden|clip|auto|scroll)$/.test(style.overflowY) &&
              (rect.top < top - 1 || rect.bottom > top + parent.clientHeight + 1)) return false;
        }
      }
    }
    return count > 0;
  });
}

async function rejectsClippedText(locator) {
  const original = await locator.getAttribute('style');
  try {
    await locator.evaluate(element => {
      element.style.cssText += ';display:block;max-width:20px;max-height:8px;overflow:hidden';
    });
    assert.equal(await textIsUnclipped(locator), false, 'Visibility check must reject clipped text');
  } finally {
    await locator.evaluate((element, style) => {
      if (style === null) element.removeAttribute('style');
      else element.setAttribute('style', style);
    }, original);
  }
}

async function main() {
  assert.equal(process.argv.length, 3, 'Pass the qa-offline.py fixture directory');
  const root = path.resolve(process.argv[2]);
  const status = fs.readFileSync(path.join(root, 'status.json'));
  const html = fs.readFileSync(path.join(root, 'index.html'));
  const marker = JSON.parse(status).offline_fixture;
  assert.equal(marker?.synthetic, true, 'Refusing an unmarked/live snapshot');
  assert.equal(marker?.publishable, false, 'Fixture must not be publishable');
  assert(html.includes('data-offline-fixture="true"'), 'Missing visible fixture label');

  const server = http.createServer((req, res) => {
    if (!['GET', 'HEAD'].includes(req.method)) {
      res.writeHead(405).end();
      return;
    }
    const pathname = new URL(req.url, 'http://localhost').pathname;
    const resource = pathname === '/status.json' ? [status, 'application/json']
      : ['/', '/index.html'].includes(pathname) ? [html, 'text/html']
      : null;
    if (!resource) {
      res.writeHead(404).end();
      return;
    }
    res.writeHead(200, { 'Content-Type': resource[1], 'Cache-Control': 'no-store' });
    res.end(req.method === 'HEAD' ? undefined : resource[0]);
  });
  let browser;
  try {
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(0, '127.0.0.1', resolve);
    });
    const origin = 'http://127.0.0.1:' + server.address().port;
    browser = await chromium.launch({ headless: true });
    for (const width of [320, 390, 1280]) {
      const context = await browser.newContext({
        viewport: { width, height: 844 }, serviceWorkers: 'block',
      });
      const errors = [], blocked = [], popups = [];
      await context.route('**/*', route => {
        const request = route.request();
        if (new URL(request.url()).origin === origin && request.method() === 'GET') {
          return route.continue();
        }
        blocked.push(request.method() + ' ' + request.url());
        return route.abort();
      });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      page.on('popup', popup => { popups.push(popup.url()); void popup.close(); });
      await page.goto(origin, { waitUntil: 'networkidle' });
      assert(await page.locator('[data-offline-fixture="true"]').isVisible());
      const glance = page.locator('#board-glance');
      // Existing test identifiers are synthetic, not access to the named repo.
      assert.equal(await glance.innerText(), 'Decide: AdoptIQ live Cisco');
      assert.equal(await glance.getAttribute('data-focus-target'), 'decision:adoptiq-live-cisco');
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      assert(await textIsUnclipped(glance), 'Glance label must be fully rendered');
      await rejectsClippedText(glance);
      await glance.click();
      const row = page.locator('[data-focus-key="decision:adoptiq-live-cisco"]');
      await row.waitFor({ state: 'visible' });
      assert((await row.innerText()).includes('AdoptIQ live Cisco readiness'));
      const title = row.locator('.ptitle');
      assert(await textIsUnclipped(title), 'Full destination title must not be clipped');
      await rejectsClippedText(title);
      assert(await row.evaluate(el => el === document.activeElement));
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      assert.deepEqual(errors, [], 'Browser script errors');
      assert.deepEqual(blocked, [], 'Unexpected external request or write');
      assert.deepEqual(popups, [], 'Navigation must not open an issue composer');
      await context.close();
      console.log('OFFLINE BROWSER PASS: ' + width + 'px, full-title exact-row focus, no overflow/writes/external requests');
    }
  } finally {
    if (browser) await browser.close();
    if (server.listening) await new Promise(resolve => server.close(resolve));
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
