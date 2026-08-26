#!/usr/bin/env node
"use strict";
/**
 * Fail-closed smoke: agent / PR / CI taps are real hrefs + iOS openBlank fallback.
 * No invented bc-ids. Auth stays gone.
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
  const refresh = fs.readFileSync(REFRESH, "utf8");
  const src = scriptsFrom(html);
  const paint = html.split("<script>")[0];

  if (refresh.indexOf("Open agent") === -1) fail("refresh.sh missing Open agent");
  if (refresh.indexOf("Open PR") === -1) fail("refresh.sh missing Open PR");
  if (refresh.indexOf("Open repo") === -1) fail("refresh.sh missing Open repo");
  if (refresh.indexOf("Open CI") === -1) fail("refresh.sh missing Open CI");
  if (refresh.indexOf("lane-links") === -1) fail("refresh.sh missing lane-links");
  if (refresh.indexOf('data-open="work"') === -1) fail("refresh.sh missing data-open=work");
  if (src.indexOf("function safeAgentUrl") === -1) fail("safeAgentUrl missing from page");
  if (src.indexOf("function laneHrefs") === -1) fail("laneHrefs missing from page");
  if (src.indexOf("function isBcId") === -1) fail("isBcId missing from page");
  if (src.indexOf("function workHref") === -1) fail("workHref missing from page");
  if (src.indexOf("function signalHref") === -1) fail("signalHref missing from page");
  if (src.indexOf("function safePullsUrl") === -1) fail("safePullsUrl missing from page");
  if (src.indexOf("function openWorkLink") === -1) fail("openWorkLink missing from page");
  if (src.indexOf("function handleWorkClick") === -1) fail("handleWorkClick missing from page");
  if (src.indexOf("window.openBlank = openBlank") === -1) fail("openBlank not exposed");
  if (src.indexOf("function openBlank") === -1) fail("openBlank missing");

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
  const safePullsUrl = eval(
    "(function (cleanPublicUrl) { return " + extractFn(src, "safePullsUrl") + "; })"
  )(cleanPublicUrl);
  const workHref = eval(
    "(function (safeAgentUrl, safePrUrl, safeActionsUrl, safePullsUrl, safeRepoUrl) { return " +
      extractFn(src, "workHref") +
      "; })"
  )(safeAgentUrl, safePrUrl, safeActionsUrl, safePullsUrl, safeRepoUrl);

  const good = "https://cursor.com/agents/bc-8e16f06d-f73f-482c-987f-e13f2d3b9fb1";
  if (safeAgentUrl(good) !== good) fail("good agent url dropped");
  if (safeAgentUrl("https://cursor.com/agents/bc-invented")) fail("short bc invented");
  if (safeAgentUrl("https://cursor.com/agents/" + "f".repeat(40))) fail("non-bc invented");
  if (safePrUrl("https://github.com/rupret007/webjam/pull/21") !== "https://github.com/rupret007/webjam/pull/21") {
    fail("safe PR url");
  }
  if (safeActionsUrl("https://github.com/rupret007/webjam/actions/runs/3") !== "https://github.com/rupret007/webjam/actions/runs/3") {
    fail("safe actions url");
  }
  if (workHref(good) !== good) fail("workHref must keep allowlisted agent");
  if (workHref("https://github.com/rupret007/webjam/pull/21") !== "https://github.com/rupret007/webjam/pull/21") {
    fail("workHref must keep allowlisted PR");
  }
  if (workHref("https://github.com/rupret007/webjam") !== "https://github.com/rupret007/webjam") {
    fail("workHref must keep allowlisted repo");
  }
  if (workHref("https://github.com/rupret007/webjam/actions/runs/3") !== "https://github.com/rupret007/webjam/actions/runs/3") {
    fail("workHref must keep allowlisted CI run");
  }
  if (workHref("https://github.com/rupret007/story-corner-shelf/pulls") !== "https://github.com/rupret007/story-corner-shelf/pulls") {
    fail("workHref must keep allowlisted pulls list");
  }
  if (workHref("https://github.com/rupret007/webjam") !== "https://github.com/rupret007/webjam") {
    fail("workHref must not rewrite repo home into /pulls");
  }
  if (workHref("https://evil.example/rupret007/webjam/pulls")) fail("workHref must drop foreign pulls");
  if (workHref("https://github.com/rupret007/webjam/pulls/1")) fail("workHref must drop pulls/N");
  if (workHref("https://evil.example/agents/bc-8e16f06d-f73f-482c-987f-e13f2d3b9fb1")) {
    fail("workHref must drop foreign host");
  }
  if (workHref("javascript:alert(1)")) fail("workHref must drop javascript:");

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
  const openBlank = eval(
    "(function (window, document) { return " + extractFn(src, "openBlank") + "; })"
  )(windowObj, documentObj);
  const openWorkLink = eval(
    "(function (workHref, window) { return " + extractFn(src, "openWorkLink") + "; })"
  )(workHref, { openBlank: openBlank });

  if (!openWorkLink(good)) fail("openWorkLink must open allowlisted agent");
  if (opened.length !== 1 || opened[0].url !== good) fail("openWorkLink must use openBlank");
  if (clicks.length !== 0) fail("openBlank fallback click must not run when window.open works");
  if (openWorkLink("https://evil.example/x")) fail("openWorkLink must refuse foreign URL");

  opened.length = 0;
  clicks.length = 0;
  windowObj.open = function (url, target, feat) {
    opened.push({ url: String(url), target: target, feat: feat });
    return null;
  };
  if (!openWorkLink("https://github.com/rupret007/webjam/pull/21")) {
    fail("blocked window.open must still return true via <a>.click");
  }
  if (opened.length !== 1) fail("blocked window.open still attempted");
  if (clicks.length !== 1) fail("iOS-blocked work popup must fall back to <a>.click");
  if (clicks[0] !== "https://github.com/rupret007/webjam/pull/21") {
    fail("fallback click href: " + clicks[0]);
  }

  const handleWorkClick = eval(
    "(function (workHref, openWorkLink) { return " + extractFn(src, "handleWorkClick") + "; })"
  )(workHref, openWorkLink);

  let prevented = false;
  const validEv = {
    target: {
      closest: function () {
        return {
          tagName: "A",
          getAttribute: function (k) {
            if (k === "href") return good;
            if (k === "target") return "_blank";
            return "";
          },
        };
      },
    },
    defaultPrevented: false,
    preventDefault: function () {
      prevented = true;
    },
  };
  opened.length = 0;
  clicks.length = 0;
  windowObj.open = function (url, target, feat) {
    opened.push({ url: String(url), target: target, feat: feat });
    return { name: "opened" };
  };
  if (!handleWorkClick(validEv)) fail("valid work <a> should be handled");
  if (prevented) fail("valid target=_blank must let native <a> proceed");
  if (opened.length !== 0) fail("native work <a> must not also window.open");

  prevented = false;
  const badEv = {
    target: {
      closest: function () {
        return {
          tagName: "A",
          getAttribute: function (k) {
            if (k === "href") return "https://evil.example/x";
            if (k === "target") return "_blank";
            return "";
          },
        };
      },
    },
    defaultPrevented: false,
    preventDefault: function () {
      prevented = true;
    },
  };
  if (handleWorkClick(badEv)) fail("foreign work href must fail closed");
  if (!prevented) fail("foreign work href must preventDefault");

  prevented = false;
  opened.length = 0;
  const fallbackEv = {
    target: {
      closest: function () {
        return {
          tagName: "A",
          getAttribute: function (k) {
            if (k === "href") return good;
            if (k === "target") return "";
            return "";
          },
        };
      },
    },
    defaultPrevented: false,
    preventDefault: function () {
      prevented = true;
    },
  };
  if (!handleWorkClick(fallbackEv)) fail("missing target must use openBlank");
  if (!prevented) fail("openBlank fallback path must preventDefault");
  if (opened.length !== 1 || opened[0].url !== good) fail("fallback must openBlank the allowlisted href");

  if (paint.indexOf('<span class="signal">1 open PR</span>') !== -1) {
    fail("first paint still has dead 1 open PR text");
  }
  if (paint.indexOf('<span class="signal">4 open PRs</span>') !== -1) {
    fail("first paint still has dead N open PRs text");
  }
  const openPrSignals = Array.from(
    paint.matchAll(/<a class="signal" data-open="work" href="([^"]+)"[^>]*>(\d+) open PRs?<\/a>/g)
  );
  openPrSignals.forEach(function (match) {
    const href = match[1];
    const count = Number(match[2]);
    if (count === 1 && !safePrUrl(href)) {
      fail("first-paint 1 open PR signal must tap one allowlisted PR: " + href);
    }
    if (count > 1 && !safePullsUrl(href)) {
      fail("first-paint N open PRs signal must tap one allowlisted pulls list: " + href);
    }
  });
  const stackSignals = Array.from(
    paint.matchAll(/<a class="signal" data-open="work" href="([^"]+)"[^>]*>(?:Stack #[^<]+|[0-9]+-PR stack)<\/a>/g)
  );
  stackSignals.forEach(function (match) {
    if (!safePullsUrl(match[1])) {
      fail("first-paint stack signal must tap one allowlisted pulls list: " + match[1]);
    }
  });

  const fakeBc = paint.match(/cursor\.com\/agents\/bc-[^"'?\s<]+/g) || [];
  fakeBc.forEach(function (u) {
    const canon = safeAgentUrl("https://" + u.replace(/^https:\/\//, ""));
    if (!canon) fail("first paint has unsafe agent href: " + u);
  });

  const macBlock = paint.split('id="agents-strip"')[1] || "";
  if (macBlock.indexOf("cursor.com/agents/bc-nope") !== -1) fail("invented bc-nope painted");

  console.log("open-links smoke ok");
}

run();
