/* parser.js(JS移植版)の出力が、Python版(match_events_parser.py)と一致するか検証する。
   使い方: node worker/verify_parser.js  (先にPython側で /tmp/expected.json を作っておく) */
import { readFileSync } from "node:fs";
import { extractNextChunks, findGoals, findCards, findSubs, findFormations, findLineupMembers, findHighlightVideoId } from "./parser.js";

const html = readFileSync(new URL("../data/tmp/sample_match_livetxt.html", import.meta.url), "utf8");
const chunks = extractNextChunks(html);
const actual = {
  chunkCount: chunks.size,
  goals: findGoals(chunks),
  cards: findCards(chunks),
  subs: findSubs(chunks),
  formations: findFormations(chunks),
  lineups: findLineupMembers(chunks),
  highlightVideoId: findHighlightVideoId(chunks),
};
const expected = JSON.parse(readFileSync("/tmp/expected.json", "utf8"));

let ng = 0;
for (const key of Object.keys(expected)) {
  const a = JSON.stringify(actual[key]), e = JSON.stringify(expected[key]);
  const ok = a === e;
  if (!ok) ng++;
  const size = Array.isArray(actual[key]) ? actual[key].length
    : (actual[key] && typeof actual[key] === "object" ? Object.keys(actual[key]).length : actual[key]);
  console.log(`${ok ? "OK " : "NG "} ${key}: ${size}`);
  if (!ok) {
    console.log("   期待:", e.slice(0, 200));
    console.log("   実際:", a.slice(0, 200));
  }
}
console.log(ng ? `\n*** ${ng}件が不一致 ***` : "\n全項目がPython版と一致");
