#!/usr/bin/env node
"use strict";

// Offline regression gate. Execute the generated dashboard's actual functions;
// every fetch, clock, timer, and navigation is an in-memory fixture.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { execFileSync } = require("node:child_process");

const ROOT = __dirname;
const INDEX = process.argv[2] || path.join(ROOT, "index.html");
const html = fs.readFileSync(INDEX, "utf8");
const source = Array.from(html.matchAll(/<script>([\s\S]*?)<\/script>/g), match => match[1]).join("\n");

function functionSource(name) {
  const start = source.indexOf("function " + name + "(");
  assert.notEqual(start, -1, "Generated script must contain " + name);
  // The generator deliberately uses consistent indentation. Matching the
  // declaration's closing line avoids interpreting regex quotes as JS strings.
  const indent = source.slice(source.lastIndexOf("\n", start) + 1, start);
  assert.match(indent, /^ *$/, "Expected standalone generated declaration " + name);
  const end = source.indexOf("\n" + indent + "}", start);
  assert.ok(end > start, "Expected closing generated declaration " + name);
  return source.slice(start, end + indent.length + 2);
}

function contextWith(names, globals = {}) {
  const context = vm.createContext({ URL, URLSearchParams, AbortController, ...globals });
  vm.runInContext(names.map(functionSource).join("\n"), context, { timeout: 1000 });
  return context;
}

function plain(value) { return JSON.parse(JSON.stringify(value)); }
function clone(value) { return JSON.parse(JSON.stringify(value)); }
async function settle() { for (let i = 0; i < 20; i += 1) await Promise.resolve(); }

const now = Date.parse("2026-09-05T12:00:00Z");
class FixtureDate extends Date {
  constructor(...args) { super(...(args.length ? args : [now])); }
  static now() { return now; }
}

const item = {
  id: "review-fixture",
  title: "Review the prepared offline fixture",
  detail: "Read the complete prepared draft before deciding. Nothing is sent or published by this board.",
  risk: "high",
  kind: "ops",
};

function snapshot(pending = [item], at = "2026-09-05T11:55:00Z") {
  return {
    generated_at: at,
    generated_at_display: "Offline fixture time",
    pending: clone(pending),
    agents: [],
    cloud_agents: [],
    fetched_repos: [],
    sections: [
      { id: "controls", title: "Decisions", projects: [] },
      { id: "live-shipping", title: "Live", projects: [] },
    ],
  };
}

const cases = [];
function test(name, run) { cases.push({ name, run }); }

const helperNames = ["decisionReviewText", "decisionReviewItem", "decisionReviewIdentity", "decisionTimestamp", "decisionSnapshotTime", "validateAcceptedSnapshot"];
function helpers() { return contextWith(helperNames, { Date: FixtureDate }); }

