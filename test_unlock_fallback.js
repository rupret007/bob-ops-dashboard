#!/usr/bin/env node
"use strict";
/**
 * Fail-closed smoke for Unlock Pages-cache fallback.
 *
 * Repro: Fastly/GitHub Pages caches status.json ~max-age 600. iPhone Unlock
 * fetch("./status.json?ts=") can still see a stale null verify after Bob
 * pushed a live challenge. curl of Pages origin may already show the hash.
 * Fallback: raw.githubusercontent.com/.../main/status.json?ts= (no-store).
 */
const fs = require("fs");
const path = require("path");

const ROOT = path.dirname(__filename);
const INDEX = process.argv[2] || path.join(ROOT, "index.html");
const REFRESH = path.join(ROOT, "refresh.sh");
const RAW = "https://raw.githubusercontent.com/rupret007/bob-ops-dashboard/main/status.json";
const JEFF = "jeffstory007@gmail.com";
const LIVE = {
  email: JEFF,
  sha256: "aa".repeat(32),
  exp: Date.now() + 60 * 60 * 1000,
  issued_at: Date.now(),
};

function fail(msg) {
  console.error("FAIL: " + msg);
  process.exit(1);
}

function extractFn(src, name) {
  const asyncMark = "async function " + name + "(";
  const syncMark = "function " + name + "(";
  let start = src.indexOf(asyncMark);
  if (start < 0) start = src.indexOf(syncMark);
  if (start < 0) throw new Error("missing " + name);
  let i = src.indexOf("{", start);
  if (i < 0) throw new Error("unopened " + name);
  let depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}") {
      depth--;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error("unclosed " + name);
}

function loadHelpers(html) {
  const scripts = html.split("<script>").slice(1).map((s) => s.split("</script>")[0]);
  const src = scripts.join("\n");
  const rawLine = src.match(/var RAW_STATUS_URL = "([^"]+)"/);
  if (!rawLine || rawLine[1] !== RAW) {
    throw new Error("RAW_STATUS_URL missing or changed: " + (rawLine && rawLine[1]));
  }
  const jeff = src.match(/var JEFF_EMAIL = "([^"]+)"/);
  if (!jeff || jeff[1] !== JEFF) throw new Error("JEFF_EMAIL allowlist changed");
  const RAW_STATUS_URL = rawLine[1];
  const hasUnlockVerify = eval("(" + extractFn(src, "hasUnlockVerify") + ")");
  const fetchJsonNoStore = eval("(" + extractFn(src, "fetchJsonNoStore") + ")");
  const loadUnlockStatus = eval("(" + extractFn(src, "loadUnlockStatus") + ")");
  return { hasUnlockVerify, fetchJsonNoStore, loadUnlockStatus, src, RAW_STATUS_URL };
}

function mockFetch(routes) {
  const calls = [];
  const fetchFn = async function (url, opts) {
    calls.push({ url: String(url), opts: opts || {} });
    if (!opts || opts.cache !== "no-store") {
      throw new Error("fetch must use cache: no-store");
    }
    const key = String(url).split("?")[0];
    const rec = routes[key];
    if (!rec) return { ok: false, status: 404, json: async () => ({}) };
    if (rec.throw) throw rec.throw;
    return {
      ok: rec.ok !== false,
      status: rec.status || 200,
      json: async () => rec.body,
    };
  };
  fetchFn.calls = calls;
  return fetchFn;
}

