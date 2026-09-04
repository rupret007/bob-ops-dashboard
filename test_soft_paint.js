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
  if (src.indexOf("function snapshotTrustState") === -1) fail("snapshotTrustState missing");
  if (src.indexOf("function safeAgentUrl") === -1) fail("safeAgentUrl missing");
  if (src.indexOf("function laneHrefs") === -1) fail("laneHrefs missing");
  if (src.indexOf("function signalHref") === -1) fail("signalHref missing");
  if (src.indexOf("function coordSignal") === -1) fail("coordSignal missing");
  if (src.indexOf("function coordPrUrl") === -1) fail("coordPrUrl missing");
  if (src.indexOf("function coordReviewSignal") === -1) fail("coordReviewSignal missing");
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
  const leaseA = {
    sections: [{ id: "live-shipping", projects: [{ name: "WebJam", open_prs: 3 }] }],
  };
  const leaseB = {
    sections: [{
      id: "live-shipping",
      projects: [{
        name: "WebJam",
        open_prs: 3,
        coord: { agent: "codex", lease_state: "active" },
      }],
    }],
  };
  const leaseExpired = {
    sections: [{
      id: "live-shipping",
      projects: [{
        name: "WebJam",
        open_prs: 3,
        coord: { agent: "codex", lease_state: "expired" },
      }],
    }],
  };
  if (boardFingerprint(leaseA) === boardFingerprint(leaseB)) {
    fail("fingerprint must change when an active coordination lease appears");
  }
  if (boardFingerprint(leaseB) === boardFingerprint(leaseExpired)) {
    fail("fingerprint must change when a coordination lease expires");
  }
  const reviewA = {
    sections: [{
      id: "live-shipping",
      projects: [{
        name: "StoryBoard",
        status: "yellow",
        coord: { agent: "none", lease_state: "none" },
      }],
    }],
  };
  const reviewB = {
    sections: [{
      id: "live-shipping",
      projects: [{
        name: "StoryBoard",
        status: "yellow",
        coord: {
          agent: "none",
          lease_state: "none",
          pr: 23,
          pr_url: "https://github.com/rupret007/StoryBoard/pull/23",
          pr_draft: true,
        },
      }],
    }],
  };
  if (boardFingerprint(reviewA) === boardFingerprint(reviewB)) {
    fail("fingerprint must change when a verified coordination draft appears");
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
  const coordPrUrl = eval(
    "(function (safeRepoUrl, safePrUrl) { return " + extractFn(src, "coordPrUrl") + "; })"
  )(safeRepoUrl, safePrUrl);
  const laneHrefs = eval(
    "(function (safeAgentUrl, safePrUrl, safeActionsUrl, safeRepoUrl, safeGameUrl, coordPrUrl) { return " +
      extractFn(src, "laneHrefs") +
      "; })"
  )(safeAgentUrl, safePrUrl, safeActionsUrl, safeRepoUrl, safeGameUrl, coordPrUrl);
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
  const coordSignal = eval("(" + extractFn(src, "coordSignal") + ")");
  const coordReviewSignal = eval(
    "(function (coordPrUrl) { return " + extractFn(src, "coordReviewSignal") + "; })"
  )(coordPrUrl);
  const compactSignal = eval(
    "(function (releaseMatchesTip, coordSignal, coordReviewSignal) { return " +
      extractFn(src, "compactSignal") + "; })"
  )(releaseMatchesTip, coordSignal, coordReviewSignal);
  if (compactSignal({ release: "v0.26.0", ci: { conclusion: "failure" }, open_prs: 1 }) !== "CI fail") {
    fail("CI fail must beat release + open PR");
  }
  const privateHostedRed = { private: true, ci: { conclusion: "failure" } };
  if (compactSignal(privateHostedRed) !== "") {
    fail("private/high-level lanes must not publish CI fail");
  }
  const activeLease = {
    open_prs: 3,
    coord: { agent: "codex", lease_state: "active" },
  };
  if (coordSignal(activeLease) !== "Codex lease") fail("active Codex lease must paint");
  if (compactSignal(activeLease) !== "Codex lease") {
    fail("active lease must beat open PR count after CI");
  }
  if (compactSignal({
    open_prs: 3,
    coord: { agent: "grok", lease_state: "active" },
  }) !== "Grok lease") fail("active Grok lease must paint");
  if (compactSignal({
    open_prs: 3,
    coord: { agent: "claude", lease_state: "active" },
  }) !== "Claude lease") fail("active Claude lease must paint");
  if (compactSignal({
    open_prs: 3,
    coord: { agent: "codex", lease_state: "expired" },
  }) !== "3 open PRs") fail("expired lease must fall through to review work");
  if (coordSignal({
    private: true,
    coord: { agent: "codex", lease_state: "active" },
  }) !== "") fail("private lease must stay off the public signal");
  if (coordSignal({
    coord: { agent: "unknown", lease_state: "active" },
  }) !== "") fail("unknown lease agent must fail closed");
  if (compactSignal({
    open_prs: 3,
    ci: { conclusion: "failure" },
    coord: { agent: "codex", lease_state: "active" },
  }) !== "CI fail") fail("CI fail must beat an active lease");
  const leftoverDraft = {
    repo_url: "https://github.com/rupret007/StoryBoard",
    open_prs: 0,
    status: "green",
    coord: {
      agent: "none",
      lease_state: "none",
      pr: 23,
      pr_url: "https://github.com/rupret007/StoryBoard/pull/23",
      pr_draft: true,
    },
  };
  if (coordPrUrl(leftoverDraft) || coordReviewSignal(leftoverDraft) || compactSignal(leftoverDraft)) {
    fail("parked leftover draft must not become public review work");
  }
  if (laneHrefs(leftoverDraft).title === leftoverDraft.coord.pr_url) {
    fail("parked leftover draft must not become the lane tap");
  }
  const coordinatedReady = {
    repo_url: "https://github.com/rupret007/StoryBoard",
    open_prs: 0,
    coord: {
      agent: "none",
      lease_state: "none",
      pr: 23,
      pr_url: "https://github.com/rupret007/StoryBoard/pull/23",
      pr_draft: false,
    },
  };
  if (coordPrUrl(coordinatedReady) !== coordinatedReady.coord.pr_url) {
    fail("same-repo ready coordination PR must validate");
  }
  if (coordReviewSignal(coordinatedReady) !== "PR #23") {
    fail("verified ready coordination PR must become review work");
  }
  if (compactSignal(coordinatedReady) !== "PR #23") {
    fail("verified ready coordination PR must beat release/empty ready count");
  }
  if (laneHrefs(coordinatedReady).title !== coordinatedReady.coord.pr_url) {
    fail("verified ready coordination PR must be the lane tap fallback");
  }
  const wrongRepoDraft = Object.assign({}, leftoverDraft, {
    coord: Object.assign({}, leftoverDraft.coord, {
      pr_url: "https://github.com/rupret007/webjam/pull/23",
    }),
  });
  if (coordPrUrl(wrongRepoDraft) || coordReviewSignal(wrongRepoDraft) || compactSignal(wrongRepoDraft)) {
    fail("cross-repo coordination PR must fail closed");
  }
  if (coordPrUrl(Object.assign({}, coordinatedReady, { private: true }))) {
    fail("private coordination PR must stay off the public board");
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
    "(function (compactSignal, laneHrefs, pullsUrlFromRepo, latestReleaseUrlFromRepo, safeReleaseUrl, coordPrUrl) { return " +
      extractFn(src, "signalHref") +
      "; })"
  )(compactSignal, laneHrefs, pullsUrlFromRepo, latestReleaseUrlFromRepo, safeReleaseUrl, coordPrUrl);
  if (signalHref(leftoverDraft)) {
    fail("parked leftover draft signal must stay dead text");
  }
  if (signalHref(coordinatedReady) !== coordinatedReady.coord.pr_url) {
    fail("verified ready coordination PR signal must tap the exact PR");
  }
  if (signalHref({
    url: "https://github.com/rupret007/webjam",
    open_prs: 3,
    open_pr_url: "https://github.com/rupret007/webjam/pull/61",
    coord: { agent: "codex", lease_state: "active" },
  }) !== "") {
    fail("active coordination lease must stay dead text, not inherit a PR href");
  }
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

  const snapshotTrustState = eval("(" + extractFn(src, "snapshotTrustState") + ")");
  const silenceLimit = 45 * 60 * 1000;
  if (snapshotTrustState(1, 0, silenceLimit) !== "current") {
    fail("fresh successful snapshot must stay current");
  }
  if (snapshotTrustState(silenceLimit, 0, silenceLimit) !== "current") {
    fail("silence boundary must not become overdue early");
  }
  if (snapshotTrustState(silenceLimit + 1, 0, silenceLimit) !== "refresh-overdue") {
    fail("overdue refresh must label the snapshot historical");
  }
  if (snapshotTrustState(1, 1, silenceLimit) !== "poll-failed") {
    fail("one failed live poll must fail closed immediately");
  }
  if (snapshotTrustState(silenceLimit + 1, 2, silenceLimit) !== "poll-failed") {
    fail("live poll failure must explain the stronger immediate condition");
  }
  if (html.indexOf('id="retry-status"') === -1 || html.indexOf("Retry now") === -1) {
    fail("stale snapshot banner must offer one manual retry");
  }
  if (html.indexOf('data-snapshot-trust="current"') === -1) {
    fail("first paint must declare its snapshot trust state");
  }
  if (src.indexOf('retryStatus.addEventListener("click"') === -1) {
    fail("Retry now must invoke the existing bounded poll path");
  }

  function fakeElement() {
    const classes = new Set();
    const attrs = {};
    return {
      hidden: true,
      textContent: "",
      disabled: false,
      classList: {
        add: (name) => classes.add(name),
        remove: (name) => classes.delete(name),
        toggle: (name, on) => on ? classes.add(name) : classes.delete(name),
        contains: (name) => classes.has(name),
      },
      setAttribute: (name, value) => { attrs[name] = String(value); },
      removeAttribute: (name) => { delete attrs[name]; },
      getAttribute: (name) => attrs[name],
    };
  }
  const fakeBody = fakeElement();
  const fakeBoard = fakeElement();
  const fakeSilence = fakeElement();
  const fakeTitle = fakeElement();
  const fakeDetail = fakeElement();
  const setSnapshotTrust = eval(
    "(function (document, boardEl, silenceEl) { return " + extractFn(src, "setSnapshotTrust") + "; })"
  )({ body: fakeBody }, fakeBoard, fakeSilence);
  const showSilence = eval(
    "(function (silenceEl, silenceTitleEl, silenceDetailEl, setSnapshotTrust) { return " +
      extractFn(src, "showSilence") + "; })"
  )(fakeSilence, fakeTitle, fakeDetail, setSnapshotTrust);
  const hideSilence = eval(
    "(function (silenceEl, silenceTitleEl, silenceDetailEl, setSnapshotTrust) { return " +
      extractFn(src, "hideSilence") + "; })"
  )(fakeSilence, fakeTitle, fakeDetail, setSnapshotTrust);

  showSilence("poll-failed", "Live check unavailable", "Showing the last verified snapshot.");
  if (fakeSilence.hidden || !fakeSilence.classList.contains("show")) {
    fail("failed poll must reveal the last-verified banner");
  }
  if (fakeSilence.getAttribute("data-state") !== "poll-failed") {
    fail("banner must expose the precise stale reason");
  }
  if (fakeBoard.getAttribute("data-snapshot-trust") !== "last-verified") {
    fail("failed poll must mark board contents as historical");
  }
  if (fakeBoard.getAttribute("aria-describedby") !== "silence-detail") {
    fail("historical board must be described by the visible warning");
  }
  if (!fakeBody.classList.contains("snapshot-unverified")) {
    fail("historical board must have a visual trust-state hook");
  }
  if (fakeTitle.textContent !== "Live check unavailable" || !fakeDetail.textContent.includes("last verified")) {
    fail("historical mode must explain the user-visible condition");
  }
  hideSilence();
  if (!fakeSilence.hidden || fakeSilence.classList.contains("show")) {
    fail("successful current snapshot must clear the warning");
  }
  if (fakeBoard.getAttribute("data-snapshot-trust") !== "current") {
    fail("successful current snapshot must restore current trust");
  }
  if (fakeBoard.getAttribute("aria-describedby") !== undefined) {
    fail("current board must drop stale warning attribution");
  }
  if (fakeBody.classList.contains("snapshot-unverified")) {
    fail("current board must clear stale styling");
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
  if (src.indexOf("function focusKey") === -1) fail("focusKey missing");
  if (src.indexOf("function glanceStatus") === -1) fail("glanceStatus missing");
  if (src.indexOf("function applyTypeTab") === -1) fail("applyTypeTab missing");
  if (src.indexOf("function findFocusTarget") === -1) fail("findFocusTarget missing");
  if (src.indexOf("function revealGlanceTarget") === -1) fail("revealGlanceTarget missing");
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
  const focusKey = eval("(" + extractFn(src, "focusKey") + ")");
  if (focusKey("project", "Story Shelf") !== "project:story-shelf") fail("project focus key mismatch");
  if (focusKey("decision", "adoptiq-live_cisco.1") !== "decision:adoptiq-live_cisco.1") {
    fail("decision focus key mismatch");
  }
  if (focusKey("decision", "../../escape") || focusKey("decision", "x".repeat(65))) {
    fail("unsafe decision focus key must drop");
  }
  if (focusKey("project", "🔥") || focusKey("other", "WebJam")) fail("invalid focus key must drop");
  const attentionRank = eval("(" + extractFn(src, "attentionRank") + ")");
  const glanceStatus = eval(
    "(function (tabId, attentionRank, focusKey) { " +
      "var TYPE_TAB_LABELS = { controls:'Decisions', 'live-shipping':'Live', " +
      "'apps-utilities':'Apps', cisco:'Cisco', messaging:'Bob', " +
      "'private-media':'Media', parked:'Parked' }; " +
      "function tabLabel(id) { return TYPE_TAB_LABELS[tabId(id)] || ''; } " +
      "return " + extractFn(src, "glanceStatus") + "; })"
  )(tabId, attentionRank, focusKey);
  const g = glanceStatus([{ id: "x", title: "AdoptIQ" }], [{ id: "live-shipping", projects: [{ status: "yellow" }] }]);
  if (g.text !== "AdoptIQ" || g.tab !== "controls" || g.focus !== "decision:x") {
    fail("pending glance must name and target the gate");
  }
  const g3 = glanceStatus([
    { id: "che-live-pull", title: "Che live pull", risk: "low" },
    { id: "logic-keys-wavs", title: "Logic keys and WAVs", risk: "low" },
    { id: "adoptiq-live-cisco", title: "AdoptIQ live Cisco readiness", risk: "high" },
  ], []);
  if (g3.text !== "AdoptIQ live Cisco readiness" || g3.tab !== "controls" || g3.focus !== "decision:adoptiq-live-cisco") {
    fail("three-gate glance must name the one next action: " + g3.text);
  }
  if (/\+\s*\d+\s*more/.test(g3.text)) {
    fail("first-screen glance must not be a leftover yes-count");
  }
  const leftoverJeff = glanceStatus([], [{
    id: "apps-utilities",
    projects: [{ name: "Door", status: "jeff-gate" }],
  }]);
  if (leftoverJeff.text !== "Quiet" || leftoverJeff.tab !== "") {
    fail("leftover lane Jeff-gate must not look like an active Jeff yes: " + leftoverJeff.text);
  }
  const live = glanceStatus([], [{ id: "live-shipping", projects: [{ name: "WebJam", status: "yellow" }] }]);
  if (live.text !== "WebJam needs a look" || live.tab !== "live-shipping" || live.focus !== "project:webjam") {
    fail("live yellow must be one short glance");
  }
  const mixed = glanceStatus([], [{
    id: "apps-utilities",
    projects: [{ name: "Door", status: "jeff-gate" }, { name: "TACTrack", status: "yellow" }],
  }]);
  if (mixed.text !== "TACTrack needs a look" || mixed.focus !== "project:tactrack") {
    fail("owner-only row must not hide actionable work");
  }
  const quiet = glanceStatus([], [{ id: "live-shipping", projects: [{ status: "green" }] }]);
  if (quiet.text !== "Quiet" || quiet.tab !== "") fail("all-green glance must stay Quiet");
  if (html.indexOf('id="type-tabs"') === -1) fail("type tab bar missing from first paint");
  if (html.indexOf('id="board-glance"') === -1) fail("short status missing from first paint");
  if (html.indexOf('aria-label="Next action"') === -1) fail("glance must be the named next action");
  const typeTabIdsFor = eval(
    "(function () { var TYPE_TAB_IDS = ['controls','live-shipping','apps-utilities'," +
      "'cisco','messaging','private-media','parked']; return " +
      extractFn(src, "typeTabIdsFor") + "; })()"
  );
  const tabIds = typeTabIdsFor(
    [{ id: "live-shipping" }, { id: "apps-utilities" }, { id: "cisco" }, { id: "parked" }],
    [{ id: "x" }]
  );
  if (tabIds.indexOf("controls") !== -1) fail("Decisions must not be a project-type tab");
  if (tabIds[0] !== "live-shipping") fail("first type tab must stay Live");
  const nav = html.split('id="type-tabs"')[1].split("</nav>")[0] || "";
  if (nav.indexOf("tab-controls") !== -1 || nav.indexOf(">Decisions<") !== -1) {
    fail("first-screen tab bar must not include Decisions chrome");
  }
  if (src.indexOf("fromGlance") === -1 || src.indexOf("revealGlanceTarget(id, focus)") === -1) {
    fail("glance must reveal its exact target");
  }
  const validFocusKey = eval(
    "(function (focusKey) { return " + extractFn(src, "validFocusKey") + "; })"
  )(focusKey);
  const findFocusTarget = eval(
    "(function (validFocusKey) { return " + extractFn(src, "findFocusTarget") + "; })"
  )(validFocusKey);
  const wrong = { getAttribute: () => "project:other" };
  const exact = { getAttribute: () => "project:webjam" };
  const panel = { querySelectorAll: () => [wrong, exact] };
  if (findFocusTarget(panel, "project:webjam") !== exact) fail("exact focus target not found");
  if (findFocusTarget(panel, "project:missing") !== null) fail("missing target must not guess");
  if (findFocusTarget(panel, "project:<script>") !== null) fail("unsafe target must fail closed");
  const duplicate = { getAttribute: () => "project:webjam" };
  const ambiguousPanel = { querySelectorAll: () => [exact, duplicate] };
  if (findFocusTarget(ambiguousPanel, "project:webjam") !== null) {
    fail("ambiguous target must not select a neighboring row");
  }
  const paintAt = src.indexOf("boardEl.innerHTML = html;");
  const restoreTabAt = src.indexOf("applyTypeTab(currentTypeTab);", paintAt);
  if (paintAt < 0 || restoreTabAt < paintAt) fail("soft paint must restore the selected tab");
  if (src.indexOf("function publicProbeDetail") === -1) fail("publicProbeDetail missing");
  const publicProbeDetail = eval("(" + extractFn(src, "publicProbeDetail") + ")");
  if (publicProbeDetail("/Users/owner/private/agents-status.json") !== "No live Mac probe") {
    fail("public probe title must drop local paths");
  }
  if (publicProbeDetail("run probe-agents-status.sh") !== "No live Mac probe") {
    fail("public probe title must drop local helper names");
  }
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
  if (html.indexOf("body.tab-home footer") === -1) fail("home screen must hide the repo footer");
  if (html.indexOf("body.tab-home .live-stamp .when") === -1) fail("home screen must hide the long timestamp");
  if (html.indexOf("is-unknown-mac .agents-unknown") !== -1) fail("Agents unknown must not become first-screen chrome");
  if (html.indexOf(".agents-strip.is-unknown-only") === -1) fail("unknown-only agent chrome must collapse");
  if (html.indexOf("font-size:1.55rem") === -1) fail("next action must be the first-screen hero");
  if (html.indexOf("flex:1 1 0") === -1) fail("phone type tabs must share one row");
  if (html.indexOf("body.tab-home .agent-links") === -1) fail("home screen must not stack Open agent buttons");
  if (html.indexOf('class="tab-home"') === -1) fail("first paint must start on the home tab screen");
  const preHow = html.split('<details class="how-board">')[0] || "";
  if (preHow.indexOf("Live CI via") !== -1) fail("fetched-repo line must not lead the first phone screen");
  if (html.indexOf('id="fetched-line"') === -1) fail("fetched-repo line must remain inside How this board works");

  console.log("soft-paint / agent age-gate smoke ok");
}

run();
