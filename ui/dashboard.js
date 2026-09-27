/* Shared, dependency-free renderer: Node at build time, browser at refresh time. */
(function () {
  'use strict';

  const LANES = [['codex', 'Codex', '>_'], ['claude', 'Claude', '*'], ['cursor', 'Cursor', '/'], ['gemini', 'Gemini', '+'], ['minimax', 'MiniMax', 'M']];
  const PROJECTS = [
    ['web-bob', 'Web Bob', 'WB', 'Operations dashboard', 'bob-ops-dashboard'],
    ['webjam', 'WebJam', 'WJ', 'Music collaboration', 'webjam'],
    ['adoptiq', 'AdoptIQ', 'AQ', 'Customer success', 'AdoptIQ'],
    ['storyboard', 'StoryBoard', 'SB', 'Band business', 'StoryBoard'],
    ['storyland-fantasy', 'Storyland Fantasy', 'SF', 'Fantasy football', 'Storyland-Fantasy-Football']
  ];
  const PROOF_KEYS = ['heartbeat_at', 'proof_at', 'checked_at', 'updated_at'];
  const INACTIVE_SOURCES = ['', 'idle', 'byok_oneshot', 'mini_paste', 'spend_wall'];
  const POLL_MS = 30000, TIMEOUT_MS = 8000, PROOF_TTL = 900000, SNAPSHOT_TTL = 2700000;
  const text = value => typeof value === 'string' ? value : '';
  const list = value => Array.isArray(value) ? value.filter(row => row && typeof row === 'object') : [];
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const stamp = value => Date.parse(text(value)) || 0;
  const laneId = value => value === 'cursor-cloud' ? 'cursor' : value;

  function safeLink(value) {
    const url = text(value);
    // Absolute, allowlisted hosts and shapes; no credentials, query or fragments.
    if (/^https:\/\/github\.com\/rupret007\/[A-Za-z0-9_.-]+(?:\/(?:pull\/[1-9][0-9]*|actions(?:\/runs\/[1-9][0-9]*)?|pulls|commit\/[0-9a-f]{7,40}|releases\/latest))?$/.test(url)) return url;
    if (/^https:\/\/cursor\.com\/agents\/bc-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(url)) return url;
    return '';
  }
  function link(url, label, attrs = '') {
    const href = safeLink(url);
    return href ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer" ${attrs}>${label}</a>` : label;
  }
  function ago(value, now) {
    const ts = typeof value === 'number' ? value : stamp(value);
    if (!ts || ts > now + 300000) return 'Age unknown';
    const mins = Math.floor(Math.max(0, now - ts) / 60000);
    if (mins < 1) return 'Just now';
    if (mins < 60) return mins + 'm ago';
    const hours = Math.floor(mins / 60);
    if (hours < 24) return hours + 'h ago';
    return Math.floor(hours / 24) + 'd ago';
  }
  function projectName(repo) {
    const name = text(repo).split('/').pop();
    const known = PROJECTS.find(p => p[4].toLowerCase() === name.toLowerCase());
    return known ? known[1] : name || 'No project assigned';
  }
  function workRows(data, now) {
    const raw = list(data.llm_work);
    return LANES.map(([id, name, icon]) => {
      // Prefer an explicit Cursor row over the legacy cloud alias.
      const row = raw.find(r => r.id === id) || raw.find(r => laneId(r.id) === id) || {};
      const proof = PROOF_KEYS.map(key => stamp(row[key])).find(Boolean) || 0;
      let state = ['running', 'idle', 'finished', 'blocked', 'unknown'].includes(row.status) ? row.status : 'unknown';
      if (state === 'running' && (INACTIVE_SOURCES.includes(text(row.source)) || !proof || now - proof > PROOF_TTL || proof > now + 300000)) state = 'unknown';
      let task = text(state === 'running' ? row.task_title : (row.last_task || row.task_title));
      task = task.replace(/^(FINISHED|IDLE|RUNNING|BLOCKED(?:_SPEND_WALL)?)\s*[-\u2014:]\s*/i, '');
      if (row.source === 'spend_wall') task = 'Waiting for capacity';
      if (!task || task === 'needs assignment') task = state === 'unknown' ? 'No verified activity' : 'Ready for the next assignment';
      return { id, name, icon, state, task, project: projectName(row.repo), proof,
        detail: text(row.goal || row.note || row.why), pr: safeLink(row.pr_url), agent: safeLink(row.agent_url) };
    });
  }
  function projectRows(data) {
    return PROJECTS.map(([id, name, icon, description, repo]) => {
      const p = list(data.projects).find(p => p.id === id) || {};
      const restricted = p.private === true || id === 'adoptiq';
      const repoUrl = !restricted && p.repo_url === 'https://github.com/rupret007/' + repo ? safeLink(p.repo_url) : '';
      const tip = repoUrl && /^[0-9a-f]{7,40}$/.test(text(p.tip_sha)) ? p.tip_sha : '';
      const ci = p.ci && typeof p.ci === 'object' ? p.ci : {};
      // CI belongs to the current tip, never an older run or a different repo.
      const matchesTip = tip && /^[0-9a-f]{7,40}$/.test(text(ci.sha)) && (ci.sha.startsWith(tip) || tip.startsWith(ci.sha));
      let conclusion = !restricted && matchesTip ? text(ci.conclusion) : '';
      if (conclusion === 'timed_out' || conclusion === 'action_required' || conclusion === 'startup_failure') conclusion = 'failure';
      const ciLabel = restricted ? 'Private' : ({ success: 'Passing', failure: 'Failing', in_progress: 'Running', queued: 'Queued', waiting: 'Waiting', pending: 'Pending', requested: 'Pending', cancelled: 'Cancelled', skipped: 'Skipped' }[conclusion] || 'No run');
      const sameRepo = url => repoUrl && text(url).startsWith(repoUrl + '/') ? safeLink(url) : '';
      const drafts = restricted ? [] : list(p.draft_prs).filter(pr => Number.isInteger(pr.number) && pr.number > 0 && pr.url === repoUrl + '/pull/' + pr.number && sameRepo(pr.url));
      const ciUrl = !restricted && matchesTip && /^https:\/\/github\.com\/rupret007\/[^/]+\/actions\/runs\/[1-9][0-9]*$/.test(text(ci.html_url)) ? sameRepo(ci.html_url) : '';
      let next = text(p.next_action);
      if (restricted) next = id === 'adoptiq' ? 'Validate the offline candidate; await owner review.' : 'Review progress in the private workspace.';
      else if (conclusion === 'failure') next = 'Resolve the failing checks on the current tip.';
      else if (['in_progress', 'queued', 'waiting', 'pending', 'requested'].includes(conclusion)) next = 'Wait for the current CI run to finish.';
      else if (drafts.length) next = 'Review ' + (drafts.length === 1 ? 'draft #' + drafts[0].number : drafts.length + ' drafts') + '; keep work in draft.';
      else if (p.open_prs > 0) next = 'Review ' + p.open_prs + ' open ' + (p.open_prs === 1 ? 'pull request.' : 'pull requests.');
      else if (!tip) next = 'Refresh repository data before choosing the next step.';
      else if (!conclusion) next = 'Verify checks for the current tip.';
      if (!next) next = 'Choose the next project task.';
      return { id, name, icon, description, restricted, repoUrl, tip, drafts,
        draftsKnown: p.pr_listing_complete === true, ciLabel, conclusion, ciUrl, next,
        notes: restricted ? (id === 'adoptiq' ? 'Offline candidate only. Live Cisco readiness remains disabled.' : 'Repository details stay in the private workspace.') : text(p.notes),
        readyPr: !restricted ? sameRepo(p.open_pr_url) : '' };
    });
  }
  function stateHtml(state, label) { return `<span class="state ${esc(state)}">${esc(label || state.charAt(0).toUpperCase() + state.slice(1))}</span>`; }
  function ageHtml(proof, now) { return `<time class="agent-age" data-age-ts="${proof}" ${proof ? `datetime="${new Date(proof).toISOString()}" title="Last evidence: ${new Date(proof).toISOString()}"` : 'title="No evidence timestamp was supplied"'}>${ago(proof, now)}</time>`; }
  function workHtml(rows, now) {
    const running = rows.filter(row => row.state === 'running').length;
    const blocked = rows.filter(row => row.state === 'blocked').length;
    return `<section id="work-now" aria-labelledby="work-heading">
      <div class="section-heading"><h2 id="work-heading">Work now</h2><span class="count">05</span><span class="section-note">Across your LLMs</span></div>
      <div class="work-panel"><div class="running-summary"><span class="summary-icon" aria-hidden="true">&#8627;</span><strong>${running ? running + (running === 1 ? ' agent running' : ' agents running') : 'No verified running work'}</strong><span>${blocked ? blocked + ' blocked' : '5 LLMs tracked'}</span></div>
      ${rows.map(row => `<article class="agent-row" data-lane-id="${row.id}" data-lane-status="${row.state}" aria-label="${row.name}: ${row.state}">
        <h3 class="agent-name"><span class="agent-icon" aria-hidden="true">${esc(row.icon)}</span>${row.name}</h3>
        <span class="agent-project">${esc(row.project)}</span><p class="agent-task">${esc(row.task)}</p>${stateHtml(row.state)}${ageHtml(row.proof, now)}
      </article>`).join('')}</div></section>`;
  }
  function projectsHtml(rows) {
    return `<section id="projects" aria-labelledby="projects-heading"><div class="section-heading"><h2 id="projects-heading">Projects</h2><span class="count">05</span><span class="section-note">The next move, for each</span></div><div class="project-grid">
      ${rows.map(p => {
        const draftLabel = p.restricted ? 'Private' : p.drafts.length ? (p.drafts.length === 1 ? '#' + p.drafts[0].number : p.drafts.length + ' drafts') : p.draftsKnown ? 'None open' : 'Unknown';
        const draftUrl = p.drafts.length === 1 ? p.drafts[0].url : p.drafts.length > 1 ? p.repoUrl + '/pulls' : '';
        return `<article class="project-card" id="project-${p.id}" data-project-id="${p.id}">
          <div class="project-top"><span class="project-icon" aria-hidden="true">${p.icon}</span><div><h3>${p.name}</h3><p class="project-description">${p.description}</p></div>${p.repoUrl ? link(p.repoUrl, '&#8599;', `class="project-open" aria-label="Open ${p.name} repository"`) : ''}</div>
          <dl class="project-meta"><div><dt>Tip SHA</dt><dd>${p.tip ? link(p.repoUrl + '/commit/' + p.tip, `<code>${p.tip.slice(0, 7)}</code>`, `aria-label="${p.name} commit ${p.tip.slice(0, 7)}"`) : p.restricted ? 'Private' : 'Unknown'}</dd></div>
          <div><dt>Draft PR</dt><dd>${link(draftUrl, esc(draftLabel))}</dd></div><div><dt>CI</dt><dd>${link(p.ciUrl, stateHtml(p.conclusion || 'unknown', p.ciLabel))}</dd></div></dl>
          <div class="next-action"><span class="next-label">Next action</span><p><span class="arrow" aria-hidden="true">&#8594;</span>${esc(p.next)}</p></div>
          <details class="project-detail" data-detail="${p.id}"><summary>Details</summary><p>${esc(p.notes || 'Repository and checks reflect the last successful refresh.')}</p>
          ${p.restricted ? '<p>Tip SHA, draft PR, and CI details are private.</p>' : `<div class="detail-links">${link(p.repoUrl, 'Repository')}${p.readyPr ? link(p.readyPr, 'Open PR') : ''}${p.ciUrl ? link(p.ciUrl, 'CI run') : ''}${p.drafts.map(pr => link(pr.url, 'Draft #' + pr.number)).join('')}</div>`}</details>
        </article>`;
      }).join('')}</div></section>`;
  }
  function decisionHref(verb, item) {
    if (!['APPROVE', 'HOLD', 'DENY'].includes(verb) || !/^[a-zA-Z0-9._-]{1,64}$/.test(text(item.id))) return '';
    const title = 'BOB-' + verb + ': ' + item.id;
    const body = 'Dashboard decision\n\nid: ' + item.id + '\ntitle: ' + text(item.title).slice(0, 160) + '\ndecision: ' + verb.toLowerCase() + '\n\nSubmit while logged in as rupret007. The GitHub issue records the decision.';
    return 'https://github.com/rupret007/bob-ops-dashboard/issues/new?title=' + encodeURIComponent(title) + '&body=' + encodeURIComponent(body);
  }
  function secondaryHtml(data, rows) {
    const pending = list(data.pending).filter(p => decisionHref('HOLD', p));
    return `<div class="secondary">
      <details data-detail="decisions"><summary>Decisions waiting<span class="count">${pending.length}</span></summary><div class="secondary-content"><p>Decision links open a draft GitHub issue. Submit the issue to record your decision.</p>
      ${pending.map(item => `<article class="decision"><h3>${esc(item.title)}</h3><p>${esc(item.detail)}</p><div class="decision-links">${['APPROVE', 'HOLD', 'DENY'].map(verb => `<a href="${esc(decisionHref(verb, item))}" target="_blank" rel="noopener noreferrer">${verb.charAt(0) + verb.slice(1).toLowerCase()}</a>`).join('')}</div></article>`).join('') || '<p>No decisions waiting.</p>'}</div></details>
      <details data-detail="activity"><summary>Activity details</summary><div class="secondary-content">${rows.map(row => `<div class="agent-detail"><strong>${row.name}</strong><p>${esc(row.detail || 'No additional activity reported.')}</p>${row.pr || row.agent ? `<div class="detail-links">${row.pr ? link(row.pr, 'Open PR') : ''}${row.agent ? link(row.agent, 'Open agent') : ''}</div>` : ''}</div>`).join('')}</div></details>
      <details data-detail="snapshot"><summary>About this snapshot</summary><div class="secondary-content"><p>Last collected: <time id="snapshot-collected">${esc(data.generated_at_display || data.generated_at || 'Unknown')}</time>.</p><p>Repository data refreshes every 15 minutes. This page checks for updates every 30 seconds while visible. Running requires activity evidence within 15 minutes; age is time since that evidence, not the last page refresh.</p><p>Missing CI means no verified run for the current tip. Private projects keep repository details private.</p>${data.harden_window ? `<p>Last verification round: ${esc(data.harden_window.round || 'Unknown')} &middot; ${esc(data.harden_window.display_status || data.harden_window.status || 'Unknown')}.</p>` : ''}</div></details>
    </div>`;
  }
  function viewModel(data, now = Date.now()) {
    return { work: workRows(data, now), projects: projectRows(data), pending: list(data.pending), harden: data.harden_window || null };
  }
  function renderBoard(data, now = Date.now()) {
    const view = viewModel(data, now);
    return workHtml(view.work, now) + projectsHtml(view.projects) + secondaryHtml(data, view.work);
  }
  function fingerprint(data, now = Date.now()) { return JSON.stringify(viewModel(data, now)); }
  function validSnapshot(data) {
    return !!(data && data.schema_version === 2 && stamp(data.generated_at) && stamp(data.generated_at) <= Date.now() + 300000 && Array.isArray(data.projects) && PROJECTS.every(([id]) => data.projects.some(p => p && p.id === id)) && Array.isArray(data.llm_work));
  }

  function startBrowser() {
    const board = document.getElementById('board');
    const refresh = document.getElementById('refresh-status');
    const retry = document.getElementById('retry-status');
    let data = JSON.parse(document.getElementById('initial-status').textContent);
    let lastFp = fingerprint(data), seq = 0, controller = null, timeout = null, interval = null;
    let failed = false, busy = false;

    function renderChanged() {
      const nextFp = fingerprint(data);
      if (nextFp === lastFp) return;
      const open = Array.from(board.querySelectorAll('details[open]'), el => el.dataset.detail);
      const focused = document.activeElement;
      const detail = focused && focused.closest('details');
      const focusKey = focused && focused.tagName === 'SUMMARY' && detail ? detail.dataset.detail : '';
      const focusHref = focused && focused.tagName === 'A' ? focused.getAttribute('href') : '';
      board.innerHTML = renderBoard(data);
      for (const el of board.querySelectorAll('details')) {
        el.open = open.includes(el.dataset.detail);
        if (focusKey && el.dataset.detail === focusKey) el.querySelector('summary').focus({ preventScroll: true });
      }
      if (focusHref) {
        const target = Array.from(board.querySelectorAll('a')).find(a => a.getAttribute('href') === focusHref);
        if (target) target.focus({ preventScroll: true });
      }
      lastFp = nextFp;
    }
    function paint() {
      // Age and heartbeat expiry continue to advance without a new snapshot.
      renderChanged();
      const now = Date.now(), generated = stamp(data.generated_at);
      const overdue = !generated || now - generated > SNAPSHOT_TTL;
      const stale = failed || overdue;
      const freshness = document.getElementById('freshness');
      freshness.textContent = (stale ? 'Last verified ' : 'Updated ') + ago(generated, now).toLowerCase();
      freshness.dataset.stale = String(stale || now - generated > PROOF_TTL);
      document.getElementById('snapshot-notice').hidden = !stale;
      document.getElementById('snapshot-message').textContent = failed ? 'Could not refresh. Showing the last verified snapshot.' : 'Refresh is overdue. Showing the last verified snapshot.';
      board.dataset.snapshotTrust = stale ? 'historical' : 'current';
      for (const el of board.querySelectorAll('[data-age-ts]')) {
        const label = ago(Number(el.dataset.ageTs), now);
        if (el.textContent !== label) el.textContent = label;
      }
      const collected = document.getElementById('snapshot-collected');
      if (collected) collected.textContent = data.generated_at_display || data.generated_at;
    }
    function setBusy(value) {
      busy = value;
      refresh.disabled = value;
      retry.disabled = value;
      refresh.setAttribute('aria-busy', String(value));
      retry.textContent = value ? 'Checking...' : 'Retry now';
    }
    async function poll() {
      if (busy || document.visibilityState === 'hidden') return;
      const current = ++seq;
      controller = new AbortController();
      const requestController = controller;
      setBusy(true);
      timeout = setTimeout(() => requestController.abort(), TIMEOUT_MS);
      try {
        const response = await fetch('./status.json?ts=' + Date.now(), { cache: 'no-store', signal: requestController.signal });
        if (!response.ok) throw new Error('HTTP ' + response.status);
        const next = await response.json();
        if (current !== seq) return;
        if (!validSnapshot(next)) throw new Error('Invalid snapshot');
        // A cached older body must never rewind data or clear a failure warning.
        if (stamp(next.generated_at) < stamp(data.generated_at)) return;
        data = next;
        failed = false;
        paint();
      } catch (_) {
        if (current !== seq) return;
        failed = true;
        paint();
      } finally {
        if (current === seq) {
          clearTimeout(timeout);
          setBusy(false);
        }
      }
    }
    function stop() {
      ++seq;
      clearInterval(interval);
      clearTimeout(timeout);
      if (controller) controller.abort();
      setBusy(false);
    }
    function start() {
      stop();
      paint();
      void poll();
      interval = setInterval(poll, POLL_MS);
    }
    refresh.addEventListener('click', poll);
    retry.addEventListener('click', poll);
    document.addEventListener('visibilitychange', () => document.visibilityState === 'hidden' ? stop() : start());
    window.addEventListener('pagehide', stop);
    window.addEventListener('pageshow', () => { if (document.visibilityState !== 'hidden') start(); });
    setInterval(() => { if (document.visibilityState !== 'hidden') paint(); }, 10000);
    paint();
    if (document.visibilityState !== 'hidden') start();
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { LANES, PROJECTS, esc, safeLink, ago, workRows, projectRows, decisionHref, viewModel, renderBoard, fingerprint, validSnapshot };
    if (require.main === module) {
      const input = require('node:fs').readFileSync(0, 'utf8');
      process.stdout.write(renderBoard(JSON.parse(input)));
    }
  } else {
    startBrowser();
  }
}());
