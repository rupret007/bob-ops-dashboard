"""Reviewable, server-owned canonical bytes of the approved Pages recipes.

Copied without transformation from immutable Stalemate commit
b6a0c10933b4522dacd7b471e219c556edb25aa4. These strings are data, never
executed or loaded from a reviewed PR. The publication verifier uses their
normal Git blob identities. Updating a recipe changes its approved fingerprint.

package-lock.json remains an opaque pin in pages_publication.py. Its exact
baseline dependencies/install lifecycles remain trusted; neither these recipes
nor their fingerprints establish dependency supply-chain safety.
"""
from __future__ import annotations

import hashlib
from types import MappingProxyType

BASELINE_COMMIT = 'b6a0c10933b4522dacd7b471e219c556edb25aa4'

# Raw strings retain canonical LF bytes, backslashes, and the final newline.
RECIPES = MappingProxyType({
    '.github/workflows/ci.yml': r"""name: Test and Pages

on:
  push:
  pull_request:

permissions:
  contents: read

jobs:
  test:
    name: test
    runs-on: ubuntu-24.04

    steps:
      - name: Check out exact workflow commit
        uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
        with:
          ref: ${{ github.sha }}

      - name: Set up Node.js
        uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4.4.0
        with:
          node-version: 24
          cache: npm

      - name: Install dependencies
        run: npm ci

      - name: Install Chromium and WebKit
        run: npx playwright install --with-deps chromium webkit

      - name: Run automated tests
        run: npm test

      - name: Preserve generated Playwright diagnostics
        if: ${{ !cancelled() }}
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2
        with:
          name: playwright-artifacts-${{ github.run_id }}-${{ github.run_attempt }}
          path: |
            playwright-report/
            test-results/
          if-no-files-found: ignore
          retention-days: 7

  deploy-pages:
    name: Deploy GitHub Pages
    needs: test
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-24.04
    permissions:
      contents: read
      pages: write
      id-token: write
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    concurrency:
      group: stalemate-pages
      cancel-in-progress: false

    steps:
      - name: Check out exact workflow commit
        uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
        with:
          ref: ${{ github.sha }}

      - name: Set up Node.js
        uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4.4.0
        with:
          node-version: 24
          cache: npm

      - name: Install dependencies
        run: npm ci

      - name: Package the public site
        run: npm run package:pages

      - name: Configure Pages
        uses: actions/configure-pages@45bfe0192ca1faeb007ade9deae92b16b8254a0d # v6.0.0

      - name: Upload Pages artifact
        uses: actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9 # v5.0.0
        with:
          path: dist

      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@368f82528645a54fb793d4d04e342629a3f51346 # v5.0.1
""".encode("utf-8"),

    'scripts/package-pages.cjs': r"""'use strict';

const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const DIST = path.join(ROOT, 'dist');

const FILES = [
    '.nojekyll',
    '404.html',
    'index.html',
    'robots.txt',
    'countdown.js',
    'script.js',
    'show-state.js',
    'site-base.js',
    'js/count.js',
    'js/track.js',
    'js/site-track.js',
    'styles.css',
    'assets/favicon.svg',
    'assets/games/turdanoid.webp',
    'assets/games/turdomania.webp',
    'assets/icon-facebook.svg',
    'assets/icon-instagram.svg',
    'assets/icon-youtube.svg',
    'assets/grain.svg',
    'assets/hero-stage.svg',
    'assets/manic-poster.jpg',
    'assets/manic.mp4',
    'assets/mark.svg',
    'assets/og.png',
    'assets/og.svg',
    'assets/torn.svg'
];

function emptyDir(dir) {
    fs.rmSync(dir, { recursive: true, force: true });
    fs.mkdirSync(dir, { recursive: true });
}

function copyFile(relativePath) {
    const from = path.join(ROOT, relativePath);
    const to = path.join(DIST, relativePath);
    fs.mkdirSync(path.dirname(to), { recursive: true });
    fs.copyFileSync(from, to);
}

const DIRS = ['play', 'qr', 'tap', 'r'];

emptyDir(DIST);
for (const relativePath of FILES) {
    copyFile(relativePath);
}
for (const dir of DIRS) {
    fs.cpSync(path.join(ROOT, dir), path.join(DIST, dir), { recursive: true });
}

process.stdout.write(`Packaged ${FILES.length} GitHub Pages files into dist/\n`);
""".encode("utf-8"),

    'package.json': r"""{
  "name": "stalemate-site",
  "version": "1.0.0",
  "private": true,
  "description": "Static site for Stalemate punk rock originals.",
  "scripts": {
    "dev:hangout": "node scripts/preview-hangout.cjs",
    "package:pilot": "node scripts/package-pilot.cjs",
    "package:beta": "node scripts/package-pilot.cjs beta",
    "package:pages": "node scripts/package-pages.cjs",
    "serve:test": "node scripts/serve-test.cjs",
    "test": "npm run test:unit && npm run test:e2e",
    "test:unit": "vitest run",
    "test:e2e": "playwright test",
    "test:install-browsers": "playwright install chromium webkit"
  },
  "devDependencies": {
    "@playwright/test": "^1.58.2",
    "jsdom": "^29.0.1",
    "vitest": "^4.1.2",
    "ws": "^8.18.3"
  },
  "dependencies": {
    "obscenity": "^0.4.6"
  }
}
""".encode("utf-8"),

    '.gitattributes': r"""* text=auto eol=lf
*.png binary
*.jpg binary
*.jpeg binary
*.webp binary
*.ico binary
*.mp4 binary
""".encode("utf-8"),

    '.github/workflows/beta-health.yml': r"""name: Private beta health

on:
  schedule:
    - cron: '7,22,37,52 * * * *'
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: stalemate-beta-health
  cancel-in-progress: false

jobs:
  health:
    name: Read-only beta health
    # Disabled until the reviewed workflow, dedicated secret and notification
    # delivery have been accepted. Dispatching another branch cannot use it.
    if: ${{ github.ref == 'refs/heads/main' && vars.STALEMATE_BETA_MONITOR_ENABLED == 'true' }}
    runs-on: ubuntu-24.04
    timeout-minutes: 2
    steps:
      - name: Check out exact workflow commit
        uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
        with:
          ref: ${{ github.sha }}
          persist-credentials: false

      - name: Set up Node.js
        uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4.4.0
        with:
          node-version: 24

      - name: Probe private beta health
        env:
          HANGOUT_BETA_OPS_KEY: ${{ secrets.HANGOUT_BETA_OPS_KEY }}
        run: node scripts/beta-health-ci.cjs
""".encode("utf-8"),

})


def recipe_blob_id(path):
    """Return the ordinary Git blob identity of one fixed publisher recipe."""
    body = RECIPES[path]
    return hashlib.sha1(b'blob ' + str(len(body)).encode('ascii') + b'\0' + body).hexdigest()