// Semantic DOM doubles: the production controller creates every review link and
// receives real bubbling-style click events. No receipt/eligibility logic lives
// in this fixture, and no external navigation is performed.
function controller(initial = snapshot()) {
  const listeners = {};
  const opened = [];
  const native = [];
  const timers = [];
  let time = now;
  let popupAvailable = true;
  let document;
  class Element {
    constructor(tag = "div") {
      this.tagName = tag.toUpperCase(); this.attributes = {}; this.childNodes = [];
      this.parentNode = null; this.disabled = false; this.hidden = false; this.style = {};
      this._text = "";
    }
    setAttribute(key, value) { this.attributes[key] = String(value); }
    getAttribute(key) { return Object.hasOwn(this.attributes, key) ? this.attributes[key] : null; }
    removeAttribute(key) { delete this.attributes[key]; }
    get className() { return this.getAttribute("class") || ""; }
    set className(value) { this.setAttribute("class", value); }
    get textContent() { return this._text + this.childNodes.map(child => child.textContent).join(""); }
    set textContent(value) { this._text = String(value); this.childNodes.forEach(child => { child.parentNode = null; }); this.childNodes = []; }
    appendChild(child) { child.parentNode = this; this.childNodes.push(child); return child; }
    removeChild(child) { this.childNodes = this.childNodes.filter(node => node !== child); child.parentNode = null; }
    remove() { if (this.parentNode) this.parentNode.removeChild(this); }
    matches(selector) {
      const attribute = selector.match(/^(?:([a-z]+))?\[([^=\]]+)(?:=["']?([^"'\]]+)["']?)?\]$/i);
      if (attribute) return (!attribute[1] || this.tagName === attribute[1].toUpperCase()) && this.getAttribute(attribute[2]) !== null && (attribute[3] === undefined || this.getAttribute(attribute[2]) === attribute[3]);
      if (selector.startsWith(".")) return this.className.split(/\s+/).includes(selector.slice(1));
      return this.tagName === selector.toUpperCase();
    }
    querySelectorAll(selector) {
      const out = [];
      for (const child of this.childNodes) { if (child.matches(selector)) out.push(child); out.push(...child.querySelectorAll(selector)); }
      return out;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    closest(selector) { return this.matches(selector) ? this : this.parentNode?.closest(selector) || null; }
    focus() { document.activeElement = this; }
    click() { return click(this); }
  }
  const body = new Element("body");
  const status = new Element();
  const initialData = new Element("script"); initialData.textContent = JSON.stringify(initial);
  const rows = [];
  for (const value of initial.pending || []) {
    const row = new Element(); row.className = "pending-item";
    row.setAttribute("data-id", value.id); row.setAttribute("data-title", value.title);
    const button = new Element("button"); button.setAttribute("data-review-decision", value.id); button.textContent = "Review choices";
    const panel = new Element(); panel.className = "decision-review";
    row.appendChild(button); row.appendChild(panel); body.appendChild(row); rows.push(row);
  }
  document = {
    body, activeElement: null,
    getElementById(id) { return id === "panel-status" ? status : id === "initial-snapshot" ? initialData : null; },
    querySelectorAll(selector) { return body.querySelectorAll(selector); },
    querySelector(selector) { return body.querySelector(selector); },
    createElement(tag) { return new Element(tag); },
    addEventListener(name, handler) { (listeners[name] ||= []).push(handler); },
  };
  function click(target, fields = {}) {
    const event = { target, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...fields };
    if (target.disabled) return event;
    for (const listener of listeners.click || []) listener(event);
    const href = target.getAttribute("href") || target.href;
    if (!event.defaultPrevented && target.tagName === "A" && href) native.push(href);
    return event;
  }
  class Clock extends Date {
    constructor(...args) { super(...(args.length ? args : [time])); }
    static now() { return time; }
  }
  const window = {
    open(url, target, options) { opened.push({ url, target, options }); return popupAvailable ? {} : null; },
    addEventListener(name, handler) { (listeners[name] ||= []).push(handler); },
  };
  const context = vm.createContext({
    document, window, Date: Clock, URL, URLSearchParams, console,
    localStorage: { removeItem() {}, setItem() {} }, navigator: {},
    setTimeout(handler) { timers.push(handler); return timers.length; }, clearTimeout() {},
  });
  const start = source.indexOf("(function () {");
  assert.ok(start >= 0, "Decision controller closure is required");
  const end = source.indexOf("})();", start);
  assert.ok(end > start, "Decision controller must be a complete generated closure");
  vm.runInContext(source.slice(0, end + 5), context, { timeout: 1000 });
  assert.ok(window.bobDecisionReview, "Generated controller must expose its production integration seam");
  return {
    api: window.bobDecisionReview, context, rows, document, opened, native, click, status,
    row(id = item.id) { return rows.find(row => row.getAttribute("data-id") === id); },
    review(id = item.id) { return click(this.row(id).querySelector("[data-review-decision]")); },
    link(verb = "APPROVE", id = item.id) { return this.row(id).querySelector('[data-dec="' + verb + '"]'); },
    advance(ms) { time += ms; },
    flushTimers() { timers.splice(0).forEach(timer => timer()); },
    blockPopup() { popupAvailable = false; },
  };
}

