#!/usr/bin/env node
"use strict";
/**
 * Fail-closed smoke: soft-paint fingerprint ignores timestamps;
 * stale agent probes never stay Running.
 */
const fs = require("fs");
const path = require("path");

const ROOT = path.dirname(__filename);
const INDEX = process.argv[2] || path.join(ROOT, "index.html");

function fail(msg) {
  console.error("FAIL: " + msg);
  process.exit(1);
}

function extractFn(src, name) {
  const mark = "function " + name + "(";
  const start = src.indexOf(mark);
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

function scriptsFrom(html) {
  return html.split("<script>").slice(1).map((s) => s.split("</script>")[0]).join("\n");
}

function run() {
  const html = fs.readFileSync(INDEX, "utf8");
  const src = scriptsFrom(html);

  if (src.indexOf("function boardFingerprint") === -1) fail("boardFingerprint missing");
  if (src.indexOf("function ageGateAgents") === -1) fail("ageGateAgents missing");
  if (html.indexOf("pending-more") === -1 && html.indexOf("lower-risk") === -1) {
    // Generator must still know how to collapse low-risk even if this paint has none.
    if (fs.readFileSync(path.join(ROOT, "refresh.sh"), "utf8").indexOf("pending-more") === -1) {
      fail("pending-more missing from refresh.sh");
    }
  }
  if (html.indexOf("class=\"card\"") !== -1) fail("essay cards leaked");
  if (html.indexOf("data-checked-at") === -1) fail("agent pills need data-checked-at");
  if (src.indexOf("pageshow") === -1) fail("pageshow resume missing");
  if (src.indexOf("AbortController") === -1) fail("poll AbortController missing");
  if (src.indexOf("POLL_TIMEOUT_MS") === -1) fail("poll timeout missing");

  const boardFingerprint = eval("(" + extractFn(src, "boardFingerprint") + ")");
  const a = {
    generated_at: "one",
    generated_at_display: "Mon",
    refresh_started_ms: 1,
    agents_source: "file:x",
    pending: [{ id: "text-send", risk: "high", title: "Send", detail: "x" }],
    agents: [{ id: "codex", state: "unknown", detail: "none" }],
    sections: [{ id: "live-shipping", title: "Live", projects: [{ name: "WebJam", status: "jeff-gate" }] }],
    fetched_repos: ["webjam"],
    decisions: [{ id: "old" }],
  };
  const b = Object.assign({}, a, {
    generated_at: "two",
    generated_at_display: "Tue",
    refresh_started_ms: 99,
    agents_source: "previous:stale->unknown",
  });
  if (boardFingerprint(a) !== boardFingerprint(b)) {
    fail("fingerprint must ignore refresh timestamps");
  }
  const c = Object.assign({}, a, {
    pending: [{ id: "text-send", risk: "high", title: "Send", detail: "changed" }],
  });
  if (boardFingerprint(a) === boardFingerprint(c)) {
    fail("fingerprint must change when pending detail changes");
  }

  const parseCheckedAt = eval("(" + extractFn(src, "parseCheckedAt") + ")");
  const ageGateAgents = eval(
    "(function (parseCheckedAt) { var AGENT_FRESH_MS = 45 * 60 * 1000; return " +
      extractFn(src, "ageGateAgents") +
      "; })"
  )(parseCheckedAt);
  const stale = ageGateAgents([
    {
      id: "codex",
      name: "Codex",
      state: "running",
      detail: "PID 1",
      checked_at: "2020-01-01T00:00:00Z",
    },
    { id: "cursor", name: "Cursor", state: "running", detail: "app", checked_at: "2020-01-01T00:00:00Z" },
    { id: "claude", name: "Claude", state: "installed", detail: "app", checked_at: "2020-01-01T00:00:00Z" },
  ]);
  if (stale.some((row) => row.state === "running")) fail("stale probe must not stay Running");
  if (!stale.every((row) => row.state === "unknown")) fail("stale probe must paint Unknown");

  const untimestamped = ageGateAgents([{ id: "codex", state: "running", detail: "PID 9" }]);
  if (untimestamped[0].state !== "unknown") fail("untimestamped Running must become unknown");

  const future = ageGateAgents([
    { id: "codex", state: "running", detail: "time travel", checked_at: "2099-01-01T00:00:00Z" },
  ]);
  if (future[0].state !== "unknown") fail("future checked_at must not count as live");

  const nowIso = new Date().toISOString();
  const fresh = ageGateAgents([
    { id: "codex", name: "Codex", state: "running", detail: "PID 2", checked_at: nowIso },
    { id: "cursor", name: "Cursor", state: "idle", detail: "app", checked_at: nowIso },
    { id: "claude", name: "Claude", state: "installed", detail: "app", checked_at: nowIso },
  ]);
  if (fresh[0].state !== "running" || fresh[1].state !== "idle" || fresh[2].state !== "installed") {
    fail("fresh probe must keep honest states");
  }

  const compactSignal = eval("(" + extractFn(src, "compactSignal") + ")");
  if (compactSignal({ release: "v0.26.0", ci: { conclusion: "failure" }, open_prs: 1 }) !== "CI fail") {
    fail("CI fail must beat release + open PR");
  }
  if (compactSignal({ release: "v0.26.0", ci: { conclusion: "in_progress" } }) !== "CI running") {
    fail("in-progress tip CI must say CI running");
  }
  if (compactSignal({ release: "v0.26.0", ci: { conclusion: "success" }, open_prs: 0 }) !== "v0.26.0") {
    fail("green release still shows the tag");
  }

  console.log("soft-paint / agent age-gate smoke ok");
}

run();
