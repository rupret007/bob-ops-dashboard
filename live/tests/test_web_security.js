'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
const elements = new Map();
const created = [];
function element() {
  const el = {
    innerHTML: '', textContent: '', style: {}, children: [],
    classList: {toggle() {}, add() {}, remove() {}},
    getAttribute() { return '0 0 300 40'; },
    setAttribute() {}, removeAttribute() {},
    appendChild(child) { this.children.push(child); },
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
assert.ok(elements.get('lanes').innerHTML.includes('&lt;IMG'), 'uppercase labels retain valid HTML entities');
elements.get('tacho').onclick();
assert.ok(elements.get('sheetBody').innerHTML.includes(escaped));
for (const el of created) {
  assert.ok(!/<img|<IMG|onerror="alert/.test(el.innerHTML), 'raw payload reached innerHTML');
}
console.log('Live UI security regression passed');