async function main() {
  const html = fs.readFileSync(INDEX, "utf8");
  const refresh = fs.readFileSync(REFRESH, "utf8");
  if (!refresh.includes(RAW)) fail("refresh.sh missing RAW_STATUS_URL");
  if (!refresh.includes("function loadUnlockStatus")) fail("refresh.sh missing loadUnlockStatus");
  if (!refresh.includes("function hasUnlockVerify")) fail("refresh.sh missing hasUnlockVerify");

  const h = loadHelpers(html);
  if (h.hasUnlockVerify({ verify: LIVE }) !== true) fail("hasUnlockVerify live");
  if (h.hasUnlockVerify({ verify: null }) !== false) fail("hasUnlockVerify null");
  if (h.hasUnlockVerify({}) !== false) fail("hasUnlockVerify missing");
  if (h.hasUnlockVerify({ verify: { email: JEFF } }) !== false) fail("hasUnlockVerify incomplete");

  // 1) Pages has verify: use it, do not call raw
  {
    const fetchFn = mockFetch({
      "./status.json": { body: { verify: LIVE } },
      [RAW]: { body: { verify: { email: "other@example.com", sha256: "bb".repeat(32), exp: 1 } } },
    });
    const data = await h.loadUnlockStatus(fetchFn);
    if (data.verify.sha256 !== LIVE.sha256) fail("pages hit should win");
    if (fetchFn.calls.some((c) => c.url.indexOf("raw.githubusercontent.com") !== -1)) {
      fail("must not hit raw when Pages verify is present");
    }
    if (fetchFn.calls[0].url.indexOf("?ts=") === -1) fail("pages url needs ts");
    if (fetchFn.calls[0].opts.cache !== "no-store") fail("pages fetch no-store");
  }

  // 2) Pages 200 but verify null: fallback to raw
  {
    const fetchFn = mockFetch({
      "./status.json": { body: { generated_at: "stale", verify: null } },
      [RAW]: { body: { verify: LIVE } },
    });
    const data = await h.loadUnlockStatus(fetchFn);
    if (data.verify.sha256 !== LIVE.sha256) fail("raw fallback on null Pages verify");
    if (fetchFn.calls.length !== 2) fail("expected pages then raw");
    if (fetchFn.calls[1].url.indexOf(RAW) !== 0) fail("second call must be raw URL");
    if (fetchFn.calls[1].url.indexOf("?ts=") === -1) fail("raw url needs ts");
    if (fetchFn.calls[1].opts.cache !== "no-store") fail("raw fetch no-store");
  }

  // 3) Pages HTTP fail: fallback to raw
  {
    const fetchFn = mockFetch({
      "./status.json": { ok: false, status: 503, body: {} },
      [RAW]: { body: { verify: LIVE } },
    });
    const data = await h.loadUnlockStatus(fetchFn);
    if (data.verify.email !== JEFF) fail("raw fallback on Pages HTTP error");
  }

  // 4) Pages throw: fallback to raw
  {
    const fetchFn = mockFetch({
      "./status.json": { throw: new Error("network") },
      [RAW]: { body: { verify: LIVE } },
    });
    const data = await h.loadUnlockStatus(fetchFn);
    if (data.verify.sha256 !== LIVE.sha256) fail("raw fallback on Pages throw");
  }

  // 5) Both missing verify: fail-closed (return raw body, caller treats as no code)
  {
    const fetchFn = mockFetch({
      "./status.json": { body: {} },
      [RAW]: { body: {} },
    });
    const data = await h.loadUnlockStatus(fetchFn);
    if (h.hasUnlockVerify(data)) fail("empty raw must not look like a challenge");
  }

  // 6) Both fail: throw (Unlock shows could-not-reach, not a forged success)
  {
    const fetchFn = mockFetch({
      "./status.json": { throw: new Error("pages down") },
      [RAW]: { ok: false, status: 500, body: {} },
    });
    let threw = false;
    try {
      await h.loadUnlockStatus(fetchFn);
    } catch (e) {
      threw = true;
    }
    if (!threw) fail("both-fail must throw");
  }

  // 7) Raw wrong-email payload is still returned; doUnlock allowlist must reject it
  {
    const bad = { email: "other@example.com", sha256: "cc".repeat(32), exp: Date.now() + 1000 };
    const fetchFn = mockFetch({
      "./status.json": { body: { verify: null } },
      [RAW]: { body: { verify: bad } },
    });
    const data = await h.loadUnlockStatus(fetchFn);
    if ((data.verify.email || "").toLowerCase() === JEFF) fail("must not rewrite raw email");
    if (!data.verify.email) fail("raw payload should keep empty-or-other email as-is");
  }

  // PR #1 fail-closed: missing email is NOT treated as Jeff.
  if (h.src.indexOf("(v.email || JEFF_EMAIL)") !== -1) {
    fail("doUnlock must not default missing email to Jeff");
  }
  if (h.src.indexOf('String(v.email || "").toLowerCase() !== JEFF_EMAIL') === -1) {
    fail("doUnlock must fail-closed on missing/wrong email");
  }
  if (!/\/\^\[0-9a-f\]\{64\}\$\//.test(h.src)) {
    fail("doUnlock must require 64-hex sha");
  }
  if (h.src.indexOf(JEFF) === -1) fail("doUnlock lost Jeff allowlist");
  if (!/v\.sha256/.test(h.src) || !/That code does not match/.test(h.src)) {
    fail("doUnlock sha check missing");
  }
  if (!/That code expired/.test(h.src)) fail("doUnlock exp check missing");
  if (!/No code active yet/.test(h.src)) fail("doUnlock missing-verify message missing");

  console.log("unlock fallback smoke ok");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