test("canonical review identity preserves every public decision field", () => {
  const api = helpers();
  assert.deepEqual(plain(api.decisionReviewItem(item)), item);
  assert.equal(api.decisionReviewIdentity(item), JSON.stringify([item.id, item.title, item.detail, item.risk, item.kind]));
  for (const field of ["id", "title", "detail", "risk", "kind"]) {
    const changed = { ...item, [field]: field === "risk" ? "low" : item[field] + "-changed" };
    assert.notEqual(api.decisionReviewIdentity(changed), api.decisionReviewIdentity(item), field + " is identity-bearing");
  }
  assert.deepEqual(plain(api.decisionReviewItem({ id: item.id, title: item.title, risk: item.risk })), { ...item, detail: "", kind: "ops" });
});

test("decision fields fail closed rather than silently truncate or coerce", () => {
  const api = helpers();
  for (const invalid of [
    null, [], "decision", { ...item, id: "bad id" }, { ...item, id: "x".repeat(65) }, { ...item, id: "valid\n" }, { ...item, id: "valid\r" },
    { ...item, title: " " }, { ...item, title: 123 }, { ...item, title: "x".repeat(161) },
    { ...item, title: "x\ny" }, { ...item, detail: null }, { ...item, detail: "x".repeat(2001) },
    { ...item, detail: "private\u0000text" }, { ...item, detail: "bad\u007ftext" },
    { ...item, detail: "bad\ud800text" }, { ...item, detail: "bad\udc00text" },
    { ...item, risk: "HIGH" }, { ...item, risk: "unknown" }, { ...item, risk: null },
    { ...item, kind: null }, { ...item, kind: " " }, { ...item, kind: "x".repeat(65) },
    { ...item, kind: "bad\nkind" }, { ...item, kind: "valid\n" }, { ...item, kind: "valid\r" }, { ...item, kind: "\u00e9" },
  ]) {
    assert.equal(api.decisionReviewItem(invalid), null, JSON.stringify(invalid));
    assert.equal(api.decisionReviewIdentity(invalid), "", "Malformed decision has no receipt identity");
  }
  const exact = { ...item, title: "x".repeat(160), detail: "\tFirst line\nSecond line\r\n" + "x".repeat(1973) };
  assert.deepEqual(plain(api.decisionReviewItem(exact)), exact);
  const supplementary = { ...item, title: "\ud83c\udfa8".repeat(80) };
  assert.equal(api.decisionReviewItem(supplementary).title, supplementary.title);
  assert.equal(api.decisionReviewItem({ ...item, title: supplementary.title + "x" }), null);
});

test("only complete current-shaped snapshots may authorize review", () => {
  const api = helpers();
  assert.equal(api.validateAcceptedSnapshot(snapshot()), true);
  assert.equal(api.validateAcceptedSnapshot(snapshot([])), true, "Verified header-equivalent empty pending list is valid");
  for (const invalid of [null, [], {}, { generated_at: snapshot().generated_at }, { error: "Unavailable" }]) {
    assert.equal(api.validateAcceptedSnapshot(invalid), false, JSON.stringify(invalid));
  }
  for (const key of ["sections", "pending", "agents", "cloud_agents", "fetched_repos"]) {
    const missing = snapshot(); delete missing[key];
    assert.equal(api.validateAcceptedSnapshot(missing), false, "Missing " + key);
    assert.equal(api.validateAcceptedSnapshot({ ...snapshot(), [key]: {} }), false, "Malformed " + key);
  }
  assert.equal(api.validateAcceptedSnapshot({ ...snapshot(), sections: [] }), false);
  assert.equal(api.validateAcceptedSnapshot({ ...snapshot(), sections: [{ id: "live-shipping", projects: [] }] }), false);
  assert.equal(api.validateAcceptedSnapshot(snapshot([item, { ...item, id: item.id.toUpperCase() }])) , false, "Case variants cannot select an ambiguous owner decision");
  assert.equal(api.validateAcceptedSnapshot({ ...snapshot(), sections: [snapshot().sections[0], snapshot().sections[0]] }), false);
  assert.equal(api.validateAcceptedSnapshot({ ...snapshot(), sections: [{ id: "controls", projects: [null] }] }), false);
});

