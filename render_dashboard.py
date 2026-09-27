"""Build a self-contained page using the same renderer as client refreshes."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def render_dashboard(status: dict, root: Path) -> str:
    # JSON script data must not be able to close its script element.
    payload = json.dumps(status, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    body = subprocess.check_output(
        ["node", str(root / "ui" / "dashboard.js")],
        input=json.dumps(status), text=True,
    )
    css = (root / "ui" / "dashboard.css").read_text()
    script = (root / "ui" / "dashboard.js").read_text()
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#101112">
<meta name="description" content="Current LLM work and project health, in one place.">
<title>Bob Ops — Operations overview</title>
<style>{css}</style>
</head>
<body>
<a class="skip-link" href="#main">Skip to overview</a>
<div class="shell">
  <header class="masthead">
    <a class="brand" href="#main" aria-label="Bob Ops overview"><span class="brand-mark" aria-hidden="true">b.</span><span>bob<span class="brand-light"> / ops</span></span></a>
    <nav aria-label="Board navigation"><a href="#work-now">Work now</a><a href="#projects">Projects</a></nav>
    <span class="owner">Jeff Story<span class="avatar" aria-hidden="true">JS</span></span>
  </header>
  <main id="main" tabindex="-1">
    <div class="page-heading"><div><p class="eyebrow">OPERATIONS / OVERVIEW</p><h1>Operations overview.</h1><p class="intro">What’s running. What’s next.</p></div>
      <div class="freshness-wrap"><span id="freshness" role="status">Saved snapshot</span><button class="refresh-button" id="refresh-status" type="button" aria-label="Refresh status"><svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 7a7 7 0 0 1 11.7-1L20 9M4 15l2.2 3A7 7 0 0 0 18 17"/></svg></button></div>
    </div>
    <div id="snapshot-notice" class="snapshot-notice" role="status" hidden><span id="snapshot-message"></span><button type="button" id="retry-status">Retry now</button></div>
    <noscript><p class="snapshot-notice">Saved snapshot. Enable JavaScript for automatic status updates.</p></noscript>
    <div id="board">{body}</div>
  </main>
  <footer><span><span class="footer-dot" aria-hidden="true"></span> Bob Ops <span class="footer-divider">/</span> A clear view of the work.</span><a href="./status.json">View snapshot <span aria-hidden="true">↗</span></a></footer>
</div>
<script type="application/json" id="initial-status">{payload}</script>
<script>{script}</script>
</body>
</html>
'''
