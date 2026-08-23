#!/usr/bin/env node
"use strict";
/**
 * Fail-closed smoke: Approve/Hold/Deny opens a GitHub issue draft.
 * Public board -- no verified-device claim, no OTP, no Unlock gate.
 */
const fs = require("fs");
const path = require("path");

const ROOT = path.dirname(__filename);
const INDEX = process.argv[2] || path.join(ROOT, "index.html");
const REFRESH = path.join(ROOT, "refresh.sh");

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

function scriptsFrom(html) {
  return html.split("<script>").slice(1).map((s) => s.split("</script>")[0]).join("\n");
}

function run() {
  const html = fs.readFileSync(INDEX, "utf8");
  const refresh = fs.readFileSync(REFRESH, "utf8");
  const src = scriptsFrom(html);

  const banned = [
    "doUnlock",
    "btn-confirm-code",
    "jeff-code",
    "ask-code",
    "How to ask",
    "Ask Bob for a new code",
    "loadUnlockStatus",
    "hasUnlockVerify",
    "unlockBusy",
    "jeff-verified",
    "verified device",
    "Unlocked on this phone",
    "No code active yet",
  ];
  banned.forEach(function (needle) {
    if (html.indexOf(needle) !== -1) fail("index.html still has " + needle);
    if (refresh.indexOf(needle) !== -1) fail("refresh.sh still has " + needle);
  });

  if (html.indexOf("Public board -- Approve opens a GitHub issue") === -1) {
    fail("missing public-board note");
  }
  if (html.indexOf('id="pending-box"') === -1) fail("pending-box missing");
  if (/id="pending-box"[^>]*\bhidden\b/.test(html)) {
    fail("pending-box must not start hidden");
  }

  let lastStatus = "";
  function setStatus(msg) {
    lastStatus = String(msg || "");
  }
  const decideBusy = {};
  const opened = [];
  const windowObj = {
    open: function (url, target, feat) {
      opened.push({ url: String(url), target: target, feat: feat });
    },
  };
  // Bind names the extracted function closes over via eval locals.
  const fnSrc = extractFn(src, "openDecisionIssue");
  const openDecisionIssue = eval(
    "(function (setStatus, decideBusy, window) { return " + fnSrc + "; })"
  )(setStatus, decideBusy, windowObj);

  openDecisionIssue("APPROVE", "dashboard-refresh", "Force dashboard refresh + push");
  if (opened.length !== 1) fail("expected one window.open");
  const u = opened[0].url;
  if (u.indexOf("https://github.com/rupret007/bob-ops-dashboard/issues/new?") !== 0) {
    fail("wrong issue URL host/path: " + u);
  }
  if (decodeURIComponent(u).indexOf("BOB-APPROVE: dashboard-refresh") === -1) {
    fail("missing BOB-APPROVE title");
  }
  if (u.toLowerCase().indexOf("verified") !== -1) fail("issue URL still says verified");
  if (lastStatus.indexOf("rupret007") === -1) fail("status must name rupret007");

  const before = opened.length;
  openDecisionIssue("APPROVE", "dashboard-refresh", "Force dashboard refresh + push");
  if (opened.length !== before) fail("decideBusy must debounce double Approve");

  opened.length = 0;
  lastStatus = "";
  openDecisionIssue("HOLD", "text-send", "Send a drafted text via Andrea");
  if (opened.length !== 1) fail("HOLD should open");
  if (decodeURIComponent(opened[0].url).indexOf("BOB-HOLD: text-send") === -1) {
    fail("missing BOB-HOLD title");
  }

  opened.length = 0;
  openDecisionIssue("DENY", "bad id", "nope");
  if (opened.length !== 0) fail("unsafe id must not open");

  opened.length = 0;
  openDecisionIssue("DELETE", "dashboard-refresh", "nope");
  if (opened.length !== 0) fail("unknown verb must not open");

  console.log("open-decision smoke ok");
}

run();
