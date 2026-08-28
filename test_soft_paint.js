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
  if (src.indexOf("function signalHref") === -1) fail("signalHref missing");
  if (src.indexOf("function safePullsUrl") === -1) fail("safePullsUrl missing");
  if (src.indexOf("function safeGameUrl") === -1) fail("safeGameUrl missing");
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
  const stackA = {
    sections: [{ id: "live-shipping", projects: [{ name: "StoryLiner", open_prs: 2, open_pr_stack: [] }] }],
  };
  const stackB = {
    sections: [{
      id: "live-shipping",
      projects: [{
        name: "StoryLiner",
        open_prs: 2,
        open_pr_stack: [
          { number: 11, url: "https://github.com/rupret007/StoryLiner/pull/11" },
          { number: 12, url: "https://github.com/rupret007/StoryLiner/pull/12" },
        ],
      }],
    }],
  };
  if (boardFingerprint(stackA) === boardFingerprint(stackB)) {
    fail("fingerprint must change when stack order appears");
  }
  const gameA = {
    sections: [{ id: "live-shipping", projects: [{ name: "Turdanoid" }] }],
  };
  const gameB = {
    sections: [{
      id: "live-shipping",
      projects: [{ name: "Turdanoid", live_game_url: "https://rupret007.github.io/Turdanoid/hub.html" }],
    }],
  };
  if (boardFingerprint(gameA) === boardFingerprint(gameB)) {
    fail("fingerprint must change when the game hub link appears");
  }
  const latestA = {
    sections: [{
      id: "live-shipping",
      projects: [{ name: "WebJam", release: "v0.26.0", release_sha: "4b52080", tip_sha: "4b52080" }],
    }],
  };
  const latestB = {
    sections: [{
      id: "live-shipping",
      projects: [{ name: "WebJam", release: "v0.26.0", release_sha: "4b52080", tip_sha: "27530d8" }],
    }],
  };
  if (boardFingerprint(latestA) === boardFingerprint(latestB)) {
    fail("fingerprint must change when Latest SHA diverges from source");
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
  const safeGameUrl = eval(
    "(function (cleanPublicUrl) { return " + extractFn(src, "safeGameUrl") + "; })"
  )(cleanPublicUrl);
  const laneHrefs = eval(
    "(function (safeAgentUrl, safePrUrl, safeActionsUrl, safeRepoUrl, safeGameUrl) { return " +
      extractFn(src, "laneHrefs") +
      "; })"
  )(safeAgentUrl, safePrUrl, safeActionsUrl, safeRepoUrl, safeGameUrl);
  const pullsUrlFromRepo = eval(
    "(function (safeRepoUrl) { return " + extractFn(src, "pullsUrlFromRepo") + "; })"
  )(safeRepoUrl);
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

  const releaseMatchesTip = eval("(" + extractFn(src, "releaseMatchesTip") + ")");
  const compactSignal = eval(
    "(function (releaseMatchesTip) { return " + extractFn(src, "compactSignal") + "; })"
  )(releaseMatchesTip);
  if (compactSignal({ release: "v0.26.0", ci: { conclusion: "failure" }, open_prs: 1 }) !== "CI fail") {
    fail("CI fail must beat release + open PR");
  }
  const privateHostedRed = { private: true, ci: { conclusion: "failure" } };
  if (compactSignal(privateHostedRed) !== "") {
    fail("private/high-level lanes must not publish CI fail");
  }
  for (const name of ["TACTrack", "CSS Conductor", "AI Music Vault", "AdoptIQ", "Bob the Bot"]) {
    const signal = compactSignal({
      name,
      private: true,
      status: "yellow",
      ci: { conclusion: "failure", run_started_at: null },
    });
    if (signal === "CI fail" || signal === "Red") {
      fail(name + " must not paint empty-runner hosted red as CI fail");
    }
    if (signal !== "") {
      fail(name + " high-level lane must publish no CI diagnosis signal");
    }
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
  if (
    compactSignal({
      release: "v0.26.0",
      release_sha: "4b52080",
      tip_sha: "27530d8",
      ci: { conclusion: "success" },
      open_prs: 0,
    }) !== "Latest != source"
  ) {
    fail("proven Latest vs source must not paint the tag as current");
  }
  if (
    compactSignal({
      release: "v0.26.0",
      release_sha: "4b52080",
      tip_sha: "4b52080",
      ci: { conclusion: "success" },
      open_prs: 0,
    }) !== "v0.26.0"
  ) {
    fail("matching Latest SHA may still show the tag");
  }
  const stack = [
    { number: 10, url: "https://github.com/rupret007/repo/pull/10" },
    { number: 11, url: "https://github.com/rupret007/repo/pull/11" },
    { number: 12, url: "https://github.com/rupret007/repo/pull/12" },
  ];
  if (compactSignal({ release: "v1", open_prs: 3, open_pr_stack: stack }) !== "Stack #10 -> #11 -> #12") {
    fail("complete stack must beat release and show base-to-tip order");
  }
  if (compactSignal({ release: "v1", open_prs: 4, open_pr_stack: stack }) !== "4 open PRs") {
    fail("mismatched stack must fail closed to the honest open-PR count");
  }
  const barkerStack = [
    { number: 41, url: "https://github.com/0xc0re/barker/pull/41" },
    { number: 42, url: "https://github.com/0xc0re/barker/pull/42" },
  ];
  if (compactSignal({ open_prs: 2, open_pr_stack: barkerStack }) !== "Stack #41 -> #42") {
    fail("canonical Barker stack must pass the exact external-repo allowlist");
  }

  const safeReleaseUrl = eval(
    "(function (cleanPublicUrl) { return " + extractFn(src, "safeReleaseUrl") + "; })"
  )(cleanPublicUrl);
  const latestReleaseUrlFromRepo = eval(
    "(function (safeRepoUrl) { return " + extractFn(src, "latestReleaseUrlFromRepo") + "; })"
  )(safeRepoUrl);
  const signalHref = eval(
    "(function (compactSignal, laneHrefs, pullsUrlFromRepo, latestReleaseUrlFromRepo, safeReleaseUrl) { return " +
      extractFn(src, "signalHref") +
      "; })"
  )(compactSignal, laneHrefs, pullsUrlFromRepo, latestReleaseUrlFromRepo, safeReleaseUrl);
  if (
    signalHref({
      url: "https://github.com/rupret007/StoryBoard",
      open_prs: 1,
      open_pr_url: "https://github.com/rupret007/StoryBoard/pull/8",
      ci: { conclusion: "success" },
    }) !== "https://github.com/rupret007/StoryBoard/pull/8"
  ) {
    fail("1 open PR must tap that PR");
  }
  if (
    signalHref({
      url: "https://github.com/rupret007/story-corner-shelf",
      open_prs: 4,
      open_pr_url: "https://github.com/rupret007/story-corner-shelf/pull/1",
      ci: { conclusion: "success" },
    }) !== "https://github.com/rupret007/story-corner-shelf/pulls"
  ) {
    fail("N open PRs must tap the pulls list, not one PR");
  }
  if (
    signalHref({
      url: "https://github.com/rupret007/StoryLiner",
      release: "v1",
      open_prs: 2,
      open_pr_stack: [
        { number: 11, url: "https://github.com/rupret007/StoryLiner/pull/11" },
        { number: 12, url: "https://github.com/rupret007/StoryLiner/pull/12" },
      ],
      ci: { conclusion: "success" },
    }) !== "https://github.com/rupret007/StoryLiner/pulls"
  ) {
    fail("stack signal must tap the allowlisted pulls list");
  }
  if (
    signalHref({
      url: "https://github.com/0xc0re/barker",
      open_prs: 2,
      open_pr_stack: barkerStack,
      ci: { conclusion: "success" },
    }) !== "https://github.com/0xc0re/barker/pulls"
  ) {
    fail("canonical Barker stack must tap its exact allowlisted pulls list");
  }
  if (
    signalHref({
      url: "https://github.com/rupret007/webjam",
      release: "v0.26.0",
      ci: { conclusion: "pending", html_url: "https://github.com/rupret007/webjam/actions/runs/9" },
    }) !== "https://github.com/rupret007/webjam/actions/runs/9"
  ) {
    fail("CI pending must still tap the run");
  }
  if (signalHref({ release: "v0.26.0", ci: { conclusion: "success" }, open_prs: 0 })) {
    fail("release tag without a repo must stay dead text");
  }
  if (
    signalHref({
      url: "https://github.com/rupret007/webjam",
      release: "v0.26.0",
      release_sha: "4b52080",
      tip_sha: "27530d8",
      open_prs: 0,
      ci: { conclusion: "success" },
    }) !== "https://github.com/rupret007/webjam/releases/latest"
  ) {
    fail("Latest != source must tap /releases/latest");
  }
  if (
    signalHref({
      url: "https://github.com/rupret007/webjam",
      release: "v0.26.0",
      release_sha: "4b52080",
      tip_sha: "4b52080",
      open_prs: 0,
      ci: { conclusion: "success" },
    }) !== "https://github.com/rupret007/webjam/releases/latest"
  ) {
    fail("matching Latest tag must still tap /releases/latest");
  }
  if (
    signalHref({
      open_prs: 4,
      open_pr_url: "https://github.com/rupret007/story-corner-shelf/pull/1",
    })
  ) {
    fail("N open PRs without a repo must not invent a pulls list or pretend one PR is all");
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
    live_game_url: "https://rupret007.github.io/Turdanoid/hub.html",
    ci: { html_url: "https://github.com/rupret007/webjam/actions/runs/9", conclusion: "failure" },
  });
  if (hrefs.title !== "https://github.com/rupret007/webjam/pull/21") fail("lane title must prefer open PR");
  if (hrefs.pr !== "https://github.com/rupret007/webjam/pull/21") fail("lane PR href missing");
  if (hrefs.repo !== "https://github.com/rupret007/webjam") fail("lane repo href missing");
  if (hrefs.ci !== "https://github.com/rupret007/webjam/actions/runs/9") fail("lane CI href missing");
  if (hrefs.agent !== goodAgent) fail("lane agent href missing");
  if (hrefs.game !== "https://rupret007.github.io/Turdanoid/hub.html") fail("lane game href missing");
  if (safeGameUrl("https://rupret007.github.io/Turdanoid/index.html")) fail("sibling game page must drop");
  const empty = laneHrefs({ name: "Show Night" });
  if (empty.pr || empty.agent || empty.ci || empty.game) fail("missing URLs must not invent taps");
  const skipped = laneHrefs({
    url: "https://github.com/rupret007/Andrea_NanoBot",
    ci: {
      html_url: "https://github.com/rupret007/Andrea_NanoBot/actions/runs/32624035652",
      conclusion: "skipped",
    },
  });
  if (skipped.ci) fail("skipped helper must not become Open CI");
  const cancelled = laneHrefs({
    url: "https://github.com/rupret007/Andrea_NanoBot",
    ci: {
      html_url: "https://github.com/rupret007/Andrea_NanoBot/actions/runs/9",
      conclusion: "cancelled",
    },
  });
  if (cancelled.ci) fail("cancelled helper must not become Open CI");

  if (src.indexOf("function tabId") === -1) fail("tabId missing");
  if (src.indexOf("function glanceStatus") === -1) fail("glanceStatus missing");
  if (src.indexOf("function applyTypeTab") === -1) fail("applyTypeTab missing");
  if (src.indexOf("data-tab-panel") === -1 && html.indexOf("data-tab-panel") === -1) {
    fail("type tab panels missing");
  }
  const tabId = eval(
    "(function () { var TYPE_TAB_LABELS = {" +
      "controls:'Decisions','live-shipping':'Live','apps-utilities':'Apps'," +
      "cisco:'Cisco',messaging:'Bob','private-media':'Media',parked:'Parked'" +
    "}; return " + extractFn(src, "tabId") + "; })()"
  );
  if (tabId("live-shipping") !== "live-shipping") fail("live-shipping is a real type tab");
  if (tabId("music") !== "") fail("invented music tab must drop");
  if (tabId("abilities") !== "") fail("footer sections are not type tabs");
  if (tabId("javascript:alert(1)") !== "") fail("evil tab id must drop");
  const attentionRank = eval("(" + extractFn(src, "attentionRank") + ")");
  const glanceStatus = eval(
    "(function (tabId, attentionRank) { " +
      "var TYPE_TAB_LABELS = { controls:'Decisions', 'live-shipping':'Live', " +
      "'apps-utilities':'Apps', cisco:'Cisco', messaging:'Bob', " +
      "'private-media':'Media', parked:'Parked' }; " +
      "function tabLabel(id) { return TYPE_TAB_LABELS[tabId(id)] || ''; } " +
      "return " + extractFn(src, "glanceStatus") + "; })"
  )(tabId, attentionRank);
  const g = glanceStatus([{ id: "x", title: "AdoptIQ" }], [{ id: "live-shipping", projects: [{ status: "yellow" }] }]);
  if (g.text !== "AdoptIQ" || g.tab !== "controls") fail("pending glance must name the gate");
  const g3 = glanceStatus([
    { id: "che-live-pull", title: "Che live pull", risk: "low" },
    { id: "logic-keys-wavs", title: "Logic keys and WAVs", risk: "low" },
    { id: "adoptiq-live-cisco", title: "AdoptIQ live Cisco readiness", risk: "high" },
  ], []);
  if (g3.text !== "AdoptIQ live Cisco readiness + 2 more" || g3.tab !== "controls") {
    fail("three-gate glance must name the high-risk title: " + g3.text);
  }
  const live = glanceStatus([], [{ id: "live-shipping", projects: [{ status: "yellow" }] }]);
  if (live.text !== "Live needs a look" || live.tab !== "live-shipping") {
    fail("live yellow must be one short glance");
  }
  const quiet = glanceStatus([], [{ id: "live-shipping", projects: [{ status: "green" }] }]);
  if (quiet.text !== "Quiet" || quiet.tab !== "") fail("all-green glance must stay Quiet");
  if (html.indexOf('id="type-tabs"') === -1) fail("type tab bar missing from first paint");
  if (html.indexOf('id="board-glance"') === -1) fail("short status missing from first paint");
  function tagFor(id) {
    var mark = 'id="' + id + '"';
    var at = html.indexOf("<section " + mark);
    if (at < 0) at = html.indexOf('<section id="' + id + '"');
    return at >= 0 ? html.slice(at, at + 260) : "";
  }
  const liveTag = tagFor("live-shipping");
  if (liveTag.indexOf("data-tab-panel") === -1) fail("live-shipping must be a tab panel");
  if (!/\bhidden\b/.test(liveTag)) {
    fail("first paint must hide live-shipping so the wall is not the first screen");
  }
  const decTag = tagFor("controls");
  if (!/\bhidden\b/.test(decTag)) {
    fail("first paint must hide Decisions until that tab is opened");
  }

  if (src.indexOf("function compactUnknownMacProbes") === -1) fail("compactUnknownMacProbes missing");
  const compactUnknownMacProbes = eval("(" + extractFn(src, "compactUnknownMacProbes") + ")");
  if (!compactUnknownMacProbes([
    { id: "codex", state: "unknown" },
    { id: "cursor", state: "unknown" },
    { id: "claude", state: "unknown" },
  ])) fail("all-unknown Mac probes must compact");
  if (!compactUnknownMacProbes(stale)) fail("stale age-gated probes must compact");
  if (compactUnknownMacProbes(fresh)) fail("fresh known probes must keep three pills");
  if (html.indexOf("Agents unknown") === -1) fail("first paint must say Agents unknown when the box has no Mac probe");
  if (html.indexOf("is-unknown-mac") === -1) fail("first paint must collapse unknown Mac pills");
  if (html.indexOf("#panel-status:empty") === -1) fail("empty panel-status must not reserve first-screen space");
  if (html.indexOf("body.tab-home section.block.foot") === -1) fail("home screen must hide footer chrome");
  if (html.indexOf("body.tab-home .agent-links") === -1) fail("home screen must not stack Open agent buttons");
  if (html.indexOf('class="tab-home"') === -1) fail("first paint must start on the home tab screen");
  const preHow = html.split('<details class="how-board">')[0] || "";
  if (preHow.indexOf("Live CI via") !== -1) fail("fetched-repo line must not lead the first phone screen");
  if (html.indexOf('id="fetched-line"') === -1) fail("fetched-repo line must remain inside How this board works");

  console.log("soft-paint / agent age-gate smoke ok");
}

run();
