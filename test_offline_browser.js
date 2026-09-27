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
  const fixture = JSON.parse(fs.readFileSync(path.join(root, 'status.json')));
  const html = fs.readFileSync(path.join(root, 'index.html'));
  assert.equal(fixture.offline_fixture?.synthetic, true);
  assert.equal(fixture.offline_fixture?.publishable, false);
  assert(html.includes('data-offline-fixture="true"'));
  let snapshot = structuredClone(fixture), mode = 'ok', releaseHeld;
  const server = http.createServer((req, res) => {
    const name = new URL(req.url, 'http://localhost').pathname;
    if (name === '/status.json') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(snapshot));
    } else {
      res.writeHead(200, { 'Content-Type': 'text/html' });
      res.end(html);
    }
  });
  let browser;
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = 'http://127.0.0.1:' + server.address().port;
    browser = await chromium.launch({ headless: true });
    for (const width of [320, 390, 768, 1280]) {
      snapshot = structuredClone(fixture); mode = 'ok';
      const context = await browser.newContext({ viewport: { width, height: 844 }, serviceWorkers: 'block', reducedMotion: 'reduce' });
      const errors = [], blocked = [], popups = [];
      await context.route('**/*', async route => {
        const request = route.request();
        const url = new URL(request.url());
        if (url.origin !== origin || request.method() !== 'GET') {
          blocked.push(request.method() + ' ' + request.url());
          return route.abort();
        }
        if (url.pathname !== '/status.json') return route.continue();
        if (mode === 'hold') {
          const held = JSON.stringify(snapshot);
          await new Promise(resolve => { releaseHeld = resolve; });
          return route.fulfill({ contentType: 'application/json', body: held }).catch(() => {});
        }
        if (mode === 'fail') return route.fulfill({ status: 503, body: 'Fixture outage' });
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify(snapshot) });
      });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      page.on('popup', popup => { popups.push(popup.url()); void popup.close(); });
      const baseTime = Date.parse(fixture.generated_at);
      await page.clock.install({ time: new Date(baseTime) });
      await page.goto(origin, { waitUntil: 'networkidle' });
      await page.waitForFunction(() => !document.querySelector('#refresh-status').disabled);
      assert.equal(await page.locator('.agent-row').count(), 5);
      assert.equal(await page.locator('.project-card').count(), 5);
      assert.equal(await page.locator('details[open]').count(), 0);
      assert.equal(await page.locator('#auth-panel, #songs, [data-lane-id="grok"]').count(), 0);
      assert((await page.locator('.running-summary').innerText()).includes('1 agent running'));
      assert((await page.locator('#project-webjam').innerText()).includes('#42'));
      assert((await page.locator('#project-storyboard').innerText()).includes('Queued'));
      assert((await page.locator('#project-web-bob').innerText()).includes('No run'));
      assert.equal(await page.locator('#project-adoptiq a').count(), 0);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      for (const locator of await page.locator('.agent-task, .agent-project, .agent-age, .project-card h3, .next-action p').all()) {
        assert(await textIsUnclipped(locator), 'Required text must be fully visible at ' + width);
      }
      await rejectsClippedText(page.locator('.agent-task').first());
      // Native details are keyboard operable; inspect secondary text without navigation.
      const summary = page.locator('[data-detail="webjam"] > summary');
      await summary.focus(); await page.keyboard.press('Enter');
      assert(await page.locator('[data-detail="webjam"]').evaluate(el => el.open));
      assert(await page.locator('[data-detail="webjam"] p').isVisible());
      await page.locator('[data-detail="decisions"] > summary').click();
      const approve = page.locator('.decision-links a').first();
      assert.equal(await approve.getAttribute('target'), '_blank');
      assert((await approve.getAttribute('href')).includes('BOB-APPROVE'));
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));

      if (width === 390) {
        async function refresh() {
          await page.locator('#refresh-status').click();
          await page.waitForFunction(() => !document.querySelector('#refresh-status').disabled);
        }
        function advanceSnapshot(seconds = 1) {
          snapshot.generated_at = new Date(Date.parse(snapshot.generated_at) + seconds * 1000).toISOString();
        }
        await page.evaluate(() => { window.originalCard = document.querySelector('#project-webjam'); });
        advanceSnapshot(); await refresh();
        assert(await page.evaluate(() => window.originalCard === document.querySelector('#project-webjam')), 'Timestamp-only poll must preserve DOM');
        await summary.focus();
        snapshot.projects.find(p => p.id === 'webjam').draft_prs[0] = { number: 43, url: 'https://github.com/rupret007/webjam/pull/43', draft: true };
        advanceSnapshot();
        // Poll through the timer so the focused disclosure can be preserved.
        await page.clock.runFor(30000);
        await page.waitForFunction(() => document.querySelector('#project-webjam').textContent.includes('#43'));
        assert(await page.locator('[data-detail="webjam"]').evaluate(el => el.open));
        assert(await page.locator('[data-detail="decisions"]').evaluate(el => el.open));
        assert.equal(await page.evaluate(() => document.activeElement.closest('details')?.dataset.detail), 'webjam');

        const newer = structuredClone(snapshot);
        snapshot = structuredClone(fixture); // older CDN response
        await refresh();
        assert((await page.locator('#project-webjam').innerText()).includes('#43'), 'Cached data cannot rewind the board');
        mode = 'fail'; await refresh();
        assert(await page.locator('#snapshot-notice').isVisible());
        assert.equal(await page.locator('#board').getAttribute('data-snapshot-trust'), 'historical');
        assert((await page.locator('#project-webjam').innerText()).includes('#43'));
        mode = 'ok'; await refresh();
        assert(await page.locator('#snapshot-notice').isVisible(), 'An older cache response must not clear the failure');
        snapshot = newer; advanceSnapshot();
        await page.locator('#retry-status').click();
        await page.waitForFunction(() => document.querySelector('#snapshot-notice').hidden);
        assert.equal(await page.locator('#board').getAttribute('data-snapshot-trust'), 'current');

        // Wrong schema is an outage, not an empty board.
        const good = snapshot; snapshot = { generated_at: good.generated_at };
        await refresh(); assert(await page.locator('#snapshot-notice').isVisible());
        snapshot = good; await refresh();
        assert(await page.locator('#snapshot-notice').isHidden());

        // Hide cancels a held request without changing the trust state. Late results lose the race.
        mode = 'hold'; releaseHeld = null;
        await page.locator('#refresh-status').click();
        for (let i = 0; !releaseHeld && i < 30; i++) await new Promise(resolve => setTimeout(resolve, 20));
        assert(releaseHeld, 'Expected a held in-flight poll');
        await page.evaluate(() => {
          Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
          document.dispatchEvent(new Event('visibilitychange'));
        });
        assert(await page.locator('#snapshot-notice').isHidden());
        mode = 'ok'; advanceSnapshot();
        await page.evaluate(() => {
          Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
          document.dispatchEvent(new Event('visibilitychange'));
        });
        releaseHeld();
        await page.waitForFunction(() => !document.querySelector('#refresh-status').disabled);
        assert(await page.locator('#snapshot-notice').isHidden());
        assert((await page.locator('#project-webjam').innerText()).includes('#43'));

        // Heartbeats expire independently of successful timestamp-only polls.
        await page.clock.setFixedTime(new Date(baseTime + 17 * 60000));
        await page.clock.runFor(10001);
        await page.waitForFunction(() => document.querySelector('[data-lane-id="codex"]').dataset.laneStatus === 'unknown');
        assert((await page.locator('.running-summary').innerText()).includes('No verified running work'));
        await page.clock.setFixedTime(new Date(baseTime + 46 * 60000));
        await page.clock.runFor(10001);
        assert(await page.locator('#snapshot-notice').isVisible());
        assert((await page.locator('#snapshot-message').innerText()).includes('overdue'));
      }
      assert.deepEqual(errors, [], 'Browser script errors');
      assert.deepEqual(blocked, [], 'Unexpected external request or write');
      assert.deepEqual(popups, [], 'Disclosure interaction must not open a popup');
      await context.close();
      console.log('BROWSER PASS: ' + width + 'px, five LLMs/cards, full text, keyboard, no overflow or external writes');
    }
    // The generated page is useful with scripting disabled.
    const staticPage = await browser.newPage({ javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
    await staticPage.goto(origin);
    assert.equal(await staticPage.locator('.project-card').count(), 5);
    assert.equal(await staticPage.locator('.agent-row').count(), 5);
    await staticPage.close();
    console.log('BROWSER PASS: soft refresh, stale cache, failure/retry, expiry, hide/resume races, no-JS first paint');
  } finally {
    if (browser) await browser.close();
    if (server.listening) await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
