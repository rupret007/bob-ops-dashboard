#!/usr/bin/env node
"use strict";
/**
 * Deep-detail P0: in-board history+detail helpers stay fail-closed and public-safe.
 * Extracts source from refresh.sh so scheduler-owned index.html is not required.
 */
const fs = require("fs");
const path = require("path");

const ROOT = path.dirname(__filename);
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

function scriptFromRefresh(refresh) {
  const start = refresh.indexOf("<script>\nfunction focusKey");
  const end = refresh.lastIndexOf("</script>");
  if (start < 0 || end < start) throw new Error("missing dashboard script in refresh.sh");
  return refresh.slice(start, end).replace(/\{\{/g, "{").replace(/\}\}/g, "}");
}

function run() {
  const refresh = fs.readFileSync(REFRESH, "utf8");
  const required = [
    "function laneIsHighLevel",
    "function sortHistoryEntries",
    "function projectHistory",
    "function addProjectFacts",
    "function relatedForWork",
    "function relatedForProject",
    "function backDetail",
    "data-detail-nav",
    'id="detail-history"',
    'id="detail-related"',
    "High-level only",
  ];
  for (const marker of required) {
    if (refresh.indexOf(marker) === -1) fail("refresh.sh missing " + marker);
  }

  const src = scriptFromRefresh(refresh);
  const laneIsHighLevel = eval("(" + extractFn(src, "laneIsHighLevel") + ")");
  const sortHistoryEntries = eval("(" + extractFn(src, "sortHistoryEntries") + ")");

  if (laneIsHighLevel(null) !== true) fail("missing lane must be high-level");
  if (laneIsHighLevel({ private: true, accessible: true }) !== true) fail("private lane must be high-level");
  if (laneIsHighLevel({ private: false, accessible: false }) !== true) fail("inaccessible lane must be high-level");
  if (laneIsHighLevel({ private: false, accessible: true }) !== false) fail("public accessible lane must expose detail");
  if (laneIsHighLevel({ name: "StoryLiner" }) !== false) fail("unmarked public lane must still expose detail");

  const sorted = sortHistoryEntries(
    [null, { text: "old", at: 10 }, { text: "new", at: 30 }, { text: "mid", at: 20 }],
    2
  );
  if (sorted.length !== 2) fail("history cap must apply");
  if (sorted[0].text !== "new" || sorted[1].text !== "mid") fail("history must sort newest first");
  const filled = sortHistoryEntries(new Array(12).fill({ text: "x", at: 1 }));
  if (filled.length !== 8) fail("default history cap is 8");

  if (src.indexOf("laneIsHighLevel(project)") === -1) fail("project facts/history must consult laneIsHighLevel");
  if (src.indexOf('addFact(meta, "Visibility", "High-level only"') === -1) {
    fail("high-level lanes must get an honest visibility fact");
  }
  if (src.indexOf("/^[0-9a-f]{7,40}$/") === -1) fail("shortSha quantifier must survive f-string rendering");

  console.log("detail-sheet deep-detail P0 smoke ok");
}

run();
