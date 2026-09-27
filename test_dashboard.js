'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const ui = require('./ui/dashboard.js');
const now = Date.parse('2026-09-27T22:00:00Z');
const recent = new Date(now - 120000).toISOString();
const repo = 'https://github.com/rupret007/webjam';
const snapshot = () => ({ schema_version: 2, generated_at: new Date(now).toISOString(),
  projects: ui.PROJECTS.map(([id]) => ({ id })), llm_work: [], pending: [] });
const work = patch => ({ id: 'codex', status: 'running', source: 'worker', heartbeat_at: recent, task_title: 'Verify the draft', repo: 'rupret007/webjam', ...patch });
const project = patch => ({ id: 'webjam', repo_url: repo, tip_sha: 'abcdef0', pr_listing_complete: true, ...patch });
const viewProject = row => ui.projectRows({ projects: [row] })[1];

test('exactly the requested five LLMs, ordered, ignoring unrelated providers', () => {
  const rows = ui.workRows({ llm_work: [work({ id: 'other', status: 'running' })] }, now);
  assert.deepEqual(rows.map(r => r.name), ['Codex', 'Claude', 'Cursor', 'Gemini', 'MiniMax']);
  assert(rows.every(r => r.state === 'unknown'));
});
test('running requires fresh proof from an active source', () => {
  assert.equal(ui.workRows({ llm_work: [work()] }, now)[0].state, 'running');
  for (const patch of [
    { heartbeat_at: '' }, { heartbeat_at: new Date(now - 900001).toISOString() },
    { heartbeat_at: new Date(now + 300001).toISOString() },
    ...['', 'idle', 'mini_paste', 'spend_wall', 'byok_oneshot'].map(source => ({ source }))
  ]) assert.equal(ui.workRows({ llm_work: [work(patch)] }, now)[0].state, 'unknown');
});
test('all supported proof fields produce honest age; generation time never substitutes', () => {
  for (const key of ['proof_at', 'checked_at', 'updated_at']) {
    const row = ui.workRows({ llm_work: [work({ heartbeat_at: '', [key]: recent })] }, now)[0];
    assert.equal(row.proof, now - 120000);
    assert.equal(row.state, 'running');
  }
  assert.equal(ui.workRows({ generated_at: recent, llm_work: [work({ heartbeat_at: '' })] }, now)[0].proof, 0);
  assert.equal(ui.ago(0, now), 'Age unknown');
  assert.equal(ui.ago(now + 600000, now), 'Age unknown');
  assert.equal(ui.ago(now - 86400000, now), '1d ago');
});
test('legacy Cursor cloud alias is accepted but explicit Cursor takes precedence', () => {
  const rows = ui.workRows({ llm_work: [work({ id: 'cursor-cloud', status: 'blocked' }), work({ id: 'cursor' })] }, now);
  assert.equal(rows[2].state, 'running');
  assert.equal(ui.workRows({ llm_work: [work({ id: 'cursor-cloud' })] }, now)[2].state, 'running');
});
test('five project cards and work fields exist before JavaScript boots', () => {
  const data = snapshot(); data.llm_work = [work()];
  const html = ui.renderBoard(data, now);
  assert.equal((html.match(/data-project-id=/g) || []).length, 5);
  assert.equal((html.match(/data-lane-id=/g) || []).length, 5);
  for (const label of ['Tip SHA', 'Draft PR', 'CI', 'Next action', 'Web Bob', 'Storyland Fantasy', 'WebJam', 'AdoptIQ', 'StoryBoard', '2m ago']) assert(html.includes(label));
  assert(!html.includes(' open>'));
});
test('CI must match the current tip and repository before it can paint green or link', () => {
  const ci = { sha: 'abcdef012345', conclusion: 'success', html_url: repo + '/actions/runs/23' };
  assert.equal(viewProject(project({ ci })).ciLabel, 'Passing');
  assert.equal(viewProject(project({ ci })).ciUrl, ci.html_url);
  assert.equal(viewProject(project({ ci: { ...ci, sha: 'fffffff' } })).ciLabel, 'No run');
  assert.equal(viewProject(project({ ci: { ...ci, html_url: 'https://github.com/rupret007/StoryBoard/actions/runs/23' } })).ciUrl, '');
  assert.equal(viewProject(project({})).ciLabel, 'No run');
});
test('failure takes priority over draft work, queued is honest, missing is not pending', () => {
  const draft_prs = [{ number: 42, url: repo + '/pull/42' }];
  const failed = viewProject(project({ draft_prs, ci: { sha: 'abcdef0', conclusion: 'failure' } }));
  assert.match(failed.next, /failing checks/);
  assert.equal(viewProject(project({ ci: { sha: 'abcdef0', conclusion: 'queued' } })).ciLabel, 'Queued');
  assert.equal(viewProject(project({})).ciLabel, 'No run');
});
test('only verified same-repository drafts are linked; none and unknown remain distinct', () => {
  const row = project({ draft_prs: [{ number: 42, url: repo + '/pull/42' }, { number: 4, url: 'https://github.com/rupret007/StoryBoard/pull/4' }] });
  assert.equal(viewProject(row).drafts.length, 1);
  assert.match(viewProject(row).next, /draft #42/);
  assert(ui.renderBoard({ projects: [project({})] }).includes('None open'));
  assert(ui.renderBoard({ projects: [] }).includes('Unknown'));
});
test('private cards reject repository metadata even from malformed published snapshots', () => {
  for (const id of ['adoptiq', 'storyland-fantasy']) {
    const p = { id, private: true, repo_url: repo, tip_sha: 'abcdef0', notes: 'PRIVATE_MARKER', next_action: 'PRIVATE_MARKER', draft_prs: [{ number: 42, url: repo + '/pull/42' }], ci: { sha: 'abcdef0', conclusion: 'success', html_url: repo + '/actions/runs/1' } };
    const html = ui.renderBoard({ projects: [p] });
    assert(!html.includes('PRIVATE_MARKER'));
    assert(!html.includes('abcdef0'));
    assert(!html.includes(repo));
  }
});
test('links fail closed for foreign hosts, credentials, scripts, and crafted paths', () => {
  for (const url of ['javascript:alert(1)', 'https://github.com.evil.test/rupret007/webjam', 'https://evil@github.com/rupret007/webjam', repo + '/../../other', repo + '?token=secret', repo + '#bad', 'http://github.com/rupret007/webjam', '//github.com/rupret007/webjam', repo + '/pull/0', repo + '/pull/1" onclick="bad']) assert.equal(ui.safeLink(url), '', url);
  for (const url of [repo, repo + '/pull/42', repo + '/commit/abcdef0', repo + '/actions/runs/42']) assert.equal(ui.safeLink(url), url);
});
test('untrusted text is escaped in cards, work, and decisions', () => {
  const attack = '<img src=x onerror=alert(1)>';
  const html = ui.renderBoard({ projects: [project({ notes: attack, next_action: attack, ci: { sha: 'abcdef0', conclusion: 'success' } })], llm_work: [work({ task_title: attack, goal: attack })], pending: [{ id: 'safe', title: attack, detail: attack }] }, now);
  assert(!html.includes(attack));
  assert(html.includes('&lt;img'));
});
test('decision links open encoded issue drafts and reject invalid IDs/verbs', () => {
  const url = new URL(ui.decisionHref('APPROVE', { id: 'adoptiq-live-cisco', title: 'Test & review' }));
  assert.equal(url.origin, 'https://github.com');
  assert.equal(url.pathname, '/rupret007/bob-ops-dashboard/issues/new');
  assert.equal(url.searchParams.get('title'), 'BOB-APPROVE: adoptiq-live-cisco');
  assert(url.searchParams.get('body').includes('Test & review'));
  for (const id of ['', '../bad', 'a\nb', '<script>', 'x'.repeat(65)]) assert.equal(ui.decisionHref('HOLD', { id }), '');
  assert.equal(ui.decisionHref('MERGE', { id: 'safe' }), '');
});
test('timestamp-only updates preserve the content fingerprint; real changes invalidate it', () => {
  const data = snapshot(); data.llm_work = [work()];
  const fp = ui.fingerprint(data, now);
  assert.equal(ui.fingerprint({ ...data, generated_at: recent, generated_at_display: 'different' }, now + 10000), fp);
  assert.notEqual(ui.fingerprint({ ...data, llm_work: [work({ task_title: 'Changed task' })] }, now), fp);
  assert.notEqual(ui.fingerprint(data, now + 900001), fp, 'Heartbeat expiry must repaint');
  data.projects[1] = project({ draft_prs: [{ number: 43, url: repo + '/pull/43' }] });
  assert.notEqual(ui.fingerprint(data, now), fp);
});
test('malformed and previous-schema poll bodies are rejected', () => {
  assert(ui.validSnapshot(snapshot()));
  for (const data of [null, {}, { ...snapshot(), projects: [] }, { ...snapshot(), generated_at: 'invalid' }, { ...snapshot(), schema_version: 1 }, { ...snapshot(), llm_work: null }]) assert.equal(ui.validSnapshot(data), false);
});
