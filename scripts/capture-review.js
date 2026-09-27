'use strict';

// Capture the generated board without contacting any external site.
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require('playwright');

async function main() {
  const root = path.resolve(process.argv[2] || '.');
  const label = process.argv[3];
  if (!/^(before|after)$/.test(label)) throw new Error('Pass a board directory and before or after');
  const output = path.resolve(__dirname, '../reviews');
  fs.mkdirSync(output, { recursive: true });
  const server = http.createServer((req, res) => {
    const file = new URL(req.url, 'http://localhost').pathname === '/status.json' ? 'status.json' : 'index.html';
    res.writeHead(200, { 'Content-Type': file.endsWith('.json') ? 'application/json' : 'text/html', 'Cache-Control': 'no-store' });
    res.end(fs.readFileSync(path.join(root, file)));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  const browser = await chromium.launch();
  try {
    for (const [device, width, height] of [['phone', 390, 844], ['desktop', 1440, 1000]]) {
      const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1, reducedMotion: 'reduce' });
      await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      await page.goto(origin, { waitUntil: 'networkidle' });
      await page.screenshot({ path: path.join(output, `${label}-${device}.png`), fullPage: true });
      console.log(`${label}-${device}.png: ${width} x ${height} viewport`);
      await page.close();
    }
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
