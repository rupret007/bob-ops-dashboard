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
  if (src.indexOf("function pollIsNewer") === -1) fail("pollIsNewer missing");
  if (src.indexOf("function pollFailureCounts") === -1) fail("pollFailureCounts missing");
  if (src.indexOf("function pollPaintDecision") === -1) fail("pollPaintDecision missing");
  if (src.indexOf("function safeAgentUrl") === -1) fail("safeAgentUrl missing");
  if (src.indexOf("function laneHrefs") === -1) fail("laneHrefs missing");
  if (src.indexOf("data-open=\"work\"") === -1 && src.indexOf("data-open=\\\"work\\\"") === -1) {
    if (html.indexOf("data-open=\"work\"") === -1 && html.indexOf("Open agent") === -1) {
      // Generator must know how to paint work links even if this snapshot has none.
      if (fs.readFileSync(path.join(ROOT, "refresh.sh"), "utf8").indexOf("data-open=\"work\"") === -1) {
        fail("work-link taps missing from refresh.sh");
      }
    }
  }

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
  const cleanPublicUrl = eval("(" + extractFn(src, "cleanPublicUrl") + ")");
  const isBcId = eval("(" + extractFn(src, "isBcId") + ")");
  const safeAgentUrl = eval(
    "(function (cleanPublicUrl, isBcId) { return " + extractFn(src, "safeAgentUrl") + "; })"
  )(cleanPublicUrl, isBcId);
  const safePrUrl = eval(
    "(function (cleanPublicUrl) { return " + extractFn(src, "safePrUrl") + "; })"
  )(cleanPublicUrl);
  const safeActionsUrl = eval(
    "(function (cleanPublicUrl) { return " + extractFn(src, "safeActionsUrl") + "; })"
  )(cleanPublicUrl);
  const safeRepoUrl = eval(
    "(function (cleanPublicUrl) { return " + extractFn(src, "safeRepoUrl") + "; })"
  )(cleanPublicUrl);
  const laneHrefs = eval(
    "(function (safeAgentUrl, safePrUrl, safeActionsUrl, safeRepoUrl) { return " +
      extractFn(src, "laneHrefs") +
      "; })"
  )(safeAgentUrl, safePrUrl, safeActionsUrl, safeRepoUrl);
  const ageGateAgents = eval(
    "(function (parseCheckedAt, safeAgentUrl, safePrUrl) { var AGENT_FRESH_MS = 45 * 60 * 1000; return " +
      extractFn(src, "ageGateAgents") +
      "; })"
  )(parseCheckedAt, safeAgentUrl, safePrUrl);
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
  if (compactSignal({ release: "v0.26.0", ci: { conclusion: "queued" } }) !== "CI pending") {
    fail("queued tip CI must say CI pending");
  }
  if (compactSignal({ release: "v0.26.0", ci: { conclusion: "pending" } }) !== "CI pending") {
    fail("unstarted tip CI must say CI pending, not the release tag");
  }
  if (compactSignal({ release: "v0.26.0", ci: { conclusion: "success" }, open_prs: 0 }) !== "v0.26.0") {
    fail("green release still shows the tag");
  }

  const parseStampMs = eval("(" + extractFn(src, "parseStampMs") + ")");
  const pollIsNewer = eval(
    "(function (parseStampMs) { return " + extractFn(src, "pollIsNewer") + "; })"
  )(parseStampMs);
  const pollFailureCounts = eval("(" + extractFn(src, "pollFailureCounts") + ")");
  const pollPaintDecision = eval(
    "(function (parseStampMs, pollIsNewer) { return " +
      extractFn(src, "pollPaintDecision") +
      "; })"
  )(parseStampMs, pollIsNewer);

  if (pollFailureCounts(1, 2)) fail("superseded / hide abort must not count as poll fail");
  if (!pollFailureCounts(3, 3)) fail("in-flight timeout must still count as poll fail");

  const older = "2026-08-23T05:00:00.000Z";
  const same = "2026-08-23T06:00:00.000Z";
  const newer = "2026-08-23T06:15:00.000Z";
  if (pollIsNewer(older, same)) fail("older stamp must not beat known");
  if (!pollIsNewer(same, same)) fail("equal stamp is usable");
  if (!pollIsNewer(newer, same)) fail("newer stamp must win");
  if (pollIsNewer("", same)) fail("empty stamp is not newer");
  if (pollIsNewer("nope", same)) fail("unparseable stamp is not newer");

  if (pollPaintDecision(older, same, null, "a") !== "ignore") {
    fail("stale JSON on first poll must ignore (no rewind)");
  }
  if (pollPaintDecision(same, same, null, "a") !== "stamp") {
    fail("first poll with same stamp must not rewrite first paint");
  }
  if (pollPaintDecision(newer, same, null, "a") !== "paint") {
    fail("newer JSON than HTML must paint once");
  }
  if (pollPaintDecision(newer, same, "a", "a") !== "stamp") {
    fail("timestamp-only newer refresh must not flash");
  }
  if (pollPaintDecision(newer, same, "a", "b") !== "paint") {
    fail("newer content change must soft-paint");
  }
  if (pollPaintDecision(older, same, "a", "b") !== "ignore") {
    fail("stale JSON must not rewrite lanes after first paint");
  }

  const stopPolling = extractFn(src, "stopPolling");
  const incAt = stopPolling.indexOf("pollSeq += 1");
  const abortAt = stopPolling.indexOf("pollAbort.abort");
  if (incAt < 0 || abortAt < 0 || incAt > abortAt) {
    fail("stopPolling must bump pollSeq before abort");
  }

  const goodBc = "bc-8e16f06d-f73f-482c-987f-e13f2d3b9fb1";
  const goodAgent = "https://cursor.com/agents/" + goodBc;
  if (safeAgentUrl(goodAgent + "?cursor_ref=x") !== goodAgent) fail("agent url must canonicalize");
  if (safeAgentUrl("https://evil.example/agents/" + goodBc)) fail("foreign agent host must drop");
  if (safeAgentUrl("https://cursor.com/agents/bc-nope")) fail("invented bc-id must drop");
  if (safePrUrl("https://github.com/evil/webjam/pull/1")) fail("foreign PR must drop");
  if (safeActionsUrl("https://github.com/rupret007/webjam/actions") ) fail("actions index is not a run");
  const hrefs = laneHrefs({
    url: "https://github.com/rupret007/webjam",
    open_pr_url: "https://github.com/rupret007/webjam/pull/21",
    agent_url: goodAgent,
    ci: { html_url: "https://github.com/rupret007/webjam/actions/runs/9", conclusion: "failure" },
  });
  if (hrefs.title !== "https://github.com/rupret007/webjam/pull/21") fail("lane title must prefer open PR");
  if (hrefs.pr !== "https://github.com/rupret007/webjam/pull/21") fail("lane PR href missing");
  if (hrefs.repo !== "https://github.com/rupret007/webjam") fail("lane repo href missing");
  if (hrefs.ci !== "https://github.com/rupret007/webjam/actions/runs/9") fail("lane CI href missing");
  if (hrefs.agent !== goodAgent) fail("lane agent href missing");
  const empty = laneHrefs({ name: "Show Night" });
  if (empty.pr || empty.agent || empty.ci) fail("missing URLs must not invent taps");

  console.log("soft-paint / agent age-gate smoke ok");
}

run();
