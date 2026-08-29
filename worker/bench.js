import { readFileSync } from "node:fs";
import { extractNextChunks, findGoals, findCards, findSubs, findFormations, findLineupMembers } from "./parser.js";
const html = readFileSync(new URL("../data/tmp/sample_match_livetxt.html", import.meta.url), "utf8");
const t = (label, fn) => {
  const s = process.hrtime.bigint();
  const r = fn();
  const ms = Number(process.hrtime.bigint() - s) / 1e6;
  console.log(`  ${label.padEnd(22)} ${ms.toFixed(1)} ms`);
  return [r, ms];
};
console.log(`HTMLサイズ: ${(html.length/1024/1024).toFixed(2)} MB`);
let total = 0;
const [chunks, m0] = t("extractNextChunks", () => extractNextChunks(html)); total += m0;
total += t("findGoals", () => findGoals(chunks))[1];
total += t("findCards", () => findCards(chunks))[1];
total += t("findSubs", () => findSubs(chunks))[1];
total += t("findFormations", () => findFormations(chunks))[1];
total += t("findLineupMembers", () => findLineupMembers(chunks))[1];
console.log(`  ${"合計".padEnd(20)} ${total.toFixed(1)} ms  (Free枠は10ms)`);
