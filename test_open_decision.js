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
  const paint = html.split("<script>")[0];
  if (paint.indexOf('id="pending-box"') === -1) fail("pending-box missing");
  if (paint.indexOf("pending-item") !== -1 && /id="pending-box"[^>]*\bhidden\b/.test(paint)) {
    fail("pending-box must not start hidden when items are painted");
  }
  if (paint.indexOf('class="pulse"') === -1) fail("pulse strip missing");
  if (paint.indexOf("abilities-foot") === -1) fail("abilities must be collapsed footer");
  if (paint.indexOf("Nothing pending") !== -1) fail("empty-pending chrome leaked");
  if (paint.indexOf('data-dec="APPROVE"') === -1) fail("Approve control missing");
  if (paint.indexOf("issues/new?") === -1) fail("first-paint decision links missing");
  if (paint.indexOf('rel="noopener noreferrer"') === -1) fail("decision links need noreferrer");
  if (src.indexOf("function decisionHref") === -1) fail("decisionHref missing");
  if (src.indexOf("function openBlank") === -1) fail("openBlank missing");

  const decisionHref = eval("(" + extractFn(src, "decisionHref") + ")");
  const href = decisionHref("APPROVE", "dashboard-refresh", "Force dashboard refresh + push");
  if (href.indexOf("https://github.com/rupret007/bob-ops-dashboard/issues/new?") !== 0) {
    fail("decisionHref host/path: " + href);
  }
  if (decodeURIComponent(href).indexOf("BOB-APPROVE: dashboard-refresh") === -1) {
    fail("decisionHref missing BOB-APPROVE title");
  }
  if (decodeURIComponent(href).indexOf("at:") !== -1) fail("decisionHref must be timestamp-stable");
  if (decisionHref("DELETE", "dashboard-refresh", "nope")) fail("unknown verb must be empty");
  if (decisionHref("APPROVE", "bad id", "nope")) fail("unsafe id must be empty");
  const pyHref = require("child_process")
    .execFileSync(
      "python3",
      [
        "-c",
        "from board_meta import decision_href; print(decision_href('APPROVE','dashboard-refresh','Force dashboard refresh + push'))",
      ],
      { cwd: ROOT, encoding: "utf8" }
    )
    .trim();
  function qs(u) {
    const out = {};
    String(u.split("?")[1] || "")
      .split("&")
      .forEach(function (part) {
        const i = part.indexOf("=");
        if (i < 0) return;
        const k = decodeURIComponent(part.slice(0, i).replace(/\+/g, " "));
        const v = decodeURIComponent(part.slice(i + 1).replace(/\+/g, " "));
        out[k] = v;
      });
    return out;
  }
  const jsQ = qs(href);
  const pyQ = qs(pyHref);
  if (jsQ.title !== pyQ.title || jsQ.body !== pyQ.body) {
    fail("JS decisionHref must match Python decision_href");
  }

  let lastStatus = "";
  function setStatus(msg) {
    lastStatus = String(msg || "");
  }
  const decideBusy = {};
  const opened = [];
  const clicks = [];
  const windowObj = {
    open: function (url, target, feat) {
      opened.push({ url: String(url), target: target, feat: feat });
      return { name: "opened" };
    },
  };
  const documentObj = {
    body: {
      appendChild: function () {},
      removeChild: function () {},
    },
    createElement: function () {
      return {
        style: {},
        href: "",
        target: "",
        rel: "",
        click: function () {
          clicks.push(this.href);
        },
        remove: function () {},
      };
    },
  };
  const openDecisionIssue = eval(
    "(function (setStatus, decideBusy, window, document) {\n" +
      extractFn(src, "decisionHref") +
      ";\n" +
      extractFn(src, "openBlank") +
      ";\n" +
      "return " +
      extractFn(src, "openDecisionIssue") +
      ";\n" +
      "})"
  )(setStatus, decideBusy, windowObj, documentObj);

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
  if (clicks.length !== 0) fail("fallback click must not run when window.open works");

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

  Object.keys(decideBusy).forEach(function (k) {
    delete decideBusy[k];
  });
  opened.length = 0;
  clicks.length = 0;
  windowObj.open = function (url, target, feat) {
    opened.push({ url: String(url), target: target, feat: feat });
    return null;
  };
  openDecisionIssue("APPROVE", "text-send", "Send a drafted text via Andrea");
  if (opened.length !== 1) fail("blocked window.open still attempted");
  if (clicks.length !== 1) fail("iOS-blocked popup must fall back to <a>.click");
  if (String(clicks[0]).indexOf("BOB-APPROVE") === -1 && decodeURIComponent(String(clicks[0])).indexOf("BOB-APPROVE") === -1) {
    fail("fallback click href missing BOB-APPROVE: " + clicks[0]);
  }

  console.log("open-decision smoke ok");
}

run();