test("unparseable and future snapshot clocks cannot revive historical decisions", () => {
  const api = helpers();
  for (const at of [null, "", "yesterday", "2026-09-05", "2026-09-05T11:55:00", "2026-09-05T12:00:01Z", "2036-09-05T11:55:00Z", "2026-09-05T11:55:00Z\n", "2026-09-05T11:55:00Z\r", "2026-02-30T11:55:00Z"]) {
    assert.equal(api.validateAcceptedSnapshot(snapshot([item], at)), false, String(at));
  }
  assert.equal(api.validateAcceptedSnapshot(snapshot([item], "2026-09-05T11:55:00Z")), true);
});

test("Python and browser receipts agree exactly without truncation", () => {
  const ctx = contextWith([...helperNames, "reviewDecisionHref"], { Date: FixtureDate });
  for (const detail of [item.detail, "Full context: café 🎨 & <tag> 'quote' !()* + \tline\nNext line", "x".repeat(2000)]) {
    const value = { ...item, detail };
    for (const at of [snapshot().generated_at, "2026-09-05T06:55:00.123456-05:00"]) {
      const expected = execFileSync("python3", ["-c", "import json,sys; from board_meta import review_decision_href; print(review_decision_href('APPROVE', json.loads(sys.argv[1]), sys.argv[2]))", JSON.stringify(value), at], { cwd: ROOT, encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } }).trim();
      assert.equal(ctx.reviewDecisionHref("APPROVE", value, at), expected);
    }
  }
  const large = { ...item, detail: "🎨".repeat(1000) };
  assert.equal(ctx.reviewDecisionHref("APPROVE", large, snapshot().generated_at), "", "Overlong encoded receipt cannot silently omit context");
});

test("soft-painted phone review exposes complete escaped text without immediate approval", () => {
  const ctx = contextWith([...helperNames, "focusKey", "esc", "pendingShell"], { Date: FixtureDate });
  const detail = "A".repeat(150) + '\n<img src="fixture" onerror="unsafe()"> & full end';
  const markup = ctx.pendingShell([{ ...item, title: "<b>Review</b>", detail }]);
  assert.ok(markup.includes("A".repeat(150)), "Detail is never the old 72-character excerpt");
  assert.ok(markup.includes("full end"));
  assert.ok(markup.includes("&lt;img")); assert.ok(markup.includes("&lt;b&gt;Review&lt;/b&gt;"));
  assert.ok(!markup.includes('<img src="fixture"'));
  assert.ok(markup.includes('data-review-decision="' + item.id + '"'));
  assert.ok(!markup.includes("issues/new?"), "No live issue composition before deliberate review");
  assert.doesNotMatch(html, /\.pending-item \.pdetail\s*\{[^}]*display:\s*none/);
});

