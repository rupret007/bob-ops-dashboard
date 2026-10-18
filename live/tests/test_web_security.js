'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
const elements = new Map();
const created = [];
function escapeText(value) {
  return String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}
function element(tagName = 'div') {
  const el = {
    tagName, _html: '', _text: '', style: {}, children: [],
    get innerHTML() {
      return this._html + escapeText(this._text) + this.children.map(child =>
        `<${child.tagName}>${child.innerHTML}</${child.tagName}>`).join('');
    },
    set innerHTML(value) { this._html = value; this._text = ''; this.children = []; },
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); },
    set textContent(value) { this._text = String(value); this._html = ''; this.children = []; },
    classList: {toggle() {}, add() {}, remove() {}},
    getAttribute() { return '0 0 300 40'; },
    setAttribute() {}, removeAttribute() {},
    appendChild(child) { this.children.push(child); },
    replaceChildren(...children) { this._html = ''; this._text = ''; this.children = children; },
  };
  created.push(el);
  return el;
}
let onState;
const document = {
  body: element(),
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  },
  createElement: element,
  addEventListener() {},
};
const sandbox = {
  document, requestAnimationFrame() {}, setTimeout() {},
  EventSource: class {
    addEventListener(name, callback) { if (name === 'state') onState = callback; }
    close() {}
  },
};
vm.runInNewContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], sandbox);
const attack = '<img src=x onerror="alert(1)">\'&';
const escaped = '&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&#39;&amp;';
const speed = {score: attack, inputs: {[attack]: attack}, components: {[attack]: attack}, weights: {[attack]: 1}};
const state = {
  nodes: [{id: '__proto__', label: attack, online: true, max_lanes: attack,
    speed: {score: 1, inputs: {active_lanes: 1}}, load: {l1: attack}, disk: {free_gb: attack},
    lanes: [{name: attack, repo: attack, status: attack, runs_on: '__proto__'}]}],
  speed_index: speed,
  needs_you: [{kind: attack, title: attack, detail: attack, severity: attack}],
  ai_capacity: [{provider: attack, reset_ct: attack, remaining_pct: attack}],
  cluster_caps: [{node: attack, role: attack, os: attack, active: attack, max_lanes: attack,
    load1: attack, caps: [attack], online: true,
    llm: {[attack]: {installed: true, logged_in: true, remaining_pct: attack}}}],
  pipeline: {draft: attack},
  pipeline_rows: [{stage: attack, repo: attack, number: attack, title: attack}],
  throughput: {merged_today: attack, merged_7d_avg: attack},
  events: [{at: attack, message: attack}],
  routines: [{name: attack, node: attack, status: attack}],
};
onState({data: JSON.stringify(state)});
// Check late sections too: render catches exceptions in the SSE callback.
for (const id of ['machines', 'lanes', 'needsList', 'aiCap', 'pipeCounts', 'pipeRows', 'thruRows', 'eventList', 'routineList']) {
  assert.ok(elements.get(id).innerHTML.includes(escaped), `${id} must render escaped values`);
}
assert.ok(elements.get('gauges').children[0].innerHTML.includes(escaped));
assert.ok(elements.get('clusterCap').innerHTML.includes(escaped), 'cluster capacity must render escaped values');
const capacity = elements.get('clusterCap');
const capacityDetails = capacity.children[0].children[0];
assert.equal(capacityDetails.children[0].tagName, 'b');
assert.equal(capacityDetails.children[0].textContent, attack, 'node identity remains literal text');
assert.equal(capacityDetails.children[2].children[0].textContent, attack + ' NaN%');
assert.equal(capacityDetails.children[3].textContent, attack, 'capabilities remain literal text');
assert.equal(capacity.children[0].children[1].textContent, 'online');
assert.ok(elements.get('lanes').innerHTML.includes('&lt;IMG'), 'uppercase labels retain valid HTML entities');
elements.get('tacho').onclick();
assert.ok(elements.get('sheetBody').innerHTML.includes(escaped));
for (const el of created) {
  assert.ok(!/<img|<IMG|onerror="alert/.test(el.innerHTML), 'raw payload reached innerHTML');
}
onState({data: JSON.stringify({...state, cluster_caps: [{node: attack, online: false,
  llm: {codex: {installed: true, logged_in: false, remaining_pct: 25}}}]})});
assert.equal(capacity.children.length, 1, 'refresh replaces previous capacity rows');
assert.equal(capacity.children[0].children[0].children[2].children[0].textContent, 'codex (signed out) 25%');
assert.equal(capacity.children[0].children[1].textContent, 'offline');
onState({data: JSON.stringify({...state, cluster_caps: [{node: 'mini', llm: {codex: {installed: false}}}]})});
assert.equal(capacity.children[0].children[0].children[2].children[0].textContent, 'no LLM sign-ins');
onState({data: JSON.stringify({...state, cluster_caps: []})});
assert.equal(capacity.children.length, 1);
assert.equal(capacity.textContent, 'No cluster heartbeats yet');
console.log('Live UI security regression passed');