test("generated page never has a second pending-only fetch", () => {
  assert.doesNotMatch(source, /fetch\([^\n]*\n?(?:[^\n]*\n){0,8}[^\n]*renderPending/);
  assert.doesNotMatch(source, /function loadPending\(/);
});

test("review is explicit, read-only, and creates exact native GitHub links", () => {
  const ui = controller();
  ui.api.accept(snapshot()); ui.api.setTrust("current");
  assert.equal(ui.link(), null);
  ui.api.open("APPROVE", item.id, item.title);
  assert.equal(ui.opened.length, 0, "Direct helper cannot bypass review");
  ui.review();
  assert.equal(ui.opened.length, 0); assert.equal(ui.native.length, 0);
  for (const [verb, label] of [["APPROVE", "Open approval draft"], ["HOLD", "Open hold draft"], ["DENY", "Open denial draft"]]) {
    const link = ui.link(verb); assert.ok(link, verb + " link after explicit review");
    assert.equal(link.textContent, label);
    assert.equal(link.getAttribute("target"), "_blank");
    assert.equal(link.getAttribute("rel"), "noopener noreferrer");
    const url = new URL(link.getAttribute("href"));
    assert.equal(url.origin + url.pathname, "https://github.com/rupret007/bob-ops-dashboard/issues/new");
    assert.equal(url.searchParams.get("title"), "BOB-" + verb + ": " + item.id);
    const body = url.searchParams.get("body");
    for (const expected of [item.id, item.title, item.detail, item.risk, item.kind, snapshot().generated_at, "rupret007"]) assert.ok(body.includes(expected), "Receipt includes " + expected);
  }
  const link = ui.link();
  const event = ui.click(link);
  assert.equal(event.defaultPrevented, false, "Native anchor is the primary iOS-safe path");
  assert.equal(ui.native.length, 1); assert.equal(ui.opened.length, 0);
  assert.equal(ui.click(link).defaultPrevented, true, "Duplicate same-choice click is debounced");
  assert.equal(ui.native.length, 1);
});

test("fallback preserves reviewed identity and never loses the iOS anchor", () => {
  const ui = controller(); ui.api.accept(snapshot()); ui.api.setTrust("current"); ui.review();
  ui.blockPopup(); ui.api.open("HOLD", item.id, "Untrusted replacement title");
  assert.equal(ui.opened.length, 1); assert.equal(ui.native.length, 1);
  assert.equal(ui.opened[0].url, ui.native[0]);
  assert.ok(new URL(ui.native[0]).searchParams.get("body").includes(item.title));
  assert.ok(!new URL(ui.native[0]).searchParams.get("body").includes("Untrusted replacement title"));
  ui.api.open("DELETE", item.id, item.title);
  assert.equal(ui.opened.length, 1);
});

test("failure and expiry invalidate review and prevent stale composition", () => {
  for (const state of ["poll-failed", "refresh-overdue", "invalid"]) {
    const ui = controller(); ui.api.accept(snapshot()); ui.api.setTrust("current"); ui.review();
    ui.api.setTrust(state);
    assert.equal(ui.link(), null); assert.equal(ui.row().querySelector("[data-review-decision]").disabled, true);
    ui.api.open("APPROVE", item.id, item.title);
    assert.equal(ui.native.length + ui.opened.length, 0);
    ui.api.accept(snapshot()); ui.api.setTrust("current");
    assert.equal(ui.link(), null, "Recovery requires a new review");
    ui.review(); assert.ok(ui.link());
  }
  const ui = controller(); ui.api.accept(snapshot()); ui.api.setTrust("current"); ui.review();
  const retained = ui.link(); ui.advance(46 * 60 * 1000);
  assert.equal(ui.click(retained).defaultPrevented, true, "Event handler rechecks clock even before paint timer runs");
  assert.equal(ui.native.length + ui.opened.length, 0);
});

test("changed, removed, duplicate and malformed pending cannot reuse a reviewed choice", () => {
  const newer = "2026-09-05T11:56:00Z";
  const changed = snapshot([{ ...item, detail: "Different exact requested scope" }], newer);
  for (const candidate of [changed, snapshot([], newer), snapshot([item, item], newer), { generated_at: newer }, snapshot([item], "2036-01-01T00:00:00Z")]) {
    const ui = controller(); ui.api.accept(snapshot()); ui.api.setTrust("current"); ui.review();
    if (ui.api.accept(candidate)) ui.api.setTrust("current");
    ui.api.reconcileRendering();
    assert.equal(ui.link(), null, "Changed or invalid snapshot removes review actions");
    ui.api.open("APPROVE", item.id, item.title);
    assert.equal(ui.native.length + ui.opened.length, 0);
  }
});

test("timestamp-only accepted refresh keeps exact choice but updates receipt time", () => {
  const ui = controller(); ui.api.accept(snapshot()); ui.api.setTrust("current"); ui.review();
  const oldLink = ui.link();
  const newer = "2026-09-05T11:56:00Z";
  assert.equal(ui.api.accept(snapshot([item], newer)), true);
  ui.api.setTrust("current"); ui.api.reconcileRendering();
  assert.ok(ui.link());
  assert.ok(new URL(ui.link().getAttribute("href")).searchParams.get("body").includes(newer));
  assert.notEqual(ui.link().getAttribute("href"), oldLink.getAttribute("href"));
  assert.equal(ui.native.length + ui.opened.length, 0);
});

test("unchanged trust preserves keyboard focus and timestamp refresh restores the exact action", () => {
  const ui = controller(); ui.api.accept(snapshot()); ui.api.setTrust("current"); ui.review();
  const hold = ui.link("HOLD"); hold.focus();
  for (let tick = 0; tick < 5; tick += 1) ui.api.setTrust("current");
  assert.equal(ui.link("HOLD"), hold, "One-second trust painting must not replace the focused control");
  assert.equal(ui.document.activeElement, hold);
  ui.api.accept(snapshot([item], "2026-09-05T11:56:00Z"));
  ui.api.reconcileRendering();
  assert.equal(ui.document.activeElement, ui.link("HOLD"), "Receipt refresh preserves the chosen action, not its neighbor");
  ui.api.setTrust("poll-failed");
  assert.equal(ui.document.activeElement, ui.row(), "A removed action returns focus to its own read-only decision");
});

function pollHarness() {
  const ui = controller(); ui.api.accept(snapshot()); ui.api.setTrust("current"); ui.review();
  const requests = [];
  const timers = new Map();
  let timerId = 0;
  let paints = 0;
  const names = [...helperNames, "boardFingerprint", "parseStampMs", "pollIsNewer", "pollFailureCounts", "pollPaintDecision", "applyStamp", "poll"];
  const ctx = contextWith(names, {
    Date: FixtureDate, window: { bobDecisionReview: ui.api },
    known: snapshot().generated_at, knownMs: Date.parse(snapshot().generated_at),
    lastFp: null, pollSeq: 0, pollFailStreak: 0, lastPollOk: now,
    pollAbort: null, pollTimeout: null, POLL_TIMEOUT_MS: 8000,
    lastAgents: [], lastCloud: [], stamp: { setAttribute() {} }, displayEl: null, dot: null,
    freshness: { textContent: "", classList: { add() {} } },
    setRetryBusy() {}, sanitizeCloudAgents(value) { return value; }, paintAgents() {},
    renderBoard() { paints += 1; }, paint() {},
    setTimeout(callback) { timerId += 1; timers.set(timerId, callback); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    fetch(url, options) {
      assert.ok(url.startsWith("./status.json?ts="), "Only local snapshot reads are permitted");
      assert.equal(options.cache, "no-store");
      assert.ok(options.signal instanceof AbortSignal);
      return new Promise((resolve, reject) => requests.push({ url, options, resolve, reject }));
    },
  });
  ctx.lastFp = ctx.boardFingerprint(snapshot());
  ctx.updateSilence = () => ui.api.setTrust(ctx.pollFailStreak ? "poll-failed" : "current");
  return { ui, ctx, requests, timers, get paints() { return paints; },
    respond(index, payload, status = 200) { requests[index].resolve({ ok: status >= 200 && status < 300, status, json: async () => payload }); },
  };
}

test("actual poll cannot relabel old decisions current from incomplete, stale or future JSON", async () => {
  const invalidBodies = [
    {}, { generated_at: "2026-09-05T11:56:00Z" },
    snapshot([item], "2026-09-05T11:54:00Z"), snapshot([item], "2036-01-01T00:00:00Z"),
    snapshot([item, { ...item, id: item.id.toUpperCase() }]),
    { ...snapshot(), sections: [{ id: "controls", projects: null }] },
  ];
  for (const payload of invalidBodies) {
    const harness = pollHarness();
    harness.ctx.poll(); harness.respond(0, payload); await settle();
    assert.equal(harness.ctx.known, snapshot().generated_at, "Invalid response must not advance displayed freshness");
    assert.equal(harness.ctx.pollFailStreak, 1);
    assert.equal(harness.paints, 0);
    assert.equal(harness.ui.link(), null);
    harness.ui.api.open("APPROVE", item.id, item.title);
    assert.equal(harness.ui.opened.length + harness.ui.native.length, 0);
  }
});

test("actual poll failures preserve last snapshot and recovery requires a fresh review", async () => {
  for (const mode of ["http", "reject", "json"]) {
    const harness = pollHarness(); harness.ctx.poll();
    if (mode === "http") harness.respond(0, snapshot(), 503);
    else if (mode === "reject") harness.requests[0].reject(new Error("Offline fixture failure"));
    else harness.requests[0].resolve({ ok: true, json: async () => { throw new SyntaxError("Malformed fixture"); } });
    await settle();
    assert.equal(harness.ctx.pollFailStreak, 1); assert.equal(harness.ui.link(), null);
    harness.ctx.poll(); harness.respond(1, snapshot()); await settle();
    assert.equal(harness.ctx.pollFailStreak, 0); assert.equal(harness.ui.link(), null);
    harness.ui.review(); assert.ok(harness.ui.link());
  }
});

test("actual poll sequence discards an old response after a newer resolved-decision snapshot", async () => {
  const harness = pollHarness(); harness.ctx.poll(); harness.ctx.poll();
  assert.equal(harness.requests[0].options.signal.aborted, true);
  harness.respond(1, snapshot([], "2026-09-05T11:56:00Z")); await settle();
  assert.equal(harness.ctx.known, "2026-09-05T11:56:00Z");
  assert.equal(harness.ctx.pollFailStreak, 0); assert.equal(harness.ui.link(), null);
  harness.respond(0, snapshot()); await settle();
  assert.equal(harness.ctx.known, "2026-09-05T11:56:00Z");
  assert.equal(harness.ctx.pollFailStreak, 0, "Superseded requests are not new failures");
  harness.ui.api.open("APPROVE", item.id, item.title);
  assert.equal(harness.ui.opened.length + harness.ui.native.length, 0);
});

test("tampered native issue URLs cannot replace the exact reviewed choice", () => {
  for (const href of ["https://evil.example/issues/new", "https://github.com/rupret007/bob-ops-dashboard/issues/new?title=BOB-APPROVE%3A+other", "javascript:alert(1)"]) {
    const ui = controller(); ui.api.accept(snapshot()); ui.api.setTrust("current"); ui.review();
    const link = ui.link(); link.setAttribute("href", href);
    assert.equal(ui.click(link).defaultPrevented, true);
    assert.equal(ui.native.length + ui.opened.length, 0);
    assert.equal(ui.link(), null, "Tampered choice forces another review");
  }
});

async function run() {
  assert.ok(source, "Expected generated inline dashboard script");
  for (const entry of cases) {
    await entry.run();
    console.log("PASS: " + entry.name);
  }
  console.log("decision-review smoke ok (" + cases.length + " offline cases)");
}

if (require.main === module) run().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});

module.exports = { functionSource, contextWith, plain, clone, settle, FixtureDate, item, snapshot, controller };
